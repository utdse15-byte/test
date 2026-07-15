"""Job management — cancel, retry, queue control (round X, agent XC).

User pain #3: "running jobs cannot be canceled yet". This suite covers the
cooperative-cancel core end to end:

- gui/jobs.py: JobRunner.cancel/retry — the in-memory queue state machine.
- build/graph.py: run_build(should_cancel=...) checkpoints (between phases,
  between per-shot generation submissions) and the render-phase ffmpeg-kill
  wiring (media/ffmpeg.py: cancel_scope/MediaCanceled).
- providers/base.py: CloudProvider poll-loop cancellation (ProviderCanceled)
  — stops waiting without lying about whether the remote job is done.
- media/ffmpeg.py: run_ffmpeg(cancel_event=...) actually kills the subprocess.
- gui/server.py: the HTTP surface (/api/jobs/cancel, /api/jobs/retry) and its
  guards.

Every test that exercises ``should_cancel``/``cancel_event`` uses REAL
``threading.Event`` objects — never a bare boolean — so the exact primitive
production code checks is what these tests drive.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.build.graph import BuildCanceled, _concurrent_generate, run_build
from manju.core.events import tail_events
from manju.gui.jobs import JobRunner
from manju.gui.server import create_server
from manju.media.ffmpeg import MediaCanceled, cancel_scope, run_ffmpeg
from manju.providers.base import CloudProvider, GenerationRequest, ProviderCanceled
from manju.runtime.state import RuntimeState

# Same convention as tests/test_asr.py, test_board_round_e.py, etc.: a real
# ffmpeg binary is required for the kill-path tests (they spawn genuine slow
# encodes), so skip gracefully rather than fail in an ffmpeg-less environment.
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")

# ============================================================== A. JobRunner


@pytest.fixture
def runner():
    r = JobRunner()
    yield r
    r.shutdown(timeout=2.0)


def test_queued_job_canceled_never_runs(runner):
    """A job canceled while still queued must NEVER invoke its work function
    — no partial spend, nothing to unwind."""
    release = threading.Event()
    started_job1 = threading.Event()

    def blocker(job):
        started_job1.set()
        release.wait(5.0)
        return {"ok": True}

    calls = []

    def never_called(job):
        calls.append(job.id)
        return {"ok": True}

    job1 = runner.submit("build", {}, blocker)
    assert started_job1.wait(5.0), "job1 did not start"
    job2 = runner.submit("build", {}, never_called)

    cancelled = runner.cancel(job2.id)
    assert cancelled is not None
    assert cancelled.state == "canceled"
    assert "从未运行" in cancelled.error  # honest: never ran, never spent

    release.set()  # let job1 finish so the worker drains the queue
    deadline = time.monotonic() + 5.0
    while runner.get(job1.id).state == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    # give the worker a moment to dequeue job2 and observe it is canceled
    time.sleep(0.2)

    assert calls == []  # the work function was NEVER invoked
    assert runner.get(job2.id).state == "canceled"


def test_running_job_cancel_sets_flag_state_canceling_then_canceled(runner):
    """A running job's cancel_event is a REAL threading.Event the work
    function checks at its own checkpoint; the runner reports "canceling"
    while it is still unwinding and "canceled" once it raises honestly."""
    checkpoint_hit = threading.Event()

    def fn(job):
        checkpoint_hit.set()
        # the work function's OWN checkpoint: keep working until it observes
        # the cancel flag, then unwind like build/graph.py's BuildCanceled
        deadline = time.monotonic() + 5.0
        while not job.cancel_event.is_set():
            if time.monotonic() > deadline:
                raise AssertionError("cancel flag never observed")
            time.sleep(0.01)
        raise BuildCanceled("已取消(测试checkpoint):0 个产物已完成并缓存;已花费 0",
                            generated=[], spent=0.0, currency=None)

    job = runner.submit("build", {}, fn)
    assert checkpoint_hit.wait(5.0)

    canceled = runner.cancel(job.id)
    assert canceled.state == "canceling"
    assert canceled.cancel_event.is_set()

    deadline = time.monotonic() + 5.0
    while runner.get(job.id).state == "canceling" and time.monotonic() < deadline:
        time.sleep(0.01)

    final = runner.get(job.id)
    assert final.state == "canceled"
    assert "已取消" in final.error


def test_cancel_is_a_harmless_noop_on_terminal_or_unknown_jobs(runner):
    done_hit = threading.Event()

    def fast(job):
        return {"ok": True}

    job = runner.submit("build", {}, fast)
    deadline = time.monotonic() + 5.0
    while runner.get(job.id).state not in ("done", "failed") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.get(job.id).state == "done"

    same = runner.cancel(job.id)
    assert same.state == "done"  # no-op: already terminal

    assert runner.cancel("does-not-exist") is None


def test_jobrunner_retry_creates_fresh_lineage(runner):
    """JobRunner.submit(..., retry_of=...) mints a NEW id with lineage —
    goal B's core contract, independent of the GUI HTTP layer."""

    def boom(job):
        raise RuntimeError("boom")

    job1 = runner.submit("build", {"target": "final"}, boom)
    deadline = time.monotonic() + 5.0
    while runner.get(job1.id).state != "failed" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.get(job1.id).state == "failed"

    job2 = runner.submit("build", dict(job1.params), lambda job: {"ok": True},
                         retry_of=job1.id)
    assert job2.id != job1.id
    assert job2.retry_of == job1.id
    deadline = time.monotonic() + 5.0
    while runner.get(job2.id).state not in ("done", "failed") and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.get(job2.id).state == "done"
    assert runner.get(job1.id).retry_of is None  # the original carries no lineage


