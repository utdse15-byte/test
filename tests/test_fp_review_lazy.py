"""FP_REVIEW_LAZY — audit finding G1 (OPT-GUI): the /review render is cheap.

The measured defect: ``GET /review`` took 16.6s at 40 shots because
``_consistency_section`` ran ``qc_brief(mode="consistency")`` INLINE on the
request thread — 316 ffprobe + 158 ffmpeg spawns composing the per-unit contact
sheets, and ``_member_frame`` re-probed a midpoint the take's sidecar probe
already carried (the SAME redundant-probe pattern Tier-1 #2 fixes for voice in
the compiler).

Two halves, both pinned here (red-first):

  1. ``qc.agent_review._member_frame`` reads ``sidecar.probe.duration_ms`` FIRST
     and only a probe-less sidecar falls back to a live ffprobe. The midpoint
     arithmetic (``dur // 2``) is UNCHANGED, so a cached-duration path and a
     live-probe path that see the same duration request the byte-IDENTICAL
     frame timestamp (parity assert) — the extracted JPEG is the same bytes.

  2. ``pages.render_review`` composes NO board inline. The consistency section
     renders its cheap unit STRUCTURE + verdict forms server-side (so the human
     flow is usable at first paint and the existing inline assertions stay
     green), and the page JS fetches the boards from a NEW token-gated
     ``POST /api/review/consistency`` after first paint. So the page GET spawns
     ZERO subprocesses (spy-pinned), the endpoint respects the token gate, and a
     fetch failure degrades to a labelled note (never a blank).

These tests use FAKE takes (a few bytes on disk) — they never require ffmpeg:
the probe/extract calls are spied, and the endpoint's board simply degrades to
``image: null`` when the bytes are not decodable, which is the no-ffmpeg case.
"""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from manju.core.models import ProbeInfo, TakeSidecar
from manju.gui import pages
from manju.gui.server import create_server

# ------------------------------------------------------------------ harness


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    """Point the user-level shelves at tmp dirs so nothing touches ~/.manju
    (mirrors tests/test_gui_pages.py::_isolate_user_dirs)."""
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
        with urllib.request.urlopen(req, timeout=15) as resp:
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


def _post(server, path, body, token=None, **kw):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _req(server, path, method="POST", body=body, headers=headers, **kw)


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _two_shot_unit_project(tmp_project, add_shot, make_take):
    """S001 + S002 both feature linxia in convenience_store (the tmp_project
    bible defaults) with a selected FAKE take each — enough for the character,
    pair and scene consistency units, and NO ffmpeg needed to build it."""
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    for sid in ("S001", "S002"):
        take = make_take(tmp_project, sid, "h")
        _select(tmp_project, sid, take.name)


# ------------------------------------------------- half 2: zero-spawn render


def test_render_review_spawns_zero_subprocesses(monkeypatch, tmp_project, add_shot, make_take):
    """The /review render itself spawns NOTHING: subprocess.run/Popen are made
    hard failures AFTER the project is built, then render_review is called
    directly. Before G1 this fired ffprobe/ffmpeg composing the boards; now the
    boards are lazy so the request thread is subprocess-free."""
    _two_shot_unit_project(tmp_project, add_shot, make_take)

    spawns = []

    def _forbid(*args, **kwargs):
        spawns.append(args[0] if args else kwargs.get("args"))
        raise AssertionError(f"render_review spawned a subprocess: {spawns[-1]!r}")

    monkeypatch.setattr(subprocess, "run", _forbid)
    monkeypatch.setattr(subprocess, "Popen", _forbid)

    html = pages.render_review(tmp_project, "tok-xyz")

    assert spawns == []                                # ZERO spawns on the request thread
    assert '审片 <span class="mj-en">Review</span>' in html  # the page still rendered
    assert "跨镜一致性 Consistency" in html              # section header present


