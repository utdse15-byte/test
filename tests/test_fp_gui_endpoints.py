"""FP_M2 — G6: jobs.jsonl fsync dropped, and the 3 HTTP-layer-untested endpoints.

The GUI research (FP_OPT_GUI_RESULTS.md #6/#7) named two gaps:
  * ``jobs.jsonl`` is fsync'd on the POST/submit thread for a self-declared
    disposable log — dropped to honest best-effort (flush, no fsync).
  * ``POST /api/impact``, ``GET /api/director/suggest`` and ``GET /api/git/diff``
    had their engines tested but never their HTTP route (token gate + JSON
    shape). Smoke-pinned here. (The research labelled director/suggest a POST;
    the route is actually a GET — pinned as it really is.)
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.gui.server import create_server


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


def _req(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, parsed


def _post(server, path, body, token=None):
    return _req(server, path, method="POST", body=body,
                headers={"X-Manju-Token": token if token is not None else server.token})


# --------------------------------------------------------------- G6: fsync


def test_jobs_submit_does_not_fsync_on_the_post_thread(tmp_path, monkeypatch):
    """The submit (POST/click) thread persists the queued line WITHOUT fsync —
    the disk-sync is off the click→202 path — but the write itself is kept."""
    import os as _os

    from manju.gui.jobs import JobRunner

    runtime = tmp_path / ".manju"
    r = JobRunner(runtime)                      # construction (pre-patch) may rewrite
    fsyncs: list = []
    real = _os.fsync

    def spy(fd):
        fsyncs.append(fd)
        return real(fd)

    monkeypatch.setattr(_os, "fsync", spy)
    try:
        started = threading.Event()

        def fn(job):
            started.set()
            return {"ok": True}

        job = r.submit("qc", {"mode": "consistency"}, fn)
        assert fsyncs == [], "submit fsync'd jobs.jsonl on the POST thread"
        # the write is kept (best-effort, flushed): the queued line is on disk
        log = runtime / "jobs.jsonl"
        recs = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert any(rec["id"] == job.id and rec["state"] == "queued" for rec in recs)
        assert started.wait(5.0)
    finally:
        r.shutdown(timeout=3.0)
    # across the WHOLE lifecycle (worker persists running/done too) — no fsync
    assert fsyncs == [], "jobs.jsonl was fsync'd (it is disposable — flush only)"


# ----------------------------------------------------- G6: the 3 HTTP endpoints


def test_git_diff_endpoint_http(gui):
    status, data = _req(gui, "/api/git/diff")
    assert status == 200
    assert "diff" in data


def test_git_diff_endpoint_http_with_path(gui):
    status, data = _req(gui, "/api/git/diff?path=project.yaml")
    assert status == 200
    assert "diff" in data


def test_director_suggest_endpoint_http(gui):
    status, data = _req(gui, "/api/director/suggest")
    assert status == 200
    assert isinstance(data.get("suggestions"), list)


def test_impact_endpoint_http_token_gated(gui):
    # a POST with NO token is refused (mirrors every mutating POST's gate)
    status, data = _req(gui, "/api/impact", method="POST", body={"shot": "S001"})
    assert status == 403


def test_impact_endpoint_http_missing_shot_400(gui):
    status, data = _post(gui, "/api/impact", {})
    assert status == 400
    assert "error" in data


def test_impact_endpoint_http_returns_report(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, data = _post(gui, "/api/impact", {"shot": "S001"})
    assert status == 200
    assert "summary_zh" in data           # the HTTP layer enriches the engine report
