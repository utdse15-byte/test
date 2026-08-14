"""`manju gui` — the 剪辑 EDIT page (round T).

Companion to :mod:`tests.test_gui_pages`: asserts the clip-level finishing
surface (:mod:`manju.gui.edit`) does everything WITHOUT opening JianYing —
timeline order, in/out trim, footage audio, the default transition and the color
look — through the SAME engine core the CLI calls (``set_inout_take``,
``apply_mixer``, ``rules.transition_default``, ``bible/style.yaml`` look), lands
the SAME events, and never bypasses the token / readonly / DNS-rebinding gates.

The page is server-rendered, so a plain GET carries the real fixture content; the
frame previews are lazy ``<img>`` and never block the page GET on ffmpeg, so the
render tests run with or without ffmpeg. The trim + frame-serving tests are
skipped where ffmpeg is absent.
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
from manju.core.models import TakeSidecar
from manju.gui.server import create_server

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    """Point the user-level shelves at tmp dirs so the page never touches the
    real ~/.manju (read at call time; the server runs in-process)."""
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


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name))


def _real_take(project, shot_id, seconds=1):
    src = project.root / f"_realsrc_{shot_id}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=duration={seconds}:size=64x48:rate=24",
         "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True)
    return project.register_take(shot_id, src,
                                 TakeSidecar(provider="test", spec_hash="h"))


def _two_shots(tmp_project, add_shot, make_take):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    take = make_take(tmp_project, "S001", "h")
    _select(tmp_project, "S001", take.name)
    return take


# ============================================================ page render


def test_edit_page_renders_with_content(gui, tmp_project, add_shot, make_take):
    take = _two_shots(tmp_project, add_shot, make_take)

    status, headers, body = _html(gui, "/edit")
    assert status == 200
    assert 'data-page="/edit"' in body
    assert '<h1>剪辑<span class="mj-en"' in body
    assert '<h2>时间线<span class="mj-en"' in body
    assert "S001" in body and "S002" in body
    # panels
    assert "转场 Transitions" in body and "调色 Look" in body
    # the selected take's card thumb points at the lazy frame endpoint
    assert "/edit/frame?take=" in body
    assert quote_take(tmp_project, take) in body
    # inspector affordances baked in server-side
    assert "裁剪 Trim" in body and "素材声" in body and "时长 Duration" in body
    # honest per-boundary note (global default picker; per-boundary override
    # now lives in rules.yaml's transition_overrides — round-U engine change)
    assert "transition_overrides" in body
    # one boundary marker between the two shots
    assert "ed-bound" in body


def quote_take(project, take):
    from urllib.parse import quote

    return quote(project.relpath(take.media_path), safe="")


def test_edit_in_nav_everywhere(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # the SPA and the sibling pages all link to /edit
    assert 'href="/edit"' in _html(gui, "/")[1] or 'href="/edit"' in _html(gui, "/")[2]
    assert 'href="/edit"' in _html(gui, "/review")[2]
    assert "剪辑" in _html(gui, "/edit")[2]


# ============================================================ transitions state


def test_edit_renders_transition_state_from_last_build(gui, tmp_project, add_shot,
                                                       make_take):
    """A seeded final_vN.transitions.json drives the per-boundary marker: a
    degraded xfade shows ✗ with its degrade reason on hover."""
    _two_shots(tmp_project, add_shot, make_take)
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.final_dir / "final_v1.mp4").write_bytes(b"final")
    (tmp_project.final_dir / "final_v1.transitions.json").write_text(
        json.dumps({"transitions": [
            {"boundary": "S001->S002", "requested": "xfade_fade",
             "applied": False, "reason": "clip too short for a 300ms cross-dissolve"}]}),
        encoding="utf-8")

    _, _, body = _html(gui, "/edit")
    assert "clip too short for a 300ms cross-dissolve" in body   # degrade reason on hover
    assert "ed-bound bad" in body                                # degraded marker


def test_edit_transition_marker_is_dash_before_any_build(gui, tmp_project, add_shot,
                                                         make_take):
    _two_shots(tmp_project, add_shot, make_take)
    _, _, body = _html(gui, "/edit")
    # no final yet → the marker is "—"
    assert "尚未构建" in body


# ============================================================ footage audio


def test_edit_audio_round_trips_mixer(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/edit/audio",
                            {"shot": "S001", "gain_db": -6.5, "mute": True})
    assert status == 200 and data["ok"] is True

    # written through the SAME shot source_audio the CLI mixer writes
    shot = tmp_project.load_shot("S001")
    assert shot.source_audio.gain_db == -6.5
    assert shot.source_audio.mute is True

    # the mixer view reflects it and one `mixer` event landed
    from manju.build.mixer import read_mixer
    view = {s["shot"]: s for s in read_mixer(tmp_project)["shots"]}
    assert view["S001"]["gain_db"] == -6.5 and view["S001"]["mute"] is True
    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "mixer"

    # unknown shot → clean 404, not a crash
    assert _post(gui, "/api/edit/audio", {"shot": "NOPE", "gain_db": 0})[0] == 404
    assert _post(gui, "/api/edit/audio", {"gain_db": 0})[0] == 400


# ============================================================ transitions default


def test_edit_transition_default_writes_rules(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/edit/transition",
                            {"type": "xfade_fade", "duration_ms": 450})
    assert status == 200 and data.get("ok") is True

    rules = tmp_project.load_rules()
    assert rules.transition_default.type == "xfade_fade"
    assert rules.transition_default.duration_ms == 450
    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "edit_rules"
    assert ev["detail"]["transition_default"]["type"] == "xfade_fade"


def test_edit_transition_rejects_invalid(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/edit/transition", {"type": "nonsense"})[0] == 400
    assert _post(gui, "/api/edit/transition",
                 {"type": "fade", "duration_ms": 99999})[0] == 400
    assert _post(gui, "/api/edit/transition",
                 {"type": "fade", "duration_ms": "abc"})[0] == 400
    # a valid one still leaves rules unbroken (default type unchanged after reject)
    assert _post(gui, "/api/edit/transition", {"type": "cut", "duration_ms": 0})[0] == 200


# ============================================================ look


def test_edit_look_writes_style_yaml(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/edit/look", {"preset": "warm", "intensity": 0.5})
    assert status == 200 and data.get("ok") is True

    from manju.media.render import load_look
    look = load_look(tmp_project)
    assert look.preset == "warm" and look.intensity == 0.5
    assert (tmp_project.root / "bible" / "style.yaml").exists()
    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "edit_bible" and ev["detail"]["file"] == "style"


def test_edit_look_intensity_and_preset_bounds(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/edit/look", {"preset": "warm", "intensity": 2.0})[0] == 400
    assert _post(gui, "/api/edit/look", {"preset": "warm", "intensity": -0.1})[0] == 400
    assert _post(gui, "/api/edit/look", {"preset": "nonsense", "intensity": 0.3})[0] == 400
    # none at any intensity is valid (a no-op look)
    assert _post(gui, "/api/edit/look", {"preset": "none", "intensity": 0})[0] == 200


def test_edit_look_preserves_other_style_keys(gui, tmp_project, add_shot):
    from manju.core.yamlio import read_yaml, write_yaml

    add_shot(tmp_project, "S001")
    style = tmp_project.root / "bible" / "style.yaml"
    write_yaml(style, {"caption": {"font": "Source Han Sans"}})
    assert _post(gui, "/api/edit/look", {"preset": "cool", "intensity": 0.8})[0] == 200
    data = read_yaml(style)
    assert data["look"] == {"preset": "cool", "intensity": 0.8}
    assert data["caption"] == {"font": "Source Han Sans"}   # untouched


# ============================================================ duration override


def test_edit_duration_override(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")   # defaults to duration: auto
    status, _, data = _post(gui, "/api/edit/duration", {"shot": "S001", "duration": 3.5})
    assert status == 200, data
    assert tmp_project.load_shot("S001").duration == 3.5

    # back to auto
    assert _post(gui, "/api/edit/duration", {"shot": "S001", "duration": "auto"})[0] == 200
    assert tmp_project.load_shot("S001").duration == "auto"

    # rejects garbage and unknown shot
    assert _post(gui, "/api/edit/duration", {"shot": "S001", "duration": -1})[0] == 400
    assert _post(gui, "/api/edit/duration", {"shot": "NOPE", "duration": 2})[0] == 404


# ============================================================ reorder


def test_edit_reorder_round_trip(gui, tmp_project, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(tmp_project, sid)
    status, _, data = _post(gui, "/api/index", {"order": ["S003", "S001", "S002"]})
    assert status == 200 and data["order"] == ["S003", "S001", "S002"]
    assert tmp_project.load_index().order == ["S003", "S001", "S002"]

    # the strip reflects the new order (S003 card renders before S001)
    _, _, body = _html(gui, "/edit")
    assert body.index("S003") < body.index("S001") < body.index("S002")


# ============================================================ trim (ffmpeg)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="trim (set_inout) needs ffmpeg/ffprobe")
def test_edit_trim_mints_take_via_op(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    src = _real_take(tmp_project, "S001")
    _select(tmp_project, "S001", src.name)
    before = len(tmp_project.takes("S001"))

    status, _, data = _post(gui, "/api/edit/trim",
                            {"shot": "S001", "in_ms": 100, "out_ms": 600})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "done", job.get("error")
    assert job["result"]["op"] == "inout" and job["result"]["source_take"] == src.name

    takes = tmp_project.takes("S001")
    assert len(takes) == before + 1                     # append-only
    new_name = job["result"]["new_take"]
    assert any(t.name == new_name for t in takes)
    assert src.media_path.exists()                      # source untouched
    # NOT auto-selected — human selection judgment is preserved
    assert tmp_project.load_shot("S001").status.selected_take == src.name

    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "repair" and ev["detail"]["op"] == "inout"
    assert ev["detail"]["via"] == "gui"


def test_edit_trim_validates(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # no selected take and none passed → clean 400
    status, _, data = _post(gui, "/api/edit/trim",
                            {"shot": "S001", "in_ms": 0, "out_ms": 100})
    assert status == 400 and "take" in data["error"]
    # unknown shot → 404
    assert _post(gui, "/api/edit/trim",
                 {"shot": "NOPE", "in_ms": 0, "out_ms": 100})[0] == 404


@pytest.mark.skipif(not _HAS_FFMPEG, reason="trim range validation needs a real take")
def test_edit_trim_bad_range_is_400(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    src = _real_take(tmp_project, "S001")
    _select(tmp_project, "S001", src.name)
    # out <= in → synchronous 400 (no job)
    assert _post(gui, "/api/edit/trim",
                 {"shot": "S001", "in_ms": 500, "out_ms": 500})[0] == 400
    assert _post(gui, "/api/edit/trim",
                 {"shot": "S001", "in_ms": 100, "out_ms": 50})[0] == 400


# ============================================================ frame previews


@pytest.mark.skipif(not _HAS_FFMPEG, reason="frame previews need ffmpeg/ffprobe")
def test_edit_frame_and_strip_and_look_serve(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    src = _real_take(tmp_project, "S001")
    rel = quote_take(tmp_project, src)

    st, headers, body = _req(gui, f"/edit/frame?take={rel}&ms=0&w=120", raw=True)
    assert st == 200 and len(body) > 0
    assert headers.get("Content-Type", "").startswith("image/")

    st, _, body = _req(gui, f"/edit/strip?take={rel}&i=0&n=4&w=80", raw=True)
    assert st == 200 and len(body) > 0

    st, _, body = _req(gui, f"/edit/look?take={rel}&ms=0&w=120&preset=warm&intensity=0.6",
                       raw=True)
    assert st == 200 and len(body) > 0

    # none look falls back to the raw frame extractor (still 200)
    st, _, _ = _req(gui, f"/edit/look?take={rel}&preset=none&intensity=0", raw=True)
    assert st == 200


def test_edit_frame_rejects_non_media_path(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # a path outside the media allowlist → 404 (never reads outside the tree)
    from urllib.parse import quote

    assert _req(gui, "/edit/frame?take=" + quote("../../etc/passwd", safe=""))[0] == 404
    assert _req(gui, "/edit/frame?take=" + quote("bible/style.yaml", safe=""))[0] == 404


# ============================================================ guards


def test_edit_dangerous_surface_still_guarded(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # CSRF: every edit action needs the token
    for path, body in (
        ("/api/edit/audio", {"shot": "S001", "gain_db": 0}),
        ("/api/edit/transition", {"type": "fade"}),
        ("/api/edit/look", {"preset": "warm", "intensity": 0.5}),
        ("/api/edit/duration", {"shot": "S001", "duration": 2}),
        ("/api/edit/trim", {"shot": "S001", "in_ms": 0, "out_ms": 100}),
    ):
        status, _, data = _req(gui, path, method="POST", body=body)
        assert status == 403 and "token" in data["error"].lower()
    # DNS-rebinding guard applies to the new page + its previews too
    assert _html(gui, "/edit", host="evil.example.com")[0] == 403
    assert _req(gui, "/edit/frame?take=x", host="evil.example.com")[0] == 403
    # containment: unlock / gc never exist on this surface either
    assert _post(gui, "/api/unlock", {})[0] == 404


def test_edit_readonly_blocks_actions(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # the page still renders read-only
        assert _html(server, "/edit")[0] == 200
        # but every mutation is refused
        for path, body in (
            ("/api/edit/audio", {"shot": "S001", "gain_db": 0}),
            ("/api/edit/transition", {"type": "fade"}),
            ("/api/edit/look", {"preset": "warm", "intensity": 0.5}),
            ("/api/edit/duration", {"shot": "S001", "duration": 2}),
            ("/api/edit/trim", {"shot": "S001", "in_ms": 0, "out_ms": 100}),
        ):
            status, _, data = _post(server, path, body)
            assert status == 403 and "readonly" in data["error"]
    finally:
        server.shutdown()
        server.close()
