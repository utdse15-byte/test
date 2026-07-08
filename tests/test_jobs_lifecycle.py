"""Round AA4 — long-task status honesty for the GUI job queue.

Covers the three pieces round-AA item 6 added on top of the existing
cooperative-cancel core (tests/test_job_cancel.py):

- gui/jobs.py: jobs.jsonl persistence (one line per submit/start/cancel/
  finish) and interrupted-job detection on JobRunner construction — a GUI
  crash/restart must not silently forget a job that was still running.
- gui/server.py: heavyweight synchronous POST handlers moved onto the jobs
  runner this round (series new-episode / sync-bible apply, ingest plan) now
  answer 202 + a job dict, exactly like every pre-existing job-backed handler.
- build/graph.py (redo_batch/voice_batch) and build/ingest.py (plan_ingest/
  apply_ingest) and core/series.py (sync_bible): the new between-item
  ``should_cancel`` checkpoints these batch/loop kinds gained this round.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.build.graph import redo_batch
from manju.gui.jobs import JobRunner
from manju.gui.server import create_server

# ============================================================ A. persistence


@pytest.fixture
def runner(tmp_path):
    r = JobRunner()
    yield r
    r.shutdown(timeout=2.0)


def _wait_state(runner, job_id, states=("done", "failed", "canceled"), timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job.state in states:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {states} within {timeout}s")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_bare_jobrunner_has_persistence_disabled(tmp_path, monkeypatch):
    """``JobRunner()`` with no runtime_dir (every existing test, and a GUI
    server started unbound with no project yet) must stay exactly as before
    this round: no file is ever written, interrupted() is always empty."""
    monkeypatch.chdir(tmp_path)
    r = JobRunner()
    try:
        assert r.interrupted() == []
        job = r.submit("build", {}, lambda job: {"ok": True})
        _wait_state(r, job.id, states=("done",))
        # nothing written anywhere under tmp_path
        assert not any(tmp_path.rglob("jobs.jsonl"))
    finally:
        r.shutdown(timeout=2.0)


def test_persist_writes_submit_start_finish_lines(tmp_path):
    runtime_dir = tmp_path / ".manju"
    r = JobRunner(runtime_dir)
    try:
        release = threading.Event()
        started = threading.Event()

        def fn(job):
            started.set()
            release.wait(5.0)
            return {"ok": True, "shot": "S001"}

        job = r.submit("build", {"target": "final", "shots": ["S001", "S002"]}, fn)
        assert started.wait(5.0)

        log_path = runtime_dir / "jobs.jsonl"
        # by the time the work function is running, submit+start are on disk
        mid_records = [rec for rec in _read_jsonl(log_path) if rec["id"] == job.id]
        assert [rec["state"] for rec in mid_records] == ["queued", "running"]

        release.set()
        _wait_state(r, job.id, states=("done",))
    finally:
        r.shutdown(timeout=2.0)

    records = [rec for rec in _read_jsonl(runtime_dir / "jobs.jsonl") if rec["id"] == job.id]
    assert [rec["state"] for rec in records] == ["queued", "running", "done"]
    assert all(rec["kind"] == "build" for rec in records)
    # params_summary is a compact stand-in for params, never the bare dict —
    # a long list gets truncated (see _summarize_params)
    assert records[0]["params_summary"]["target"] == "final"
    assert records[0]["params_summary"]["shots"] == ["S001", "S002"]
    assert records[-1]["error"] is None
    assert all("ts" in rec for rec in records)


def test_persist_records_cancel_and_honest_error(tmp_path):
    runtime_dir = tmp_path / ".manju"
    r = JobRunner(runtime_dir)
    try:
        started = threading.Event()
        release = threading.Event()

        def fn(job):
            started.set()
            while not job.should_cancel():
                if release.is_set():
                    return {"ok": True}
                time.sleep(0.01)
            return {"canceled": True, "errors": ["已取消:测试"]}

        job = r.submit("redo_batch", {"shots": ["S001"]}, fn)
        assert started.wait(5.0)
        r.cancel(job.id)
        _wait_state(r, job.id, states=("canceled",))
    finally:
        r.shutdown(timeout=2.0)

    records = [rec for rec in _read_jsonl(runtime_dir / "jobs.jsonl") if rec["id"] == job.id]
    states = [rec["state"] for rec in records]
    # queued -> running -> canceling (cancel() while running) -> canceled (finish)
    assert states == ["queued", "running", "canceling", "canceled"]
    assert "已取消" in records[-1]["error"]


def test_persist_log_capped_at_startup(tmp_path):
    """jobs.jsonl is disposable operational history (module docstring) — a
    fresh JobRunner construction caps it to the newest N lines rather than
    letting it grow forever, mirroring the simplest existing precedent
    (unlike events.jsonl/failures.jsonl, this log is single-process/
    single-writer, so a plain startup rewrite is enough)."""
    from manju.gui import jobs as jobs_mod

    runtime_dir = tmp_path / ".manju"
    runtime_dir.mkdir(parents=True)
    log_path = runtime_dir / "jobs.jsonl"
    over_cap = jobs_mod._JOBS_LOG_CAP + 50
    lines = []
    for i in range(over_cap):
        lines.append(json.dumps({
            "ts": "2020-01-01T00:00:00+00:00", "id": f"old{i}", "kind": "build",
            "state": "done", "params_summary": {}, "error": None,
        }))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = JobRunner(runtime_dir)
    try:
        pass
    finally:
        r.shutdown(timeout=2.0)

    remaining = _read_jsonl(log_path)
    assert len(remaining) == jobs_mod._JOBS_LOG_CAP
    # the newest lines survived, the oldest were dropped
    assert remaining[-1]["id"] == f"old{over_cap - 1}"
    assert remaining[0]["id"] == f"old{over_cap - jobs_mod._JOBS_LOG_CAP}"


# ========================================================== B. interrupted()


def test_interrupted_detection_no_duplicate_on_second_construction(tmp_path):
    """Simulates a GUI crash: a job's jsonl trail stops at "running" — no
    finish line ever landed. A fresh JobRunner construction must flag it as
    interrupted (once), and a SECOND construction over the same file must
    keep surfacing it (so the queue panel doesn't silently drop it after one
    restart) WITHOUT writing a second synthetic record."""
    runtime_dir = tmp_path / ".manju"
    runtime_dir.mkdir(parents=True)
    log_path = runtime_dir / "jobs.jsonl"
    log_path.write_text(
        json.dumps({"ts": "2020-01-01T00:00:00+00:00", "id": "dangling1", "kind": "build",
                    "state": "queued", "params_summary": {"target": "final"}, "error": None}) + "\n"
        + json.dumps({"ts": "2020-01-01T00:00:01+00:00", "id": "dangling1", "kind": "build",
                      "state": "running", "params_summary": {"target": "final"}, "error": None}) + "\n",
        encoding="utf-8",
    )

    r1 = JobRunner(runtime_dir)
    try:
        interrupted1 = r1.interrupted()
    finally:
        r1.shutdown(timeout=2.0)

    assert len(interrupted1) == 1
    rec = interrupted1[0]
    assert rec["id"] == "dangling1"
    assert rec["kind"] == "build"
    assert rec["state"] == "interrupted"
    assert rec["cancelable"] is False
    # round AA item 6: an interrupted job's params_summary is a LOSSY
    # compaction (long lists/strings truncated), so a faithful resubmit is
    # not possible in general — retryable is now False, with a 中文 note
    # explaining why in place of the retry button.
    assert rec["retryable"] is False
    assert "上次退出" in rec["error"]
    assert "无法原样重试" in rec["note"]

    lines_after_1 = _read_jsonl(log_path)
    synthetic_1 = [ln for ln in lines_after_1 if ln.get("state") == "interrupted"]
    assert len(synthetic_1) == 1

    # a SECOND construction (another restart) sees the SAME dangling job —
    # still surfaced, but no second synthetic record appended.
    r2 = JobRunner(runtime_dir)
    try:
        interrupted2 = r2.interrupted()
    finally:
        r2.shutdown(timeout=2.0)

    assert len(interrupted2) == 1
    assert interrupted2[0]["id"] == "dangling1"
    # the re-read-from-disk path (rec already state="interrupted") gets the
    # same retryable=False + note treatment as the freshly-detected one above.
    assert interrupted2[0]["retryable"] is False
    assert "无法原样重试" in interrupted2[0]["note"]

    lines_after_2 = _read_jsonl(log_path)
    synthetic_2 = [ln for ln in lines_after_2 if ln.get("state") == "interrupted"]
    assert len(synthetic_2) == 1  # still exactly one — never duplicated


def test_finished_job_is_never_flagged_interrupted(tmp_path):
    runtime_dir = tmp_path / ".manju"
    runtime_dir.mkdir(parents=True)
    log_path = runtime_dir / "jobs.jsonl"
    log_path.write_text(
        json.dumps({"ts": "2020-01-01T00:00:00+00:00", "id": "ok1", "kind": "build",
                    "state": "queued", "params_summary": {}, "error": None}) + "\n"
        + json.dumps({"ts": "2020-01-01T00:00:01+00:00", "id": "ok1", "kind": "build",
                      "state": "running", "params_summary": {}, "error": None}) + "\n"
        + json.dumps({"ts": "2020-01-01T00:00:02+00:00", "id": "ok1", "kind": "build",
                      "state": "done", "params_summary": {}, "error": None}) + "\n",
        encoding="utf-8",
    )
    r = JobRunner(runtime_dir)
    try:
        assert r.interrupted() == []
    finally:
        r.shutdown(timeout=2.0)


# =================================================== C. routed-handler 202+job


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
            parsed = {}
        return exc.code, parsed


def _post(server, path, body):
    return _req(server, path, method="POST", body=body, headers={"X-Manju-Token": server.token})


def _poll_job(server, job_id, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, data = _req(server, "/api/jobs")
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed", "canceled"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_ingest_plan_routed_through_runner_202_and_job(gui, tmp_project):
    """``/api/ingest/plan`` (round AA4: hashing a large batch is genuinely
    multi-second) answers 202 + a job dict, exactly like every other
    heavyweight POST — never a synchronous 200 with the plan inline."""
    batch = "b1"
    stage = tmp_project.runtime_dir / "ingest-tmp" / batch
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "unnamed.mp4").write_bytes(b"fake-video-bytes")

    status, data = _post(gui, "/api/ingest/plan", {"batch": batch})
    assert status == 202
    assert data["job"]["state"] in ("queued", "running")
    assert data["job"]["kind"] == "ingest_plan"

    job = _poll_job(gui, data["job"]["id"])
    assert job["state"] == "done"
    plan = job["result"]
    assert plan["batch"] == batch
    assert plan["rows"][0]["name"] == "unnamed.mp4"
    assert plan["canceled"] is False


def test_ingest_plan_bad_batch_is_a_synchronous_400_never_a_job(gui, tmp_project):
    """Cheap validation stays OUTSIDE the closure — a request that could
    never succeed is a plain 400, never a pointless queued job."""
    status, data = _post(gui, "/api/ingest/plan", {"batch": "../evil"})
    assert status == 400
    assert "error" in data


# ============================================ D. batch-kind cancel checkpoint


def test_redo_batch_should_cancel_stops_between_shots_honest_partial(
    tmp_project, add_shot, monkeypatch
):
    """build/graph.py: redo_batch's new between-shot checkpoint (round AA4) —
    mirrors test_job_cancel.py's run_build checkpoint test, but for the batch
    kind that previously had NO should_cancel plumbing at all."""
    for i in range(1, 5):
        add_shot(tmp_project, f"S{i:03d}")

    import manju.providers.registry as registry_mod

    original = registry_mod.generate_with_fallback
    cancel_event = threading.Event()

    def fake(req, chain=None, log=None):
        takes = original(req, chain, log=log)
        if req.shot.id == "S002":
            # simulate "cancel clicked right after S002 finished" — S003/S004
            # must never even start.
            cancel_event.set()
        return takes

    monkeypatch.setattr(registry_mod, "generate_with_fallback", fake)

    result = redo_batch(tmp_project, all_shots=True, actor="ai", assume_yes=True,
                        should_cancel=cancel_event.is_set)

    assert result.canceled is True
    assert set(result.ran) == {"S001", "S002"}
    assert any("已取消" in e for e in result.errors)
    assert any("2/4" in e and "保留" in e for e in result.errors)
    # build_lock released — a canceled batch must never leave the project locked
    assert not (tmp_project.runtime_dir / "build.lock").exists()
    # append-only: the two shots that already ran keep their takes
    assert tmp_project.takes("S001")
    assert tmp_project.takes("S002")
    assert tmp_project.takes("S003") == []
    assert tmp_project.takes("S004") == []


def test_jobrunner_batch_fn_cancel_mid_run_partial_progress_recorded():
    """Generic (kind-agnostic) shape every batch kind above follows: a work
    function that iterates items and checks ``job.should_cancel()`` between
    them, canceled mid-run, unwinds to "canceled" with whatever already
    landed recorded honestly in the result — gui/jobs.py's own contract,
    independent of any one engine function."""
    r = JobRunner()
    try:
        items = ["a", "b", "c", "d", "e"]
        started_second = threading.Event()
        resume = threading.Event()

        def fn(job):
            landed = []
            canceled = False
            for item in items:
                if job.should_cancel():
                    canceled = True
                    break
                if item == "b":
                    started_second.set()
                    assert resume.wait(5.0), "test never resumed the worker"
                landed.append(item)
            return {
                "landed": landed,
                "canceled": canceled,
                "errors": ([f"已取消:{len(landed)}/{len(items)} 项已完成并保留"]
                          if canceled else []),
            }

        job = r.submit("redo_batch", {"shots": items}, fn)
        assert started_second.wait(5.0)

        canceled_job = r.cancel(job.id)
        assert canceled_job.state == "canceling"
        resume.set()

        final = _wait_state(r, job.id, states=("canceled",))
        assert final.state == "canceled"
        assert "已取消" in final.error
        assert final.result["landed"] == ["a", "b"]  # partial progress recorded
        assert final.result["canceled"] is True
    finally:
        r.shutdown(timeout=2.0)


# ============================== E. interrupted-job GUI surfacing (item 6)


def _seed_dangling_jobs_log(runtime_dir):
    """A jobs.jsonl trail whose last record for ``dangling9`` is "running" —
    the same crash shape :meth:`JobRunner._scan_interrupted` looks for. Must
    be written BEFORE the GUI server (and therefore its JobRunner) is
    constructed — interrupted() is computed once at construction time."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_path = runtime_dir / "jobs.jsonl"
    log_path.write_text(
        json.dumps({"ts": "2020-01-01T00:00:00+00:00", "id": "dangling9", "kind": "build",
                    "state": "queued", "params_summary": {"target": "final"}, "error": None}) + "\n"
        + json.dumps({"ts": "2020-01-01T00:00:01+00:00", "id": "dangling9", "kind": "build",
                      "state": "running", "params_summary": {"target": "final"}, "error": None}) + "\n",
        encoding="utf-8",
    )
    return log_path


