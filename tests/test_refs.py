"""Reference inputs reaching every provider (goal item 7).

One resolver (`providers/refs.resolve_refs`) with a shared tiering + lineage
contract, and per-adapter delivery: generic_cloud base64/url/multipart, the real
comfyui POST /upload/image, local_cmd {video_ref}. Every delivery mode is
exercised against a scripted transport; the zero-cost pre-submit validation and
the build-time reliability signals are pinned too. The Ken Burns refactor keeps
its own suite (test_round_a::test_kenburns_generic_ref_advisory) green.
"""

from __future__ import annotations

import base64
import json
import types

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.check import run_check
from manju.core.refs import RefsError, assign_ref, refs_report
from manju.core.yamlio import read_yaml, write_yaml
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.comfyui import ComfyUIProvider
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.jsonpath import JsonPathError, assign, extract
from manju.providers.local_cmd import LocalCommandProvider
from manju.providers.manifest import (
    COMFYUI_ADAPTER,
    LOCAL_CMD_ADAPTER,
    ProviderManifest,
)
from manju.providers.refs import (
    TIER_BIBLE,
    TIER_PARAMS,
    TIER_REFS_DIR,
    TIER_SHOT,
    RefSet,
    encode_multipart,
    ref_reliability_notes,
    resolve_refs,
)

runner = CliRunner()


# --------------------------------------------------------------- fixtures


def _touch(project, rel, data=b"img"):
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if not self.script:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.script.pop(0)


def _resp(status, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(status, {}, body)


# =============================================================== resolver


def test_tiering_params_over_shot_over_bible_over_dir(tmp_project, add_shot):
    """Most-specific tier wins for the primary image, and lineage names it."""
    _touch(tmp_project, "media/refs/param.png")
    _touch(tmp_project, "media/refs/shotref.png")
    _touch(tmp_project, "media/refs/bible.png")
    _touch(tmp_project, "media/refs/zzz_generic.png")

    # bible ref_image on the character
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_image"] = "media/refs/bible.png"

    # params beats everything
    shot = add_shot(tmp_project, "S001",
                    refs={"images": ["media/refs/shotref.png"]},
                    generation={"params": {"image": "media/refs/param.png"}})
    rs = resolve_refs(tmp_project, shot, bible)
    assert rs.primary_image.name == "param.png"
    assert rs.primary_image_source == TIER_PARAMS
    assert rs.lineage["primary_image"]["tier"] == TIER_PARAMS

    # drop params -> shot refs win
    shot2 = add_shot(tmp_project, "S002", refs={"images": ["media/refs/shotref.png"]})
    rs2 = resolve_refs(tmp_project, shot2, bible)
    assert rs2.primary_image.name == "shotref.png"
    assert rs2.primary_image_source == TIER_SHOT

    # drop shot refs -> bible wins
    shot3 = add_shot(tmp_project, "S003")
    rs3 = resolve_refs(tmp_project, shot3, bible)
    assert rs3.primary_image.name == "bible.png"
    assert rs3.primary_image_source == TIER_BIBLE

    # no declared ref anywhere -> media/refs fallback (first by name)
    shot4 = add_shot(tmp_project, "S004", characters=[], scene=None)
    rs4 = resolve_refs(tmp_project, shot4, {})
    assert rs4.primary_image.name == "bible.png"  # first alphabetically present
    assert rs4.primary_image_source == TIER_REFS_DIR
    assert not rs4.has_declared_refs()


def test_video_refs_and_url_refs(tmp_project, add_shot):
    _touch(tmp_project, "media/refs/clip.mp4", b"video")
    shot = add_shot(tmp_project, "S001", generation={"params": {
        "video": "media/refs/clip.mp4",
        "image": "https://cdn.example.com/hero.png",
    }})
    rs = resolve_refs(tmp_project, shot, {})
    # local video resolves to a Path; the URL image stays a URL item (no Path)
    assert rs.primary_video.name == "clip.mp4"
    assert rs.primary_image is None  # a URL has no local path
    img_items = rs.image_items()
    assert img_items and img_items[0].is_url and img_items[0].ref.startswith("https://")
    assert rs.video_items()[0].tier == TIER_PARAMS


def test_missing_declared_ref_is_recorded_not_silently_dropped(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/nope.png"}})
    rs = resolve_refs(tmp_project, shot, {})
    items = rs.image_items()
    assert items and items[0].tier == TIER_PARAMS and items[0].exists is False
    assert rs.primary_image is None  # missing file never becomes the primary


def test_redo_params_override_drives_params_tier(tmp_project, add_shot):
    """resolve_refs(..., params=...) reads the REQUEST params (a redo carries the
    reused take's image there, not in shot.generation.params)."""
    _touch(tmp_project, "media/refs/reused.png")
    shot = add_shot(tmp_project, "S001")
    rs = resolve_refs(tmp_project, shot, {}, params={"image": "media/refs/reused.png"})
    assert rs.primary_image.name == "reused.png"
    assert rs.primary_image_source == TIER_PARAMS


# =============================================================== jsonpath


def test_assign_creates_and_sets():
    body = {"input": {"prompt": "x"}}
    assign(body, "$.input.image_url", "DATA")
    assert body["input"]["image_url"] == "DATA"
    assign(body, "top", 5)
    assert body["top"] == 5
    assign(body, "$.a.b.c", "deep")  # creates intermediate dicts
    assert body["a"]["b"]["c"] == "deep"
    assert extract(body, "$.a.b.c") == "deep"
    with pytest.raises(JsonPathError):
        assign(body, "", "x")


# ======================================================= generic_cloud


def _cloud_manifest(refs, **overrides):
    base = {
        "id": "video_x",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["image_to_video"],
        "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}", "image_url": "", "video_url": ""},
            "job_id_path": "$.data.task_id",
        },
        "poll": {
            "url": "https://api.example.com/v1/videos/{job_id}",
            "status_path": "$.data.status",
            "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed",
                           "PROCESSING": "running"},
            "result_url_path": "$.data.video_url",
        },
        "cost": {"per_second": 0.0, "currency": "CNY"},
        "refs": refs,
    }
    base.update(overrides)
    return ProviderManifest.model_validate(base)


