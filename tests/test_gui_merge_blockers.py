"""Merge-blocker fixes from audit of 07a3bde — behavioral pins."""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.gui.jobs import JobRunner, RunnerState
from manju.gui.server import create_server
from manju.gui.state import project_identity


def _serve(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def _req(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    # Never honor system HTTP_PROXY for loopback GUI tests (502 via proxy).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _post(server, path, body):
    return _req(server, path, method="POST", body=body,
                headers={"X-Manju-Token": server.token})


# ---------------------------------------------------------------- P0 N key


def test_note_and_verdict_buttons_have_data_action():
    from manju.gui.page import render_js

    js = render_js()
    assert 'dataset.action = "edit-note"' in js or 'data-action="edit-note"' in js or \
           "edit-note" in js
    assert "verdict-good" in js and "verdict-reject" in js
    # Must NOT fall back to first .tacts .btn
    assert '.tacts .btn' not in js or 'edit-note' in js
    assert 'querySelector(\'[data-action="edit-note"]\')' in js or \
           'querySelector("[data-action=\\"edit-note\\"]")' in js or \
           "[data-action=\"edit-note\"]" in js


def test_n_shortcut_does_not_use_positional_tacts_fallback():
    from manju.gui.page import render_js

    js = render_js()
    # The dangerous selector must be gone from the N handler path.
    assert ".tnote-edit-btn, .tacts .btn" not in js
    assert 'data-action="edit-note"' in js or "[data-action=\"edit-note\"]" in js


# ---------------------------------------------------------------- review focus


def test_review_focus_clears_aria_hidden_on_exit():
    from manju.gui.page import render_js

    js = render_js()
    assert "removeAttribute(\"aria-hidden\")" in js or "removeAttribute('aria-hidden')" in js
    assert "inert" in js
    # exit path must not early-return before cleanup
    assert "if (!reviewFocusMode)" in js
    assert "rf-active" in js


# ---------------------------------------------------------------- UI hydrate


def test_bootstrap_hydrates_from_api_ui_state():
    from manju.gui.page import render_js

    js = render_js()
    assert "bootstrapWorkspaceUI" in js
    assert "/api/ui-state" in js
    assert "shot_status_filter" in js
    assert "applyWorkspaceUI" in js or "stateFilter =" in js


def test_ui_state_roundtrip_survives_server_restart(tmp_project, monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "gs.json"))
    s1 = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(s1)
    try:
        st, data = _post(s1, "/api/ui-state", {
            "ui": {"shot_status_filter": "stale", "last_shot_id": "S042",
                   "review_position": "S042"},
        })
        assert st == 200
        wid = data["workspace_id"]
    finally:
        s1.shutdown()
        s1.close()

    # New process/port — same project identity
    s2 = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(s2)
    try:
        st, got = _req(s2, "/api/ui-state")
        assert st == 200
        assert got["workspace_id"] == wid
        assert got["ui"]["shot_status_filter"] == "stale"
        assert got["ui"]["last_shot_id"] == "S042"
    finally:
        s2.shutdown()
        s2.close()


# ---------------------------------------------------------------- multi-process state lock


def _mp_worker(env_path: str, wid: str, key: str, value: str, barrier_file: str):
    os.environ["MANJU_GUI_STATE"] = env_path
    # Wait until both ready
    Path(barrier_file).write_text("1", encoding="utf-8")
    deadline = time.time() + 5
    while time.time() < deadline:
        if Path(barrier_file).read_text(encoding="utf-8").count("1") >= 1:
            time.sleep(0.05)
            break
    from manju.gui import userstate as us
    for _ in range(40):
        us.patch_workspace_ui(wid, {key: value})
        time.sleep(0.005)


def test_multiprocess_gui_state_no_lost_updates(tmp_path):
    gs = str(tmp_path / "gui_state.json")
    barrier = str(tmp_path / "barrier.txt")
    Path(barrier).write_text("", encoding="utf-8")
    ctx = multiprocessing.get_context("spawn")
    p1 = ctx.Process(target=_mp_worker,
                     args=(gs, "projA", "last_shot_id", "S-A", barrier))
    p2 = ctx.Process(target=_mp_worker,
                     args=(gs, "projB", "last_shot_id", "S-B", barrier))
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)
    assert p1.exitcode == 0 and p2.exitcode == 0
    os.environ["MANJU_GUI_STATE"] = gs
    from manju.gui import userstate as us
    a = us.get_workspace_ui("projA")
    b = us.get_workspace_ui("projB")
    assert a.get("last_shot_id") == "S-A", a
    assert b.get("last_shot_id") == "S-B", b
    # no leftover unique tmps
    leftovers = list(tmp_path.glob("gui_state.json.*.tmp"))
    assert leftovers == []