def test_consistency_section_structure_renders_inline_without_boards(
        tmp_project, add_shot, make_take):
    """The cheap unit STRUCTURE (units, members, verdict form, coverage chip)
    still renders server-side inline — this is exactly what the existing
    tests/test_qc_consistency.py inline assertions depend on — while the board
    itself is a LAZY placeholder slot the JS fills after first paint."""
    _two_shot_unit_project(tmp_project, add_shot, make_take)

    html = pages.render_review(tmp_project, "tok")

    assert 'data-unit="character:linxia"' in html       # character unit inline
    assert 'data-unit="pair:S001~S002"' in html         # pair unit inline
    assert "提交裁决" in html                             # verdict form inline
    assert "未判读" in html                               # coverage chip inline (never judged)
    assert "cs-board-slot" in html                        # board is a lazy slot, not composed
    # the composed <img class="cs-board"> is NOT baked into the GET
    assert 'class="cs-board" src="/media/' not in html


def test_consistency_section_empty_state_still_inline(gui, tmp_project, add_shot):
    """A single shot -> no comparison unit -> the honest empty-state line still
    renders inline (protects test_qc_consistency::…_empty_state)."""
    add_shot(tmp_project, "S001")
    status, headers, body = _req(gui, "/review", raw=True)
    text = body.decode("utf-8")
    assert status == 200
    assert "暂无可判读的一致性组合" in text


# --------------------------------------- half 2: the lazy endpoint + token gate


def test_consistency_endpoint_returns_units_and_token_gated(
        gui, tmp_project, add_shot, make_take):
    _two_shot_unit_project(tmp_project, add_shot, make_take)

    # token gate: a POST with NO X-Manju-Token is refused 403 (mirrors
    # test_qc_verdict_endpoint_guards / test_dangerous_surface_still_guarded)
    status, _, data = _req(gui, "/api/review/consistency", method="POST", body={})
    assert status == 403 and "token" in data["error"].lower()

    # with the token: 200 + the consistency units the page JS fills in
    status, _, data = _post(gui, "/api/review/consistency", {})
    assert status == 200 and data["ok"] is True
    unit_ids = {u["unit"] for u in data["units"]}
    assert "character:linxia" in unit_ids                 # character unit
    assert "pair:S001~S002" in unit_ids                   # adjacent-pair unit
    # every unit carries an image field; without a decodable board it is null,
    # never a crash and never a raw filesystem path
    for u in data["units"]:
        assert "image" in u
        assert u["image"] is None or u["image"].startswith("/media/")


def test_consistency_endpoint_wrong_token_is_403(gui, tmp_project, add_shot, make_take):
    _two_shot_unit_project(tmp_project, add_shot, make_take)
    status, _, data = _req(gui, "/api/review/consistency", method="POST", body={},
                           headers={"X-Manju-Token": "wrong"})
    assert status == 403 and "token" in data["error"].lower()


def test_consistency_endpoint_readonly_allowed(tmp_project, tmp_path, add_shot, make_take):
    """The board compute is a pure read (composes only the content-addressed
    frame cache, writes no truth file) so — like /api/validate + /api/impact —
    it is permitted under readonly, preserving the pre-G1 behaviour where the
    consistency boards rendered even on a readonly workbench."""
    _two_shot_unit_project(tmp_project, add_shot, make_take)
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, data = _post(server, "/api/review/consistency", {})
        assert status == 200 and data["ok"] is True
    finally:
        server.shutdown()
        server.close()


# --------------------------------------- half 2: the JS lazy fetch + degradation


def test_page_js_lazy_fetch_and_degradation_marker():
    js = pages.render_pages_js()
    assert "/api/review/consistency" in js       # the lazy board fetch target
    assert "cs-board-slot" in js                  # the JS fills the lazy slots
    assert 'details.addEventListener("toggle"' in js
    assert "if (details.open) loadBoards();" in js
    # fetch failure -> a labelled note, never a blank (degradation contract)
    assert "看板加载失败" in js


def test_review_theater_defers_main_media_and_keeps_actions_first(
        tmp_project, add_shot, make_take):
    _two_shot_unit_project(tmp_project, add_shot, make_take)

    html = pages.render_review(tmp_project, "tok")
    assert html.count('<video class="rv-video" data-src=') == 2
    assert '<video class="rv-video" src=' not in html
    assert html.index('class="rv-actions') < html.index('class="rv-body')
    assert '<details class="panel rv-pro-section">' in html

    js = pages.render_pages_js()
    assert 'activeVideo.removeAttribute("src")' in js
    assert "activateMedia(shots[active]);" in js


