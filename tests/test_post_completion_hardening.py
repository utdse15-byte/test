"""POST_COMPLETION_HARDENING_V1 — WP1/WP2/WP3 red-first tests.

Scope (H1 track): paid recovery on an empty/new SQLite (WP1), 07C release-gate
strictness (WP2 §4.1-4.5), Provider strict identity + legacy pending
correlation (WP3 §5.1-5.2). Every test here first REPRODUCES the gap as a red
against HEAD, then pins the fail-closed behaviour the contract requires.

ffmpeg-free / network-free: paid cloud behaviour is driven by the in-process
``ScriptedCloud`` from the DR06 sandbox; submission evidence is appended
straight to ``events.jsonl`` through the real coordinator.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from manju.build import attempts as A
from manju.build import baseline as BL
from manju.providers import submission as S
from manju.providers.base import FailureKind, ProviderFailure
from manju.runtime.state import RuntimeState

from tests.test_dr06_admission import ScriptedCloud, _req, _states


# --------------------------------------------------------------- helpers


def _seed_chain(project, sid, *pairs, digest="sha256:d", shot="S001",
                provider="cloud_test"):
    """Append a VALID per-submission hash chain to events.jsonl (evidence only —
    no intents row is written, i.e. exactly the empty/new-SQLite world)."""
    prev_digest = None
    prev_state = None
    for to_state, job in pairs:
        rec = A.append_submission_event(
            project, submission_id=sid, request_digest=digest,
            from_state=prev_state, to_state=to_state, provider_id=provider,
            shot=shot, remote_job_id=job, prev_event_digest=prev_digest)
        prev_digest = S.submission_event_digest(rec)
        prev_state = to_state


# =====================================================================
# WP1 — paid recovery on empty / new SQLite (contract §3, tests 1-2)
# =====================================================================


def test_wp1_empty_sqlite_with_unresolved_evidence_blocks_before_transport(
        tmp_project, add_shot):
    """§3.4/§9.1 — submission evidence says DISPATCHING but the runtime
    projection was lost (no intents row / no state.sqlite at all). A direct
    paid generate must NOT read the empty projection as "nothing in flight": it
    recovers the unresolved submission from evidence and fail-closes with
    transport 0. RED at HEAD: _resolve_resume consults state.submissions() only,
    so a fresh empty state returns [] and a fresh paid submit fires."""
    shot = add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_evid", (S.PREPARED, None), (S.DISPATCHING, None))
    # ensure the projection is genuinely empty/new
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0                       # no paid transport
    assert exc.value.detail.get("automatic_resubmit") is False


def test_wp1_delete_dot_manju_no_rebuild_still_fail_closes(tmp_project, add_shot):
    """§3.4 acceptance 1 (also WP6 §2) — deleting `.manju` and NOT running an
    explicit rebuild is still safe: the auto-guard projects the unresolved
    submission from evidence on the next generate. transport 0."""
    shot = add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_ou", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.OUTCOME_UNKNOWN, None))
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_ou",
                       request_digest="sha256:d", state=S.PREPARED)
        st.set_submission_state("sub_ou", S.OUTCOME_UNKNOWN)
    shutil.rmtree(tmp_project.root / ".manju")              # projection lost, NO rebuild

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure):
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0


def test_wp1_evidence_read_failure_fails_closed_transport_zero(
        tmp_project, add_shot, monkeypatch):
    """§3.3/§9.2 — the evidence read/verify itself raising must NOT degrade to
    an empty set; it fail-closes as submission_recovery_unavailable, transport
    0. RED at HEAD: the empty-projection consult never reads evidence, so a
    read failure there is invisible and a fresh submit fires."""
    shot = add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_x", (S.PREPARED, None), (S.DISPATCHING, None))
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    def boom(project, submission_id=None):
        raise OSError("events.jsonl unreadable")

    # _restore_submissions_from_events resolves this via a local import of
    # `..build.attempts.read_submission_events`, so patch it on that module.
    monkeypatch.setattr(A, "read_submission_events", boom)

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_recovery_unavailable"


def test_wp1_unrelated_shot_provider_not_blocked(tmp_project, add_shot):
    """§3.4 — recovering S001's unresolved submission from evidence must not
    block an unrelated shot: S002 through the same provider still submits once."""
    add_shot(tmp_project, "S001")
    s2 = add_shot(tmp_project, "S002")
    _seed_chain(tmp_project, "sub_s1", (S.PREPARED, None), (S.DISPATCHING, None),
                shot="S001")
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    fine = ScriptedCloud()
    takes = fine.generate(_req(tmp_project, s2))
    assert takes and fine.submit_calls == 1                 # S002 unaffected


def test_wp1_terminal_chain_not_recovered_as_unresolved(tmp_project, add_shot):
    """§3.4 — a TERMINAL_SUCCESS chain is resolved; the auto-guard must not
    resurrect it as unresolved and block a legitimate fresh generate."""
    shot = add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_done", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.ADMITTED, "job_done"), (S.TERMINAL_SUCCESS, None))
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    provider = ScriptedCloud()
    takes = provider.generate(_req(tmp_project, shot))
    assert takes and provider.submit_calls == 1             # not blocked


def test_wp1_auto_guard_matches_explicit_rebuild_classification(tmp_project, add_shot):
    """§3.4 — the auto-guard and an explicit rebuild reach the SAME unresolved
    classification (no second algorithm)."""
    add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_cmp", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.OUTCOME_UNKNOWN, None))
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    with RuntimeState(tmp_project.root) as st:
        proj = st.ensure_submission_projection(tmp_project, shot="S001",
                                               provider="cloud_test")
        auto = {r["submission_id"]: r["state"] for r in proj["unresolved"]}
    shutil.rmtree(tmp_project.root / ".manju")
    with RuntimeState(tmp_project.root) as st:
        st.rebuild(tmp_project)
        rebuilt = {r["submission_id"]: r["state"] for r in st.unresolved_submissions()}
    assert auto == rebuilt == {"sub_cmp": S.OUTCOME_UNKNOWN}


# =====================================================================
# WP2 — 07C release gate strictness (contract §4, tests 3-11)
# =====================================================================


def _final_key(project, tl):
    from manju.media.render import final_content_key
    ass = project.captions_dir / "captions.ass"
    return final_content_key(project, tl, ass_file=ass if ass.exists() else None,
                             target="final")


def _manual_timeline(project):
    from tests.test_c07_baseline import _manual_timeline as mt
    return mt(project)


def _clean_final(project):
    from tests.test_c07_baseline import _clean_current_final
    return _clean_current_final(project)


def _codes(assessment):
    return {b["code"] for b in assessment["blockers"]}


def test_wp2_release_db_missing_with_unresolved_evidence_blocks(tmp_project, add_shot):
    """§4.1/§9.3 — no state.sqlite but evidence has an unresolved submission →
    the release gate BLOCKS. RED at HEAD: _submission_blockers returns [] the
    instant state.sqlite is absent."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _seed_chain(tmp_project, "sub_rel", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.OUTCOME_UNKNOWN, None))
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "SUBMISSION_OUTCOME_UNKNOWN" in _codes(a)


