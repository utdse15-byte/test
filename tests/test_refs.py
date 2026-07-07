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
    script.write_text('#!/bin/sh\nprintf "%s" "$MANJU_VIDEO_REF" > "$3"\n')
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
    assert takes[0].media_path.read_text() == base_abs
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


def test_refset_empty_defaults():
    rs = RefSet()
    assert rs.images == [] and rs.videos == [] and rs.primary_image is None
    assert rs.primary_image_source == "none"
    assert not rs.has_declared_refs()