def _cloud_req(tmp_project, add_shot, **params):
    shot = add_shot(tmp_project, "S001", generation={"params": params})
    return GenerationRequest(
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="sha256:x", duration_ms=3000, candidates=1, params=dict(params),
    )


def test_generic_cloud_base64_field_embeds_data_uri(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    raw = b"\x89PNG-bytes"
    _touch(tmp_project, "media/refs/hero.png", raw)
    manifest = _cloud_manifest({"image_mode": "base64_field", "field": "$.image_url",
                                "max_images": 1})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _cloud_req(tmp_project, add_shot, image="media/refs/hero.png")

    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    expect = "data:image/png;base64," + base64.b64encode(raw).decode()
    assert sent["image_url"] == expect
    # delivery lineage recorded on the request params (lands on the take)
    assert req.params["ref_delivery"]["images"][0]["delivered_as"] == "base64_field"
    assert req.params["ref_delivery"]["images"][0]["tier"] == TIER_PARAMS


def test_generic_cloud_base64_raw_no_prefix_for_kling(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    raw = b"jpegbytes"
    _touch(tmp_project, "media/refs/hero.jpg", raw)
    manifest = _cloud_manifest({"image_mode": "base64_field", "field": "$.image_url",
                                "data_uri": False})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    provider.submit(_cloud_req(tmp_project, add_shot, image="media/refs/hero.jpg"))
    sent = json.loads(transport.requests[0][3])
    assert sent["image_url"] == base64.b64encode(raw).decode()  # no data: prefix


def test_generic_cloud_url_field_passes_url(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    manifest = _cloud_manifest({"image_mode": "url_field", "field": "$.image_url"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    provider.submit(_cloud_req(tmp_project, add_shot,
                               image="https://cdn.example.com/hero.png"))
    sent = json.loads(transport.requests[0][3])
    assert sent["image_url"] == "https://cdn.example.com/hero.png"


def test_generic_cloud_url_field_local_path_is_config_error(tmp_project, add_shot,
                                                            monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    _touch(tmp_project, "media/refs/hero.png")
    manifest = _cloud_manifest({"image_mode": "url_field", "field": "$.image_url"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.submit(_cloud_req(tmp_project, add_shot, image="media/refs/hero.png"))
    assert exc.value.kind is FailureKind.invalid
    assert "$.image_url" in str(exc.value)  # names the manifest field
    assert transport.requests == []  # errored BEFORE the paid POST


def test_generic_cloud_missing_ref_errors_before_submit(tmp_project, add_shot,
                                                        monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    manifest = _cloud_manifest({"image_mode": "base64_field", "field": "$.image_url"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.submit(_cloud_req(tmp_project, add_shot, image="media/refs/gone.png"))
    assert exc.value.kind is FailureKind.invalid
    assert "media/refs/gone.png" in str(exc.value) and "params" in str(exc.value)
    assert transport.requests == []  # zero-cost failure


def test_absolute_ref_path_is_refused_not_read(tmp_project, add_shot, tmp_path):
    """goal item 13: an absolute local path must be REFUSED at resolution —
    never resolved to the real outside-project file. Before this fix,
    ``refs._as_path`` returned the absolute path AS-IS whenever it was
    absolute, so a downstream provider would read and upload it."""
    outside = tmp_path / "outside_secret.png"
    outside.write_bytes(b"secret-bytes")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": str(outside)}})
    rs = resolve_refs(tmp_project, shot, {})
    item = rs.image_items()[0]
    assert item.path is None
    assert not item.exists
    assert item.blocked_reason is not None
    assert "把文件放进项目" in item.blocked_reason
    from manju.providers.refs import unreadable_ref_message

    assert unreadable_ref_message([item]) == item.blocked_reason


def test_escaping_relative_ref_path_is_refused(tmp_project, add_shot, tmp_path):
    """A relative path that resolves OUTSIDE the project root via ``../`` is
    refused the same way an absolute path is."""
    import os

    outside = tmp_path / "outside_secret2.png"
    outside.write_bytes(b"secret-bytes")
    escaping = os.path.relpath(str(outside), start=str(tmp_project.root))
    assert escaping.startswith("..")  # sanity: genuinely escapes
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": escaping}})
    rs = resolve_refs(tmp_project, shot, {})
    item = rs.image_items()[0]
    assert item.path is None
    assert not item.exists
    assert item.blocked_reason is not None


def test_generic_cloud_absolute_ref_refuses_before_submit(tmp_project, add_shot,
                                                           monkeypatch, tmp_path):
    """End-to-end: an absolute ref path must never reach base64/multipart
    encoding (which would read the file) — the provider refuses BEFORE any
    HTTP call, and the outside file's bytes never appear anywhere."""
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    outside = tmp_path / "outside_ref.png"
    outside.write_bytes(b"TOP-SECRET-OUTSIDE-REF-BYTES")
    manifest = _cloud_manifest({"image_mode": "base64_field", "field": "$.image_url"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.submit(_cloud_req(tmp_project, add_shot, image=str(outside)))
    assert exc.value.kind is FailureKind.invalid
    assert "把文件放进项目" in str(exc.value)
    assert transport.requests == []  # refused before the paid POST


def test_generic_cloud_multipart_shape(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    raw = b"filebytes"
    _touch(tmp_project, "media/refs/hero.png", raw)
    manifest = _cloud_manifest({"image_mode": "multipart", "multipart_field": "image"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    provider.submit(_cloud_req(tmp_project, add_shot, image="media/refs/hero.png"))
    method, url, headers, body = transport.requests[0]
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="image"; filename="hero.png"' in body
    assert raw in body
    # JSON body fields ride alongside as text parts
    assert b'name="prompt"' in body


def test_generic_cloud_video_url_field(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    manifest = _cloud_manifest({"video_mode": "url_field", "video_field": "$.video_url"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    provider.submit(_cloud_req(tmp_project, add_shot,
                               video="https://cdn.example.com/base.mp4"))
    sent = json.loads(transport.requests[0][3])
    assert sent["video_url"] == "https://cdn.example.com/base.mp4"


def test_manifest_refs_validation():
    m = ProviderManifest.model_validate({
        "id": "x", "adapter": "generic_cloud",
        "submit": {"url": "https://a/x", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://a/{job_id}", "status_path": "$.s",
                 "status_map": {"S": "succeeded"}},
        "refs": {"image_mode": "base64_field"},  # missing field
    })
    problems = m.validate_for_generic()
    assert any("refs.field" in p for p in problems)


# ============================================================== comfyui

_WF = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 0}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    "52": {"class_type": "LoadImage", "inputs": {"image": ""}},
    "9": {"class_type": "VHS_VideoCombine", "inputs": {}},
}


def _comfy_manifest(tmp_project, input_map):
    wf = tmp_project.root / "comfyui" / "wf.json"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(json.dumps(_WF), encoding="utf-8")
    return ProviderManifest.model_validate({
        "id": "comfyui", "type": "video", "adapter": COMFYUI_ADAPTER,
        "capabilities": ["image_to_video"], "cost": {"per_call": 0.0},
        "comfyui": {"base_url": "http://127.0.0.1:9999", "workflow_file": "comfyui/wf.json",
                    "input_map": input_map, "poll_interval_s": 0.01},
    })


def test_comfyui_upload_substitutes_loadimage_name(tmp_project, add_shot):
    raw = b"PNGREF"
    _touch(tmp_project, "media/refs/hero.png", raw)
    manifest = _comfy_manifest(tmp_project,
                               {"6.text": "{prompt}", "52.image": "{image_upload}"})
    transport = ScriptedTransport([
        _resp(200, {"name": "hero.png", "subfolder": "", "type": "input"}),  # /upload/image
        _resp(200, {"prompt_id": "p1", "node_errors": {}}),                  # /prompt
        _resp(200, {"p1": {"outputs": {"9": {"gifs": [
            {"filename": "out.mp4", "subfolder": "", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True, "messages": []}}}),
        HttpResponse(200, {}, b"VID"),                                       # /view
    ])
    provider = ComfyUIProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/hero.png"}})
    req = GenerationRequest(project=tmp_project, shot=shot,
                            bible=tmp_project.load_bible(), spec_hash="sha256:x",
                            duration_ms=3000, candidates=1,
                            params={"image": "media/refs/hero.png"})
    takes = provider.generate(req)

    # the upload came first and was a real multipart POST carrying the file
    up_method, up_url, up_headers, up_body = transport.requests[0]
    assert up_method == "POST" and up_url.endswith("/upload/image")
    assert up_headers["Content-Type"].startswith("multipart/form-data")
    assert raw in up_body and b'filename="hero.png"' in up_body

    # the returned name landed on the LoadImage node's image input
    graph = json.loads(transport.requests[1][3])["prompt"]
    assert graph["52"]["inputs"]["image"] == "hero.png"

    # lineage records the upload
    rd = takes[0].sidecar.params["ref_delivery"]
    assert rd["image_mode"] == "upload"
    assert rd["images"][0]["uploaded"]["name"] == "hero.png"


def test_comfyui_upload_subfolder_join(tmp_project, add_shot):
    _touch(tmp_project, "media/refs/hero.png")
    manifest = _comfy_manifest(tmp_project, {"52.image": "{image_upload}"})
    transport = ScriptedTransport([
        _resp(200, {"name": "hero.png", "subfolder": "manju", "type": "input"}),
        _resp(200, {"prompt_id": "p1"}),
        _resp(200, {"p1": {"outputs": {"9": {"images": [
            {"filename": "o.png", "subfolder": "", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True, "messages": []}}}),
        HttpResponse(200, {}, b"IMG"),
    ])
    provider = ComfyUIProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/hero.png"}})
    req = GenerationRequest(project=tmp_project, shot=shot,
                            bible=tmp_project.load_bible(), spec_hash="sha256:x",
                            duration_ms=3000, candidates=1,
                            params={"image": "media/refs/hero.png"})
    provider.generate(req)
    graph = json.loads(transport.requests[1][3])["prompt"]
    assert graph["52"]["inputs"]["image"] == "manju/hero.png"  # subfolder joined


def test_comfyui_image_upload_without_ref_is_invalid(tmp_project, add_shot):
    manifest = _comfy_manifest(tmp_project, {"52.image": "{image_upload}"})
    provider = ComfyUIProvider(manifest, transport=ScriptedTransport([]),
                               sleep_fn=lambda s: None)
    shot = add_shot(tmp_project, "S001", characters=[], scene=None)
    req = GenerationRequest(project=tmp_project, shot=shot, bible={},
                            spec_hash="sha256:x", duration_ms=3000, candidates=1,
                            params={})
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.kind is FailureKind.invalid
    assert "{image_upload}" in str(exc.value)


def test_comfyui_raw_image_placeholder_stays_a_path(tmp_project, add_shot):
    """{image} is a raw path (no upload) for path-based workflows."""
    hero = _touch(tmp_project, "media/refs/hero.png")
    manifest = _comfy_manifest(tmp_project, {"52.image": "{image}"})
    transport = ScriptedTransport([
        _resp(200, {"prompt_id": "p1"}),  # NO /upload/image call
        _resp(200, {"p1": {"outputs": {"9": {"images": [
            {"filename": "o.png", "subfolder": "", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True, "messages": []}}}),
        HttpResponse(200, {}, b"IMG"),
    ])
    provider = ComfyUIProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/hero.png"}})
    req = GenerationRequest(project=tmp_project, shot=shot,
                            bible=tmp_project.load_bible(), spec_hash="sha256:x",
                            duration_ms=3000, candidates=1,
                            params={"image": "media/refs/hero.png"})
    provider.generate(req)
    assert transport.requests[0][1].endswith("/prompt")  # first call is /prompt
    graph = json.loads(transport.requests[0][3])["prompt"]
    assert graph["52"]["inputs"]["image"] == str(hero)  # raw absolute path


# ============================================================= local_cmd


def _localcmd(command):
    return ProviderManifest.model_validate({
        "id": "svd", "type": "video", "adapter": LOCAL_CMD_ADAPTER,
        "capabilities": ["image_to_video"], "cost": {"per_call": 0.0},
        "local_cmd": {"command": command, "timeout_s": 10.0, "output_ext": ".mp4"},
    })


def test_local_cmd_video_ref_placeholder_and_env(tmp_project, add_shot, tmp_path):
    _touch(tmp_project, "media/refs/base.mp4", b"video")
    # the script copies MANJU_VIDEO_REF (env passthrough) into {out}
    script = tmp_path / "gen.sh"
    script.write_text('#!/bin/sh\nprintf "%s" "$MANJU_VIDEO_REF" > "$3"\n', encoding="utf-8")
    script.chmod(0o755)
    provider = LocalCommandProvider(_localcmd(f"sh {script} --video {{video_ref}} {{out}}"))
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"video": "media/refs/base.mp4"}})
    req = GenerationRequest(project=tmp_project, shot=shot,
                            bible=tmp_project.load_bible(), spec_hash="sha256:x",
                            duration_ms=3000, candidates=1,
                            params={"video": "media/refs/base.mp4"})
    takes = provider.generate(req)
    base_abs = str(tmp_project.resolve("media/refs/base.mp4"))
    # {video_ref} substituted as the absolute path, one argv element
    assert base_abs in takes[0].sidecar.params["argv"]
    # env passthrough (MANJU_VIDEO_REF) matched it
    assert takes[0].media_path.read_text(encoding="utf-8") == base_abs
    rd = takes[0].sidecar.params["ref_delivery"]
    assert rd["video_mode"] == "path" and rd["videos"][0]["tier"] == TIER_PARAMS


# ============================================================ reliability


def _fake_take(provider, ref_delivery):
    sidecar = types.SimpleNamespace(provider=provider,
                                    params={"ref_delivery": ref_delivery})
    return types.SimpleNamespace(sidecar=sidecar)


def test_reliability_advisory_when_provider_drops_refs(tmp_project, add_shot):
    _touch(tmp_project, "media/refs/hero.png")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/hero.png"}})
    rs = resolve_refs(tmp_project, shot, {}, params={"image": "media/refs/hero.png"})
    # provider recorded image_mode none -> silent drop becomes a visible advisory
    take = _fake_take("video_x", {"image_mode": "none", "images": [], "videos": []})
    notes = ref_reliability_notes("S001", rs, [take])
    assert notes and "S001" in notes[0] and "none" in notes[0]


def test_reliability_tier_mismatch_when_zero_delivered(tmp_project, add_shot):
    _touch(tmp_project, "media/refs/hero.png")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/hero.png"}})
    rs = resolve_refs(tmp_project, shot, {}, params={"image": "media/refs/hero.png"})
    # provider DECLARES support (base64_field) yet delivered zero images
    take = _fake_take("video_x", {"image_mode": "base64_field", "images": [], "videos": []})
    notes = ref_reliability_notes("S001", rs, [take])
    assert notes and "tier mismatch" in notes[0]


def test_reliability_silent_when_delivered_ok(tmp_project, add_shot):
    _touch(tmp_project, "media/refs/hero.png")
    shot = add_shot(tmp_project, "S001",
                    generation={"params": {"image": "media/refs/hero.png"}})
    rs = resolve_refs(tmp_project, shot, {}, params={"image": "media/refs/hero.png"})
    take = _fake_take("video_x", {"image_mode": "base64_field",
                                  "images": [{"ref": "media/refs/hero.png",
                                              "tier": TIER_PARAMS}], "videos": []})
    assert ref_reliability_notes("S001", rs, [take]) == []


def test_reliability_silent_for_generic_dir_fallback(tmp_project, add_shot):
    """A generic media/refs fallback is NOT a declared ref — the kenburns
    '通用参考图' advisory covers it, so ref_reliability_notes stays quiet."""
    _touch(tmp_project, "media/refs/generic.png")
    shot = add_shot(tmp_project, "S001", characters=[], scene=None)
    rs = resolve_refs(tmp_project, shot, {})
    assert rs.primary_image_source == TIER_REFS_DIR
    take = _fake_take("kenburns", {"image_mode": "path", "images": [], "videos": []})
    assert ref_reliability_notes("S001", rs, [take]) == []


# --------------------------------------------------------------- helpers


def test_encode_multipart_roundtrip_shape():
    ct, body = encode_multipart({"a": "1"}, [("image", "f.png", b"XYZ")])
    boundary = ct.split("boundary=")[1]
    assert f"--{boundary}".encode() in body
    assert b'name="a"' in body and b"1\r\n" in body
    assert b'name="image"; filename="f.png"' in body and b"XYZ" in body
    assert body.rstrip().endswith(f"--{boundary}--".encode())


def test_encode_multipart_sanitizes_hostile_filename():
    """Goal 44: a filename carrying quotes/CR/LF must never reach the
    Content-Disposition header raw — it could terminate the filename
    attribute early or inject extra header/body content."""
    hostile = 'evil".jpg\r\nX-Injected: yes\r\n\r\n--boundary'
    ct, body = encode_multipart({}, [("image", hostile, b"XYZ")])
    text = body.decode("utf-8", errors="replace")
    lines = text.split("\r\n")
    disposition = next(ln for ln in lines if ln.startswith("Content-Disposition"))
    # the CRLFs that would have split the hostile string into a real injected
    # "X-Injected: yes" header line are gone — it survives only as inert text
    # fused into the single filename attribute, never its own header line.
    assert not any(ln.strip() == "X-Injected: yes" for ln in lines)
    # the double quote is stripped too, so filename="" still closes cleanly
    # right after the (sanitized) filename value, not mid-string.
    assert disposition.count('"') == 4  # name="image"  filename="...&lt;one close&gt;"

    # an all-hostile filename (nothing printable survives) falls back to a
    # generated safe name rather than an empty filename="" attribute.
    ct2, body2 = encode_multipart({}, [("image", '"\r\n\r\n', b"XYZ")])
    text2 = body2.decode("utf-8", errors="replace")
    assert 'filename=""' not in text2
    assert "filename=\"upload-" in text2


def test_refset_empty_defaults():
    rs = RefSet()
    assert rs.images == [] and rs.videos == [] and rs.primary_image is None
    assert rs.primary_image_source == "none"
    assert not rs.has_declared_refs()


# ============================================================ core/refs.py
# Reference-asset OWNERSHIP / traceability (round-AA goal item 3): the
# media/refs report derivation (`refs_report`) and the one write action that
# makes an ownership relationship real (`assign_ref`), plus the `manju check`
# advisory and the CLI surface. Distinct from everything above this marker —
# that's `providers/refs.py`'s reference-INPUT resolver (goal item 7); this
# section is about who OWNS a file already sitting in media/refs.


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


# --------------------------------------------------------------- refs_report


def test_report_shot_ref_owner_via_naming(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/S001_ref.png")
    report = refs_report(tmp_project)
    assert report["total"] == 1
    row = report["files"][0]
    assert row == {
        "file": "media/refs/S001_ref.png", "kind": "image", "role": "shot_ref",
        "owners": ["S001"], "bible_pinned": False, "orphan": False,
    }
    assert report["counts_by_role"]["shot_ref"] == 1
    assert report["orphan_count"] == 0
    assert report["missing"] == []


def test_report_character_ref_owner_via_naming(tmp_project):
    _touch(tmp_project, "media/refs/linxia_ref.png")
    report = refs_report(tmp_project)
    row = report["files"][0]
    assert row["role"] == "character_ref"
    assert row["owners"] == ["characters:linxia"]
    assert row["bible_pinned"] is False  # naming matched, but no bible pin was SET
    assert row["orphan"] is False
    assert report["counts_by_role"]["character_ref"] == 1


def test_report_scene_ref_and_prop_ref_roles(tmp_project):
    write_yaml(tmp_project.root / "bible" / "props.yaml", {"coin": {"name": "coin"}})
    _touch(tmp_project, "media/refs/convenience_store_ref.png")
    _touch(tmp_project, "media/refs/coin_ref.png")
    report = refs_report(tmp_project)
    by_file = {r["file"]: r for r in report["files"]}
    assert by_file["media/refs/convenience_store_ref.png"]["role"] == "scene_ref"
    assert by_file["media/refs/convenience_store_ref.png"]["owners"] == ["scenes:convenience_store"]
    assert by_file["media/refs/coin_ref.png"]["role"] == "prop_ref"
    assert by_file["media/refs/coin_ref.png"]["owners"] == ["props:coin"]
    assert report["counts_by_role"]["scene_ref"] == 1
    assert report["counts_by_role"]["prop_ref"] == 1


def test_report_bible_pin_on_nonconventional_filename(tmp_project):
    """A file that does NOT match the naming convention is still traced back
    to its owner when a bible entry explicitly pins it — role stays
    'unknown' (role is naming-only, per contract) but owners/bible_pinned
    reflect the real usage."""
    _touch(tmp_project, "media/refs/weird_name.png")
    chars = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    chars["linxia"]["ref_image"] = "media/refs/weird_name.png"
    write_yaml(tmp_project.root / "bible" / "characters.yaml", chars)
    report = refs_report(tmp_project)
    row = report["files"][0]
    assert row["role"] == "unknown"
    assert row["bible_pinned"] is True
    assert row["owners"] == ["characters:linxia"]
    assert row["orphan"] is False
    assert report["counts_by_role"]["unknown"] == 1


def test_report_shot_declared_ref_without_naming_convention(tmp_project, add_shot):
    """A shot that wires up a ref file via generation.params.image WITHOUT
    renaming it to the convention still traces back — owners must reflect
    real usage, not just the filename guess (module docstring's point)."""
    _touch(tmp_project, "media/refs/custom.png")
    add_shot(tmp_project, "S001", generation={"params": {"image": "media/refs/custom.png"}})
    report = refs_report(tmp_project)
    row = report["files"][0]
    assert row["role"] == "unknown"  # naming convention doesn't match "custom"
    assert row["owners"] == ["S001"]
    assert row["orphan"] is False


def test_report_shot_top_level_refs_field_is_traced(tmp_project, add_shot):
    _touch(tmp_project, "media/refs/anotherone.png")
    add_shot(tmp_project, "S001", refs={"images": ["media/refs/anotherone.png"]})
    report = refs_report(tmp_project)
    assert report["files"][0]["owners"] == ["S001"]


def test_report_orphan(tmp_project):
    _touch(tmp_project, "media/refs/nobody_wants_me.png")
    report = refs_report(tmp_project)
    row = report["files"][0]
    assert row["role"] == "unknown"
    assert row["owners"] == []
    assert row["bible_pinned"] is False
    assert row["orphan"] is True
    assert report["orphan_count"] == 1
    assert report["counts_by_role"]["unknown"] == 1


def test_report_missing_bible_pointer(tmp_project):
    chars = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    chars["linxia"]["ref_image"] = "media/refs/ghost.png"
    write_yaml(tmp_project.root / "bible" / "characters.yaml", chars)
    report = refs_report(tmp_project)
    assert report["files"] == []  # ghost.png doesn't exist -> no row for it
    assert report["missing"] == [{
        "bible_file": "characters", "asset_id": "linxia",
        "field": "ref_image", "value": "media/refs/ghost.png",
    }]


def test_report_shot_wins_over_bible_on_ambiguous_naming(tmp_project, add_shot):
    """A shot id that collides with a bible asset id: the ingest classifier's
    stance (shot wins) is mirrored here."""
    add_shot(tmp_project, "linxia")
    _touch(tmp_project, "media/refs/linxia_ref.png")
    report = refs_report(tmp_project)
    row = report["files"][0]
    assert row["role"] == "shot_ref"
    assert row["owners"] == ["linxia"]


def test_report_empty_refs_dir(tmp_project):
    report = refs_report(tmp_project)
    assert isinstance(report.pop("note"), str) and report["files"] == []
    assert report == {
        "files": [], "total": 0, "missing": [],
        "counts_by_role": {k: 0 for k in
                           ("shot_ref", "character_ref", "scene_ref", "prop_ref", "unknown")},
        "orphan_count": 0,
    }


def test_report_deterministic_ordering(tmp_project):
    _touch(tmp_project, "media/refs/zzz.png")
    _touch(tmp_project, "media/refs/aaa.png")
    _touch(tmp_project, "media/refs/sub/mmm.png")
    files_a = [r["file"] for r in refs_report(tmp_project)["files"]]
    files_b = [r["file"] for r in refs_report(tmp_project)["files"]]
    assert files_a == files_b == sorted(files_a)


# ---------------------------------------------------------------- assign_ref


def test_assign_ref_to_shot_renames_and_report_reflects_it(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    src = _touch(tmp_project, "media/refs/loose.png")
    result = assign_ref(tmp_project, "media/refs/loose.png", shot="S001", actor="test")
    assert result["ok"] is True
    assert result["old"] == "media/refs/loose.png"
    assert result["new"] == "media/refs/S001_ref.png"
    assert result["renamed"] is True
    assert result["address"] == "S001"
    assert not src.exists()
    assert (tmp_project.root / "media" / "refs" / "S001_ref.png").exists()

    report = refs_report(tmp_project)
    row = report["files"][0]
    assert row["file"] == "media/refs/S001_ref.png"
    assert row["owners"] == ["S001"]
    assert row["orphan"] is False


def test_assign_ref_to_character_sets_ref_image(tmp_project):
    _touch(tmp_project, "media/refs/loose2.png")
    result = assign_ref(tmp_project, "media/refs/loose2.png", character="linxia", actor="test")
    assert result["new"] == "media/refs/linxia_ref.png"
    assert result["address"] == "characters:linxia"
    bible = tmp_project.load_bible()
    assert bible["linxia"]["ref_image"] == "media/refs/linxia_ref.png"

    report = refs_report(tmp_project)
    row = report["files"][0]
    assert row["bible_pinned"] is True
    assert row["owners"] == ["characters:linxia"]


def test_assign_ref_to_scene_and_prop(tmp_project):
    write_yaml(tmp_project.root / "bible" / "props.yaml", {"coin": {"name": "coin"}})
    _touch(tmp_project, "media/refs/s.png")
    _touch(tmp_project, "media/refs/p.png")
    r1 = assign_ref(tmp_project, "media/refs/s.png", scene="convenience_store", actor="test")
    r2 = assign_ref(tmp_project, "media/refs/p.png", prop="coin", actor="test")
    assert r1["new"] == "media/refs/convenience_store_ref.png"
    assert r2["new"] == "media/refs/coin_ref.png"
    props = read_yaml(tmp_project.root / "bible" / "props.yaml")
    assert props["coin"]["ref_image"] == "media/refs/coin_ref.png"


def test_assign_ref_never_overwrites_additive_ref_image(tmp_project):
    """Assigning a SECOND file to an already-pinned asset appends, mirroring
    build/ingest.py's `_set_bible_ref_image` contract (never destructive)."""
    _touch(tmp_project, "media/refs/first.png")
    _touch(tmp_project, "media/refs/second.png")
    assign_ref(tmp_project, "media/refs/first.png", character="linxia", actor="test")
    assign_ref(tmp_project, "media/refs/second.png", character="linxia", actor="test")
    bible = tmp_project.load_bible()
    assert bible["linxia"]["ref_image"] == ["media/refs/linxia_ref.png", "media/refs/linxia_ref_2.png"]


def test_assign_ref_repoints_existing_bible_pin_on_rename(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/weird_name_xyz.png")
    scenes = read_yaml(tmp_project.root / "bible" / "scenes.yaml")
    scenes["convenience_store"]["ref_image"] = "media/refs/weird_name_xyz.png"
    write_yaml(tmp_project.root / "bible" / "scenes.yaml", scenes)

    result = assign_ref(tmp_project, "media/refs/weird_name_xyz.png", shot="S001", actor="test")
    assert result["repointed"] == ["scenes:convenience_store"]
    scenes = read_yaml(tmp_project.root / "bible" / "scenes.yaml")
    assert scenes["convenience_store"]["ref_image"] == result["new"]


def test_assign_ref_noop_when_already_correctly_named(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/S001_ref.png")
    result = assign_ref(tmp_project, "media/refs/S001_ref.png", shot="S001", actor="test")
    assert result["renamed"] is False
    assert result["old"] == result["new"] == "media/refs/S001_ref.png"


def test_assign_ref_appends_ref_assign_event(tmp_project, add_shot):
    from manju.core.events import tail_events

    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/loose.png")
    assign_ref(tmp_project, "media/refs/loose.png", shot="S001", actor="test")
    events = tail_events(tmp_project.root, n=5)
    assert events[-1]["action"] == "ref_assign"
    assert events[-1]["detail"]["new"] == "media/refs/S001_ref.png"


@pytest.mark.parametrize("kwargs", [{}, {"shot": "S001", "character": "linxia"}])
def test_assign_ref_requires_exactly_one_target(tmp_project, add_shot, kwargs):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/loose.png")
    with pytest.raises(RefsError):
        assign_ref(tmp_project, "media/refs/loose.png", actor="test", **kwargs)


def test_assign_ref_refuses_missing_file(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    with pytest.raises(RefsError):
        assign_ref(tmp_project, "media/refs/nope.png", shot="S001", actor="test")


def test_assign_ref_refuses_path_outside_refs_dir(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    with pytest.raises(RefsError, match="media/refs"):
        assign_ref(tmp_project, "story/brief.md", shot="S001", actor="test")


def test_assign_ref_refuses_escaping_path(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    with pytest.raises(RefsError):
        assign_ref(tmp_project, "../outside.png", shot="S001", actor="test")


def test_assign_ref_refuses_unknown_shot(tmp_project):
    _touch(tmp_project, "media/refs/loose.png")
    with pytest.raises(RefsError):
        assign_ref(tmp_project, "media/refs/loose.png", shot="S999", actor="test")


def test_assign_ref_refuses_unknown_bible_asset(tmp_project):
    _touch(tmp_project, "media/refs/loose.png")
    with pytest.raises(RefsError):
        assign_ref(tmp_project, "media/refs/loose.png", character="nope", actor="test")


# ------------------------------------------------------------- check advisory


def test_check_warns_on_orphan_refs(tmp_project):
    _touch(tmp_project, "media/refs/nobody_wants_me.png")
    report = run_check(tmp_project)
    assert report.ok  # a warning, never an error
    assert any("孤儿" in w and "media/refs" in w for w in report.warnings)


def test_check_orphan_warning_disappears_after_assign(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/loose.png")
    before = run_check(tmp_project)
    assert any("孤儿" in w for w in before.warnings)

    assign_ref(tmp_project, "media/refs/loose.png", shot="S001", actor="test")
    after = run_check(tmp_project)
    assert not any("孤儿" in w for w in after.warnings)


def test_check_warns_on_missing_bible_ref_pointer(tmp_project):
    chars = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    chars["linxia"]["ref_image"] = "media/refs/ghost.png"
    write_yaml(tmp_project.root / "bible" / "characters.yaml", chars)
    report = run_check(tmp_project)
    assert report.ok
    assert any("ghost.png" in w and "characters.yaml" in w for w in report.warnings)


# --------------------------------------------------------------------- CLI


def test_cli_refs_json_matches_refs_report(in_project, add_shot):
    add_shot(in_project, "S001")
    _touch(in_project, "media/refs/S001_ref.png")
    _touch(in_project, "media/refs/orphan.png")
    result = runner.invoke(app, ["refs", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == refs_report(in_project)


def test_cli_refs_orphans_flag_filters(in_project, add_shot):
    add_shot(in_project, "S001")
    _touch(in_project, "media/refs/S001_ref.png")  # owned — must be filtered OUT
    _touch(in_project, "media/refs/nobody.png")     # orphan — must survive the filter
    result = runner.invoke(app, ["refs", "--orphans", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert all(r["orphan"] for r in data["files"])
    assert {r["file"] for r in data["files"]} == {"media/refs/nobody.png"}


def test_cli_refs_table_prints_orphan_count(in_project):
    _touch(in_project, "media/refs/nobody.png")
    result = runner.invoke(app, ["refs"])
    assert result.exit_code == 0, result.output
    assert "孤儿 1" in result.output


def test_cli_refs_assign_to_shot(in_project, add_shot):
    add_shot(in_project, "S001")
    _touch(in_project, "media/refs/loose.png")
    result = runner.invoke(app, ["refs", "assign", "media/refs/loose.png", "--shot", "S001", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["new"] == "media/refs/S001_ref.png"
    assert (in_project.root / "media" / "refs" / "S001_ref.png").exists()
    assert not (in_project.root / "media" / "refs" / "loose.png").exists()


def test_cli_refs_assign_to_character(in_project):
    _touch(in_project, "media/refs/loose.png")
    result = runner.invoke(
        app, ["refs", "assign", "media/refs/loose.png", "--character", "linxia", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["new"] == "media/refs/linxia_ref.png"
    bible = in_project.load_bible()
    assert bible["linxia"]["ref_image"] == "media/refs/linxia_ref.png"


def test_cli_refs_assign_refuses_no_target(in_project):
    _touch(in_project, "media/refs/loose.png")
    result = runner.invoke(app, ["refs", "assign", "media/refs/loose.png", "--json"])
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["code"] == "refs_assign_invalid"


def test_cli_refs_assign_refuses_unknown_shot(in_project):
    _touch(in_project, "media/refs/loose.png")
    result = runner.invoke(
        app, ["refs", "assign", "media/refs/loose.png", "--shot", "S999", "--json"])
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert "S999" in data["error"]


def test_cli_refs_shot_subcommand_still_resolves(in_project, add_shot):
    """The pre-existing per-shot resolution/budget/cleanliness inspect (goal
    items 9 & 10) moved from bare `refs <id>` to `refs shot <id>` — guard
    that the move kept its shape."""
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["refs", "shot", "S001", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["shot"] == "S001"
    assert "resolution" in data and "budget" in data and "cleanliness" in data