# ========================================================= B. run_build core


def test_run_build_checkpoint_stops_between_shots_honest_partial_result(
    tmp_project, add_shot, monkeypatch
):
    """A should_cancel() backed by a REAL threading.Event trips the "between
    per-shot generation submissions" checkpoint: shots already generated stay
    (content-addressed, reusable — §3), later ones never start, build_lock is
    released, and the result is an honest ok=False/canceled=True — never a
    bare exception reaching the caller."""
    for i in range(1, 5):
        add_shot(tmp_project, f"S{i:03d}")

    import manju.providers.registry as registry_mod

    original = registry_mod.generate_with_fallback
    cancel_event = threading.Event()

    def fake(req, chain=None, log=None):
        takes = original(req, chain, log=log)
        if req.shot.id == "S002":
            # simulate "the user clicked cancel" right after S002 finished —
            # S003/S004 must never even start.
            cancel_event.set()
        return takes

    monkeypatch.setattr(registry_mod, "generate_with_fallback", fake)

    result = run_build(tmp_project, target="qc", gen="missing", actor="ai",
                       should_cancel=cancel_event.is_set)

    assert result.ok is False
    assert result.canceled is True
    assert any("已取消" in e for e in result.errors)
    assert any("缓存" in e for e in result.errors)  # honest: cache is reusable
    generated_shots = {g.split("/")[0] for g in result.generated}
    assert generated_shots == {"S001", "S002"}

    # build_lock released — a canceled build must never leave the project locked
    assert not (tmp_project.runtime_dir / "build.lock").exists()

    # the completed shots' takes are real, on-disk, content-addressed cache
    assert tmp_project.takes("S001")
    assert tmp_project.takes("S002")
    assert tmp_project.takes("S003") == []
    assert tmp_project.takes("S004") == []

    # a canceled build still mirrors into events.jsonl (the collaboration log)
    events = tail_events(tmp_project.root, 20)
    build_events = [e for e in events if e.get("action") == "build"]
    assert build_events and build_events[-1]["detail"].get("canceled") is True