def test_wp2_release_db_query_failure_blocks(tmp_project, add_shot, monkeypatch):
    """§4.1 — the submission consult raising must BLOCK, never []. RED at HEAD:
    `except Exception: return []` swallows it into a clean gate."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _seed_chain(tmp_project, "sub_rel", (S.PREPARED, None), (S.DISPATCHING, None))

    def boom(self, *a, **k):
        raise RuntimeError("state query exploded")

    monkeypatch.setattr(RuntimeState, "ensure_submission_projection", boom, raising=False)
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "SUBMISSION_RECOVERY_UNAVAILABLE" in _codes(a)


def test_wp2_baseline_verifications_unreadable_blocks(tmp_project, add_shot):
    """§4.2 tri-state — verifications.jsonl PRESENT but unreadable is
    BASELINE_EVIDENCE_UNAVAILABLE + blocking, never NO_BASELINE. RED at HEAD:
    _read_baseline_events swallows OSError into ([],0) → NO_BASELINE (unblocked
    first release)."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    # make the verifications log unreadable by making it a directory
    vpath = tmp_project.reports_dir / "verifications.jsonl"
    vpath.parent.mkdir(parents=True, exist_ok=True)
    vpath.mkdir()

    cur = BL.current_baseline(tmp_project)
    assert cur["status"] == "BASELINE_EVIDENCE_UNAVAILABLE"
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "BASELINE_EVIDENCE_UNAVAILABLE" in _codes(a)