def test_api_jobs_merges_interrupted_entries_retryable_false_with_note(tmp_project):
    """GET /api/jobs (round AA item 6): interrupted() is merged into the
    SAME "jobs" array (state="interrupted") — response shape stays exactly
    {"jobs": [...]}, so every existing poller (lab/ingest/edit/series/
    exports pages, all of which filter by job id) is unaffected, but a human
    looking at the queue panel now sees the dangling job with retryable
    False and the honesty note instead of it silently being absent."""
    _seed_dangling_jobs_log(tmp_project.runtime_dir)

    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, data = _req(server, "/api/jobs")
        assert status == 200
        hits = [j for j in data["jobs"] if j["id"] == "dangling9"]
        assert len(hits) == 1
        j = hits[0]
        assert j["state"] == "interrupted"
        assert j["kind"] == "build"
        assert j["cancelable"] is False
        assert j["retryable"] is False
        assert "无法原样重试" in j["note"]
    finally:
        server.shutdown()
        server.close()


def test_api_jobs_retry_on_interrupted_id_is_clean_4xx_zh_error(tmp_project):
    """POST /api/jobs/retry on an interrupted job id (round AA item 6): a
    clean 4xx 中文 error, never a bare "unknown job" 404 and never a 500/
    stack trace — the explicit guard in ``_act_jobs_retry`` this round added."""
    _seed_dangling_jobs_log(tmp_project.runtime_dir)

    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, data = _post(server, "/api/jobs/retry", {"job_id": "dangling9"})
        assert 400 <= status < 500
        assert "error" in data
        msg = data["error"]
        # a 中文 message, not the generic English "unknown job: ..." 404
        assert any("一" <= ch <= "鿿" for ch in msg)
        assert "重试" in msg
    finally:
        server.shutdown()
        server.close()