def test_run_build_should_cancel_none_is_byte_identical_default(
    tmp_project, add_shot
):
    """The default no-cancel path (should_cancel=None) must behave exactly
    like before this round — a normal, uncanceled build."""
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, target="qc", gen="missing", actor="ai")
    assert result.ok is True
    assert result.canceled is False
    assert result.generated  # S001 generated normally


def test_run_build_first_checkpoint_cancels_before_any_spend(tmp_project, add_shot):
    """should_cancel() already tripped before the build even starts: the very
    first phase checkpoint raises — nothing is generated, nothing is spent."""
    add_shot(tmp_project, "S001")
    always_cancel = threading.Event()
    always_cancel.set()

    result = run_build(tmp_project, target="qc", gen="missing", actor="ai",
                       should_cancel=always_cancel.is_set)
    assert result.ok is False
    assert result.canceled is True
    assert result.generated == []
    assert not (tmp_project.runtime_dir / "build.lock").exists()


def test_run_build_dry_run_never_checks_should_cancel(tmp_project, add_shot):
    """Dry-run is read-only and free (§8.3) — should_cancel is not threaded
    into it at all, so even an already-tripped event never raises."""
    add_shot(tmp_project, "S001")
    always_cancel = threading.Event()
    always_cancel.set()
    result = run_build(tmp_project, target="final", gen="missing", dry_run=True,
                       actor="ai", should_cancel=always_cancel.is_set)
    assert result.ok is True
    assert any(p["shot"] == "S001" for p in result.plan)


def test_render_phase_media_canceled_becomes_build_canceled(
    tmp_project, add_shot, make_take, monkeypatch
):
    """media/render.py is a read-only surface this round: build/graph.py
    catches MediaCanceled from render_timeline and ALWAYS translates it into
    a clean BuildCanceled result — never a generic render-failure error."""
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )

    import manju.media.render as render_mod

    def forced_cancel(*a, **kw):
        raise MediaCanceled("已取消:ffmpeg 进程已终止(测试强制)")

    monkeypatch.setattr(render_mod, "render_timeline", forced_cancel)

    result = run_build(tmp_project, target="final", gen="off", actor="ai")
    assert result.ok is False
    assert result.canceled is True
    assert not (tmp_project.runtime_dir / "build.lock").exists()


def test_concurrent_generate_should_cancel_stops_new_submissions():
    """_concurrent_generate's should_cancel is checked before every new
    submission — mirrors the serial-loop checkpoint for --mode-driven
    concurrent builds (goal 14 x goal: honest job cancellation)."""
    items = [{"shot": f"S{i:03d}"} for i in range(1, 9)]
    submitted: list[str] = []
    lock = threading.Lock()
    cancel_event = threading.Event()

    def gen_one(item):
        with lock:
            submitted.append(item["shot"])
        if item["shot"] == "S002":
            cancel_event.set()
        time.sleep(0.01)
        return {"shot": item["shot"], "actual_cost": 0.0}

    results, tripped, canceled, running, in_flight = _concurrent_generate(
        items, gen_one, max_workers=1, budget_limit=None,
        should_cancel=cancel_event.is_set)
    assert canceled is True
    assert tripped is False
    assert len(submitted) < len(items)
    assert set(results) == set(submitted)


# ==================================================== C. provider poll cancel


class _NeverFinishingCloud(CloudProvider):
    """submit() succeeds; poll() never terminates — the honest stand-in for
    a slow remote job the caller stops WAITING for without lying about its
    remote state."""

    id = "cloud_never_finishes"

    def __init__(self, **kw):
        kw.setdefault("sleep_fn", lambda _s: None)
        super().__init__(**kw)
        self.submit_calls = 0
        self.poll_calls = 0

    def submit(self, req: GenerationRequest) -> str:
        self.submit_calls += 1
        return "job_stuck_1"

    def poll(self, job_id: str):
        self.poll_calls += 1
        return "running", {}

    def download(self, job_id, dest_dir):  # pragma: no cover - never reached
        raise AssertionError("download must never be reached — job never completed")