def test_wp2_baseline_malformed_only_is_not_no_baseline(tmp_project, add_shot):
    """§4.2 — a verifications log whose only baseline line is malformed is
    CORRUPT, never equated to NO_BASELINE."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    vpath = tmp_project.reports_dir / "verifications.jsonl"
    vpath.parent.mkdir(parents=True, exist_ok=True)
    vpath.write_text('{"kind": "release_baseline_approved" TORN\n', encoding="utf-8")

    cur = BL.current_baseline(tmp_project)
    assert cur["status"] == "CORRUPT"


def test_wp2_baseline_event_tamper_without_id_change_is_corrupt(tmp_project, add_shot):
    """§4.3 self-verification — mutate a stored baseline event's payload while
    leaving its event_id → CORRUPT. RED at HEAD: current_baseline never
    recomputes the event_id over the stored payload, so tampering stays VALID."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    BL.approve_baseline(tmp_project, None, reason="baseline")
    assert BL.current_baseline(tmp_project)["status"] == "VALID"

    vpath = tmp_project.reports_dir / "verifications.jsonl"
    lines = vpath.read_text(encoding="utf-8").splitlines()
    ev = json.loads(lines[-1])
    ev["reason"] = "TAMPERED — different reason, same event_id"   # id NOT recomputed
    lines[-1] = json.dumps(ev, ensure_ascii=False)
    vpath.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert BL.current_baseline(tmp_project)["status"] == "CORRUPT"


@pytest.mark.parametrize("run_status,mkterminal", [
    ("FAILED", lambda p, rid: A.append_run_terminal(p, rid, status="failed")),
    ("CANCELED", lambda p, rid: A.append_run_terminal(p, rid, status="canceled")),
    ("WAITING_USER", lambda p, rid: A.append_run_terminal(p, rid, status="waiting_user")),
])
def test_wp2_run_terminal_matrix_blocks(tmp_project, add_shot, run_status, mkterminal):
    """§4.5 run gate — every non-success terminal (FAILED / CANCELED /
    WAITING_USER) blocks the release. RED at HEAD: _run_blockers maps only
    INCOMPLETE, so a FAILED/CANCELED/WAITING_USER run passes the gate."""
    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    from tests.test_c07_baseline import _fab_final
    _fab_final(tmp_project, _final_key(tmp_project, tl), run_id="run_t",
               output_sha256="auto")
    A.append_run_started(tmp_project, "run_t", target="final", gen="missing")
    mkterminal(tmp_project, "run_t")

    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False, run_status
    codes = _codes(a)
    assert any(c.startswith("RUN_") for c in codes), codes


def test_wp2_run_not_found_blocks(tmp_project, add_shot):
    """§4.5 — a final that claims a run_id with NO events at all (NOT_FOUND) is
    a broken linkage and blocks."""
    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    from tests.test_c07_baseline import _fab_final
    _fab_final(tmp_project, _final_key(tmp_project, tl), run_id="run_ghost",
               output_sha256="auto")

    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert any(c.startswith("RUN_") for c in _codes(a))


