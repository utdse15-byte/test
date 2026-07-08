"""`manju gui` — native cut depth (round U, agent UJ).

Deepens the /edit 剪辑 page into a real native-editing layer, all WITHOUT opening
JianYing:

  * multi-track lanes (主轨道 / 字幕 / 音频) rendered from the COMPILED timeline;
  * per-clip waveforms (lazy ``/edit/wave`` <img>);
  * caption/voice sync hints (:mod:`manju.gui.synchints`, pure);
  * per-boundary transition overrides (``rules.transition_overrides``, ½-clip cap);
  * generative handle rebuild (补拍手柄) through the existing redo path.

Companion to :mod:`tests.test_gui_edit` (which stays green): the lanes VISUALIZE
the compiled truth, every mutation still flows through the same engine core and
lands the same events, and no surface bypasses the token / readonly / host gates.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.core.events import tail_events
from manju.core.models import (
    AudioClip,
    CaptionLine,
    Timeline,
    TimelineTracks,
    VideoClip,
)
from manju.gui.server import create_server

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "_user_routing.yaml"))


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, host=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), (payload if raw else
                                                     json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _html(server, path, **kw):
    status, headers, body = _req(server, path, raw=True, **kw)
    return status, headers, (body.decode("utf-8") if isinstance(body, bytes) else body)


def _post(server, path, body, token=None, **kw):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _req(server, path, method="POST", body=body, headers=headers, **kw)


def _wait_job(server, job_id, timeout=90.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _, data = _req(server, "/api/jobs")
        assert status == 200
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _fake_media(tmp_project, rel: str, data: bytes = b"x") -> str:
    """Plant fake bytes at a project-relative media path so a clip source
    resolves to a real file (lane render only checks is_file, never probes)."""
    p = tmp_project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return rel


def _full_timeline(tmp_project):
    """A compiled timeline exercising every track type, saved so the lanes view
    (and sync hints) read real truth."""
    v1 = _fake_media(tmp_project, "media/gen/S001/take_01.mp4")
    v2 = _fake_media(tmp_project, "media/gen/S002/take_01.mp4")
    voice = _fake_media(tmp_project, "media/gen/S001/voice_take_01.wav")
    music = _fake_media(tmp_project, "media/audio/bgm.wav")
    sfx = _fake_media(tmp_project, "media/audio/ding.wav")
    amb = _fake_media(tmp_project, "media/audio/amb.wav")
    tl = Timeline(
        fps=24, width=1080, height=1920, duration_ms=6000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="take_01", source=v1, start_ms=0, duration_ms=3000),
                VideoClip(shot="S002", take="take_01", source=v2, start_ms=3000, duration_ms=3000),
            ],
            voice=[AudioClip(source=voice, start_ms=200, duration_ms=2000)],
            music=[AudioClip(source=music, start_ms=0, duration_ms=6000)],
            sfx=[AudioClip(source=sfx, start_ms=3000, duration_ms=500)],
            ambient=[AudioClip(source=amb, start_ms=0, duration_ms=6000, loop=True)],
            captions=[CaptionLine(start_ms=300, end_ms=1500, text="你好世界", speaker="linxia")],
        ),
    )
    tmp_project.save_timeline(tl)
    return tl


# ================================================================ A. lanes view


def test_lanes_render_all_track_types(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    _full_timeline(tmp_project)

    status, _, body = _html(gui, "/edit")
    assert status == 200
    # lane section + every typed lane
    assert "多轨道 Lanes" in body
    assert "主轨道 Main" in body and "字幕 Captions" in body
    assert "配音 Voice" in body and "音乐 Music" in body
    assert "音效 SFX" in body and "环境 Ambient" in body
    # video clips carry shot + ms geometry
    assert "ed-vclip" in body and 'data-shot="S001"' in body and 'data-shot="S002"' in body
    assert 'data-ms-start="3000"' in body
    # caption block jumps to /subtitles and carries the cue text
    assert "ed-cclip" in body and 'href="/subtitles"' in body and "你好世界" in body
    # per-clip waveform imgs on the audio lanes (lazy)
    assert "/edit/wave?take=" in body
    # global playhead ruler + zoom
    assert 'id="ed-ruler"' in body and 'id="ed-playhead"' in body and 'id="ed-zoom"' in body


def test_lanes_note_before_any_build(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _, _, body = _html(gui, "/edit")
    # no timeline yet → honest note, no lane rows (page GET never runs ffmpeg)
    assert "多轨道 Lanes" in body
    assert "构建一次后" in body
    assert "ed-vclip" not in body


# ================================================================ B. waveforms


@pytest.mark.skipif(not _HAS_FFMPEG, reason="waveform needs ffmpeg/ffprobe")
def test_wave_endpoint_cache_and_no_audio(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # a real audio source
    tone = tmp_project.resolve("media/audio/tone.wav")
    tone.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         str(tone)], check=True, capture_output=True)

    from urllib.parse import quote
    rel = quote("media/audio/tone.wav", safe="")
    st, headers, body = _req(gui, f"/edit/wave?take={rel}&w=240&h=40", raw=True)
    assert st == 200 and len(body) > 0
    assert headers.get("Content-Type", "").startswith("image/")
    # a second request hits the content-addressed cache → still 200
    st2, _, body2 = _req(gui, f"/edit/wave?take={rel}&w=240&h=40", raw=True)
    assert st2 == 200 and len(body2) > 0

    # an audio-less source → honest 404 JSON note, never a 500
    silent = tmp_project.resolve("media/gen/S001/take_01.mp4")
    silent.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x48:rate=24",
         "-an", "-pix_fmt", "yuv420p", str(silent)], check=True, capture_output=True)
    relv = quote("media/gen/S001/take_01.mp4", safe="")
    st3, _, data3 = _req(gui, f"/edit/wave?take={relv}")
    assert st3 == 404 and "error" in data3


def test_wave_rejects_non_media_path(gui, tmp_project, add_shot):
    from urllib.parse import quote
    add_shot(tmp_project, "S001")
    assert _req(gui, "/edit/wave?take=" + quote("../../etc/passwd", safe=""))[0] == 404
    assert _req(gui, "/edit/wave?take=" + quote("bible/style.yaml", safe=""))[0] == 404


# ================================================================ C. sync hints


def _sync_timeline():
    """A synthetic timeline for the pure sync analyzer: three shots, three voice
    clips (silent / silence-then-speech / all-speech), two cues."""
    return Timeline(
        fps=24, duration_ms=7000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="t", source="a", start_ms=0, duration_ms=2000),
                VideoClip(shot="S002", take="t", source="b", start_ms=2000, duration_ms=2000),
                VideoClip(shot="S003", take="t", source="c", start_ms=4000, duration_ms=3000),
            ],
            voice=[
                AudioClip(source="vA", start_ms=0, duration_ms=2000),
                AudioClip(source="vB", start_ms=2000, duration_ms=2000),
                AudioClip(source="vC", start_ms=4000, duration_ms=3000),
            ],
            captions=[
                CaptionLine(start_ms=200, end_ms=1200, text="甲"),   # over silent vA
                CaptionLine(start_ms=2100, end_ms=3900, text="乙"),  # precedes vB speech
            ],
        ),
    )


def _fake_levels(source):
    if source == "vA":
        return [0.0] * 20                       # all silence
    if source == "vB":
        return [0.0] * 10 + [0.6] * 10          # silent 0-1000, speech 1000-2000
    if source == "vC":
        return [0.6] * 30                       # speech throughout, no cue
    return None


def test_sync_hints_all_three_kinds():
    from manju.gui.synchints import sync_hints

    hints = sync_hints(None, _sync_timeline(), levels_fn=_fake_levels)
    by_kind = {h.kind: h for h in hints}
    assert set(by_kind) == {"cue_no_speech", "cue_early", "speech_no_cue"}

    # (1) cue over near-silence → names the cue + shot
    assert by_kind["cue_no_speech"].shot == "S001"
    assert by_kind["cue_no_speech"].text == "甲"
    assert "没有对应语音" in by_kind["cue_no_speech"].message
    assert "repair --op voice" in by_kind["cue_no_speech"].fix

    # (3) cue precedes its speech onset by >300ms
    assert by_kind["cue_early"].shot == "S002"
    assert "提前" in by_kind["cue_early"].message

    # (2) sustained speech with no cue → shot S003, mentions 字幕
    assert by_kind["speech_no_cue"].shot == "S003"
    assert "没有字幕覆盖" in by_kind["speech_no_cue"].message
    assert by_kind["speech_no_cue"].start_ms >= 4000


def test_sync_hints_escapes_cue_text():
    from manju.gui.synchints import sync_hints_data

    tl = Timeline(
        fps=24, duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="t", source="a", start_ms=0, duration_ms=2000)],
            voice=[AudioClip(source="vA", start_ms=0, duration_ms=2000)],
            captions=[CaptionLine(start_ms=100, end_ms=1500, text="<script>x</script>")],
        ),
    )
    data = sync_hints_data(None, tl, levels_fn=lambda s: [0.0] * 20)
    assert data["hints"]
    assert "&lt;script&gt;" in data["hints"][0]["text"]  # escaped in to_dict


def test_sync_hints_degrade_without_ffmpeg():
    from manju.gui.synchints import sync_hints, sync_hints_data

    tl = _sync_timeline()
    # levels_fn that can't analyze anything (no ffmpeg / no audio)
    assert sync_hints(None, tl, levels_fn=lambda s: None) == []
    data = sync_hints_data(None, tl, levels_fn=lambda s: None)
    assert data["hints"] == [] and data["available"] is False and data["note"]

    # no timeline at all → empty + note, never raises
    assert sync_hints(None, None) == []
    assert sync_hints_data(None, None)["note"]


def test_synchints_endpoint(gui, tmp_project, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(tmp_project, sid)
    _full_timeline(tmp_project)  # fake audio bytes → rms can't read → degrade
    st, _, data = _req(gui, "/api/edit/synchints")
    assert st == 200
    assert "hints" in data and "note" in data and "available" in data


# ============================================== D. per-boundary transition


def test_transition_override_writes_rules_and_validity(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)   # auto duration → 3000ms each → cap = 1500ms

    # set a per-boundary override on S001's out-edge
    st, _, data = _post(gui, "/api/edit/transition-override",
                        {"shot": "S001", "type": "xfade_fade", "duration_ms": 400})
    assert st == 200 and data.get("ok") is True
    rules = tmp_project.load_rules()
    assert rules.transition_overrides["S001"].type == "xfade_fade"
    assert rules.transition_overrides["S001"].duration_ms == 400
    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "edit_rules"
    assert ev["detail"]["transition_override"]["shot"] == "S001"

    # ½-clip validity: 2000ms > half of the shorter neighbour (1500ms) → 400
    st2, _, data2 = _post(gui, "/api/edit/transition-override",
                          {"shot": "S001", "type": "xfade_fade", "duration_ms": 2000})
    assert st2 == 400 and "上限" in data2["error"]
    # the valid override is unchanged after the rejected one
    assert tmp_project.load_rules().transition_overrides["S001"].duration_ms == 400


def test_transition_override_cut_and_reset(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)

    # null = hard cut
    assert _post(gui, "/api/edit/transition-override", {"shot": "S001", "cut": True})[0] == 200
    ov = tmp_project.load_rules().transition_overrides
    assert "S001" in ov and ov["S001"] is None

    # 恢复默认 removes the key
    assert _post(gui, "/api/edit/transition-override",
                 {"shot": "S001", "action": "reset"})[0] == 200
    assert "S001" not in tmp_project.load_rules().transition_overrides

    # resetting a key that isn't there → clean 400
    assert _post(gui, "/api/edit/transition-override",
                 {"shot": "S001", "action": "reset"})[0] == 400


def test_transition_override_rejects_bad_type(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/edit/transition-override",
                 {"shot": "S001", "type": "nonsense"})[0] == 400
    assert _post(gui, "/api/edit/transition-override", {"type": "fade"})[0] == 400


def test_seam_markers_are_clickable_buttons(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    _, _, body = _html(gui, "/edit")
    # the seam marker is a button carrying its out-edge + cap for the picker
    assert 'class="ed-bound' in body and 'data-out="S001"' in body
    assert 'data-max=' in body
    # the seam picker popover exists
    assert 'id="ed-seam-modal"' in body


# ============================================== E. generative handle rebuild


def test_handle_rebuild_proposal_math():
    """The extended duration + centred window match the render's ½-clip math."""
    import types

    from manju.core.container import Project
    from manju.gui.edit_engine import handle_rebuild_proposal
    from manju.media.render import _xfade_half_ms

    # a tiny throwaway project with one shot
    import tempfile
    from pathlib import Path
    from manju.core.models import ShotSpec

    d = Path(tempfile.mkdtemp())
    project = Project.create(d / "p", git_init=False)
    project.save_shot(ShotSpec.model_validate({"id": "S001", "duration": 2}))
    idx = project.load_index()
    idx.order.append("S001")
    project.save_index(idx)

    prop = handle_rebuild_proposal(project, "S001", transition_ms=500)
    fps = project.load_config().fps
    half = _xfade_half_ms(500, fps)
    assert prop["clip_ms"] == 2000
    assert prop["half_ms"] == half
    assert prop["extended_ms"] == prop["clip_ms"] + 2 * prop["half_ms"]
    # centred content span: half handle on each side
    assert prop["in_ms"] == prop["half_ms"]
    assert prop["out_ms"] == prop["half_ms"] + prop["clip_ms"]
    assert prop["supported"] is True  # no manifest → local provider honours duration


