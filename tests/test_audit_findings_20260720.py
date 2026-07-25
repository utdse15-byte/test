"""Regression tests for the 2026-07-20 external audit (22 findings).

One behavioral test per confirmed defect (tests/CONVENTIONS.md: assert on
runtime behavior, never source text). Concurrency findings use barrier-
controlled threads, not sleeps. Provider findings inject faults at the response
boundary. Each test is RED against the pre-fix code and names its finding.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from manju.providers.generic_cloud import HttpResponse


# ============================================================ gui/jobs.py


def test_f1_jobs_log_wrong_shape_line_never_bricks_startup(tmp_path):
    """Finding 1: a valid-JSON but wrong-shape line (`[]`, a scalar, an object
    with an array `id`) must not crash JobRunner construction."""
    from manju.gui.jobs import JobRunner

    log = tmp_path / "jobs.jsonl"
    log.write_text(
        "[]\n"
        "42\n"
        '"just a string"\n'
        '{"id": ["not", "scalar"], "kind": "build", "state": "running"}\n'
        '{"id": "j1", "kind": "build", "state": "running", "ts": "2026-07-20T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    runner = JobRunner(tmp_path)  # must NOT raise
    try:
        ids = {rec.get("id") for rec in runner.interrupted()}
        assert "j1" in ids  # the one well-formed dangling job is recovered
    finally:
        runner.shutdown(timeout=1)


def test_f2_non_serializable_param_does_not_strand_the_job(tmp_path):
    """Finding 2: a Path inside a short list param must not raise out of submit
    (json.dumps TypeError) and leave the job admitted-but-never-queued."""
    from manju.gui.jobs import JobRunner

    runner = JobRunner(tmp_path)
    ran = threading.Event()
    try:
        job = runner.submit("build", {"files": [Path("input.wav")]},
                            lambda j: ran.set() or {"ok": True})
        assert ran.wait(timeout=5), "job callback never executed (stranded)"
        deadline = threading.Event()
        for _ in range(50):
            if runner.get(job.id).state in ("done", "failed"):
                break
            deadline.wait(0.05)
        assert runner.get(job.id).state == "done"
    finally:
        runner.shutdown(timeout=2)


def test_f3_submit_racing_shutdown_never_strands_a_queued_job(tmp_path):
    """Finding 3: submit admission + queue insertion are atomic under the lock,
    so shutdown's exit sentinel can never be posted before a just-admitted job
    reaches the queue (which left it permanently queued with a dead worker)."""
    from manju.gui.jobs import JobRunner

    for _ in range(40):
        runner = JobRunner(tmp_path)
        started = threading.Barrier(2)
        result = {}

        def do_submit():
            started.wait()
            try:
                result["job"] = runner.submit("build", {}, lambda j: {"ok": True})
            except Exception as exc:  # RunnerClosed is a legitimate outcome
                result["exc"] = exc

        t = threading.Thread(target=do_submit)
        t.start()
        started.wait()
        rep = runner.shutdown(timeout=5, cancel_queued=False)
        t.join(5)
        job = result.get("job")
        if job is not None:
            j = runner.get(job.id)
            # the invariant: a job that was admitted is never left QUEUED while
            # the worker is dead — it either ran or shutdown will still drain it
            assert not (j.state == "queued" and rep.stopped), (
                f"stranded job: state={j.state}, worker_stopped={rep.stopped}")


def test_f4_unrelated_failure_racing_a_cancel_is_failed_not_canceled(tmp_path):
    """Finding 4: an UNRELATED exception that happens to race a cancel click
    must classify as `failed`, not be masked as a clean user cancellation."""
    from manju.gui.jobs import JobRunner

    runner = JobRunner(tmp_path)
    try:
        def fn(job):
            job.cancel_event.set()  # cancel requested...
            raise ValueError("unrelated invariant failure")  # ...but this is NOT a cancel

        job = runner.submit("build", {}, fn)
        deadline = threading.Event()
        for _ in range(100):
            if runner.get(job.id).state in ("failed", "canceled", "done"):
                break
            deadline.wait(0.05)
        j = runner.get(job.id)
        assert j.state == "failed"
        assert "unrelated" in (j.error or "")
    finally:
        runner.shutdown(timeout=2)


def test_f4_genuine_cancellation_exception_still_lands_canceled(tmp_path):
    """Finding 4 boundary: a real cancellation exception (MediaCanceled) with the
    flag set still classifies as canceled (behavior preserved)."""
    from manju.gui.jobs import JobRunner
    from manju.media.ffmpeg import MediaCanceled

    runner = JobRunner(tmp_path)
    try:
        def fn(job):
            job.cancel_event.set()
            raise MediaCanceled("已取消:ffmpeg 进程已终止")

        job = runner.submit("export", {}, fn)
        deadline = threading.Event()
        for _ in range(100):
            if runner.get(job.id).state in ("failed", "canceled", "done"):
                break
            deadline.wait(0.05)
        assert runner.get(job.id).state == "canceled"
    finally:
        runner.shutdown(timeout=2)


# ============================================================ runtime/buildlock.py


def test_f5_release_never_deletes_a_successor_with_the_same_pid(tmp_project):
    """Finding 5 (ABA): after A acquires and a SUCCESSOR lock is minted at the
    same path with the same pid+hostname (different acquisition), A.release()
    must NOT delete the successor — its token no longer matches."""
    import os
    import socket

    from manju.runtime.buildlock import BuildLock

    a = BuildLock(tmp_project.root, actor="human")
    a.acquire()
    # Simulate the successor: same pid/host, a DIFFERENT acquisition token.
    successor = {
        "pid": os.getpid(), "actor": "engine",
        "started": "2026-07-20T00:00:00+00:00",
        "hostname": socket.gethostname(), "token": "successor-token-xyz",
    }
    a.path.write_text(json.dumps(successor), encoding="utf-8")
    a.release()
    assert a.path.exists(), "A.release() deleted the successor's lock (ABA bug)"
    assert json.loads(a.path.read_text())["token"] == "successor-token-xyz"
    a.path.unlink()


def test_f6_two_acquisitions_use_distinct_staging_temp_names(tmp_project):
    """Finding 6: the staging temp file is named by the per-acquisition token,
    not the pid, so two BuildLock instances in one process never collide on one
    O_TRUNC temp inode."""
    from manju.runtime.buildlock import BuildLock

    a = BuildLock(tmp_project.root, actor="human")
    b = BuildLock(tmp_project.root, actor="ai")
    assert a._token != b._token
    a_tmp = a.path.with_name(a.path.name + f".{a._token}.tmp")
    b_tmp = b.path.with_name(b.path.name + f".{b._token}.tmp")
    assert a_tmp != b_tmp


# ============================================================ core/events.py


def test_f7_best_effort_append_never_raises_on_lock_open_failure(tmp_path, monkeypatch):
    """Finding 7: a best-effort append must return False, never propagate, when
    the lock file cannot even be opened (a caller that already committed state
    must not be told it failed)."""
    import manju.core.events as EV

    real_open = EV.os.open

    def boom(path, *a, **k):
        if str(path).endswith(".lock"):
            raise PermissionError(13, "simulated lock open failure")
        return real_open(path, *a, **k)

    monkeypatch.setattr(EV.os, "open", boom)
    # best-effort: returns False, no exception
    assert EV.append_jsonl_line(tmp_path, {"x": 1}, durable=False, required=False) is False
    # required: converts to the structured EvidenceWriteError
    with pytest.raises(EV.EvidenceWriteError):
        EV.append_jsonl_line(tmp_path, {"x": 1}, durable=True, required=True)


# ============================================================ providers/jsonpath.py


def test_f19_jsonpath_rejects_malformed_paths_instead_of_redirecting():
    """Finding 19: malformed syntax must RAISE, never silently resolve to a
    different (wrong) node."""
    from manju.providers.jsonpath import JsonPathError, extract

    data = {"data": {"task_id": "j1", "items": [{"url": "u0"}, {"url": "u1"}]}}
    # valid paths still resolve
    assert extract(data, "$.data.task_id") == "j1"
    assert extract(data, "data.task_id") == "j1"
    assert extract(data, "$.data.items[1].url") == "u1"
    # malformed paths are rejected, not silently redirected
    for bad in ("$", "$.", "$.data..id", "$.data[abc]", "$.data.items[0]junk.url"):
        with pytest.raises(JsonPathError):
            extract(data, bad)


# ============================================================ providers/generic_cloud.py guards


def test_f12_null_and_container_ids_are_rejected_not_stringified():
    """Finding 12: null/array/object/bool must never become a `str()` id that
    then polls `/jobs/None`."""
    from manju.providers.base import ProviderFailure
    from manju.providers.generic_cloud import require_remote_id

    assert require_remote_id("job-123", "px") == "job-123"
    assert require_remote_id(42, "px") == "42"
    for bad in (None, {"id": 1}, ["id"], True, "", "   "):
        with pytest.raises(ProviderFailure):
            require_remote_id(bad, "px")


def test_f13_malformed_json_body_becomes_structured_failure():
    """Finding 13: a 2xx whose body is not valid UTF-8/JSON raises a structured
    ProviderFailure, not a raw UnicodeDecodeError/JSONDecodeError."""
    from manju.providers.base import ProviderFailure
    from manju.providers.generic_cloud import parse_json_response

    bad_utf8 = HttpResponse(200, {}, b"\xff\xfe not json")
    bad_json = HttpResponse(200, {}, b"{not json")
    for resp in (bad_utf8, bad_json):
        with pytest.raises(ProviderFailure):
            parse_json_response(resp, "px", post_submit=True)


def test_f16_audio_format_rejects_path_traversal():
    """Finding 16: tts.audio_format is an extension, not a path — a separator/
    `..`/drive must be refused."""
    from manju.providers.base import ProviderFailure
    from manju.providers.generic_cloud import safe_audio_ext

    assert safe_audio_ext("wav", "px") == "wav"
    assert safe_audio_ext(".mp3", "px") == "mp3"
    for bad in ("x/../../escaped.bin", "..", "a\\b", "C:evil", "wav/x", "exe"):
        with pytest.raises(ProviderFailure):
            safe_audio_ext(bad, "px")


# ============================================================ providers/tts.py


def _tts_env(monkeypatch, tmp_path, **manifest_overrides):
    from manju.core.yamlio import write_yaml
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    base = {
        "id": "tts_x", "type": "tts", "adapter": "generic_tts",
        "auth": {"key_env": "TTS_X_KEY"},
        "submit": {"url": "https://api/tts",
                   "body_template": {"text": "{text}"}, "job_id_path": "$.data.task_id"},
        "tts": {"audio_b64_path": "$.data.audio_b64", "audio_format": "wav"},
        "cost": {"per_call": 0.02, "currency": "CNY"},
    }
    base.update(manifest_overrides)
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", base)


def test_f14_corrupt_base64_is_rejected_not_written_as_audio(monkeypatch, tmp_path,
                                                             tmp_project, add_shot):
    """Finding 14: corrupt base64 ("AAAA!!!!") must RAISE, not silently drop the
    invalid chars and register 3 junk bytes as a voice take."""
    from manju.providers.base import ProviderFailure
    from manju.providers.tts import get_tts_provider

    class ScriptedTransport:
        def __init__(self, script):
            self.script, self.requests = list(script), []

        def __call__(self, method, url, headers, body):
            self.requests.append((method, url))
            return self.script.pop(0)

    _tts_env(monkeypatch, tmp_path)  # sync form: submit IS the result
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    transport = ScriptedTransport([
        HttpResponse(200, {}, json.dumps({"data": {"audio_b64": "AAAA!!!!"}}).encode()),
    ])
    provider = get_tts_provider("tts_x", transport=transport, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure):
        provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    # no voice take was registered from the junk
    assert tmp_project.voice_takes("S001") == []


def test_f8_tts_journal_is_fail_closed(monkeypatch, tmp_path, tmp_project, add_shot):
    """Finding 8: if the paid remote job id cannot be durably journaled, the
    submit surfaces a failure that STILL carries the id — never silent loss.

    The journal failure is scoped to the ``provider_remote_submit`` record so it
    lands where finding 8 aimed it: AFTER the provider accepted the paid job.
    PROVIDER-SUBMISSION-001 added durable admission evidence AHEAD of the POST,
    and blanket-failing every evidence write now trips that earlier gate instead
    (which the companion test below pins) — so this one keeps its original
    target by failing only the post-acceptance write."""
    import manju.core.events as EV
    from manju.providers.base import ProviderFailure
    from manju.providers.tts import get_tts_provider

    class ScriptedTransport:
        def __init__(self, script):
            self.script = list(script)

        def __call__(self, method, url, headers, body):
            return self.script.pop(0)

    _tts_env(monkeypatch, tmp_path,
             poll={"url": "https://api/tts/{job_id}", "status_path": "$.data.status",
                   "status_map": {"DONE": "succeeded"}})
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    transport = ScriptedTransport([
        HttpResponse(200, {}, json.dumps({"data": {"task_id": "remote-777"}}).encode()),
    ])
    # force the durable journal of the ACCEPTED job to fail
    real_append = EV.append_jsonl_line

    def _fail_the_remote_submit_journal(root, record, *a, **k):
        if isinstance(record, dict) and record.get("action") == "provider_remote_submit":
            raise EV.EvidenceWriteError("io_error")
        return real_append(root, record, *a, **k)

    monkeypatch.setattr(EV, "append_jsonl_line", _fail_the_remote_submit_journal)
    provider = get_tts_provider("tts_x", transport=transport, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert "remote-777" in str(exc.value)  # the id is surfaced, not lost


def test_f8_tts_never_dispatches_when_evidence_cannot_be_written(
        monkeypatch, tmp_path, tmp_project, add_shot):
    """PROVIDER-SUBMISSION-001: unwritable evidence BEFORE the POST means the
    paid call is never made at all.

    This is the stronger half of finding 8. Surfacing the remote id was the best
    that ordering could do once the money was already spent; refusing to spend
    it is better, and it is only possible because the admission claim is now
    durable before the transport is entered. The transport must therefore never
    be called — no id to reconcile because there is no submission."""
    import manju.core.events as EV
    from manju.providers.base import ProviderFailure
    from manju.providers.tts import get_tts_provider

    calls = []

    def _transport(method, url, headers, body):
        calls.append((method, url))
        raise AssertionError("the paid POST must not be reached")

    _tts_env(monkeypatch, tmp_path,
             poll={"url": "https://api/tts/{job_id}", "status_path": "$.data.status",
                   "status_map": {"DONE": "succeeded"}})
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    monkeypatch.setattr(EV, "append_jsonl_line",
                        lambda *a, **k: (_ for _ in ()).throw(EV.EvidenceWriteError("io_error")))
    provider = get_tts_provider("tts_x", transport=_transport, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert calls == []  # nothing was sent, so nothing was billed
    assert "fail-closed admission gate" in str(exc.value)


# ============================================================ providers/asr.py


def test_f17_asr_segments_container_validated(monkeypatch, tmp_path):
    """Finding 17: `{"segments": null}` (and a non-list / non-mapping entry)
    must be a structured ProviderFailure, not a raw TypeError."""
    from manju.providers.asr import GenericAsrProvider
    from manju.providers.base import ProviderFailure
    from manju.providers.manifest import ProviderManifest

    monkeypatch.setenv("ASR_X_KEY", "k")
    m = ProviderManifest.model_validate({
        "id": "asr_x", "type": "asr", "adapter": "generic_asr",
        "auth": {"key_env": "ASR_X_KEY"},
        "submit": {"url": "https://api/asr", "body_template": {"a": "{audio_b64}"},
                   "job_id_path": "$.data.task_id"},
        "asr": {"segments_path": "$.data.segments", "text_key": "text",
                "start_key": "begin", "end_key": "end", "time_unit": "s"},
    })
    provider = GenericAsrProvider(m, transport=lambda *a: None, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure):
        provider._segments_from({"data": {"segments": None}})
    with pytest.raises(ProviderFailure):
        provider._segments_from({"data": {"segments": ["not-a-mapping"]}})
    # nonfinite timestamp → structured failure, never OverflowError
    with pytest.raises(ProviderFailure):
        provider._segments_from({"data": {"segments": [
            {"begin": 0.0, "end": float("inf"), "text": "x"}]}})


def test_f17_asr_segments_sorted_and_nonnegative(monkeypatch, tmp_path):
    """Finding 17: out-of-order / negative segments are normalized (sorted,
    clamped) rather than passed through as invalid timing."""
    from manju.providers.asr import GenericAsrProvider
    from manju.providers.manifest import ProviderManifest

    monkeypatch.setenv("ASR_X_KEY", "k")
    m = ProviderManifest.model_validate({
        "id": "asr_x", "type": "asr", "adapter": "generic_asr",
        "auth": {"key_env": "ASR_X_KEY"},
        "submit": {"url": "https://api/asr", "body_template": {"a": "{audio_b64}"},
                   "job_id_path": "$.data.task_id"},
        "asr": {"segments_path": "$.data.segments", "text_key": "text",
                "start_key": "begin", "end_key": "end", "time_unit": "ms"},
    })
    provider = GenericAsrProvider(m, transport=lambda *a: None, sleep_fn=lambda s: None)
    segs = provider._segments_from({"data": {"segments": [
        {"begin": 500, "end": 900, "text": "second"},
        {"begin": -50, "end": 400, "text": "first"},  # negative start
    ]}})
    assert [s.text for s in segs] == ["first", "second"]  # sorted
    assert all(s.start_ms >= 0 for s in segs)             # clamped


def test_f18_distribute_text_never_exceeds_media_duration():
    """Finding 18: cues must stay inside [0, duration_ms] and be monotonic even
    when the minimum spans/gaps cannot fit."""
    from manju.providers.asr import distribute_text

    segs = distribute_text("一。二。三", 1000)  # 3 pieces, tight 1s media
    assert len(segs) == 3
    prev_end = 0
    for s in segs:
        assert 0 <= s.start_ms < s.end_ms <= 1000, (s.start_ms, s.end_ms)
        assert s.start_ms >= prev_end - 1  # monotonic (gap may be 0 after scaling)
        prev_end = s.end_ms


# ============================================================ providers/manifest.py


def test_f15_tts_config_requires_exactly_one_audio_source():
    """Finding 15: setting BOTH audio_url_path and audio_b64_path is invalid
    (XOR), not silently accepted with base64 winning."""
    from manju.providers.manifest import ProviderManifest

    both = ProviderManifest.model_validate({
        "id": "tts_x", "type": "tts", "adapter": "generic_tts",
        "auth": {"key_env": "K"},
        "submit": {"url": "https://api/tts", "body_template": {"t": "{text}"},
                   "job_id_path": "$.id"},
        "tts": {"audio_url_path": "$.u", "audio_b64_path": "$.b"},
    })
    problems = both.validate_for_generic()
    assert any("EXACTLY one" in p for p in problems), problems


# ============================================================ core/hashing.py


def test_f20_toctou_gate_does_not_persist_a_mid_read_torn_digest(tmp_path, monkeypatch):
    """Finding 20 (TOCTOU angle): a file mutated DURING the streaming read must
    not be admitted to the memo under its pre-read (size, mtime) key."""
    import manju.core.hashing as H

    H._reset_hash_cache()
    f = tmp_path / "m.bin"
    f.write_bytes(b"AAAA")
    st_before = f.stat()

    real_stream = H._hash_file_stream

    def mutate_then_hash(path, chunk):
        # simulate a writer changing the file WHILE we read it
        Path(path).write_bytes(b"BBBBBB")
        return real_stream(path, chunk)

    monkeypatch.setattr(H, "_hash_file_stream", mutate_then_hash)
    H.hash_file(f)  # miss → mid-read change detected by the post-hash re-stat
    key = (str(f.resolve()), st_before.st_size, st_before.st_mtime_ns)
    assert key not in H._hash_cache  # the torn digest was NOT persisted


# ============================================================ core/series.py


def _make_series(tmp_path):
    from manju.core.series import Series
    from manju.core.yamlio import write_yaml

    series = Series.create(tmp_path / "剧集", name="剧集", git_init=False)
    write_yaml(series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "冷静"}})
    write_yaml(series.bible_dir / "scenes.yaml", {"store": {"name": "便利店"}})
    return series


def test_f21_concurrent_same_eid_never_corrupts_identity(tmp_path):
    """Finding 21: two concurrent new_episode calls with the SAME eid but
    different titles must not both scaffold — no series.yaml/project.yaml
    divergence, at most one success."""
    from manju.core.series import SeriesError, new_episode

    tmp_series = _make_series(tmp_path)

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def make(title):
        barrier.wait()
        try:
            results[title] = new_episode(tmp_series, "E01", title=title)
        except SeriesError as exc:
            results[title] = exc

    t1 = threading.Thread(target=make, args=("Title-A",))
    t2 = threading.Thread(target=make, args=("Title-B",))
    t1.start(); t2.start(); t1.join(10); t2.join(10)

    from manju.core.container import Project
    ep_dir = tmp_series.episode_project_dir("E01")
    assert (ep_dir / "project.yaml").exists()
    project_name = Project(ep_dir).load_config().name
    cfg = tmp_series.load_config()
    registered = [e for e in cfg.episodes if e.id == "E01"]
    assert len(registered) == 1                       # registered exactly once
    assert registered[0].title == project_name        # NO title/name divergence


def test_f22_registration_resumes_after_a_scaffold_without_register(tmp_path):
    """Finding 22: a directory scaffolded by a prior attempt whose register step
    failed is COMPLETED on retry (not dead-ended on 'already exists')."""
    from manju.core.container import Project
    from manju.core.series import new_episode

    tmp_series = _make_series(tmp_path)
    # First attempt scaffolds the dir but we simulate a lost registration by
    # creating the project dir directly, unregistered.
    ep_dir = tmp_series.episode_project_dir("E07")
    Project.create(ep_dir, name="E07", git_init=False)
    assert not any(e.id == "E07" for e in tmp_series.load_config().episodes)

    # retry completes the registration instead of erroring
    new_episode(tmp_series, "E07", title="E07")
    assert any(e.id == "E07" for e in tmp_series.load_config().episodes)
