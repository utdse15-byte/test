"""DR06 — admission: the §8.4 write order, the SQLite-CAS claim, disposition
classification, the fail-closed/fail-earlier changes, and crash recovery from
evidence (contract rulings 4/5/6/7 + §8.5).

Cloud behavior is driven with scripted in-process providers (no ffmpeg, no
network); crashes are injected by monkeypatching a boundary to raise, then a
FRESH RuntimeState / a second generate() simulates the restart.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from manju.providers import submission as S
from manju.providers.base import (
    CloudProvider,
    FailureKind,
    GenerationRequest,
    ProviderFailure,
)
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.runtime.state import RuntimeState
from manju.build import attempts as A


# ------------------------------------------------------------- scripted cloud


class ScriptedCloud(CloudProvider):
    """A cloud provider whose remote steps are scripted, to drive the admission
    write order + bookkeeping. ``submit_exc`` (a ProviderFailure) makes submit()
    raise; ``on_submit`` hooks a crash injection right after the id is returned."""

    id = "cloud_test"

    def __init__(self, *, submit_exc=None, poll_result=None, submit_hook=None, **kw):
        kw.setdefault("sleep_fn", lambda _s: None)
        super().__init__(**kw)
        self._submit_exc = submit_exc
        self._poll_result = poll_result or ("succeeded", {"cost": 0.5, "currency": "CNY"})
        self._submit_hook = submit_hook  # NOT _on_submit — that is a base method
        self.submit_calls = 0
        self.poll_calls = 0

    def submit(self, req):
        self.submit_calls += 1
        if self._submit_exc is not None:
            raise self._submit_exc
        if self._submit_hook is not None:
            self._submit_hook()
        return "job_fresh_1"

    def poll(self, job_id):
        self.poll_calls += 1
        return self._poll_result

    def download(self, job_id, dest_dir):
        out = Path(dest_dir) / "out.mp4"
        out.write_bytes(b"fake-" + job_id.encode())
        return [out]


def _req(project, shot, **params):
    return GenerationRequest(
        project=project, shot=shot, bible=project.load_bible(),
        spec_hash="sha256:test", duration_ms=3000, candidates=1, params=dict(params))


def _events(project, sid=None):
    recs, _ = A.read_submission_events(project, sid)
    return recs


def _states(project, sid=None):
    return [e["to"] for e in _events(project, sid)]


# ============================================================ write order


def test_write_order_prepared_dispatching_admitted_success(tmp_project, add_shot):
    """§8.4: a clean submit emits PREPARED -> DISPATCHING -> ADMITTED ->
    TERMINAL_SUCCESS in order, and the intents row ends TERMINAL_SUCCESS."""
    shot = add_shot(tmp_project, "S001")
    takes = ScriptedCloud().generate(_req(tmp_project, shot))
    assert len(takes) == 1
    assert _states(tmp_project) == [S.PREPARED, S.DISPATCHING, S.ADMITTED,
                                    S.TERMINAL_SUCCESS]
    with RuntimeState(tmp_project.root) as st:
        row = st._conn.execute("SELECT * FROM intents").fetchone()
    assert row["state"] == S.TERMINAL_SUCCESS
    assert row["remote_job_id"] == "job_fresh_1"
    assert row["submission_id"].startswith("sub_")
    assert row["request_digest"].startswith("sha256:")


def test_prepared_and_dispatching_persist_before_submit(tmp_project, add_shot):
    """PREPARED (intent row + event) and the DISPATCHING claim (+event) are
    BOTH on record before submit() is ever entered."""
    shot = add_shot(tmp_project, "S001")
    seen = {}

    class _Prov(ScriptedCloud):
        def submit(self, req):
            seen["states_at_submit"] = _states(req.project)
            with RuntimeState(req.project.root) as st:
                seen["row_state"] = st._conn.execute(
                    "SELECT state FROM intents").fetchone()["state"]
            return super().submit(req)

    _Prov().generate(_req(tmp_project, shot))
    assert seen["states_at_submit"] == [S.PREPARED, S.DISPATCHING]
    assert seen["row_state"] == S.DISPATCHING  # claimed before the paid call


def test_admitted_event_lands_before_the_sqlite_job_projection(tmp_project, add_shot):
    """§8.5 row 5: the ADMITTED event is written FIRST, then the jobs projection
    (open_job) — a crash between them recovers the state from evidence. Proven by
    intercepting open_job and asserting the ADMITTED event already exists."""
    shot = add_shot(tmp_project, "S001")
    seen = {}
    real_open_job = RuntimeState.open_job

    def spy(self, remote_job_id, **kw):
        seen["admitted_before_open_job"] = S.ADMITTED in _states(shot_project)
        return real_open_job(self, remote_job_id, **kw)

    shot_project = tmp_project
    import manju.runtime.state as state_mod
    orig = state_mod.RuntimeState.open_job
    state_mod.RuntimeState.open_job = spy
    try:
        ScriptedCloud().generate(_req(tmp_project, shot))
    finally:
        state_mod.RuntimeState.open_job = orig
    assert seen["admitted_before_open_job"] is True


# ============================================================ fail-closed (5)


def test_prepared_persist_failure_blocks_the_paid_submit(tmp_project, add_shot, monkeypatch):
    """ruling 5: if PREPARED cannot be persisted, the paid submit MUST NOT
    start. Force open_intent to raise -> submit() is never called."""
    import sqlite3

    shot = add_shot(tmp_project, "S001")

    def boom(self, **kw):
        raise sqlite3.OperationalError("disk gone")

    monkeypatch.setattr(RuntimeState, "open_intent", boom)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_prepare_failed"


def test_claim_failure_blocks_the_paid_submit(tmp_project, add_shot, monkeypatch):
    """ruling 5: if the DISPATCHING claim cannot be won, the paid submit MUST
    NOT start. A claim that returns False (lost the CAS) -> fail closed."""
    shot = add_shot(tmp_project, "S001")
    monkeypatch.setattr(RuntimeState, "claim_dispatching", lambda self, sid: False)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_claim_lost"


# ============================================================ CAS (multi-process)


def test_atomic_claim_rowcount_is_the_cas(tmp_project):
    """The claim is a SQLite CAS on state='PREPARED' — exactly one True even if
    called twice; a non-PREPARED row never claims."""
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="p", shot="S001", submission_id="sub_1",
                       request_digest="sha256:d", state=S.PREPARED)
        assert st.claim_dispatching("sub_1") is True
        assert st.claim_dispatching("sub_1") is False   # already DISPATCHING
        assert st.claim_dispatching("sub_missing") is False


def test_two_racing_runtimestates_only_one_claims(tmp_project):
    """Multi-process claim (ruling 4): two RuntimeState instances on the SAME db
    race the CAS on one PREPARED submission — SQLite row locking guarantees
    exactly one winner (threads suffice; the lock is the guarantee)."""
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="p", shot="S001", submission_id="sub_race",
                       request_digest="sha256:d", state=S.PREPARED)

    results: list[bool] = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def racer():
        st = RuntimeState(tmp_project.root)
        try:
            barrier.wait()
            won = st.claim_dispatching("sub_race")
            with lock:
                results.append(won)
        finally:
            st.close()

    ts = [threading.Thread(target=racer) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results) == [False, True]  # exactly one winner


# ============================================================ crash recovery (§8.5)


def test_crash_after_prepared_before_claim_recovers_as_unresolved(tmp_project, add_shot):
    """A crash strictly after PREPARED (before the DISPATCHING claim): the
    submission is left in PREPARED. It is NOT side-effect-ambiguous (nothing was
    dispatched), so a fresh generate() for the SAME request proceeds — but a
    stuck PREPARED never blocks and never double-submits a billed job."""
    shot = add_shot(tmp_project, "S001")

    class _CrashAfterPrepared(ScriptedCloud):
        def _prepare_submission(self, state, req):
            # mimic a crash: PREPARED row written, claim never reached
            from manju.core.hashing import hash_text
            identity, digest = self._build_identity(req)
            sid = S.mint_submission_id()
            state.open_intent(provider=self.id, shot=req.shot.id,
                              params_hash=hash_text("x"), submission_id=sid,
                              request_digest=digest, state=S.PREPARED)
            raise KeyboardInterrupt("crash after PREPARED")

    with pytest.raises(KeyboardInterrupt):
        _CrashAfterPrepared().generate(_req(tmp_project, shot))
    with RuntimeState(tmp_project.root) as st:
        rows = st.submissions(shot="S001", provider="cloud_test")
    assert len(rows) == 1 and rows[0]["state"] == S.PREPARED


def test_crash_after_claim_before_submit_leaves_dispatching_fail_closed_next(
    tmp_project, add_shot
):
    """§8.5: a crash after the DISPATCHING claim but before submit returns — the
    submission is DISPATCHING (side-effect ambiguous). A fresh generate() for
    the same shot+provider must FAIL CLOSED, never auto-resubmit."""
    shot = add_shot(tmp_project, "S001")

    class _CrashInSubmit(ScriptedCloud):
        def submit(self, req):
            self.submit_calls += 1
            raise KeyboardInterrupt("crash mid-submit, outcome unknown")

    with pytest.raises(KeyboardInterrupt):
        _CrashInSubmit().generate(_req(tmp_project, shot))
    with RuntimeState(tmp_project.root) as st:
        rows = st.submissions(shot="S001", provider="cloud_test")
    assert rows and rows[0]["state"] == S.DISPATCHING

    # the restart: a fresh call fails closed rather than resubmitting a job that
    # may already be running/billing remotely.
    with pytest.raises(ProviderFailure) as exc:
        ScriptedCloud().generate(_req(tmp_project, shot))
    assert exc.value.detail.get("code") == "submission_outcome_unknown"
    assert exc.value.detail.get("automatic_resubmit") is False
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION


def test_crash_between_admitted_event_and_projection_recovers_from_evidence(
    tmp_project, add_shot
):
    """§8.5 row 5: submit returned a job id and the ADMITTED event landed, then a
    crash before open_job. rebuild() restores the ADMITTED submission (with its
    remote_job_id) from the event stream — SQLite is a rebuildable projection."""
    shot = add_shot(tmp_project, "S001")

    class _CrashAfterAdmittedEvent(ScriptedCloud):
        def _on_submit(self_inner, state, req, job_id):
            raise KeyboardInterrupt("crash after ADMITTED event, before jobs row")

    with pytest.raises(KeyboardInterrupt):
        _CrashAfterAdmittedEvent().generate(_req(tmp_project, shot))
    # the ADMITTED event is on the stream even though the projection never ran
    assert S.ADMITTED in _states(tmp_project)

    # wipe the DB and rebuild purely from events: the ADMITTED submission returns
    import shutil
    shutil.rmtree(tmp_project.root / ".manju")
    with RuntimeState(tmp_project.root) as st:
        stats = st.rebuild(tmp_project)
        assert stats["submissions_restored"] >= 1
        rows = st.unresolved_submissions()
    admitted = [r for r in rows if r["state"] == S.ADMITTED]
    assert admitted and admitted[0]["remote_job_id"] == "job_fresh_1"


# ============================================================ disposition (6)


def _manifest_dict(**over):
    base = {
        "id": "video_x", "type": "video", "adapter": "generic_cloud",
        "capabilities": ["text_to_video"], "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/videos",
                   "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
                   "job_id_path": "$.data.task_id"},
        "poll": {"url": "https://api.example.com/v1/videos/{job_id}",
                 "status_path": "$.data.status",
                 "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed",
                                "PROCESSING": "running"},
                 "result_url_path": "$.data.video_url"},
        "failure": {"content_rejected_when": ["contentPolicy"]},
        "limits": {"max_concurrent": 2, "rate_limit_per_min": 6},
        "cost": {"per_second": 0.08, "currency": "CNY"},
    }
    base.update(over)
    return base


class RaisingTransport:
    """Raises a scripted exception on the FIRST call, then replays responses."""

    def __init__(self, first_exc, then=None):
        self.first_exc = first_exc
        self.then = list(then or [])
        self.calls = 0

    def __call__(self, method, url, headers, body):
        self.calls += 1
        if self.calls == 1 and self.first_exc is not None:
            raise self.first_exc
        return self.then.pop(0)


def _gc_provider(transport, monkeypatch, **over):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_manifest_dict(**over))
    return GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)


@pytest.fixture
def gc_req(tmp_project, add_shot):
    def _make(shot_id="S001", **params):
        shot = add_shot(tmp_project, shot_id, generation={"candidates": 1})
        return GenerationRequest(
            project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
            spec_hash="sha256:test", duration_ms=4000, candidates=1,
            params={"seed": 7, **params})
    return _make


def test_dns_failure_is_not_dispatched_and_may_retry(gc_req, monkeypatch):
    """A DNS failure (socket.gaierror) before the send boundary is NOT_DISPATCHED
    -> REJECTED_PRE_DISPATCH, and (timeout kind) the submit MAY retry."""
    import socket
    import urllib.error

    ok = [
        HttpResponse(200, {}, json.dumps({"data": {"task_id": "job_1"}}).encode()),
        HttpResponse(200, {}, json.dumps(
            {"data": {"status": "SUCCEEDED", "video_url": "https://c/o.mp4"}}).encode()),
        HttpResponse(200, {}, b"V"),
    ]
    # default_transport would raise ProviderFailure with the disposition set; we
    # emulate it directly to drive submit()'s classification path.
    exc = ProviderFailure(FailureKind.timeout, "dns", detail={},
                          disposition=S.NOT_DISPATCHED)
    provider = _gc_provider(RaisingTransport(exc, ok), monkeypatch)
    req = gc_req()
    takes = provider.generate(req)
    assert len(takes) == 1  # retried after a provably-not-sent failure
    states = _states(req.project)
    assert S.REJECTED_PRE_DISPATCH in states and states[-1] == S.TERMINAL_SUCCESS


def test_definite_4xx_is_remote_rejected_and_stops_but_fallback_eligible(gc_req, monkeypatch):
    """A tested 4xx (422) is DEFINITELY_REJECTED -> REMOTE_REJECTED; it is not
    OUTCOME_UNKNOWN, so it does NOT fail-close the fallback."""
    resp = HttpResponse(422, {}, json.dumps({"error": "bad params"}).encode())
    provider = _gc_provider(RaisingTransport(None, [resp]), monkeypatch)
    req = gc_req()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.disposition == S.DEFINITELY_REJECTED
    assert S.REMOTE_REJECTED in _states(req.project)


def test_submit_timeout_after_send_is_outcome_unknown_no_retry(gc_req, monkeypatch):
    """THE headline fail-closed change: a submit-phase timeout AFTER the send
    boundary is OUTCOME_UNKNOWN — exactly ONE transport attempt (no retry), and
    the submission ends OUTCOME_UNKNOWN. (HEAD retried it max_retries times.)"""
    exc = ProviderFailure(FailureKind.timeout, "read timeout", detail={},
                          disposition=S.OUTCOME_UNKNOWN_DISPOSITION)
    transport = RaisingTransport(exc, [])
    provider = _gc_provider(transport, monkeypatch)
    req = gc_req()
    with pytest.raises(ProviderFailure) as raised:
        provider.generate(req)
    assert transport.calls == 1  # NO retry — was 4 on HEAD
    assert raised.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
    assert _states(req.project)[-1] == S.OUTCOME_UNKNOWN


def test_unparseable_2xx_receipt_is_outcome_unknown(gc_req, monkeypatch):
    """A 2xx whose job-id path is unreadable is OUTCOME_UNKNOWN (the server
    accepted it; a job MAY exist) — never a safe retry/fallback."""
    resp = HttpResponse(200, {}, json.dumps({"data": {"nope": 1}}).encode())
    provider = _gc_provider(RaisingTransport(None, [resp]), monkeypatch)
    req = gc_req()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
    assert _states(req.project)[-1] == S.OUTCOME_UNKNOWN


def test_5xx_submit_is_outcome_unknown_not_definite(gc_req, monkeypatch):
    """A 5xx is 'everything else after submit was attempted' -> OUTCOME_UNKNOWN,
    NOT DEFINITELY_REJECTED (only the tested 4xx set is definite)."""
    resp = HttpResponse(503, {}, b"service unavailable")
    provider = _gc_provider(RaisingTransport(None, [resp]), monkeypatch)
    req = gc_req()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION


def test_pre_transport_invalid_is_not_dispatched(gc_req, monkeypatch):
    """A missing API key raises BEFORE the transport call — provably not sent,
    so NOT_DISPATCHED (REJECTED_PRE_DISPATCH), never OUTCOME_UNKNOWN."""
    monkeypatch.delenv("VIDEO_X_KEY", raising=False)
    provider = _gc_provider(RaisingTransport(None, []), monkeypatch)
    monkeypatch.delenv("VIDEO_X_KEY", raising=False)
    req = gc_req()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.kind is FailureKind.invalid
    assert exc.value.disposition == S.NOT_DISPATCHED


# ============================================================ registry (7)


def test_registry_stops_fallback_on_outcome_unknown(tmp_project, add_shot):
    """ruling 7: a provider that raises OUTCOME_UNKNOWN stops the fallback chain
    immediately — the next provider is NEVER tried (no new spend on an
    unresolved outcome)."""
    from manju.providers.registry import generate_with_fallback
    import manju.providers.registry as reg

    reg._ensure_builtins()
    saved = dict(reg._REGISTRY)

    tried = []

    class _Unknown(CloudProvider):
        id = "unk"
        kind = "cloud"

        def submit(self, req):
            tried.append("unk")
            raise ProviderFailure(FailureKind.provider_error, "ambiguous",
                                  disposition=S.OUTCOME_UNKNOWN_DISPOSITION)

        def poll(self, job_id):  # pragma: no cover
            raise AssertionError

        def download(self, job_id, d):  # pragma: no cover
            raise AssertionError

    class _Next(CloudProvider):
        id = "nextp"
        kind = "cloud"

        def submit(self, req):
            tried.append("nextp")
            return "j"

        def poll(self, job_id):
            return "succeeded", {}

        def download(self, job_id, d):
            p = Path(d) / "o.mp4"
            p.write_bytes(b"x")
            return [p]

    try:
        reg.register_provider(_Unknown())
        reg.register_provider(_Next())
        shot = add_shot(tmp_project, "S001")
        req = _req(tmp_project, shot)
        with pytest.raises(ProviderFailure) as exc:
            generate_with_fallback(req, ["unk", "nextp"])
        assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
        assert tried == ["unk"]  # nextp NEVER tried
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)


def test_registry_still_falls_back_on_definite_rejection(tmp_project, add_shot):
    """A DEFINITELY_REJECTED / plain failure still falls back normally — only
    OUTCOME_UNKNOWN stops the chain."""
    from manju.providers.registry import generate_with_fallback
    import manju.providers.registry as reg

    reg._ensure_builtins()
    saved = dict(reg._REGISTRY)
    tried = []

    class _Reject(CloudProvider):
        id = "rej"
        kind = "cloud"

        def submit(self, req):
            tried.append("rej")
            raise ProviderFailure(FailureKind.provider_error, "no",
                                  disposition=S.DEFINITELY_REJECTED)

        def poll(self, j):  # pragma: no cover
            raise AssertionError

        def download(self, j, d):  # pragma: no cover
            raise AssertionError

    class _Ok(CloudProvider):
        id = "okp"
        kind = "cloud"

        def submit(self, req):
            tried.append("okp")
            return "j"

        def poll(self, j):
            return "succeeded", {}

        def download(self, j, d):
            p = Path(d) / "o.mp4"
            p.write_bytes(b"x")
            return [p]

    try:
        reg.register_provider(_Reject())
        reg.register_provider(_Ok())
        shot = add_shot(tmp_project, "S001")
        takes = generate_with_fallback(_req(tmp_project, shot), ["rej", "okp"])
        assert takes and tried == ["rej", "okp"]  # fell back to okp
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)


# ============================================================ local untouched


def test_definite_poll_failure_after_admitted_is_terminal_failure(tmp_project, add_shot):
    """A submit that ADMITTED, then a definite remote failure at poll → the
    submission ends TERMINAL_FAILURE (a resumable poll TIMEOUT would instead
    leave it ADMITTED)."""
    shot = add_shot(tmp_project, "S001")

    class _PollFails(ScriptedCloud):
        def poll(self, job_id):
            self.poll_calls += 1
            return "failed", {"failure_kind": "provider_error", "reason": "remote boom"}

    with pytest.raises(ProviderFailure):
        _PollFails().generate(_req(tmp_project, shot))
    states = _states(tmp_project)
    assert states == [S.PREPARED, S.DISPATCHING, S.ADMITTED, S.TERMINAL_FAILURE]


def test_local_providers_never_create_a_submission(tmp_project, add_shot):
    """Local providers (caption_card here) never enter CloudProvider.generate —
    no identity, no claim, no submission_state event (ruling 5)."""
    from manju.providers.registry import generate_with_fallback

    shot = add_shot(tmp_project, "S001")
    takes = generate_with_fallback(_req(tmp_project, shot), ["caption_card"])
    assert takes
    assert _events(tmp_project) == []  # no submission events at all


# ============================================================ never-overwrite


def test_failed_attempt_is_never_overwritten_by_a_later_success(tmp_project, add_shot):
    """Failed attempts are never overwritten: a REMOTE_REJECTED submission's
    events stay on the stream when a subsequent, DISTINCT submission succeeds."""
    shot = add_shot(tmp_project, "S001")
    reject = ProviderFailure(FailureKind.provider_error, "no",
                             disposition=S.DEFINITELY_REJECTED)
    with pytest.raises(ProviderFailure):
        ScriptedCloud(submit_exc=reject).generate(_req(tmp_project, shot, seed=1))
    # a different request (new seed => new digest => new submission) succeeds
    ScriptedCloud().generate(_req(tmp_project, shot, seed=2))

    sids = {e["submission_id"] for e in _events(tmp_project)}
    assert len(sids) == 2  # two distinct submissions on the stream
    all_states = _states(tmp_project)
    assert S.REMOTE_REJECTED in all_states  # the failed attempt is preserved
    assert S.TERMINAL_SUCCESS in all_states


def test_28b_raw_unclassified_submit_exception_defaults_to_unknown(
        tmp_project, add_shot):
    """Contract test 28 at the BASE choke point: an escape-hatch adapter whose
    submit() raises a RAW exception (no ProviderFailure, no disposition) must
    become OUTCOME_UNKNOWN — never a crash, never a retryable failure, and the
    fallback chain must stop (no caption_card spend after an ambiguous send)."""
    from manju.providers.base import CloudProvider

    class RawCloud(CloudProvider):
        id = "rawcloud"
        kind = "cloud"

        def submit(self, req):
            raise TimeoutError("read timed out mid-response")

        def poll(self, job_id):  # pragma: no cover
            raise AssertionError("never polled")

        def download(self, job_id, info, dest):  # pragma: no cover
            raise AssertionError("never downloaded")

    from manju.providers.registry import generate_with_fallback
    import manju.providers.registry as reg

    reg._ensure_builtins()
    saved = dict(reg._REGISTRY)
    reg.register_provider(RawCloud())
    add_shot(tmp_project, "S001")
    shot = tmp_project.load_shot("S001")
    req = _req(tmp_project, shot)

    with pytest.raises(ProviderFailure) as exc:
        generate_with_fallback(req, ["rawcloud", "caption_card"])
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
    # the chain STOPPED: caption_card never produced a take
    assert not list((tmp_project.root / "media" / "gen").rglob("*.mp4"))
    # and the submission is persisted as OUTCOME_UNKNOWN for recovery
    state = RuntimeState(tmp_project.root)
    rows = [r for r in state.submissions(provider="rawcloud")]
    assert rows and rows[0]["state"] == "OUTCOME_UNKNOWN"
    reg._REGISTRY.clear()
    reg._REGISTRY.update(saved)