def _write_no_duration_manifest(tmp_path):
    prov = tmp_path / "_providers" / "nodur"
    prov.mkdir(parents=True, exist_ok=True)
    (prov / "provider.yaml").write_text(
        "id: nodur\n"
        "type: video\n"
        "adapter: generic_cloud\n"
        "capabilities: [text_to_video]\n"
        "submit:\n"
        "  url: https://example.com/v\n"
        "  body_template:\n"
        "    prompt: '{prompt}'\n"
        "    size: '{width}x{height}'\n"
        "  job_id_path: $.id\n"
        "poll:\n"
        "  url: https://example.com/v/{job_id}\n"
        "  status_path: $.status\n"
        "  status_map: {done: succeeded}\n"
        "  result_url_path: $.url\n"
        "cost:\n"
        "  per_second: 0.0\n",
        encoding="utf-8")


def test_handle_rebuild_no_duration_advisory(gui, tmp_project, add_shot, tmp_path):
    _write_no_duration_manifest(tmp_path)
    add_shot(tmp_project, "S001", generation={"provider": "nodur", "candidates": 1})

    # the proposal is honest: no duration control → advisory, no cost
    st, _, prop = _post(gui, "/api/edit/handle-rebuild-plan", {"shot": "S001"})
    assert st == 200
    assert prop["supported"] is False
    assert prop["advisory"] == "该生成来源不支持指定时长,无法补拍手柄"

    # actually running it is refused synchronously with the same advisory
    st2, _, data2 = _post(gui, "/api/edit/handle-rebuild", {"shot": "S001"})
    assert st2 == 400 and "无法补拍手柄" in data2["error"]


