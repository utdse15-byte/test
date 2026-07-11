"""P0 WP3 — fail-closed recovery + chain projection (contract §5.1/§5.2, §9 6-8).

RED at HEAD 76be954 (recorded before the fix):

* D — ``state.submissions`` raising during the consult was swallowed
  (``subs = []``) → the correlation was silently skipped → FRESH paid submit,
  transport submit count 1.
* E — ``_build_identity`` raising during the consult was swallowed
  (``digest = None``) → the ADMITTED match/conflict checks were skipped → a
  fresh submit sailed PAST an in-flight ADMITTED job, transport submit count 1.
* F — rebuild restored "latest event wins": a valid PREPARED→DISPATCHING prefix
  with a corrupt TERMINAL_SUCCESS tail restored NOTHING (0 rows, treated
  resolved) → fresh submit, transport 1. A totally corrupt chain restored
  nothing either (no sentinel).

Now: consult failures raise submission_recovery_unavailable (OUTCOME_UNKNOWN
disposition, transport 0, fallback stops); rebuild projects the LONGEST VALID
PREFIX via providers.submission.project_chain and restores the
RECOVERY_EVIDENCE_CORRUPT sentinel when no prefix is valid.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from manju.build import attempts as A
from manju.providers import submission as S
from manju.providers.base import CloudProvider, FailureKind, ProviderFailure
from manju.runtime.state import RuntimeState
from tests.test_dr06_admission import ScriptedCloud, _req, _states


# ------------------------------------------------------------------ helpers


def _seed_valid_chain(project, sid, *states_and_jobs, digest="sha256:d"):
    """Append a VALID hash chain for ``sid``: states_and_jobs is a sequence of
    (to_state, remote_job_id) pairs; from_state threads automatically."""
    prev_digest = None
    prev_state = None
    events = []
    for to_state, job in states_and_jobs:
        rec = A.append_submission_event(
            project, submission_id=sid, request_digest=digest,
            from_state=prev_state, to_state=to_state, provider_id="cloud_test",
            shot="S001", remote_job_id=job, prev_event_digest=prev_digest)
        prev_digest = S.submission_event_digest(rec)
        prev_state = to_state
        events.append(rec)
    return events


def _corrupt_line(project, sid, to_state, *, digest="sha256:d",
                  prev="sha256:bogus"):
    """Append ONE chain-breaking event (bad prev_event_digest)."""
    return A.append_submission_event(
        project, submission_id=sid, request_digest=digest, from_state=None,
        to_state=to_state, provider_id="cloud_test", shot="S001",
        prev_event_digest=prev)


# ============================== 1 (WP0-E). identity failure during the consult


def test_consult_identity_failure_fails_closed_zero_transport(tmp_project, add_shot, monkeypatch):
    """§9.6 / WP3 §5.1: identity computation raising during the consult is a
    structured submission_recovery_unavailable — transport 0, and NO fresh
    submission row is minted. RED at HEAD: with an ADMITTED job in flight, the
    consult swallowed the error and FRESH-submitted past it (transport 1)."""
    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_adm",
                       request_digest="sha256:prior", state=S.PREPARED)
        st.set_submission_state("sub_adm", S.ADMITTED, remote_job_id="job_prior")

    real = CloudProvider._build_identity
    calls = {"n": 0}

    def flaky(self, req):
        calls["n"] += 1
        if calls["n"] == 1:  # fail ONLY during the consult
            raise RuntimeError("identity exploded")
        return real(self, req)

    monkeypatch.setattr(CloudProvider, "_build_identity", flaky)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))

    assert provider.submit_calls == 0                       # transport never reached
    d = exc.value.detail
    assert d.get("code") == "submission_recovery_unavailable"
    assert d.get("stage") == "identity"
    assert d.get("automatic_resubmit") is False
    assert d.get("possible_remote_side_effect") is True
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
    with RuntimeState(tmp_project.root) as st:
        rows = st.submissions(shot="S001", provider="cloud_test")
    assert [r["submission_id"] for r in rows] == ["sub_adm"]  # no fresh row


# ============================ 2 (WP0-D). state query failure during the consult


def test_consult_state_query_failure_fails_closed_zero_transport(tmp_project, add_shot, monkeypatch):
    """§9.7 / WP3 §5.1: state.submissions raising during the consult is the same
    structured failure with stage=state_query, transport 0. RED at HEAD: the
    raise was swallowed (subs=[]) and a fresh paid submit proceeded (transport 1,
    take produced)."""
    import sqlite3

    shot = add_shot(tmp_project, "S001")

    def boom(self, **kw):
        raise sqlite3.OperationalError("db locked")

    monkeypatch.setattr(RuntimeState, "submissions", boom)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))

    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_recovery_unavailable"
    assert exc.value.detail.get("stage") == "state_query"
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION


def test_recovery_unavailable_stops_the_fallback_chain(tmp_project, add_shot, monkeypatch):
    """WP3 §5.1: a recovery_unavailable failure carries OUTCOME_UNKNOWN, so the
    registry never falls back to provider B (no new spend while prior outcomes
    are unverifiable)."""
    import sqlite3

    import manju.providers.registry as reg
    from manju.providers.registry import generate_with_fallback

    monkeypatch.setattr(RuntimeState, "submissions",
                        lambda self, **kw: (_ for _ in ()).throw(
                            sqlite3.OperationalError("db locked")))
    reg._ensure_builtins()
    saved = dict(reg._REGISTRY)
    tried: list[str] = []

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
        reg.register_provider(ScriptedCloud())
        reg.register_provider(_Next())
        shot = add_shot(tmp_project, "S001")
        with pytest.raises(ProviderFailure) as exc:
            generate_with_fallback(_req(tmp_project, shot), ["cloud_test", "nextp"])
        assert exc.value.detail.get("code") == "submission_recovery_unavailable"
        assert tried == []                                  # provider B never tried
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)


# ==================== 3 (WP0-F). corrupt tail: rebuild = longest valid prefix


def test_rebuild_restores_last_valid_prefix_and_blocks_fresh_submit(tmp_project, add_shot):
    """§9.8 / WP3 §5.2: valid PREPARED→DISPATCHING + ONE corrupt third line,
    SQLite deleted → rebuild restores the submission in DISPATCHING (the last
    valid prefix tail — never "last event wins"), the chain reports corrupt, and
    a fresh generate for the same shot+provider fail-closes with transport 0.

    RED at HEAD: rebuild restored 0 rows (the corrupt TERMINAL_SUCCESS tail was
    trusted as resolved) and a fresh generate submitted again (transport 1)."""
    shot = add_shot(tmp_project, "S001")
    _seed_valid_chain(tmp_project, "sub_f",
                      (S.PREPARED, None), (S.DISPATCHING, None))
    _corrupt_line(tmp_project, "sub_f", S.TERMINAL_SUCCESS)  # forged resolution

    events, _ = A.read_submission_events(tmp_project, "sub_f")
    proj = S.project_chain(events)
    assert proj["state"] == S.DISPATCHING                    # prefix tail wins
    assert proj["corrupt"] is True and proj["last_valid_index"] == 1

    shutil.rmtree(tmp_project.root / ".manju")
    with RuntimeState(tmp_project.root) as st:
        stats = st.rebuild(tmp_project)
        rows = st.unresolved_submissions()
    assert stats["submissions_restored"] == 1
    assert rows and rows[0]["submission_id"] == "sub_f"
    assert rows[0]["state"] == S.DISPATCHING

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0                        # no fresh paid submit
    # the consult re-verifies the chain from evidence and names the corruption
    assert exc.value.detail.get("code") == "RECOVERY_EVIDENCE_CORRUPT"
    assert exc.value.detail.get("automatic_resubmit") is False
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION


# ================== 4. totally corrupt chain: sentinel + attach/abandon paths


def test_totally_corrupt_chain_restores_sentinel_attach_and_abandon_work(
        tmp_project, add_shot, monkeypatch):
    """§9.8 / WP3 §5.2: a chain whose FIRST line is already bad rebuilds to the
    RECOVERY_EVIDENCE_CORRUPT sentinel; a fresh submit is blocked; `tasks
    attach-remote-job` lifts it to ADMITTED (poll-only) and `tasks abandon`
    closes one from the sentinel too. RED at HEAD: rebuild restored nothing —
    no sentinel, nothing blocked."""
    shot = add_shot(tmp_project, "S001")
    _corrupt_line(tmp_project, "sub_c1", S.PREPARED)          # first event bad
    _corrupt_line(tmp_project, "sub_c2", S.PREPARED)

    shutil.rmtree(tmp_project.root / ".manju")
    with RuntimeState(tmp_project.root) as st:
        stats = st.rebuild(tmp_project)
        rows = {r["submission_id"]: r for r in st.unresolved_submissions()}
    assert stats["submissions_restored"] == 2
    assert rows["sub_c1"]["state"] == S.RECOVERY_EVIDENCE_CORRUPT
    assert rows["sub_c2"]["state"] == S.RECOVERY_EVIDENCE_CORRUPT

    # fresh submit blocked (transport 0) while the sentinel stands
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION

    # attach: sentinel -> ADMITTED (poll-only), via the CLI command
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    out = runner.invoke(app, ["tasks", "attach-remote-job", "sub_c1", "job_att",
                              "--json"])
    assert out.exit_code == 0, out.output
    with RuntimeState(tmp_project.root) as st:
        row = st.get_submission("sub_c1")
    assert row["state"] == S.ADMITTED
    assert row["remote_job_id"] == "job_att"

    # abandon works straight from the sentinel too
    out = runner.invoke(app, ["tasks", "abandon", "sub_c2",
                              "--reason", "corrupt evidence, accepted", "--json"])
    assert out.exit_code == 0, out.output
    with RuntimeState(tmp_project.root) as st:
        row = st.get_submission("sub_c2")
    assert row["state"] == S.ABANDONED_BY_USER


# ==================== 5. valid ADMITTED chain: resume stays poll-only (pin)


def test_valid_admitted_chain_rebuild_resumes_poll_only(tmp_project, add_shot):
    """§9 / WP3: a VALID chain ending ADMITTED, SQLite deleted → rebuild restores
    ADMITTED with its remote_job_id, and the next generate RESUMES poll-only:
    submit count 0, poll happened, the job completes under the SAME submission.
    (Pin of the already-working path — must not regress under project_chain.)"""
    shot = add_shot(tmp_project, "S001")
    provider = ScriptedCloud()
    # the seeded chain must carry the REAL request digest so the resume matches
    _identity, digest = provider._build_identity(_req(tmp_project, shot))
    _seed_valid_chain(tmp_project, "sub_ok",
                      (S.PREPARED, None), (S.DISPATCHING, None),
                      (S.ADMITTED, "job_resume"), digest=digest)

    shutil.rmtree(tmp_project.root / ".manju")
    with RuntimeState(tmp_project.root) as st:
        stats = st.rebuild(tmp_project)
        rows = st.unresolved_submissions()
    assert stats["submissions_restored"] == 1
    assert rows[0]["state"] == S.ADMITTED
    assert rows[0]["remote_job_id"] == "job_resume"

    takes = provider.generate(_req(tmp_project, shot))
    assert takes and provider.submit_calls == 0              # poll-only resume
    assert provider.poll_calls >= 1
    states = _states(tmp_project, "sub_ok")
    assert states[-1] == S.TERMINAL_SUCCESS                  # same submission closed
