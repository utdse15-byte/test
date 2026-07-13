"""P0 paid-submission safety — the WP0 injection tests (contract §9 1-5, 14 + WP1).

Each of these drives the ONE paid choke point and proves a fail-closed / fail-
earlier property the remediation adds:

* WP1 durable admission evidence — a PREPARED / DISPATCHING evidence append that
  cannot be made durable (coordinator failure, held lock, no reliable lock)
  REFUSES the paid submit; the transport is never reached and nothing is written
  unlocked.
* WP2 phase-aware disposition — inside the submit phase an unclassified
  ProviderFailure defaults to OUTCOME_UNKNOWN (no retry, fallback stops); the
  SAME failure raised from poll() keeps today's kind-based retry and never leaves
  ADMITTED for a retryable exhaustion.
* WP2 manifest-declared definite rejection — a post-send status is
  DEFINITELY_REJECTED only when the manifest declares it.

The helpers are the existing DR06 admission sandbox (scripted in-process cloud +
a transport probe counting requests) reused verbatim.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import manju.core.events as core_events
from manju.build import attempts as A
from manju.core.events import EvidenceWriteError
from manju.providers import submission as S
from manju.providers.base import (
    CloudProvider,
    FailureKind,
    GenerationRequest,
    ProviderFailure,
)
from manju.providers.generic_cloud import HttpResponse
from manju.runtime.state import RuntimeState

# Reuse the DR06 admission helpers verbatim (registry sandbox pattern, the
# scripted cloud, the transport probe, the manifest fixture builders).
from tests.test_dr06_admission import (
    RaisingTransport,
    ScriptedCloud,
    _gc_provider,
    _req,
    _states,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX only in CI
    fcntl = None


# --------------------------------------------------------------- coordinator probe


class _FailingCoordinator:
    """Wraps ``core.events.append_jsonl_line`` and fails the Nth call (1-based):
    raises :class:`EvidenceWriteError` in required mode, returns ``False`` in
    best-effort mode — exactly what a lock/io failure at the coordinator does."""

    def __init__(self, real, fail_on: int, reason: str = "lock_timeout"):
        self.real = real
        self.fail_on = fail_on
        self.reason = reason
        self.calls = 0

    def __call__(self, project_root, record, *, durable, required):
        self.calls += 1
        if self.calls == self.fail_on:
            if required:
                raise EvidenceWriteError(self.reason)
            return False
        return self.real(project_root, record, durable=durable, required=required)


def _patch_coordinator(monkeypatch, fail_on: int, reason: str = "lock_timeout"):
    real = core_events.append_jsonl_line
    monkeypatch.setattr(core_events, "append_jsonl_line",
                        _FailingCoordinator(real, fail_on, reason))


# ================================================= 1. PREPARED evidence fails


def test_prepared_evidence_fail_refuses_submit_zero_transport(tmp_project, add_shot, monkeypatch):
    """§9.1 / WP1: if the PREPARED evidence cannot be durably appended the paid
    submit REFUSES — transport count 0, ProviderFailure(submission_evidence_
    unavailable, NOT_DISPATCHED), and no DISPATCHING/ADMITTED event follows.

    RED at HEAD: emit only warned, submit() ran (transport==1), the job reached
    ADMITTED -> TERMINAL_SUCCESS."""
    shot = add_shot(tmp_project, "S001")
    _patch_coordinator(monkeypatch, fail_on=1)  # PREPARED is the 1st append
    provider = ScriptedCloud()

    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))

    assert provider.submit_calls == 0                       # transport never reached
    assert exc.value.detail.get("code") == "submission_evidence_unavailable"
    assert exc.value.disposition == S.NOT_DISPATCHED         # nothing was sent
    states = _states(tmp_project)
    assert S.DISPATCHING not in states and S.ADMITTED not in states
    assert states == []                                     # PREPARED never landed


# ================================================ 2. DISPATCHING evidence fails


def test_dispatching_evidence_fail_refuses_submit_zero_transport(tmp_project, add_shot, monkeypatch):
    """§9.2 / WP1: PREPARED lands, the DISPATCHING evidence append fails -> same
    fail-closed shape, transport count 0, no ADMITTED."""
    shot = add_shot(tmp_project, "S001")
    _patch_coordinator(monkeypatch, fail_on=2)  # PREPARED ok (1), DISPATCHING (2) fails
    provider = ScriptedCloud()

    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))

    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_evidence_unavailable"
    assert exc.value.disposition == S.NOT_DISPATCHED
    states = _states(tmp_project)
    assert states == [S.PREPARED]                           # DISPATCHING never landed
    assert S.ADMITTED not in states


# ==================================================== 3. lock timeout (required)


def _hold_events_lock(root: Path) -> int:
    """flock (LOCK_EX) the sibling events.lock from a SEPARATE fd so the
    coordinator (a distinct open file description) cannot acquire it."""
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(root / "events.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


@pytest.mark.skipif(fcntl is None, reason="POSIX flock required")
def test_lock_timeout_required_refuses_and_best_effort_drops(tmp_project, add_shot, monkeypatch):
    """§9.3 / WP1: a held events.lock times out the REQUIRED PREPARED append ->
    the paid path refuses (transport 0). And a best-effort append under the held
    lock DROPS the record (file unchanged) rather than writing unlocked."""
    monkeypatch.setattr(core_events, "DEFAULT_LOCK_TIMEOUT_S", 0.2)  # fast timeout
    shot = add_shot(tmp_project, "S001")
    held = _hold_events_lock(tmp_project.root)
    try:
        provider = ScriptedCloud()
        with pytest.raises(ProviderFailure) as exc:
            provider.generate(_req(tmp_project, shot))
        assert provider.submit_calls == 0
        assert exc.value.detail.get("code") == "submission_evidence_unavailable"
        assert exc.value.detail.get("reason") == "lock_timeout"

        # best-effort append under the SAME held lock: dropped, never torn/unlocked
        events_path = tmp_project.root / "events.jsonl"
        before = events_path.read_bytes() if events_path.exists() else b""
        core_events.append_event(tmp_project.root, "engine", "drop_me", {"x": 1})
        after = events_path.read_bytes() if events_path.exists() else b""
        assert after == before                              # nothing written unlocked
        assert b"drop_me" not in after
        assert A.append_attempt(tmp_project.root,
                                {"run_id": "r", "stage": "x", "action": "y",
                                 "state": A.SUCCEEDED}) == {}  # best-effort -> {}
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)


# ===================================== 4. submit ProviderFailure(timeout, None)


class _TimeoutNoDispCloud(CloudProvider):
    """submit() raises ProviderFailure(timeout) with NO disposition — the exact
    injection C: an adapter that did not classify its own submit outcome."""

    id = "tn"
    kind = "cloud"

    def __init__(self, **kw):
        kw.setdefault("sleep_fn", lambda _s: None)
        super().__init__(**kw)
        self.submit_calls = 0

    def submit(self, req):
        self.submit_calls += 1
        raise ProviderFailure(FailureKind.timeout, "read timed out mid-submit")

    def poll(self, job_id):  # pragma: no cover
        raise AssertionError("never polled")

    def download(self, job_id, dest):  # pragma: no cover
        raise AssertionError("never downloaded")


def test_submit_timeout_none_defaults_unknown_no_retry_no_fallback(tmp_project, add_shot):
    """§9.4 / WP2: a submit-phase ProviderFailure(timeout, disposition=None)
    defaults to OUTCOME_UNKNOWN — submit called exactly ONCE (no retry), the
    submission ends OUTCOME_UNKNOWN, and generate_with_fallback does NOT try the
    next provider. RED at HEAD: retried max_retries times (4 submit attempts)."""
    import manju.providers.registry as reg
    from manju.providers.registry import generate_with_fallback

    reg._ensure_builtins()
    saved = dict(reg._REGISTRY)
    tried: list[str] = []

    unknown = _TimeoutNoDispCloud()

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
        reg.register_provider(unknown)
        reg.register_provider(_Next())
        shot = add_shot(tmp_project, "S001")
        with pytest.raises(ProviderFailure) as exc:
            generate_with_fallback(_req(tmp_project, shot), ["tn", "nextp"])

        assert unknown.submit_calls == 1                     # exactly one attempt
        assert tried == []                                   # nextp NEVER tried
        assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
        assert exc.value.detail.get("disposition_defaulted") == "submit_phase"
        assert exc.value.kind is FailureKind.timeout          # kind stays honest
        assert _states(tmp_project)[-1] == S.OUTCOME_UNKNOWN
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)


# ================================= 5. poll ProviderFailure(timeout, None) retries


class _PollTimeoutNoDispCloud(ScriptedCloud):
    """submit() succeeds (ADMITTED); poll() raises ProviderFailure(timeout) with
    NO disposition — the phase that KEEPS today's kind-based retry."""

    def poll(self, job_id):
        self.poll_calls += 1
        raise ProviderFailure(FailureKind.timeout, "read timed out mid-poll")