# ---------------------------------------- half 1: _member_frame sidecar probe


def _run_member_frame(monkeypatch, project, shot_id, take_name, *, live_duration_ms):
    """Run _member_frame with a probe SPY and an extract_frame CAPTURE, so we
    see (a) how many live probes it spawned and (b) the exact frame-request
    timestamp it handed extract_frame. Returns (probe_call_count, at_ms)."""
    import importlib

    from manju.qc.agent_review import _member_frame

    probe_calls: list = []
    captured: dict = {}

    def _spy_probe(path, **kw):
        probe_calls.append(path)
        return ProbeInfo(duration_ms=live_duration_ms)

    def _spy_extract(project_, media_rel, at_ms, **kw):
        captured["at_ms"] = at_ms
        return project_.root / "_frame.jpg"

    # _member_frame does `from ..media.probe import probe` / `from ..media.frames
    # import extract_frame` at call time. The media package re-exports `probe`
    # the function, which shadows the submodule for attribute-walking resolvers
    # (both `import ... as` and monkeypatch's dotted string), so grab the real
    # module objects from sys.modules via importlib and patch those directly.
    probe_mod = importlib.import_module("manju.media.probe")
    frames_mod = importlib.import_module("manju.media.frames")
    monkeypatch.setattr(probe_mod, "probe", _spy_probe)
    monkeypatch.setattr(frames_mod, "extract_frame", _spy_extract)

    _member_frame(project, shot_id, take_name)
    return len(probe_calls), captured.get("at_ms")


def test_member_frame_prefers_sidecar_probe_and_is_byte_identical(
        monkeypatch, tmp_project, add_shot, make_take, tmp_path):
    """The core parity pin: a take whose sidecar carries a probe duration makes
    ZERO live probe calls, and the frame timestamp it requests is IDENTICAL to
    the one the live-probe path computes from the same duration — so the cached
    path and the (legacy) live path extract the byte-identical JPEG."""
    add_shot(tmp_project, "S001")

    # take WITH a sidecar probe carrying the duration (what a generated take has)
    src = tmp_path / "_probed.mp4"
    src.write_bytes(b"probed-take-bytes")
    probed = tmp_project.register_take(
        "S001", src,
        TakeSidecar(provider="test", spec_hash="h", probe=ProbeInfo(duration_ms=4000)),
    )
    # take WITHOUT a probe (make_take writes none) — the fallback path
    live = make_take(tmp_project, "S001", "h2")

    # cached path: live probe would (deliberately) report a DIFFERENT duration,
    # so if the sidecar were ignored at_ms would be 499999, not 2000
    n_cached, at_cached = _run_member_frame(
        monkeypatch, tmp_project, "S001", probed.name, live_duration_ms=999999)
    # live path: probe-less sidecar -> one live probe reporting the SAME 4000ms
    n_live, at_live = _run_member_frame(
        monkeypatch, tmp_project, "S001", live.name, live_duration_ms=4000)

    assert n_cached == 0                 # sidecar path spawns NO probe
    assert n_live == 1                   # probe-less sidecar falls back to one live probe
    assert at_cached == 2000             # 4000 // 2, from the sidecar
    assert at_live == 2000               # 4000 // 2, from the live probe
    assert at_cached == at_live          # PARITY: byte-identical frame-request timestamp


def test_member_frame_falls_back_when_sidecar_probe_absent(
        monkeypatch, tmp_project, add_shot, make_take):
    """A sidecar with no probe (or probe.duration_ms=None) still works: exactly
    one live probe, midpoint from its duration."""
    add_shot(tmp_project, "S001")
    live = make_take(tmp_project, "S001", "h")
    n, at_ms = _run_member_frame(
        monkeypatch, tmp_project, "S001", live.name, live_duration_ms=3000)
    assert n == 1
    assert at_ms == 1500
