"""GUI safety wave — seven owner-path failures on the single-user Windows box.

Every test here is red-first against the pre-fix tree; each names the finding
id it pins so a future session can trace the intent:

- GUI-JOB-008    retry replays a paid job's ``assume_yes`` (one click becomes a
                 reusable spending authorization)
- GUI-EDIT-P1-001 bible/rules/packaging whole-file saves have no compare-and-
                 swap, so a stale tab silently clobbers the other's work
- GUI-INDEX-P1-001 the reorder endpoint has no ``expected_rev`` guard
- GUI-SHUTDOWN-P1-001 the quit coordinator thread is non-daemon and
                 ``after_current`` has no deadline → Quit/Ctrl-C never returns
- GUI-SHUTDOWN-P1-002 the ``after_current`` → ``cancel_running`` upgrade never
                 establishes the 60s deadline (mode is read once)
- GUI-MULTI-P1-001 a second GUI instance writes a FALSE terminal
                 ``interrupted`` over instance 1's live paid build
- GUI-READONLY-P1-001 readonly mode cannot open a project or quit
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.core.container import Project
from manju.gui.jobs import JobRunner
from manju.gui.server import create_server


# ---------------------------------------------------------------- helpers


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
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {"raw": payload.decode("utf-8", "replace")}
        return exc.code, parsed


def _post(server, path, body, token=None):
    return _request(server, path, method="POST", body=body,
                    headers={"X-Manju-Token": token if token is not None
                             else server.token})


def _close(server):
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.close()
    except Exception:
        pass


def _wait_job(server, job_id, timeout=120.0):
    # 载荷敏感对策: the 10s deadline wrapped five REAL jobs (incl. a
    # full build) and fired under CPU contention — reproduced with
    # spinners, never without. Generous deadline, same assertions.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = server.runner.get(job_id)
        if job is not None and job.state in ("done", "failed", "canceled"):
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached a terminal state")


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    _serve(server)
    try:
        yield server
    finally:
        _close(server)


# ===================================================== GUI-JOB-008 (money)


def test_retry_never_replays_assume_yes(gui, tmp_project, add_shot):
    """A ``assume_yes`` click authorizes ONE run. The retry closure rebuilt by
    ``_build_retry_fn`` read it straight back out of the failed job's params,
    so the queue panel's 重试 button was an unlimited, re-clickable spend
    authorization for all five (all PAID) retryable kinds."""
    add_shot(tmp_project, "S001")

    status, data = _post(gui, "/api/voice", {"shot": "S001", "assume_yes": True})
    assert status == 202
    job1_id = data["job"]["id"]
    assert gui.runner.get(job1_id).params.get("assume_yes") is True
    _wait_job(gui, job1_id)  # no TTS provider configured -> failed

    status, data = _post(gui, "/api/jobs/retry", {"job_id": job1_id})
    assert status == 202
    job2 = gui.runner.get(data["job"]["id"])
    assert job2.params.get("assume_yes") is not True, (
        "retry replayed the spend confirmation — one click became a reusable "
        "authorization")
    assert data["job"]["params"].get("assume_yes") is not True


def test_retry_of_every_paid_kind_drops_the_confirmation(gui, tmp_project, add_shot):
    """All five retryable kinds are paid kinds (core.jobkinds.paid_kinds), so
    the drop must be unconditional, not voice-only."""
    from manju.core import jobkinds

    assert jobkinds.retryable_kinds() <= jobkinds.paid_kinds()
    add_shot(tmp_project, "S001")

    for kind, params in (
        ("build", {"target": "final", "gen": "missing", "assume_yes": True}),
        ("redo", {"shot": "S001", "assume_yes": True}),
        ("voice", {"shot": "S001", "assume_yes": True}),
        ("redo_batch", {"shots": ["S001"], "assume_yes": True}),
        ("voice_batch", {"shots": ["S001"], "assume_yes": True}),
    ):
        job = gui.runner.submit(kind, dict(params), lambda j: {"ok": True})
        _wait_job(gui, job.id)
        job.state = "failed"  # force the retryable precondition deterministically
        status, data = _post(gui, "/api/jobs/retry", {"job_id": job.id})
        assert status == 202, (kind, status, data)
        retried = gui.runner.get(data["job"]["id"])
        assert retried.params.get("assume_yes") is not True, kind


# ================================================ GUI-EDIT-P1-001 (dataloss)


@pytest.mark.parametrize(
    "url,path_parts",
    [
        ("/api/bible/characters", ("bible", "characters.yaml")),
        ("/api/rules", ("timeline", "rules.yaml")),
        ("/api/packaging", ("timeline", "packaging.yaml")),
    ],
)
def test_truth_editor_get_exposes_a_cas_token(gui, tmp_project, url, path_parts):
    status, data = _request(gui, url)
    assert status == 200
    assert isinstance(data.get("rev"), str), (
        f"{url} serves the whole file with no CAS token to echo back")
    from manju.core.hashing import hash_text

    path = tmp_project.root.joinpath(*path_parts)
    expected = hash_text(path.read_text(encoding="utf-8")) if path.exists() else ""
    assert data["rev"] == expected


def test_bible_save_refuses_a_stale_buffer(gui, tmp_project):
    """Two tabs on bible/characters.yaml: tab A saves, tab B (opened earlier)
    saves its own buffer. Without CAS, B silently clobbers A."""
    status, data = _request(gui, "/api/bible/characters")
    stale_rev = data.get("rev")
    assert stale_rev  # the token the second tab is holding

    a_text = data["yaml"] + "\nlinxia_b:\n  name: 乙\n  appearance: 白衬衫\n"
    status, _ = _post(gui, "/api/bible/characters",
                      {"yaml": a_text, "expected_rev": stale_rev})
    assert status == 200

    b_text = data["yaml"] + "\nlinxia_c:\n  name: 丙\n  appearance: 黑外套\n"
    status, payload = _post(gui, "/api/bible/characters",
                            {"yaml": b_text, "expected_rev": stale_rev})
    assert status == 409, "the stale tab overwrote the other tab's save"
    on_disk = (tmp_project.root / "bible" / "characters.yaml").read_text(encoding="utf-8")
    assert "linxia_b" in on_disk and "linxia_c" not in on_disk
    assert payload.get("current") == on_disk


def test_rules_and_packaging_saves_refuse_a_stale_buffer(gui, tmp_project):
    for url in ("/api/rules", "/api/packaging"):
        status, data = _request(gui, url)
        assert status == 200
        text = data["yaml"] or "{}\n"
        status, first = _post(gui, url, {"yaml": text})
        assert status == 200, (url, first)
        status, payload = _post(gui, url, {"yaml": text + "\n# 第二个标签页\n",
                                           "expected_rev": "sha256:stale"})
        assert status == 409, f"{url} accepted a stale expected_rev"


def test_truth_editor_save_without_expected_rev_still_works(gui):
    """Compat: the CAS token is OPTIONAL — an older page (or a curl) that
    sends no expected_rev keeps the historical behaviour."""
    status, _ = _post(gui, "/api/rules", {"yaml": "transitions:\n  default: cut\n"})
    assert status == 200


# ==================================================== GUI-INDEX-P1-001


def test_index_reorder_refuses_a_stale_expected_rev(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    add_shot(tmp_project, "S003")

    status, state = _request(gui, "/api/state")
    assert status == 200
    rev = state.get("index_rev")
    assert isinstance(rev, str) and rev, "/api/state carries no index CAS token"

    # tab A reorders
    status, data = _post(gui, "/api/index",
                         {"order": ["S002", "S001", "S003"], "expected_rev": rev})
    assert status == 200
    assert data.get("rev") and data["rev"] != rev

    # tab B, still holding the pre-reorder rev, moves a different pair
    status, payload = _post(gui, "/api/index",
                            {"order": ["S001", "S003", "S002"], "expected_rev": rev})
    assert status == 409, "the stale tab silently undid the other tab's reorder"
    assert tmp_project.load_index().order == ["S002", "S001", "S003"]

    # no expected_rev → unchanged behaviour (CLI/older pages)
    status, _ = _post(gui, "/api/index", {"order": ["S003", "S002", "S001"]})
    assert status == 200


# =============================================== GUI-SHUTDOWN-P1-001/002


class _FakeServer:
    def __init__(self, runner):
        self.runner = runner
        self.closing = threading.Event()
        self.closed = threading.Event()

    def app_status(self):
        return {}

    def shutdown(self):
        self.closed.set()

    def server_close(self):
        pass


def _wedged_runner(tmp_project):
    """A runner with a job that ignores cooperative cancel — a wedged
    provider, the case the owner actually hits."""
    runner = JobRunner(tmp_project.runtime_dir)
    started, release = threading.Event(), threading.Event()

    def stubborn(job):
        started.set()
        release.wait(30)
        return {"ok": True}

    runner.submit("build", {}, stubborn)
    assert started.wait(5)
    return runner, release


def test_quit_thread_is_a_daemon(tmp_project):
    """GUI-SHUTDOWN-P1-001: a non-daemon coordinator thread joined at
    interpreter exit — Ctrl-C on a wedged job never returned the prompt."""
    import manju.gui.shutdown as sd

    runner, release = _wedged_runner(tmp_project)
    srv = _FakeServer(runner)
    coord = sd.AppShutdownCoordinator(srv)
    try:
        coord.request("after_current")
        assert coord._thread is not None
        assert coord._thread.daemon is True, (
            "quit coordinator is non-daemon: the process cannot exit while a "
            "wedged job holds the worker")
    finally:
        release.set()
        runner.shutdown(timeout=5)


def test_after_current_has_a_deadline(tmp_project, monkeypatch):
    """GUI-SHUTDOWN-P1-001: after_current waited with timeout=None. A wedged
    provider means the coordinator never reports anything, forever."""
    import importlib

    monkeypatch.setenv("MANJU_QUIT_AFTER_CURRENT_TIMEOUT", "1.0")
    import manju.gui.shutdown as sd
    sd = importlib.reload(sd)
    try:
        runner, release = _wedged_runner(tmp_project)
        srv = _FakeServer(runner)
        coord = sd.AppShutdownCoordinator(srv)
        try:
            coord.request("after_current")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not coord.stuck:
                time.sleep(0.05)
            assert coord.stuck, "after_current never gave up on a wedged job"
            assert coord._payload(code="x", mode="after_current",
                                  report=None)["shutdown_state"] == "stuck"
        finally:
            release.set()
            runner.shutdown(timeout=5)
    finally:
        importlib.reload(sd)


def test_upgrade_to_cancel_running_establishes_the_deadline(tmp_project, monkeypatch):
    """GUI-SHUTDOWN-P1-002: ``mode`` was read ONCE per wait, so upgrading
    after_current → cancel_running left the coordinator in the unbounded
    branch: the 60s deadline never started and the state stayed ``closing``."""
    import importlib

    monkeypatch.setenv("MANJU_QUIT_CANCEL_TIMEOUT", "1.0")
    monkeypatch.setenv("MANJU_QUIT_AFTER_CURRENT_TIMEOUT", "3600")
    import manju.gui.shutdown as sd
    sd = importlib.reload(sd)
    try:
        runner, release = _wedged_runner(tmp_project)
        srv = _FakeServer(runner)
        coord = sd.AppShutdownCoordinator(srv)
        try:
            coord.request("after_current")
            time.sleep(0.4)
            payload = coord.request("cancel_running")
            assert payload["mode"] == "cancel_running"
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not coord.stuck:
                time.sleep(0.05)
            assert coord.stuck, (
                "upgraded quit never established the cancel deadline — "
                "shutdown_state stayed closing forever")
        finally:
            release.set()
            runner.shutdown(timeout=5)
    finally:
        importlib.reload(sd)


# ==================================================== GUI-MULTI-P1-001


def test_live_owner_jobs_are_never_declared_interrupted(tmp_project):
    """A second GUI instance on the same project scanned jobs.jsonl and wrote
    a terminal ``interrupted`` record over instance 1's LIVE paid build."""
    runner_a, release = _wedged_runner(tmp_project)
    log = tmp_project.runtime_dir / "jobs.jsonl"
    try:
        runner_b = JobRunner(tmp_project.runtime_dir)
        try:
            assert runner_b.interrupted() == [], (
                "instance 2 declared instance 1's live job interrupted")
            lines = [json.loads(x) for x in
                     log.read_text(encoding="utf-8").splitlines() if x.strip()]
            assert not [r for r in lines if r.get("state") == "interrupted"], (
                "a FALSE terminal record was written over a live paid build")
        finally:
            runner_b.shutdown(timeout=5)
    finally:
        release.set()
        runner_a.shutdown(timeout=5)