@pytest.mark.skipif(not _HAS_FFMPEG, reason="handle rebuild generates + probes real media")
def test_handle_rebuild_runs_with_mock_provider(tmp_project, add_shot):
    """The orchestration: regen LONGER through the redo path, then virtual-trim
    to the centred span leaving real handles."""
    from pathlib import Path
    import tempfile

    from manju.core.models import TakeSidecar
    from manju.providers.base import GenerationRequest, Provider
    from manju.providers.registry import register_provider
    from manju.gui.edit_engine import run_handle_rebuild

    class MockDurationProvider(Provider):
        id = "mockdur"
        kind = "local"

        def generate(self, req: GenerationRequest):
            secs = max(0.2, req.duration_ms / 1000.0)
            d = Path(tempfile.mkdtemp())
            out = d / "gen.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"testsrc=duration={secs}:size=64x48:rate=24",
                 "-pix_fmt", "yuv420p", str(out)], check=True, capture_output=True)
            sidecar = TakeSidecar(provider=self.id, spec_hash=req.spec_hash)
            return [req.project.register_take(req.shot.id, out, sidecar, move=True)]

    register_provider(MockDurationProvider())
    add_shot(tmp_project, "S001", duration=2,
             generation={"provider": "mockdur", "candidates": 1,
                         "fallback": ["caption_card"]})

    result = run_handle_rebuild(tmp_project, "S001", transition_ms=500,
                                actor="human", assume_yes=True)
    assert result["shot"] == "S001"
    gen = result["gen_take"]
    trim = result["trim_take"]
    assert gen != trim

    # the trim take carries a real window with spare head/tail (the handles)
    ti = tmp_project.get_take("S001", trim)
    assert ti is not None
    assert ti.sidecar.source_in_ms > 0
    assert ti.sidecar.source_out_ms is not None
    assert 0 < ti.sidecar.source_in_ms < ti.sidecar.source_out_ms

    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "handle_rebuild"
    assert ev["detail"]["shot"] == "S001" and ev["detail"]["trim_take"] == trim


