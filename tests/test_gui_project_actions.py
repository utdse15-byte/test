"""GUI follow-up: project next_action contract, launch, safe quit."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.core.container import Project
from manju.gui.jobs import RunnerState
from manju.gui.server import create_server
from manju.gui.state import project_identity


def _serve(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def _request(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _post(server, path, body, token=None):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _request(server, path, method="POST", body=body, headers=headers)


def _mk(tmp_path, name):
    return Project.create(tmp_path / name, name=name, git_init=False)


# ---------------------------------------------------- project open/create


def test_unbound_open_returns_project_bound(tmp_path):
    a = _mk(tmp_path, "bound_a")
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, data = _post(server, "/api/workspace/open", {"path": str(a.root)})
        assert status == 200
        assert data["ok"] is True
        assert data["code"] == "project_bound"
        assert data["next_action"]["kind"] == "reload_current"
        assert data["project"]["root"] == str(a.root)
        assert data["project"]["id"] == project_identity(a)
        assert server.session is not None
        assert server.session.project.root == a.root
    finally:
        server.shutdown()
        server.close()


def test_bound_open_other_returns_409_next_action(tmp_project, tmp_path):
    other = _mk(tmp_path, "other_b")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        before = server.session.project_id
        status, data = _post(server, "/api/workspace/open", {"path": str(other.root)})
        assert status == 409
        assert data["ok"] is False
        assert data["code"] == "project_session_immutable"
        assert data["next_action"]["kind"] == "open_in_new_window"
        assert data["next_action"]["display_command"]
        assert "--port 0" in data["next_action"]["display_command"]
        assert data["project"]["root"] == str(other.root)
        assert data.get("open_in_new_window") is True
        assert server.session.project_id == before
    finally:
        server.shutdown()
        server.close()


def test_bound_new_project_201_created_session_unchanged(tmp_path):
    a = _mk(tmp_path, "host_a")
    server = create_server(a, host="127.0.0.1", port=0)
    _serve(server)
    try:
        before = server.session.project_id
        status, data = _post(server, "/api/workspace/new", {
            "name": "新片", "path": str(tmp_path),
        })
        assert status == 201
        assert data["ok"] is True
        assert data["code"] == "project_created"
        assert data["next_action"]["kind"] == "open_in_new_window"
        assert data["project"]["name"] == "新片"
        assert server.session.project_id == before
        assert (tmp_path / "新片.manju").exists() or any(
            p.name.startswith("新片") for p in tmp_path.iterdir())
    finally:
        server.shutdown()
        server.close()


def test_unbound_new_project_created_and_bound(tmp_path):
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, data = _post(server, "/api/workspace/new", {
            "name": "首绑", "path": str(tmp_path),
        })
        assert status == 201
        assert data["code"] == "project_created_and_bound"
        assert data["next_action"]["kind"] == "reload_current"
        assert server.session is not None
    finally:
        server.shutdown()
        server.close()


def test_switch_never_mutates_session(tmp_path):
    from manju.gui.server import discover_workspace

    ws = tmp_path / "studio"
    ws.mkdir()
    a = Project.create(ws / "甲", git_init=False)
    Project.create(ws / "乙", git_init=False)
    server = create_server(a, host="127.0.0.1", port=0,
                           workspace=discover_workspace(ws))
    _serve(server)
    try:
        before = server.session.project_id
        status, data = _post(server, "/api/switch", {"slug": "乙"})
        assert status == 409
        assert data["code"] == "project_session_immutable"
        assert data["next_action"]["kind"] == "open_in_new_window"
        assert server.session.project_id == before
    finally:
        server.shutdown()
        server.close()


def test_response_keeps_root_code_next_action(tmp_project, tmp_path):
    other = _mk(tmp_path, "keep_fields")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, data = _post(server, "/api/workspace/open", {"path": str(other.root)})
        assert status == 409
        for key in ("code", "root", "next_action", "project", "cli"):
            assert key in data, key
        assert "kind" in data["next_action"]
        assert "display_command" in data["next_action"]
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- launch


def test_workspace_launch_validates_project(tmp_project, monkeypatch):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    launched = []

    class FakePopen:
        def __init__(self, argv, **kw):
            launched.append((argv, kw))
            self.pid = 4242

    monkeypatch.setattr("subprocess.Popen", FakePopen)
    try:
        status, data = _post(server, "/api/workspace/launch",
                             {"path": str(tmp_project.root)})
        assert status == 200
        assert data["ok"] is True
        assert data["code"] == "launched"
        assert launched
        argv, kw = launched[0]
        assert kw.get("shell") is False
        assert "--app" in argv
        assert "--port" in argv and "0" in argv
        assert str(tmp_project.root) in argv
    finally:
        server.shutdown()
        server.close()


def test_workspace_launch_rejects_unknown_path(tmp_project, tmp_path):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, data = _post(server, "/api/workspace/launch",
                             {"path": str(tmp_path / "nope")})
        assert status == 404
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- app quit / status


def test_app_status_shape(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, app_mode=True)
    _serve(server)
    try:
        status, data = _request(server, "/api/app/status")
        assert status == 200
        assert data["app_mode"] is True
        assert data["closing"] is False
        assert data["runner_state"] == "open"
        assert data["queued_count"] == 0
        assert data["running_job"] is None
    finally:
        server.shutdown()
        server.close()


def test_quit_after_current_cancels_queued(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        release = threading.Event()
        started = threading.Event()
        second_ran = []

        def first(job):
            started.set()
            release.wait(8.0)
            return {"ok": True}

        def second(job):
            second_ran.append(job.id)
            return {"ok": True}

        j1 = server.runner.submit("a", {}, first)
        j2 = server.runner.submit("b", {}, second)
        assert started.wait(5.0)
        status, data = _post(server, "/api/app/quit", {"mode": "after_current"})
        assert status == 200
        assert data["ok"] is True
        assert server.closing.is_set()
        # New writes refused
        st, body = _post(server, "/api/lock",
                         {"shot": "S001", "field": "dialogue.text"})
        assert st == 503
        assert body.get("code") == "server_closing"
        release.set()
        # Wait for finish thread
        deadline = time.monotonic() + 8.0
        while server.runner.state() is not RunnerState.CLOSED and time.monotonic() < deadline:
            time.sleep(0.05)
        assert second_ran == []
        assert server.runner.get(j2.id).state == "canceled"
        assert server.runner.get(j1.id).state in ("done", "canceled", "failed")
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass


def test_quit_cancel_running_sets_flag(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        started = threading.Event()
        saw_cancel = threading.Event()

        def fn(job):
            started.set()
            while not job.should_cancel():
                time.sleep(0.02)
            saw_cancel.set()
            return {"canceled": True, "errors": ["已取消"]}

        server.runner.submit("long", {}, fn)
        assert started.wait(5.0)
        status, data = _post(server, "/api/app/quit", {"mode": "cancel_running"})
        assert status == 200
        assert saw_cancel.wait(5.0)
        deadline = time.monotonic() + 5.0
        while server.runner.state() is not RunnerState.CLOSED and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.runner.state() is RunnerState.CLOSED
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass


def test_quit_is_idempotent(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        # Hold a running job so the quit coordinator does not close the
        # socket before the second request lands.
        release = threading.Event()

        def hold(job):
            release.wait(5.0)
            return {"ok": True}

        server.runner.submit("hold", {}, hold)
        time.sleep(0.05)
        s1, d1 = _post(server, "/api/app/quit", {"mode": "after_current"})
        s2, d2 = _post(server, "/api/app/quit", {"mode": "after_current"})
        assert s1 == 200 and s2 == 200
        assert d1["ok"] and d2["ok"]
        assert d2["code"] in ("quit_started", "quit_already_in_progress")
        release.set()
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass


# ---------------------------------------------------- frontend source pins


def test_webclient_and_project_action_served(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        for path, needle in (
            ("/webclient.js", "ManjuApiError"),
            ("/webclient.js", "requestJson"),
            ("/project-action.js", "handleProjectAction"),
            ("/project-action.js", "showProjectActionDialog"),
            ("/project-action.js", "showQuitDialog"),
            ("/project-action.css", "mj-pa-dlg"),
            ("/app.js", "handleSpaProjectAction"),
            ("/app.js", "项目已创建"),
            ("/glossary.js", "handleProjectAction"),
            ("/workspace.js", "next_action"),
        ):
            url = f"http://127.0.0.1:{server.port}{path}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = resp.read().decode("utf-8")
            assert needle in body, f"{path} missing {needle}"
        # SPA must not still claim "已切换" on switch path
        with urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/app.js", timeout=10) as resp:
            app_js = resp.read().decode("utf-8")
        assert "已创建并切换" not in app_js
        assert "已切换项目 (switched)" not in app_js
    finally:
        server.shutdown()
        server.close()


def test_spa_html_loads_webclient_before_app(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{server.port}/", timeout=10) as resp:
            html = resp.read().decode("utf-8")
        assert "/webclient.js" in html
        assert "/project-action.js" in html
        assert html.index("/webclient.js") < html.index("/app.js")
    finally:
        server.shutdown()
        server.close()