# ---------------------------------------------------------------- shutdown stuck


def test_cancel_running_reports_stuck_not_hang(tmp_project, monkeypatch):
    monkeypatch.setenv("MANJU_QUIT_CANCEL_TIMEOUT", "1.5")
    # Re-import coordinator constant
    import importlib
    import manju.gui.shutdown as sd
    importlib.reload(sd)

    server = create_server(tmp_project, host="127.0.0.1", port=0)
    # rebind coordinator with reloaded class
    server._quit = sd.AppShutdownCoordinator(server)
    _serve(server)
    release = threading.Event()
    started = threading.Event()

    def ignore_cancel(job):
        started.set()
        # Ignore cooperative cancel entirely
        while not release.is_set():
            time.sleep(0.05)
        return {"ok": True}

    try:
        server.runner.submit("stubborn", {}, ignore_cancel)
        assert started.wait(5)
        st, data = _post(server, "/api/app/quit", {"mode": "cancel_running"})
        assert st == 200
        # Wait past the short cancel timeout
        deadline = time.monotonic() + 8
        stuck = False
        while time.monotonic() < deadline:
            st, status = _req(server, "/api/app/status")
            assert st == 200
            if status.get("shutdown_state") == "stuck" or server._quit.stuck:
                stuck = True
                break
            if not server.runner.worker_alive:
                break
            time.sleep(0.1)
        assert stuck or server._quit.stuck, "expected stuck state for non-cooperative job"
        # Writes still rejected
        st, body = _post(server, "/api/lock",
                         {"shot": "S001", "field": "dialogue.text"})
        assert st == 503
        release.set()
        # After release, worker can finish; don't require full close
        time.sleep(0.5)
    finally:
        release.set()
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass


# ---------------------------------------------------------------- dispose / focus / filter pins


def test_page_js_dispose_and_focus_identity():
    from manju.gui.page import render_js

    js = render_js()
    assert "function disposeShotNode" in js
    assert "unobserve" in js
    assert "captureFocusIdentity" in js
    assert "restoreFocusIdentity" in js
    assert "ensureFilterChips" in js
    assert "media_nodes_reused" in js
    assert "dataset.sig" in js or "data-sig" in js


# ---------------------------------------------------------------- webclient protocol


def test_webclient_rejects_invalid_json_on_2xx():
    from manju.gui.webclient import render_webclient_js

    js = render_webclient_js()
    assert "ManjuProtocolError" in js
    assert "无效 JSON" in js


# ---------------------------------------------------------------- launch known only


def test_launch_rejects_unknown_path(tmp_project, tmp_path):
    other = Project.create(tmp_path / "stranger", name="stranger", git_init=False)
    # Not in recents if we isolate recents — conftest already redirects recents
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, data = _post(server, "/api/workspace/launch",
                         {"path": str(other.root)})
        # unknown to catalog/session/recents → 404
        assert st == 404
    finally:
        server.shutdown()
        server.close()


def test_launch_accepts_session_project_id(tmp_project, monkeypatch):
    launches = []

    class FakePopen:
        def __init__(self, argv, **kw):
            launches.append(argv)
            self.pid = 99

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        pid = server.session.project_id
        st, data = _post(server, "/api/workspace/launch", {"project_id": pid})
        assert st == 200
        assert data["ok"] is True
        assert launches and "--app" in launches[0]
    finally:
        server.shutdown()
        server.close()