def test_a_dead_owners_job_is_still_interrupted(tmp_project):
    """The guard must not swallow the real crash case it exists for."""
    log = tmp_project.runtime_dir / "jobs.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"ts": "2020-01-01T00:00:00+00:00", "id": "ghost", "kind": "build",
                    "state": "running", "params_summary": {}, "error": None,
                    "project_id": "p", "session": "deadsession",
                    "pid": 2 ** 22 + 7}) + "\n",
        encoding="utf-8")
    runner = JobRunner(tmp_project.runtime_dir, project_id="p")
    try:
        assert [r["id"] for r in runner.interrupted()] == ["ghost"]
    finally:
        runner.shutdown(timeout=5)


def test_records_carry_the_owning_session_and_pid(tmp_project):
    import os

    runner = JobRunner(tmp_project.runtime_dir)
    try:
        job = runner.submit("build", {}, lambda j: {"ok": True})
        _deadline = time.monotonic() + 5
        while time.monotonic() < _deadline and runner.get(job.id).state != "done":
            time.sleep(0.01)
        lines = [json.loads(x) for x in
                 (tmp_project.runtime_dir / "jobs.jsonl")
                 .read_text(encoding="utf-8").splitlines() if x.strip()]
        mine = [r for r in lines if r.get("id") == job.id]
        assert mine and all(r.get("pid") == os.getpid() for r in mine)
        assert mine and all(isinstance(r.get("session"), str) and r["session"]
                            for r in mine)
    finally:
        runner.shutdown(timeout=5)