def test_wp2_concurrent_verification_and_baseline_append_no_torn_or_truncate(
        tmp_project, add_shot):
    """§4.4 — normal verification and baseline approval share ONE cross-process
    locked append helper (verifications.lock); interleaved appends never tear a
    line nor truncate the other writer's committed bytes."""
    import threading
    from manju.build import exportstatus as ES

    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    vpath = tmp_project.reports_dir / "verifications.jsonl"
    vpath.parent.mkdir(parents=True, exist_ok=True)

    from manju.core.events import append_jsonl_line, EVENTS_FILE  # noqa: F401

    def approver():
        for _ in range(15):
            try:
                BL.approve_baseline(tmp_project, None, reason="race")
            except BL.BaselineError:
                pass

    def verifier():
        for i in range(15):
            append_jsonl_line(tmp_project.reports_dir, {"kind": "draft_v", "n": i},
                              durable=True, required=False,
                              file_name=ES.VERIFICATIONS_FILE,
                              lock_name=ES.VERIFICATIONS_LOCK)

    ts = [threading.Thread(target=approver), threading.Thread(target=verifier)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    # every line is intact JSON (no torn/truncated bytes) and both writers survive
    lines = [l for l in vpath.read_text(encoding="utf-8").splitlines() if l.strip()]
    parsed = [json.loads(l) for l in lines]                 # raises if any line torn
    assert any(r.get("kind") == BL.BASELINE_KIND for r in parsed)
    assert any(r.get("kind") == "draft_v" for r in parsed)


# =====================================================================
# WP3 — strict identity + legacy pending correlation (§5, tests 13-17)
# =====================================================================


def test_wp3_prompt_compile_failure_transport_zero(tmp_project, add_shot, monkeypatch):
    """§5.1 — a compiled-prompt failure must fail-closed (identity unavailable),
    transport 0. RED at HEAD: _build_identity swallows the compile exception to
    None and submits anyway."""
    shot = add_shot(tmp_project, "S001")

    def boom(*a, **k):
        raise RuntimeError("prompt compile exploded")

    monkeypatch.setattr("manju.providers.prompt.compile_prompt", boom)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_identity_unavailable"
    assert exc.value.disposition == S.NOT_DISPATCHED


def test_wp3_ref_resolution_failure_transport_zero(tmp_project, add_shot, monkeypatch):
    """§5.1 — a ref-set resolution failure fails closed, transport 0. RED at
    HEAD: _build_identity swallows it to an empty ref list and submits."""
    shot = add_shot(tmp_project, "S001")

    from manju.providers.base import GenerationRequest

    def boom(self):
        raise RuntimeError("refset resolution exploded")

    monkeypatch.setattr(GenerationRequest, "refset", boom, raising=False)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_identity_unavailable"


def test_wp3_ref_hash_failure_transport_zero(tmp_project, add_shot, monkeypatch):
    """§5.1 — a local ref that should be hashed but cannot be read fails closed,
    transport 0. RED at HEAD: the OSError is swallowed to sha=None."""
    shot = add_shot(tmp_project, "S001")

    # a request whose refset yields one local, readable image ref…
    class _Item:
        kind = "image"
        ref = "refs/hero.png"
        is_url = False

        def __init__(self, path):
            self.path = path

    class _RefSet:
        def __init__(self, path):
            self._p = path

        def image_items(self):
            return [_Item(self._p)]

        def video_items(self):
            return []

    ref_file = tmp_project.root / "hero.png"
    ref_file.write_bytes(b"PNGDATA")

    monkeypatch.setattr("manju.providers.base.GenerationRequest.refset",
                        lambda self: _RefSet(ref_file), raising=False)

    def boom(path):
        raise OSError("cannot read ref bytes")

    # _build_identity resolves hash_file via `from ..core.hashing import hash_file`
    monkeypatch.setattr("manju.core.hashing.hash_file", boom)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_identity_unavailable"


def test_wp3_local_ref_missing_file_transport_zero(tmp_project, add_shot, monkeypatch):
    """§5.1 — a declared local (non-URL) ref whose file is missing cannot be
    hashed → identity unavailable, transport 0."""
    shot = add_shot(tmp_project, "S001")

    class _Item:
        kind = "image"
        ref = "refs/missing.png"
        is_url = False
        path = tmp_project.root / "does_not_exist.png"

    class _RefSet:
        def image_items(self):
            return [_Item()]

        def video_items(self):
            return []

    monkeypatch.setattr("manju.providers.base.GenerationRequest.refset",
                        lambda self: _RefSet(), raising=False)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_identity_unavailable"


def test_wp3_legacy_pending_unknown_correlation_requires_human(tmp_project, add_shot):
    """§5.2 — a legacy pending job (shot+provider only, no correlatable request
    digest) is NO longer auto-resumed: it fail-closes as
    LEGACY_PENDING_CORRELATION_UNKNOWN and transport stays 0. RED at HEAD:
    _resume_job_id resumes on shot+provider alone (double-charge / wrong-output
    risk). This deliberately flips the DR06 characterization pin."""
    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        st.open_job("job_legacy_1", provider="cloud_test", shot="S001",
                    params={"unrelated": "params"})

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot, seed=123))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "LEGACY_PENDING_CORRELATION_UNKNOWN"
    assert exc.value.detail.get("automatic_resubmit") is False
    assert set(exc.value.detail.get("actions") or []) == {
        "attach_remote_job", "abandon_with_duplicate_risk"}