def test_poll_timeout_none_retries_and_stays_admitted(tmp_project, add_shot):
    """§9.5 / WP2: the SAME ProviderFailure(timeout, None) raised from poll()
    (after a successful submit) still retries by kind — submit stays 1, the poll
    retries happen, and a retryable exhaustion leaves the submission ADMITTED
    (resume-safe: a later build re-polls, never resubmits). It must NOT default
    to OUTCOME_UNKNOWN (that is submit-phase only)."""
    shot = add_shot(tmp_project, "S001")
    provider = _PollTimeoutNoDispCloud(max_retries=2)

    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))

    assert provider.submit_calls == 1                        # submit NOT retried
    assert provider.poll_calls == 3                          # 1 + max_retries(2)
    assert exc.value.kind is FailureKind.timeout
    # submit-phase defaulting NEVER touched a poll-phase failure
    assert exc.value.disposition != S.OUTCOME_UNKNOWN_DISPOSITION
    states = _states(tmp_project)
    assert states[-1] == S.ADMITTED                          # resume-safe, not terminal
    assert S.OUTCOME_UNKNOWN not in states
    assert S.TERMINAL_FAILURE not in states


# ================================================= 6. no-fcntl platform (WP1)


def test_no_fcntl_platform_refuses_paid_and_drops_best_effort(tmp_project, add_shot, monkeypatch):
    """§9.14 / WP1: with no reliable file lock (fcntl unavailable) the REQUIRED
    PREPARED append refuses the paid submit (no_reliable_lock, transport 0), and
    a best-effort append DROPS the record (False/{}) rather than writing
    unlocked. (This CHANGES the old no-fcntl no-op-and-write.)

    Windows gate round 1 / DECISIONS #36: on a REAL Windows host msvcrt now
    provides the lock, so paid appends WORK there — this pin is about the
    no-primitive-at-all contract, so both coordinators are stubbed away."""
    monkeypatch.setattr(core_events, "fcntl", None)
    monkeypatch.setattr(core_events, "msvcrt", None, raising=False)
    shot = add_shot(tmp_project, "S001")

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_evidence_unavailable"
    assert exc.value.detail.get("reason") == "no_reliable_lock"

    # best-effort writers drop instead of writing unlocked
    events_path = tmp_project.root / "events.jsonl"
    before = events_path.read_bytes() if events_path.exists() else b""
    core_events.append_event(tmp_project.root, "engine", "drop_me")
    assert A.append_attempt(tmp_project.root,
                            {"run_id": "r", "stage": "x", "action": "y",
                             "state": A.SUCCEEDED}) == {}
    after = events_path.read_bytes() if events_path.exists() else b""
    assert after == before and b"drop_me" not in after