def test_open_refuses_a_project_a_live_instance_already_owns(tmp_path):
    """The workspace open/launch path must not spawn a second writer over a
    project a LIVE other process is already driving."""
    from manju.gui import jobs as jobs_mod

    project = Project.create(tmp_path / "held", name="held", git_init=False)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        project.runtime_dir.mkdir(parents=True, exist_ok=True)
        (project.runtime_dir / jobs_mod._OWNER_FILE).write_text(
            json.dumps({"session": "otherinstance", "pid": child.pid,
                        "started": "2026-07-24T00:00:00+00:00"}),
            encoding="utf-8")
        assert jobs_mod.live_foreign_owner(project.runtime_dir) is not None

        server = create_server(None, host="127.0.0.1", port=0)
        _serve(server)
        try:
            status, data = _post(server, "/api/workspace/open",
                                 {"path": str(project.root)})
            assert status == 409, "a second instance bound a live-owned project"
            assert data.get("code") == "project_already_open"
            assert server.session is None
        finally:
            _close(server)
    finally:
        child.kill()
        child.wait(timeout=10)

    # owner gone → the project opens normally again
    assert jobs_mod.live_foreign_owner(project.runtime_dir) is None


# ==================================================== GUI-READONLY-P1-001


def test_readonly_can_open_a_project(tmp_path):
    project = Project.create(tmp_path / "ro", name="ro", git_init=False)
    server = create_server(None, host="127.0.0.1", port=0, readonly=True)
    _serve(server)
    try:
        status, data = _post(server, "/api/workspace/open", {"path": str(project.root)})
        assert status == 200, "readonly workbench could not open anything at all"
        assert server.session is not None
    finally:
        _close(server)


def test_readonly_can_quit(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    _serve(server)
    try:
        status, data = _post(server, "/api/app/quit", {"mode": "after_current"})
        assert status == 200, "readonly window's Quit button always failed"
        assert data.get("ok") is True
    finally:
        _close(server)


def test_readonly_still_refuses_project_writes(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    _serve(server)
    try:
        status, _ = _post(server, "/api/rules", {"yaml": "transitions:\n  default: cut\n"})
        assert status == 403
        status, _ = _post(server, "/api/index", {"order": []})
        assert status == 403
        status, _ = _post(server, "/api/workspace/new", {"name": "nope"})
        assert status == 403
    finally:
        _close(server)
