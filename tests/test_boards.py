"""Round U (goal item 12): multi-image storyboards, keyframe sequences,
first/last-frame video tasks, and deterministic action breakdown.

Coverage:
- board grids (4/9 panel dims, labels don't crash, content-addressed cache key
  stability) — the ffmpeg compositor (skipped when ffmpeg is absent);
- keyframe schema defaults are byte-identical everywhere (spec_hash / voice_hash
  never move; a keyframed shot hashes exactly like a bare one);
- first/last-frame delivery against a scripted transport (the mock-server
  pattern): Kling-style image+image_tail fields, Runway-style array, multipart,
  bible-asset-id resolution, and byte-identity in every absence case;
- action breakdown determinism over pure-CJK and English text;
- `manju board keyframes` scaffold flag gating + lock respect.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess

import pytest

from manju.core.models import KeyframeSpec, ShotSpec
from manju.core.spec import compute_spec_hash, compute_voice_hash
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import (
    FIRST_LAST_CAPABILITY,
    ProviderManifest,
)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required",
)


# --------------------------------------------------------------- helpers


def _solid_png(path, color="red", size="200x140"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s={size}", "-frames:v", "1", str(path)],
        check=True,
    )
    return path


def _dims(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return tuple(int(x) for x in r.stdout.strip().split(","))


class ScriptedTransport:
    """The mock-server pattern: replays scripted responses, records requests."""

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


# =============================================================== board grids


@requires_ffmpeg
def test_make_board_4_panel_dims_and_labels(tmp_path):
    from manju.media.boards import make_board

    imgs = [_solid_png(tmp_path / f"c{i}.png", color=c)
            for i, c in enumerate(("red", "green", "blue"))]
    # 3 real images + 1 empty cell; CJK + ascii labels must not break the graph
    out = make_board(imgs + [None], 4, tmp_path / "b4.jpg",
                     labels=["S001", "镜头二", "S003", None], cell=(320, 180))
    assert out.exists() and out.stat().st_size > 0
    assert _dims(out) == (640, 360)  # 2×2 of 320×180


@requires_ffmpeg
def test_make_board_9_panel_dims(tmp_path):
    from manju.media.boards import make_board

    imgs = [_solid_png(tmp_path / f"c{i}.png") for i in range(4)]
    out = make_board(imgs, 9, tmp_path / "b9.png", labels=[f"S{i}" for i in range(4)],
                     cell=(200, 120))
    assert out.exists()
    assert _dims(out) == (600, 360)  # 3×3 of 200×120


def test_grid_dims_rejects_other_counts():
    from manju.media.boards import grid_dims
    from manju.media.ffmpeg import MediaError

    assert grid_dims(4) == (2, 2) and grid_dims(9) == (3, 3)
    with pytest.raises(MediaError):
        grid_dims(6)


@requires_ffmpeg
def test_scene_board_ref_and_take_frame_with_stable_cache(tmp_project, add_shot, tmp_path):
    from manju.media.boards import _board_key, _best_frame, _shot_cell_dims, scene_board

    # S1: a declared ref image (params tier) → used directly as the cell frame
    ref = tmp_project.resolve("media/refs/hero.png")
    _solid_png(ref, color="orange", size="300x200")
    add_shot(tmp_project, "S1", generation={"params": {"image": "media/refs/hero.png"}})
    # S2: no ref, but a real take video → a mid-frame is extracted
    add_shot(tmp_project, "S2")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=200:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
        check=True,
    )
    from manju.core.models import TakeSidecar
    tmp_project.register_take("S2", clip, TakeSidecar(provider="test", spec_hash="sha256:x"))
    # S3: nothing → a black labelled cell
    add_shot(tmp_project, "S3")

    out = scene_board(tmp_project, "convenience_store", grid=4)
    assert out.exists()
    cw, ch = _shot_cell_dims(tmp_project)  # project aspect (1080×1920)
    assert _dims(out) == (2 * cw, 2 * ch)

    # content-addressed cache: a second call returns the SAME file, no re-render
    mtime = out.stat().st_mtime_ns
    out2 = scene_board(tmp_project, "convenience_store", grid=4)
    assert out2 == out and out2.stat().st_mtime_ns == mtime

    # the cache KEY is stable for identical inputs, and rides in the filename
    bible = tmp_project.load_bible()
    frames = [_best_frame(tmp_project, tmp_project.load_shot(s), bible)
              for s in ("S1", "S2", "S3")]
    k1 = _board_key(frames, ["S1", "S2", "S3"], 4, cw, ch)
    k2 = _board_key(frames, ["S1", "S2", "S3"], 4, cw, ch)
    assert k1 == k2 and k1 in out.name


def test_scene_board_no_shots_is_clean_error(tmp_project):
    from manju.media.boards import scene_board
    from manju.media.ffmpeg import MediaError

    with pytest.raises(MediaError):
        scene_board(tmp_project, "nonexistent_scene", grid=4)


# =================================================== keyframe schema defaults


def test_keyframes_default_empty_and_typed(add_shot, tmp_project):
    assert ShotSpec(id="X").keyframes == []
    shot = add_shot(tmp_project, "S1", keyframes=[
        {"position": "start", "prompt": "opens door", "image": "media/refs/a.png"},
        {"at_ms": 0, "prompt": "explicit start"},
        {"position": "end", "prompt": "sits"},
    ])
    reloaded = tmp_project.load_shot("S1")
    assert all(isinstance(k, KeyframeSpec) for k in reloaded.keyframes)
    assert reloaded.keyframes[0].role == "start"
    assert reloaded.keyframes[1].role == "start"   # at_ms<=0
    assert reloaded.keyframes[2].role == "end"
    assert KeyframeSpec(prompt="x").role is None
    _ = shot  # add_shot returns the pre-save spec; reloaded is the on-disk truth


def test_keyframes_do_not_move_spec_or_voice_hash(tmp_project):
    """The byte-identity contract: keyframes are advisory picture guidance, NOT
    part of spec_payload/voice_payload — a keyframed shot hashes EXACTLY like the
    same shot without keyframes (so no key/render can move)."""
    bible = tmp_project.load_bible()
    base = {
        "id": "S1", "scene": "convenience_store", "characters": ["linxia"],
        "dialogue": {"speaker": "linxia", "text": "这不可能。"},
        "action": {"main": "推门而入"},
    }
    bare = ShotSpec.model_validate(base)
    keyed = ShotSpec.model_validate({**base, "keyframes": [
        {"position": "start", "image": "media/refs/a.png", "prompt": "开门"},
        {"position": "end", "image": "media/refs/b.png", "prompt": "落座"},
    ]})
    assert compute_spec_hash(bare, bible) == compute_spec_hash(keyed, bible)
    assert compute_voice_hash(bare, bible) == compute_voice_hash(keyed, bible)


# =============================================== first/last-frame delivery


def _fl_manifest(refs, *, caps=("image_to_video", FIRST_LAST_CAPABILITY)):
    return ProviderManifest.model_validate({
        "id": "kling_x", "type": "video", "adapter": "generic_cloud",
        "capabilities": list(caps),
        "auth": {"key_env": "KLING_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}", "image": "", "image_tail": "",
                              "frames": ""},
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
    })


def _kf_req(tmp_project, add_shot, keyframes, *, duration_ms=4000, shot_id="S1"):
    shot = add_shot(tmp_project, shot_id, keyframes=keyframes)
    return GenerationRequest(
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="sha256:x", duration_ms=duration_ms, candidates=1, params={"seed": 1},
    )


def _two_frames(tmp_project):
    a = tmp_project.resolve("media/refs/f0.png")
    b = tmp_project.resolve("media/refs/f1.png")
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_bytes(b"\x89PNG-start-bytes")
    b.write_bytes(b"\x89PNG-end-bytes")
    return a, b


def test_first_last_fields_base64_kling(tmp_project, add_shot, monkeypatch):
    """Kling shape: two JSONPaths (image + image_tail), base64 data-URIs."""
    monkeypatch.setenv("KLING_X_KEY", "k")
    a, b = _two_frames(tmp_project)
    manifest = _fl_manifest({"first_last_mode": "fields", "first_frame_field": "$.image",
                             "last_frame_field": "$.image_tail"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": "media/refs/f0.png"},
                   {"position": "end", "image": "media/refs/f1.png"}])
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["image"] == "data:image/png;base64," + base64.b64encode(a.read_bytes()).decode()
    assert sent["image_tail"] == "data:image/png;base64," + base64.b64encode(b.read_bytes()).decode()
    fl = req.params["ref_delivery"]["first_last"]
    assert fl["mode"] == "fields" and fl["start"]["role"] == "start" and fl["end"]["role"] == "end"


def test_first_last_array_runway(tmp_project, add_shot, monkeypatch):
    """Runway shape: one JSONPath receiving [first, last]; raw base64 (no prefix)."""
    monkeypatch.setenv("KLING_X_KEY", "k")
    a, b = _two_frames(tmp_project)
    manifest = _fl_manifest({"first_last_mode": "array", "frames_field": "$.frames",
                             "data_uri": False})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": "media/refs/f0.png"},
                   {"position": "end", "image": "media/refs/f1.png"}])
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["frames"] == [base64.b64encode(a.read_bytes()).decode(),
                              base64.b64encode(b.read_bytes()).decode()]


def test_first_last_multipart(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("KLING_X_KEY", "k")
    a, b = _two_frames(tmp_project)
    manifest = _fl_manifest({"first_last_mode": "fields", "first_frame_field": "$.image",
                             "last_frame_field": "$.image_tail",
                             "first_last_encoding": "multipart"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": "media/refs/f0.png"},
                   {"position": "end", "image": "media/refs/f1.png"}])
    provider.submit(req)
    _, _, headers, body = transport.requests[0]
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="image"; filename="f0.png"' in body
    assert b'name="image_tail"; filename="f1.png"' in body
    assert a.read_bytes() in body and b.read_bytes() in body


def test_first_last_resolves_bible_asset_id(tmp_project, add_shot, monkeypatch):
    """A keyframe image that is a bible asset id resolves to its ref_image."""
    monkeypatch.setenv("KLING_X_KEY", "k")
    hero = tmp_project.resolve("media/refs/linxia.png")
    hero.parent.mkdir(parents=True, exist_ok=True)
    hero.write_bytes(b"\x89PNG-linxia")
    _, b = _two_frames(tmp_project)
    # point the bible character at a ref_image, then use the character id as the frame
    from manju.core.yamlio import read_yaml, write_yaml
    chars = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    chars["linxia"]["ref_image"] = "media/refs/linxia.png"
    write_yaml(tmp_project.root / "bible" / "characters.yaml", chars)
    manifest = _fl_manifest({"first_last_mode": "fields", "first_frame_field": "$.image",
                             "last_frame_field": "$.image_tail"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": "linxia"},
                   {"position": "end", "image": "media/refs/f1.png"}])
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["image"] == "data:image/png;base64," + base64.b64encode(hero.read_bytes()).decode()


def test_first_last_absent_no_capability_is_byte_identical(tmp_project, add_shot, monkeypatch):
    """A provider that does NOT do first/last (refs.first_last_mode unset) sends a
    byte-identical request even for a fully keyframed shot."""
    monkeypatch.setenv("KLING_X_KEY", "k")
    _two_frames(tmp_project)
    manifest = _fl_manifest({}, caps=("image_to_video",))  # feature off
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": "media/refs/f0.png"},
                   {"position": "end", "image": "media/refs/f1.png"}])
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["image"] == "" and sent["image_tail"] == "" and sent["frames"] == ""
    assert "first_last" not in req.params.get("ref_delivery", {})


def test_first_last_absent_no_keyframes_is_byte_identical(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("KLING_X_KEY", "k")
    _two_frames(tmp_project)
    manifest = _fl_manifest({"first_last_mode": "array", "frames_field": "$.frames"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot, [])  # no keyframes
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["frames"] == "" and "first_last" not in req.params.get("ref_delivery", {})


def test_first_last_only_start_keyframe_no_delivery(tmp_project, add_shot, monkeypatch):
    """Needs BOTH a start and an end keyframe — one alone delivers nothing."""
    monkeypatch.setenv("KLING_X_KEY", "k")
    _two_frames(tmp_project)
    manifest = _fl_manifest({"first_last_mode": "array", "frames_field": "$.frames"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": "media/refs/f0.png"}])
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["frames"] == ""


# ------------------------------------------ goal item 17: keyframe resolution
# must go through the SAME containment guard refs.py uses, not an independent
# (and leakier) absolute-path fallback.


def test_first_last_absolute_keyframe_path_is_refused_before_submit(
    tmp_project, add_shot, monkeypatch, tmp_path
):
    """The concrete leak this closes: before the fix, an absolute keyframe
    image path that ``project.resolve`` refused fell back to reading the RAW
    absolute path — an outside-project file's bytes ended up base64-encoded
    into the request body. Now it must refuse with a ProviderFailure BEFORE
    any network call, and the outside file's bytes must never appear
    anywhere in what would have been sent."""
    monkeypatch.setenv("KLING_X_KEY", "k")
    _, b = _two_frames(tmp_project)
    outside = tmp_path / "outside_keyframe.png"
    outside.write_bytes(b"TOP-SECRET-OUTSIDE-KEYFRAME-BYTES")
    manifest = _fl_manifest({"first_last_mode": "fields", "first_frame_field": "$.image",
                             "last_frame_field": "$.image_tail"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": str(outside)},
                   {"position": "end", "image": "media/refs/f1.png"}])
    with pytest.raises(ProviderFailure) as exc:
        provider.submit(req)
    assert exc.value.kind is FailureKind.invalid
    assert "把文件放进项目" in str(exc.value)
    assert transport.requests == []  # refused BEFORE the paid POST


def test_first_last_escaping_relative_keyframe_path_is_refused(
    tmp_project, add_shot, monkeypatch, tmp_path
):
    import os

    monkeypatch.setenv("KLING_X_KEY", "k")
    _two_frames(tmp_project)
    outside = tmp_path / "outside_keyframe2.png"
    outside.write_bytes(b"outside-bytes")
    escaping = os.path.relpath(str(outside), start=str(tmp_project.root))
    assert escaping.startswith("..")
    manifest = _fl_manifest({"first_last_mode": "fields", "first_frame_field": "$.image",
                             "last_frame_field": "$.image_tail"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": escaping},
                   {"position": "end", "image": "media/refs/f1.png"}])
    with pytest.raises(ProviderFailure):
        provider.submit(req)
    assert transport.requests == []


def test_resolve_keyframe_uses_shared_refs_guard(tmp_project, add_shot):
    """``_resolve_keyframe`` must go through ``providers.refs.resolve_local_ref``
    — the SAME function refs.py uses — rather than its own resolution."""
    from manju.providers import generic_cloud, refs

    assert generic_cloud.resolve_local_ref is refs.resolve_local_ref


def test_first_last_unresolvable_end_frame_skips(tmp_project, add_shot, monkeypatch):
    """The delivery gate requires BOTH images to resolve to real local files; a
    missing end frame means the condition is unmet → the request stays
    byte-identical (no partial delivery, no spend on a half-formed task)."""
    monkeypatch.setenv("KLING_X_KEY", "k")
    a, _ = _two_frames(tmp_project)
    manifest = _fl_manifest({"first_last_mode": "fields", "first_frame_field": "$.image",
                             "last_frame_field": "$.image_tail"})
    transport = ScriptedTransport([_resp(200, {"data": {"task_id": "j1"}})])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    # end frame points at a file that does not exist → base64 encoding cannot read it
    (tmp_project.resolve("media/refs/f1.png")).unlink()
    req = _kf_req(tmp_project, add_shot,
                  [{"position": "start", "image": "media/refs/f0.png"},
                   {"position": "end", "image": "media/refs/f1.png"}])
    # end is unresolvable to an existing local file → delivery skipped (byte-identical),
    # NOT an error (the round-U "resolve to real local files" gate)
    provider.submit(req)
    sent = json.loads(transport.requests[0][3])
    assert sent["image"] == "" and sent["image_tail"] == ""
    assert _ is not None and a.exists()


def test_manifest_first_last_validation():
    base = {
        "id": "x", "adapter": "generic_cloud",
        "submit": {"url": "https://a/x", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://a/{job_id}", "status_path": "$.s",
                 "status_map": {"S": "succeeded"}},
    }
    # fields mode, missing fields + missing capability → two problems
    m = ProviderManifest.model_validate({**base, "refs": {"first_last_mode": "fields"}})
    probs = m.validate_for_generic()
    assert any("first_frame_field AND refs.last_frame_field" in p for p in probs)
    assert any(FIRST_LAST_CAPABILITY in p and "capabilities" in p for p in probs)
    # array fully configured with capability → clean of first_last problems
    ok = ProviderManifest.model_validate({**base, "capabilities": [FIRST_LAST_CAPABILITY],
        "refs": {"first_last_mode": "array", "frames_field": "$.f"}})
    assert not any("first_last" in p or "frames_field" in p
                   for p in ok.validate_for_generic())


# ===================================================== action breakdown (D)


def test_breakdown_cjk_no_space_determinism():
    from manju.media.boards import breakdown_action

    text = "他推开门然后走进房间接着坐下再点了一支烟"
    beats = breakdown_action(text, 4)
    assert beats == ["他推开门", "走进房间", "坐下", "点了一支烟"]
    # deterministic: byte-identical on a repeat run
    assert breakdown_action(text, 4) == beats
    # 并且 / 随后 also split
    assert breakdown_action("举起相机并且按下快门", 2) == ["举起相机", "按下快门"]


def test_breakdown_english_connectives():
    from manju.media.boards import breakdown_action

    text = "She opens the door then walks in and then sits down and lights a cigarette"
    assert breakdown_action(text, 4) == [
        "She opens the door", "walks in", "sits down", "lights a cigarette"]


def test_breakdown_pad_and_regroup_deterministic():
    from manju.media.boards import breakdown_action

    # fewer clauses than n → split the longest (deterministic, repeatable)
    padded = breakdown_action("独自走过长长的走廊", 3)
    assert len(padded) == 3 and "".join(padded) == "独自走过长长的走廊"
    assert breakdown_action("独自走过长长的走廊", 3) == padded
    # more clauses than n → contiguous even regroup joined by 、
    assert breakdown_action("一,二,三,四,五", 2) == ["一、二、三", "四、五"]


def test_breakdown_empty_uses_qichengzhuanhe_labels():
    from manju.media.boards import breakdown_action

    assert breakdown_action("", 4) == ["起", "承", "转", "合"]
    assert breakdown_action("   ", 6) == ["起", "承", "转", "合", "起2", "承2"]
    assert breakdown_action("x", 1) == ["x"]


# ============================================ scaffold gating + lock respect


def test_scaffold_only_writes_with_flag(tmp_project, add_shot):
    import os

    from typer.testing import CliRunner

    from manju.cli import app

    add_shot(tmp_project, "S1", action={"main": "推门然后落座"})
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(tmp_project.root)
    try:
        r = runner.invoke(app, ["board", "keyframes", "S1", "--n", "2"])
        assert r.exit_code == 0 and "推门" in r.output
        assert tmp_project.load_shot("S1").keyframes == []  # nothing written

        r = runner.invoke(app, ["board", "keyframes", "S1", "--n", "2", "--scaffold"])
        assert r.exit_code == 0
        kfs = tmp_project.load_shot("S1").keyframes
        assert [k.position for k in kfs] == ["start", "end"]
        assert kfs[0].prompt == "推门" and kfs[1].prompt == "落座"
    finally:
        os.chdir(cwd)


def test_scaffold_respects_keyframes_lock(tmp_project, add_shot):
    from manju.core.locks import seal_lock
    from manju.media.boards import KeyframeScaffoldError, breakdown_action, scaffold_keyframes

    add_shot(tmp_project, "S1", action={"main": "推门然后落座"})
    # seal the (currently empty) keyframes field
    tmp_project.update_shot_raw(
        "S1", lambda d: d.__setitem__("locked", {"keyframes": seal_lock(d, "keyframes")}))
    with pytest.raises(KeyframeScaffoldError):
        scaffold_keyframes(tmp_project, "S1", breakdown_action("推门然后落座", 2))
    # the sealed value is untouched
    assert tmp_project.load_shot("S1").keyframes == []