def _req(project, shot, *, should_cancel=None) -> GenerationRequest:
    return GenerationRequest(
        project=project, shot=shot, bible=project.load_bible(),
        spec_hash="sha256:test", duration_ms=3000, candidates=1,
        should_cancel=should_cancel,
    )


def test_provider_poll_cancel_stops_waiting_leaves_pending_job_honest(
    tmp_project, add_shot
):
    project = tmp_project
    shot = add_shot(project, "S001")
    cancel_event = threading.Event()
    cancel_event.set()  # already tripped: deterministic, no timing games
    provider = _NeverFinishingCloud()
    req = _req(project, shot, should_cancel=cancel_event.is_set)

    with pytest.raises(ProviderCanceled) as excinfo:
        provider.generate(req)

    assert provider.submit_calls == 1  # the remote job WAS submitted
    assert provider.poll_calls == 0  # but we never waited on a poll result
    # the message tells the truth: it may still complete and bill
    assert "可能仍在进行并计费" in str(excinfo.value)
    assert "job_stuck_1" in str(excinfo.value)

    with RuntimeState(project.root) as state:
        pending = state.pending_jobs()
    assert len(pending) == 1
    assert pending[0]["remote_job_id"] == "job_stuck_1"
    assert pending[0]["shot"] == "S001"  # resumable: a later build/poll picks it up


def test_provider_poll_cancel_default_none_is_byte_identical(tmp_project, add_shot):
    """should_cancel=None (the default) never interrupts polling — an
    unrelated failure still surfaces normally."""
    project = tmp_project
    shot = add_shot(project, "S001")

    class _FailsOnce(_NeverFinishingCloud):
        def poll(self, job_id):
            self.poll_calls += 1
            return "failed", {"failure_kind": "provider_error", "reason": "nope"}

    provider = _FailsOnce()
    req = _req(project, shot)  # should_cancel defaults to None
    from manju.providers.base import ProviderFailure

    with pytest.raises(ProviderFailure):
        provider.generate(req)
    assert provider.poll_calls == 1


# ============================================================ D. ffmpeg kill


@needs_ffmpeg
def test_run_ffmpeg_cancel_event_kills_slow_subprocess(tmp_path):
    """A REAL, genuinely slow ffmpeg (30s synthetic source) is spawned; a
    background thread trips a real threading.Event shortly after start. The
    process must actually die and run_ffmpeg must return (raise) in well
    under the encode's nominal duration."""
    out = tmp_path / "slow.mp4"
    cancel_event = threading.Event()

    def trip_soon():
        time.sleep(0.3)
        cancel_event.set()

    threading.Thread(target=trip_soon, daemon=True).start()

    start = time.monotonic()
    with pytest.raises(MediaCanceled) as excinfo:
        run_ffmpeg(
            # -re: read the synthetic source at its native (wall-clock) frame
            # rate — without it ffmpeg renders a tiny 64x64 pattern far faster
            # than real time and the whole "30s" encode finishes in well
            # under a second, defeating the point of this test.
            ["-re", "-f", "lavfi", "-i", "testsrc=duration=30:size=64x64:rate=5", str(out)],
            cancel_event=cancel_event,
        )
    elapsed = time.monotonic() - start
    assert elapsed < 10.0, f"cancel took {elapsed}s — the subprocess was not killed promptly"
    assert isinstance(excinfo.value, MediaCanceled)  # distinguishable from a real MediaError
    assert "已取消" in str(excinfo.value)
    # no truncated file left behind: run_ffmpeg wrote nothing to `out` itself
    # (callers durable-write via atomic_output; this call used a bare path)


def test_run_ffmpeg_default_path_unaffected_when_no_cancel_active(tmp_path):
    """No cancel_event, no ambient cancel_scope: byte-identical to before —
    a normal fast ffmpeg call just succeeds."""
    out = tmp_path / "fast.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=duration=0.2:size=64x64:rate=5", str(out)])
    assert out.exists() and out.stat().st_size > 0