# =========================== 7. manifest-declared definite rejection (WP2)


def _submit_block(**extra):
    return {"url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
            "job_id_path": "$.data.task_id", **extra}


def _gc_req(tmp_project, add_shot, shot_id: str, **params):
    shot = add_shot(tmp_project, shot_id, generation={"candidates": 1})
    return GenerationRequest(
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="sha256:test", duration_ms=4000, candidates=1,
        params={"seed": 7, **params})


def _sub_state(project, shot_id: str) -> str | None:
    with RuntimeState(project.root) as st:
        rows = st.submissions(shot=shot_id, provider="video_x")
    return rows[0]["state"] if rows else None


def test_definite_rejection_is_manifest_declared_only(tmp_project, add_shot, monkeypatch):
    """§9 / WP2: a 400 is DEFINITELY_REJECTED (fallback allowed) ONLY when the
    manifest declares it; the SAME 400 WITHOUT the declaration is OUTCOME_UNKNOWN
    (the chain stops). A declared list never affects a PRE-SEND failure."""
    def r400():
        return HttpResponse(400, {}, json.dumps({"error": "bad"}).encode())

    # (a) declared [400] -> DEFINITELY_REJECTED -> REMOTE_REJECTED (fallback ok)
    prov_d = _gc_provider(RaisingTransport(None, [r400()]), monkeypatch,
                          submit=_submit_block(definite_rejection_statuses=[400]))
    with pytest.raises(ProviderFailure) as exc_d:
        prov_d.generate(_gc_req(tmp_project, add_shot, "S001"))
    assert exc_d.value.disposition == S.DEFINITELY_REJECTED
    assert _sub_state(tmp_project, "S001") == S.REMOTE_REJECTED

    # (b) SAME 400, NO declaration -> OUTCOME_UNKNOWN (chain stops)
    prov_u = _gc_provider(RaisingTransport(None, [r400()]), monkeypatch,
                          submit=_submit_block())
    with pytest.raises(ProviderFailure) as exc_u:
        prov_u.generate(_gc_req(tmp_project, add_shot, "S002"))
    assert exc_u.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
    assert _sub_state(tmp_project, "S002") == S.OUTCOME_UNKNOWN

    # (c) a declared list NEVER affects a pre-send failure: a missing key is
    #     provably-not-sent -> NOT_DISPATCHED, regardless of the declaration.
    prov_p = _gc_provider(RaisingTransport(None, []), monkeypatch,
                          submit=_submit_block(definite_rejection_statuses=[400]))
    monkeypatch.delenv("VIDEO_X_KEY", raising=False)
    with pytest.raises(ProviderFailure) as exc_p:
        prov_p.generate(_gc_req(tmp_project, add_shot, "S003"))
    assert exc_p.value.kind is FailureKind.invalid
    assert exc_p.value.disposition == S.NOT_DISPATCHED