def test_handle_rebuild_refuses_when_routing_drifted_since_proposal(tmp_project, add_shot):
    """Goal 63: the executed redo is pinned to the provider the PROPOSAL
    priced (the GUI passes back what handle_rebuild_proposal showed); if
    routing.yaml changes in the window between propose and confirm, refuse
    with a 已重新报价 message instead of silently running a different,
    un-priced provider. No ffmpeg needed — the refusal fires before any
    generation is attempted."""
    from manju.core.models import ShotSpec, TakeSidecar
    from manju.core.yamlio import write_yaml
    from manju.providers.base import GenerationRequest, Provider
    from manju.providers.registry import register_provider
    from manju.gui.edit_engine import (
        HandleRebuildError,
        handle_rebuild_proposal,
        run_handle_rebuild,
    )

    class _StubProvider(Provider):
        kind = "local"

        def __init__(self, id_: str):
            self.id = id_
            self.called = False

        def generate(self, req: GenerationRequest):
            self.called = True  # must never be reached by this test
            raise AssertionError("generate() should not run when routing drifted")

    prov_a = _StubProvider("route_a")
    prov_b = _StubProvider("route_b")
    register_provider(prov_a)
    register_provider(prov_b)
    # no explicit generation.provider -> routing decides the head
    add_shot(tmp_project, "S001", duration=2,
             generation={"candidates": 1, "fallback": ["caption_card"]})
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "default",
        "strategies": {"default": {"rules": [], "else": "route_a"}}})

    prop = handle_rebuild_proposal(tmp_project, "S001", transition_ms=500)
    assert prop["supported"] is True
    assert prop["provider"] == "route_a"

    # routing drifts in the window between propose (shown to the human) and
    # confirm (this call) — e.g. someone edited routing.yaml meanwhile.
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "default",
        "strategies": {"default": {"rules": [], "else": "route_b"}}})

    with pytest.raises(HandleRebuildError) as exc:
        run_handle_rebuild(tmp_project, "S001", transition_ms=500,
                           provider=prop["provider"], actor="human", assume_yes=True)
    assert "已重新报价" in str(exc.value)
    assert "route_a" in str(exc.value) and "route_b" in str(exc.value)
    assert not prov_a.called and not prov_b.called  # refused before any spend

    # unchanged routing (no drift) still runs through to the pinned provider —
    # confirms the check is a real refuse-on-mismatch, not a blanket block.
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "default",
        "strategies": {"default": {"rules": [], "else": "route_a"}}})
    with pytest.raises(AssertionError):  # our stub deliberately raises once called
        run_handle_rebuild(tmp_project, "S001", transition_ms=500,
                           provider="route_a", actor="human", assume_yes=True)
    assert prov_a.called


