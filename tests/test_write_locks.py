"""Round Z (agent ZA): cross-process write-lock coverage for GUI/MCP/board
LIGHT-WRITERS — the round-W debt the external review flagged (#5).

Round W wired the cross-process ``.manju/build.lock`` (runtime/buildlock.py)
into every HEAVY engine entrance (build/redo/voice/qc) and into the CLI's
generic ``_write_lock`` for its light writes (select/import/gc/lock/rollback/
snapshot/repair, cli.py's ``_write_lock``). It did NOT reach every light
truth-file write on the GUI/MCP/board surfaces — a mixer/caption/packaging/
routing/shot edit, or a board rollback, could still land on disk WHILE a
separate CLI ``manju build`` process held the lock. This module is NOT a
corruption test (atomic writes + content-keys already make a torn/stale
write self-healing, see runtime/buildlock.py's own module docstring) — it is
a CONSISTENCY test: a light writer must now visibly REFUSE (busy) rather
than silently interleave with a build that is mid-flight.

Covers a representative set, not every handler (the full classification
lives in the round Z commit message / PR description):

  * GUI light-writers newly wrapped this round — a ``_gated_save``-backed
    editor (``/api/rules``, protects 11 call sites at once) and a
    hand-wired one (``/api/subtitles/save``) — busy under contention,
    normal otherwise.
  * MCP ``update_shot`` (the only shot-file-writing tool) — same shape.
  * Board ``rollback_shot`` (previously had NO process lock at all) — same
    shape, surfaced as the board's existing 409 "busy" JSON.
  * A NO-DOUBLE-ACQUIRE regression guard: handlers whose underlying engine
    call ALREADY takes ``build_lock`` internally (GUI ``/api/select``,
    ``/api/mixer/apply`` via ``build.mixer.apply_mixer``) must keep working
    with no external contention — proof this round did not add a second,
    self-conflicting wrap around them.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.runtime.buildlock import BuildLock

# --------------------------------------------------------------- GUI harness

from manju.gui.server import create_server  # noqa: E402


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed


def _post(server, path, body):
    return _req(server, path, method="POST", body=body,
                headers={"X-Manju-Token": server.token})


# =========================================================== GUI light-writers


def test_gui_rules_save_busy_under_build_lock_no_mutation(gui, tmp_project):
    """``/api/rules`` funnels through ``_gated_save`` — wrapping THAT one
    helper (rather than each of its 11 callers) is this round's main lever;
    assert it actually took effect here."""
    before = tmp_project.rules_path.read_text(encoding="utf-8")
    new_yaml = before.replace("timing:", "timing:  # touched\n") if "timing:" in before else (
        before + "\n# touched\n")

    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        status, data = _post(gui, "/api/rules", {"yaml": new_yaml})
    finally:
        lock.release()
    assert status == 409
    assert "error" in data
    assert tmp_project.rules_path.read_text(encoding="utf-8") == before  # untouched

    # released -> the SAME request now succeeds normally
    status, data = _post(gui, "/api/rules", {"yaml": new_yaml})
    assert status == 200 and data["ok"] is True
    assert tmp_project.rules_path.read_text(encoding="utf-8") == new_yaml


def test_gui_caption_edit_busy_under_build_lock_no_mutation(gui, tmp_project):
    """``/api/subtitles/save`` (captions/captions.srt) — a hand-wired light
    writer (not funneled through ``_gated_save``), wrapped separately."""
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt_before = "1\n00:00:00,000 --> 00:00:01,000\n自动字幕\n\n"
    (tmp_project.captions_dir / "captions.srt").write_text(srt_before, encoding="utf-8")

    cues = {"cues": [{"start_ms": 0, "end_ms": 1200, "text": "手改字幕"}],
            "confirm_manual": True}

    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        status, data = _post(gui, "/api/subtitles/save", cues)
    finally:
        lock.release()
    assert status == 409
    assert "error" in data
    assert tmp_project.load_rules().captions.mode == "compiled"  # untouched
    assert (tmp_project.captions_dir / "captions.srt").read_text(encoding="utf-8") == srt_before

    # released -> succeeds normally, flips to manual and writes the cue
    status, data = _post(gui, "/api/subtitles/save", cues)
    assert status == 200 and data["ok"] is True and data["flipped"] is True
    assert tmp_project.load_rules().captions.mode == "manual"
    assert "手改字幕" in (tmp_project.captions_dir / "captions.srt").read_text(encoding="utf-8")


# ================================================================= MCP tool


def test_mcp_update_shot_respects_build_lock(tmp_project, add_shot):
    """``update_shot`` is the ONLY MCP tool that writes a shot file (§5) and
    had no process-lock coverage before this round."""
    import yaml

    from manju.mcp.tools import TOOL_DEFS
    from manju.runtime.buildlock import BuildLocked

    add_shot(tmp_project, "S001")
    handler = next(t["handler"] for t in TOOL_DEFS if t["name"] == "update_shot")

    path = tmp_project.shot_path("S001")
    before_text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(before_text)
    data["dialogue"]["text"] = "改写后的新台词。"
    new_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        with pytest.raises(BuildLocked):
            handler(tmp_project, {"shot_id": "S001", "yaml_content": new_yaml})
    finally:
        lock.release()
    assert path.read_text(encoding="utf-8") == before_text  # untouched

    # released -> runs normally
    result = handler(tmp_project, {"shot_id": "S001", "yaml_content": new_yaml})
    assert result.get("ok") is True
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["dialogue"]["text"] == "改写后的新台词。"


# =============================================================== board action


@pytest.fixture
def board_project(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "manual")  # take_01
    make_take(tmp_project, "S001", "manual")  # take_02
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01")
    )
    return tmp_project


def test_board_rollback_shot_busy_under_build_lock_no_mutation(board_project):
    """``rollback_shot`` writes ``status.selected_take`` straight to the shot
    file (core/history.py) with NO lock of its own by design (its docstring
    explains why it skips the VALUE-lock guard in ``select_take_checked`` —
    that is a different lock from the cross-process one guarded here). The
    board action wrapping it had zero process-lock coverage before this
    round; now it surfaces via the SAME 409 ``mutation_lock``-busy shape."""
    from manju.board.server import make_server

    server = make_server(board_project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path, body):
            req = urllib.request.Request(
                f"{base}{path}", data=json.dumps(body).encode("utf-8"), method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Manju-Token", server.token)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read() or b"{}")

        # two selects give rollback something to return to
        assert post("/api/select", {"shot": "S001", "take": "take_01"})[1]["ok"]
        assert post("/api/select", {"shot": "S001", "take": "take_02"})[1]["ok"]
        assert board_project.load_shot("S001").status.selected_take == "take_02"

        lock = BuildLock(board_project.root, actor="human").acquire()
        try:
            status, data = post("/api/rollback_shot", {"shot": "S001"})
        finally:
            lock.release()
        assert status == 409
        assert data["ok"] is False and "error" in data
        # rejected click did NOT mutate anything
        assert board_project.load_shot("S001").status.selected_take == "take_02"

        # released -> succeeds normally
        status, data = post("/api/rollback_shot", {"shot": "S001"})
        assert status == 200 and data["ok"] is True and data["take"] == "take_01"
        assert board_project.load_shot("S001").status.selected_take == "take_01"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ===================================================== no-double-acquire guard


def test_gui_select_already_locked_handler_still_works(gui, tmp_project, add_shot, make_take):
    """``/api/select`` wraps ``select_take_checked`` in ``build_lock`` itself
    (round W) — confirm this round's audit did not add a SECOND wrap around
    it (which would self-deadlock: BuildLock is non-reentrant, so a second
    ``acquire()`` in the same call always raises, even with zero external
    contention)."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    status, data = _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    assert status == 200 and data["ok"] is True
    assert tmp_project.load_shot("S001").status.selected_take == take.name


def test_gui_mixer_apply_already_locked_handler_still_works(gui, tmp_project):
    """``/api/mixer/apply`` calls ``build.mixer.apply_mixer``, which ALREADY
    takes ``build_lock`` internally (round W, #9/#64) — this round correctly
    left it unwrapped at the GUI layer (double-wrapping would self-deadlock
    exactly like the select case above). Confirm it still works standalone."""
    status, data = _post(gui, "/api/mixer/apply", {"changes": {"voice_gain_db": -3.0}})
    assert status == 200 and data["ok"] is True
    assert tmp_project.load_rules().audio.voice_gain_db == -3.0
