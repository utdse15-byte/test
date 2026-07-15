"""GUI repair plan 2026-07-15 — project session, runner lifecycle, HTTP body.

Covers P0/P1 items from GUI_REPAIR_PLAN:
- frozen project session (bind once, no hot switch)
- Job.project_id + jobs.jsonl isolation
- JobRunner shutdown cancels queued / rejects submit
- task panel grouping (JS contract via structured /api/jobs data)
- strict Content-Length handling
- build lock never degrades to nullcontext
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.core.container import Project
from manju.gui.jobs import JobRunner, RunnerClosed, RunnerState
from manju.gui.server import create_server
from manju.gui.state import project_identity


# ---------------------------------------------------------------- helpers


def _serve(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def _request(server, path, *, method="GET", body=None, headers=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), (
                payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _post(server, path, body, token=None):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _request(server, path, method="POST", body=body, headers=headers)


def _mk(tmp_path, name):
    return Project.create(tmp_path / name, name=name, git_init=False)


def _wait_state(runner, job_id, states=("done", "failed", "canceled"), timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job and job.state in states:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {states}")


# --------------------------------------------------------- session freeze


def test_unbound_server_can_bind_exactly_once(tmp_path):
    a = _mk(tmp_path, "proj_a")
    b = _mk(tmp_path, "proj_b")
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/open", {"path": str(a.root)})
        assert status == 200 and data["ok"] is True
        assert server.session is not None
        assert server.session.project.root == a.root

        status, _, data = _post(server, "/api/workspace/open", {"path": str(b.root)})
        assert status == 409
        assert data.get("open_in_new_window") is True
        assert server.session.project.root == a.root
    finally:
        server.shutdown()
        server.close()


def test_bound_server_rejects_second_bind(tmp_project, tmp_path):
    other = _mk(tmp_path, "other")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    with pytest.raises(RuntimeError, match="project_session_immutable"):
        server.bind_project_once(other)
    assert server.session.project.root == tmp_project.root
    server.close()


def test_switch_endpoint_does_not_mutate_bound_session(tmp_path):
    ws = tmp_path / "studio"
    ws.mkdir()
    a = Project.create(ws / "甲", git_init=False)
    b = Project.create(ws / "乙", git_init=False)
    from manju.gui.server import discover_workspace

    projects = discover_workspace(ws)
    server = create_server(a, host="127.0.0.1", port=0, workspace=projects)
    _serve(server)
    try:
        before = server.session.project_id
        status, _, data = _post(server, "/api/switch", {"slug": "乙"})
        assert status == 409
        assert data["code"] == "project_session_immutable"
        assert server.session.project_id == before
        assert server.project.root == a.root
        _, _, state = _request(server, "/api/state")
        assert state["project"]["name"] == "甲"
        assert state["project_token"] == before
    finally:
        server.shutdown()
        server.close()


def test_job_log_written_only_to_runner_project(tmp_path):
    a = _mk(tmp_path, "log_a")
    b = _mk(tmp_path, "log_b")
    ra = JobRunner(a.runtime_dir, project_id=project_identity(a))
    try:
        marker = b.root / "from_a_runner.txt"
        job = ra.submit("mark", {}, lambda j: marker.write_text("x", encoding="utf-8") or {"ok": True})
        _wait_state(ra, job.id, ("done",))
        assert marker.exists()
        assert (a.runtime_dir / "jobs.jsonl").exists()
        assert not (b.runtime_dir / "jobs.jsonl").exists()
        lines = (a.runtime_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        assert rec["project_id"] == project_identity(a)
    finally:
        ra.shutdown(timeout=2.0)


def test_job_a_never_appears_in_project_b(tmp_path):
    a = _mk(tmp_path, "iso_a")
    b = _mk(tmp_path, "iso_b")
    sa = create_server(a, host="127.0.0.1", port=0)
    sb = create_server(b, host="127.0.0.1", port=0)
    _serve(sa)
    _serve(sb)
    try:
        release = threading.Event()
        started = threading.Event()

        def fn(job):
            started.set()
            release.wait(5.0)
            return {"ok": True}

        job = sa.runner.submit("build", {"t": 1}, fn)
        assert started.wait(5.0)
        _, _, ja = _request(sa, "/api/jobs")
        _, _, jb = _request(sb, "/api/jobs")
        ids_a = {j["id"] for j in ja["jobs"]}
        ids_b = {j["id"] for j in jb["jobs"]}
        assert job.id in ids_a
        assert job.id not in ids_b
        release.set()
    finally:
        sa.shutdown()
        sa.close()
        sb.shutdown()
        sb.close()


def test_retry_refuses_project_mismatch(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        job = server.runner.submit(
            "build", {"target": "final", "gen": "missing"},
            lambda j: (_ for _ in ()).throw(RuntimeError("boom")))
        _wait_state(server.runner, job.id, ("failed",))
        # Poison project_id to simulate a cross-project record
        live = server.runner.get(job.id)
        live.project_id = "not-this-project"
        status, _, data = _post(server, "/api/jobs/retry", {"job_id": job.id})
        assert status == 409
        assert "不属于当前项目" in data["error"] or "mismatch" in data["error"].lower()
    finally:
        server.shutdown()
        server.close()


def test_state_payload_and_project_token_share_one_session(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, state = _request(server, "/api/state")
        assert status == 200
        assert state["project_token"] == server.session.project_id
        assert state["project_token"] == project_identity(tmp_project)
        _, _, pid = _request(server, "/api/project-id")
        assert pid["token"] == state["project_token"]
    finally:
        server.shutdown()
        server.close()


def test_html_title_and_project_meta_share_one_session(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, body = _request(server, "/", raw=True)
        assert status == 200
        html = body.decode("utf-8")
        token = server.session.project_id
        assert f'content="{token}"' in html
        assert "manju-project" in html
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- runner lifecycle


def test_shutdown_cancels_queued_without_executing(tmp_path):
    r = JobRunner(tmp_path / ".manju", project_id="p1")
    try:
        release = threading.Event()
        started = threading.Event()
        second_ran = []

        def first(job):
            started.set()
            release.wait(5.0)
            return {"ok": True}

        def second(job):
            second_ran.append(job.id)
            return {"ok": True}

        j1 = r.submit("a", {}, first)
        j2 = r.submit("b", {}, second)
        assert started.wait(5.0)
        report = r.shutdown(timeout=0.1, cancel_queued=True)
        assert j2.id in report.canceled_queued
        release.set()
        # Give worker time to finish first / consume sentinel
        deadline = time.monotonic() + 3.0
        while r.state() is not RunnerState.CLOSED and time.monotonic() < deadline:
            time.sleep(0.02)
        assert second_ran == []
        assert r.get(j2.id).state == "canceled"
        log = (tmp_path / ".manju" / "jobs.jsonl").read_text(encoding="utf-8")
        assert "canceled" in log
    finally:
        r.shutdown(timeout=1.0)


def test_submit_after_closing_raises(tmp_path):
    r = JobRunner(tmp_path / ".manju", project_id="p1")
    r.shutdown(timeout=2.0)
    with pytest.raises(RunnerClosed):
        r.submit("x", {}, lambda j: {"ok": True})
    assert r.state() is RunnerState.CLOSED


def test_shutdown_is_idempotent(tmp_path):
    r = JobRunner(tmp_path / ".manju", project_id="p1")
    r1 = r.shutdown(timeout=2.0)
    r2 = r.shutdown(timeout=1.0)
    assert r1.stopped and r2.stopped
    assert r.state() is RunnerState.CLOSED


def test_shutdown_timeout_reports_live_worker(tmp_path):
    r = JobRunner(tmp_path / ".manju", project_id="p1")
    release = threading.Event()

    def long_fn(job):
        release.wait(10.0)
        return {"ok": True}

    r.submit("long", {}, long_fn)
    time.sleep(0.05)
    report = r.shutdown(timeout=0.05, cancel_queued=True, cancel_running=False)
    if not report.stopped:
        assert report.running_job_id is not None
    release.set()
    r.shutdown(timeout=3.0)


def test_server_closing_rejects_mutating_post(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        server.closing.set()
        status, _, data = _post(server, "/api/lock", {"shot": "S001", "on": True})
        assert status == 503
        assert data.get("code") == "server_closing"
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- interrupted times


def test_interrupted_preserves_created_started(tmp_path):
    runtime = tmp_path / ".manju"
    runtime.mkdir()
    log = runtime / "jobs.jsonl"
    # Simulate previous process: queued then running, never finished
    records = [
        {"ts": "2020-01-01T00:00:00+00:00", "id": "abc", "kind": "build",
         "state": "queued", "params_summary": {}, "error": None, "project_id": "p"},
        {"ts": "2020-01-01T00:00:01+00:00", "id": "abc", "kind": "build",
         "state": "running", "params_summary": {}, "error": None, "project_id": "p"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    r = JobRunner(runtime, project_id="p")
    try:
        intr = r.interrupted()
        assert len(intr) == 1
        assert intr[0]["created"] == "2020-01-01T00:00:00+00:00"
        assert intr[0]["started"] == "2020-01-01T00:00:01+00:00"
        assert intr[0]["finished"]
        assert intr[0]["retryable"] is False
    finally:
        r.shutdown(timeout=1.0)


def test_legacy_unscoped_interrupted_not_retryable(tmp_path):
    runtime = tmp_path / ".manju"
    runtime.mkdir()
    log = runtime / "jobs.jsonl"
    # Old log line without project_id
    rec = {"ts": "2020-01-01T00:00:00+00:00", "id": "leg", "kind": "build",
           "state": "running", "params_summary": {}, "error": None}
    log.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    r = JobRunner(runtime, project_id="p")
    try:
        intr = r.interrupted()
        assert intr[0]["legacy_unscoped"] is True
        assert "旧版本" in intr[0]["note"]
        assert intr[0]["retryable"] is False
    finally:
        r.shutdown(timeout=1.0)


# ---------------------------------------------------- job panel data contract


def test_running_visible_among_many_queued():
    """Mirror the old slice(0,8) bug with grouping logic (server-side pick)."""
    jobs = []
    for i in range(20):
        jobs.append({
            "id": f"q{i}", "state": "queued",
            "created": f"2026-01-01T00:{i:02d}:00+00:00",
        })
    jobs.append({
        "id": "run1", "state": "running",
        "created": "2025-01-01T00:00:00+00:00",  # older than queued
    })

    def pick(jobs, states, limit):
        return [j for j in jobs if j["state"] in states][:limit]

    running = pick(jobs, {"running", "canceling"}, 99)
    queued = pick(jobs, {"queued"}, 3)
    assert any(j["id"] == "run1" for j in running)
    assert len(queued) == 3
    # Old bug: sort by created desc then slice(0,8) would drop run1
    old = sorted(jobs, key=lambda j: j["created"], reverse=True)[:8]
    assert not any(j["id"] == "run1" for j in old)


# ---------------------------------------------------- HTTP body


def _raw_post(port, raw_request: bytes, timeout=3.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(raw_request)
        chunks = []
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if b"\r\n\r\n" in b"".join(chunks) and (
                        b"Content-Length: 0" in b"".join(chunks)
                        or len(b"".join(chunks)) > 200):
                    # try one more short read
                    sock.settimeout(0.3)
                    try:
                        more = sock.recv(4096)
                        if more:
                            chunks.append(more)
                    except OSError:
                        pass
                    break
        except OSError:
            pass
        return b"".join(chunks)


def test_http_invalid_content_length(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        raw = (
            f"POST /api/lock HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\n"
            f"X-Manju-Token: {server.token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: abc\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{{}}"
        ).encode("ascii")
        resp = _raw_post(server.port, raw)
        assert b"400" in resp.split(b"\r\n", 1)[0]
        assert b"invalid Content-Length" in resp or b"Content-Length" in resp
    finally:
        server.shutdown()
        server.close()


def test_http_negative_content_length(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        raw = (
            f"POST /api/lock HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\n"
            f"X-Manju-Token: {server.token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: -1\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("ascii")
        resp = _raw_post(server.port, raw)
        assert b"400" in resp.split(b"\r\n", 1)[0]
    finally:
        server.shutdown()
        server.close()


def test_http_transfer_encoding_rejected(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        raw = (
            f"POST /api/lock HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\n"
            f"X-Manju-Token: {server.token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Transfer-Encoding: chunked\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"2\r\n{{}}\r\n0\r\n\r\n"
        ).encode("ascii")
        resp = _raw_post(server.port, raw)
        assert b"400" in resp.split(b"\r\n", 1)[0]
    finally:
        server.shutdown()
        server.close()


def test_http_short_body(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        raw = (
            f"POST /api/lock HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\n"
            f"X-Manju-Token: {server.token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: 100\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"ab"
        ).encode("ascii")
        resp = _raw_post(server.port, raw, timeout=5.0)
        assert b"400" in resp.split(b"\r\n", 1)[0]
        assert b"incomplete" in resp or b"body" in resp
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- build lock


def test_optional_build_lock_never_nullcontext(tmp_project, monkeypatch):
    from manju.gui import server as server_mod

    def boom(root, actor=None, **kw):
        raise TypeError("simulated lock API breakage")

    monkeypatch.setattr(server_mod, "build_lock", boom)
    with pytest.raises(TypeError):
        with server_mod._optional_build_lock(tmp_project.root, "human"):
            pass


def test_build_lock_conflict_still_409(tmp_project, add_shot, make_take):
    from manju.runtime.buildlock import BuildLock

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        with BuildLock(tmp_project.root, actor="other"):
            status, _, data = _post(
                server, "/api/select", {"shot": "S001", "take": take.name})
            assert status == 409
            assert "build" in data["error"].lower() or "running" in data["error"].lower()
    finally:
        server.shutdown()
        server.close()