# ================================================================ F. mode + guards


def test_advanced_panels_are_pro_only(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _, _, body = _html(gui, "/edit")
    # advanced panels carry mj-pro-only (hidden in 新手 via CSS, never removed)
    assert "ed-trans-panel mj-pro-only" in body       # 转场
    assert "ed-look-panel mj-pro-only" in body         # 调色
    assert "ed-sync-panel mj-pro-only" in body         # 字幕/语音同步
    # the lanes view itself is CORE (visible in both modes)
    assert "ed-lanes-panel" in body and "mj-pro-only" not in body.split("ed-lanes-panel")[0][-40:]


def test_native_cut_token_and_readonly_guards(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # CSRF: the new mutating POSTs need the token
    for path, b in (
        ("/api/edit/transition-override", {"shot": "S001", "cut": True}),
        ("/api/edit/handle-rebuild", {"shot": "S001"}),
        ("/api/edit/handle-rebuild-plan", {"shot": "S001"}),
    ):
        status, _, data = _req(gui, path, method="POST", body=b)
        assert status == 403 and "token" in data["error"].lower()
    # DNS-rebinding guard on the new GET surfaces
    assert _req(gui, "/edit/wave?take=x", host="evil.example.com")[0] == 403
    assert _req(gui, "/api/edit/synchints", host="evil.example.com")[0] == 403


def test_native_cut_readonly_blocks_mutations(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _html(server, "/edit")[0] == 200
        for path, b in (
            ("/api/edit/transition-override", {"shot": "S001", "cut": True}),
            ("/api/edit/handle-rebuild", {"shot": "S001"}),
        ):
            status, _, data = _post(server, path, b)
            assert status == 403 and "readonly" in data["error"]
        # the synchints GET stays available read-only
        assert _req(server, "/api/edit/synchints")[0] == 200
    finally:
        server.shutdown()
        server.close()