@needs_ffmpeg
def test_cancel_scope_makes_ambient_run_ffmpeg_calls_cancelable(tmp_path):
    """media/render.py is read-only this round; cancel_scope is how
    build/graph.py's render phase makes EVERY run_ffmpeg call it makes
    transitively cancelable without a single line of render.py changing."""
    out = tmp_path / "ambient.mp4"
    cancel_event = threading.Event()

    def trip_soon():
        time.sleep(0.3)
        cancel_event.set()

    threading.Thread(target=trip_soon, daemon=True).start()

    start = time.monotonic()
    with pytest.raises(MediaCanceled):
        with cancel_scope(cancel_event.is_set):
            run_ffmpeg(  # note: NO explicit cancel_event kwarg here
                ["-re", "-f", "lavfi", "-i", "testsrc=duration=20:size=64x64:rate=5", str(out)],
            )
    elapsed = time.monotonic() - start
    assert elapsed < 10.0

    # outside the scope, an identical call is unaffected (no ambient leak)
    out2 = tmp_path / "outside.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=duration=0.2:size=64x64:rate=5", str(out2)])
    assert out2.exists()


# ==================================================================== E. GUI


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _request(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    # Never honor system HTTP_PROXY for loopback GUI tests (502 via proxy).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed


def _post(server, path, body):
    return _request(server, path, method="POST", body=body,
                    headers={"X-Manju-Token": server.token})


def _wait_job(server, job_id, timeout=30.0, states=("done", "failed", "canceled")):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, data = _request(server, "/api/jobs")
        assert status == 200
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach {states} within {timeout}s")


def test_gui_cancel_running_build_stops_before_next_shot(gui, tmp_project, add_shot, monkeypatch):
    for i in range(1, 4):
        add_shot(tmp_project, f"S{i:03d}")

    import manju.providers.registry as registry_mod

    original = registry_mod.generate_with_fallback
    started = threading.Event()
    resume = threading.Event()

    def fake(req, chain=None, log=None):
        if req.shot.id == "S001":
            started.set()
            assert resume.wait(10.0), "test never resumed the worker"
        return original(req, chain, log=log)

    monkeypatch.setattr(registry_mod, "generate_with_fallback", fake)

    status, data = _post(gui, "/api/build", {"target": "qc", "gen": "missing"})
    assert status == 202
    job_id = data["job"]["id"]

    assert started.wait(10.0)
    status, data = _post(gui, "/api/jobs/cancel", {"job_id": job_id})
    assert status == 200
    assert data["job"]["state"] in ("canceling", "canceled")
    resume.set()

    job = _wait_job(gui, job_id)
    assert job["state"] == "canceled"
    assert "已取消" in job["error"]
    assert job["cancelable"] is False
    assert job["retryable"] is True

    # S002/S003 never started (S001 was already mid-flight when canceled)
    assert tmp_project.takes("S002") == []
    assert tmp_project.takes("S003") == []


def test_gui_cancel_queued_job_never_runs(gui, tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")

    hold = threading.Event()
    release = threading.Event()

    import manju.build.graph as graph_mod

    def blocking_run_build(project, **kwargs):
        hold.set()
        release.wait(10.0)
        return graph_mod.BuildResult()

    monkeypatch.setattr(graph_mod, "run_build", blocking_run_build)

    status, data = _post(gui, "/api/build", {"target": "qc", "gen": "off"})
    assert status == 202
    job1_id = data["job"]["id"]
    assert hold.wait(10.0)

    status, data = _post(gui, "/api/build", {"target": "qc", "gen": "off"})
    assert status == 202
    job2_id = data["job"]["id"]

    status, data = _post(gui, "/api/jobs/cancel", {"job_id": job2_id})
    assert status == 200
    assert data["job"]["state"] == "canceled"

    release.set()
    _wait_job(gui, job1_id)
    job2 = _wait_job(gui, job2_id)
    assert job2["state"] == "canceled"
    assert "从未运行" in job2["error"]


def test_gui_cancel_unknown_job_404(gui):
    status, data = _post(gui, "/api/jobs/cancel", {"job_id": "no-such-job"})
    assert status == 404


def test_gui_cancel_terminal_job_is_noop(gui, tmp_project, add_shot, make_take):
    shot_id = "S001"
    add_shot(tmp_project, shot_id)
    take = make_take(tmp_project, shot_id, "h")
    tmp_project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    status, data = _post(gui, "/api/build", {"target": "qc", "gen": "off"})
    job_id = data["job"]["id"]
    job = _wait_job(gui, job_id)
    assert job["state"] == "done"

    status, data = _post(gui, "/api/jobs/cancel", {"job_id": job_id})
    assert status == 200
    assert data["job"]["state"] == "done"  # unaffected — nothing to cancel


def test_gui_retry_round_trip_and_lineage(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # dialogue.text set by the add_shot fixture

    # no TTS provider is configured in a fresh test project -> a deterministic
    # TtsUnavailable failure, no timing tricks needed
    status, data = _post(gui, "/api/voice", {"shot": "S001"})
    assert status == 202
    job1_id = data["job"]["id"]
    job1 = _wait_job(gui, job1_id)
    assert job1["state"] == "failed"

    status, data = _post(gui, "/api/jobs/retry", {"job_id": job1_id})
    assert status == 202
    job2 = data["job"]
    assert job2["id"] != job1_id
    assert job2["retry_of"] == job1_id

    job2_final = _wait_job(gui, job2["id"])
    assert job2_final["state"] == "failed"  # same recipe, same deterministic outcome
    assert job2_final["retry_of"] == job1_id


def test_gui_retry_refuses_non_terminal_job(gui, tmp_project, add_shot, make_take):
    shot_id = "S001"
    add_shot(tmp_project, shot_id)
    take = make_take(tmp_project, shot_id, "h")
    tmp_project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    status, data = _post(gui, "/api/build", {"target": "qc", "gen": "off"})
    job_id = data["job"]["id"]
    _wait_job(gui, job_id)  # -> done

    status, data = _post(gui, "/api/jobs/retry", {"job_id": job_id})
    assert status == 409
    assert "已失败" in data["error"] or "已取消" in data["error"]


def test_gui_retry_refuses_when_params_no_longer_validate(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, data = _post(gui, "/api/voice", {"shot": "S001"})
    job_id = data["job"]["id"]
    job = _wait_job(gui, job_id)
    assert job["state"] == "failed"

    # corrupt the recorded params directly (simulates "the world changed"
    # since this job failed) — the retry path must re-validate, not blindly re-run
    live_job = gui.runner.get(job_id)
    live_job.params["target"] = "nonsense"  # only meaningful for a build kind,
    # but proves the retry path is per-kind and does not just replay blindly;
    # exercise the REAL invalid-params case for THIS kind instead:
    live_job.params["shot"] = "S404_NO_SUCH_SHOT"

    status, data = _post(gui, "/api/jobs/retry", {"job_id": job_id})
    assert status == 400


def test_gui_jobs_list_shape_includes_cancel_metadata(gui, tmp_project, add_shot, make_take):
    shot_id = "S001"
    add_shot(tmp_project, shot_id)
    take = make_take(tmp_project, shot_id, "h")
    tmp_project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    status, data = _post(gui, "/api/build", {"target": "qc", "gen": "off"})
    job_id = data["job"]["id"]
    _wait_job(gui, job_id)

    status, data = _request(gui, "/api/jobs")
    assert status == 200
    job = next(j for j in data["jobs"] if j["id"] == job_id)
    assert set(("cancelable", "retryable", "retry_of")) <= set(job)
    assert job["cancelable"] is False
    assert job["retryable"] is False  # a "done" job is not retryable