# ============================ 8. redaction survives the coordinator move


def test_redaction_survives_coordinator_move(tmp_project):
    """WP1 constraint 6: moving the locked write into the coordinator must not
    change redaction — a submission event whose detail carries an Authorization
    value / signed URL is still scrubbed byte-identically (same policy as the
    DR03C attempt stream)."""
    secret = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    A.append_submission_event(
        tmp_project, submission_id="sub_red", request_digest="sha256:d",
        from_state=None, to_state=S.PREPARED, provider_id="p",
        detail={"Authorization": f"Bearer {secret}",
                "api_key": secret,
                "result_url": "https://cdn.example.com/x.mp4?X-Amz-Signature=deadbeef&e=9"})

    blob = (tmp_project.root / "events.jsonl").read_text(encoding="utf-8")
    assert secret not in blob                                # credential never leaks
    assert "X-Amz-Signature=deadbeef" not in blob            # signed-url query stripped

    recs, _ = A.read_submission_events(tmp_project, "sub_red")
    detail = recs[0]["detail"]
    assert detail["Authorization"] == "<redacted>"
    assert detail["api_key"] == "<redacted>"
    assert detail["result_url"].endswith("?<redacted>")


# =============================== 9. REDISPATCH DISPATCHING evidence (Fable review)


class _RetryableNotDispatchedCloud(CloudProvider):
    """submit() #1 raises an EXPLICIT pre-send refusal (NOT_DISPATCHED,
    kind=rate_limited => retryable); submit() #2 would succeed. The retry
    re-enters DISPATCHING via _redispatch — whose evidence append is just as
    load-bearing as the first attempt's (WP1: DISPATCHING evidence before EVERY
    paid submit, constraint 4)."""

    id = "rn"
    kind = "cloud"

    def __init__(self, **kw):
        kw.setdefault("sleep_fn", lambda _s: None)
        super().__init__(**kw)
        self.submit_calls = 0

    def submit(self, req):
        self.submit_calls += 1
        if self.submit_calls == 1:
            raise ProviderFailure(
                FailureKind.rate_limited, "engine-side pre-send refusal",
                disposition=S.NOT_DISPATCHED)
        return "job-redispatch-1"

    def poll(self, job_id):
        return "succeeded", {}

    def download(self, job_id, dest):  # pragma: no cover - not reached here
        return []


def test_redispatch_dispatching_evidence_fail_refuses_second_submit(
        tmp_project, add_shot, monkeypatch):
    """WP1 (Fable review find): the REDISPATCH path's DISPATCHING evidence must
    be as durable as the first attempt's. Appends: PREPARED(1), DISPATCHING(2),
    REJECTED_PRE_DISPATCH classify(3), redispatch DISPATCHING(4). Failing #4 must
    refuse the SECOND transport call.

    RED at A1's tree: transition() emitted best-effort -> warning -> submit #2
    ran (submit_calls == 2)."""
    shot = add_shot(tmp_project, "S001")
    _patch_coordinator(monkeypatch, fail_on=4)
    provider = _RetryableNotDispatchedCloud()

    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))

    assert provider.submit_calls == 1                # the retry never re-submitted
    assert exc.value.detail.get("code") == "submission_evidence_unavailable"
    assert exc.value.disposition == S.NOT_DISPATCHED
    states = _states(tmp_project)
    assert states.count(S.DISPATCHING) == 1          # the redispatch never landed
