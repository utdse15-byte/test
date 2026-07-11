"""Derived assurance (DR02 WP3) — the pure, file-only account of whether a
shot's EXPLICIT promises have been met by CURRENT-bound review evidence.

Three states are kept strictly separate (DR02 non-negotiable):

    execution done     a take exists and is selected (build/stale.py)
    derived assurance  THIS module — a pure function of expectations vs bound
                       observations; it NEVER writes ShotStatus
    human review       ShotStatus.review_state — read here, never written

Everything here is DERIVED and READ-ONLY. Assurance is computed only from files
(shot YAML, bible, reports/qc_agent.jsonl, reports/qc_packets, media) so it is
fully explainable with ``.manju/state.sqlite`` deleted. A model never writes the
result: reviewers report per-expectation OBSERVATIONS, and the PASS/FAIL/UNKNOWN
verdict is the pure :func:`diff` below. Legacy (pre-v2) evidence is honoured as
history but NEVER satisfies v2 acceptance. Repair proposals are pure data that
never auto-execute, auto-regenerate, or auto-spend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .agent_review import (
    _current_expectation_digest,
    _current_selected_media,
    _current_spec_hash,
    _read_records,
    read_v2_records,
)
from .expectations import compile_expectations

if TYPE_CHECKING:
    from ..core.container import Project
    from .checks import QCReport

SCHEMA = "manju.qc.assurance/v1"

# the derived per-shot states, in precedence order (first match wins).
STATES = (
    "not_reviewable",          # 1
    "no_explicit_expectations",  # 2
    "unreviewed",              # 3a
    "legacy_reviewed",         # 3b
    "stale",                   # 4
    "rejected",                # 5
    "unknown",                 # 6
    "accepted",                # 7
)


# ------------------------------------------------------- deterministic diff


def diff(expectations: list[dict], observations: list[dict]) -> dict[str, str]:
    """Pure per-expectation verdict from an expectation set + a reviewer's
    observations. The ENTIRE judgment lives in this truth table — no model, no
    confidence, no side effects::

        present + present                      -> PASS
        present + absent                       -> FAIL
        present + uncertain/not_evaluated/—    -> UNKNOWN
        absent  + absent                       -> PASS
        absent  + present                      -> FAIL
        absent  + uncertain/not_evaluated/—    -> UNKNOWN

    ``—`` (missing) is any expectation with no matching observation. Returns
    ``{expectation_id: "PASS"|"FAIL"|"UNKNOWN"}``.
    """
    observed_by_id = {
        o.get("expectation_id"): o.get("observed")
        for o in observations if isinstance(o, dict)
    }
    out: dict[str, str] = {}
    for e in expectations:
        out[e["id"]] = _verdict(e.get("polarity"), observed_by_id.get(e["id"]))
    return out


def _verdict(polarity: str | None, observed: str | None) -> str:
    if polarity == "present":
        if observed == "present":
            return "PASS"
        if observed == "absent":
            return "FAIL"
        return "UNKNOWN"
    # polarity == "absent"
    if observed == "absent":
        return "PASS"
    if observed == "present":
        return "FAIL"
    return "UNKNOWN"


# --------------------------------------------------------- assurance compute


def compute_assurance(project: "Project", shot_id: str, *,
                      qc_report: "QCReport | None" = None) -> dict:
    """The derived assurance for one shot (schema ``manju.qc.assurance/v1``).
    Pure and read-only; output ordering is deterministic (sorted ids) so two
    calls deep-equal. See the module docstring for the three-state separation.

    ``qc_report`` lets a surface that already ran the deterministic QC pass its
    report in (the ``manju qc`` path, or :func:`assurance_for_all`) instead of
    re-running ``run_qc`` per shot; omitted, the gate computes its own."""
    human_review_state = _human_review_state(project, shot_id)

    es, compile_error = _safe_compile(project, shot_id)
    expectations = es.get("expectations", [])
    spec_hash = es.get("spec_hash")
    expectation_digest = es.get("digest")

    def result(state, *, reasons, failed=None, unknown=None, evidence=None,
               stale_reasons=None, qc=None, expectation_compile_error=None):
        return {
            "schema": SCHEMA,
            "subject": {"kind": "shot", "id": shot_id},
            "assurance_state": state,
            "human_review_state": human_review_state,
            "spec_hash": spec_hash,
            "expectation_digest": expectation_digest,
            "reasons": list(reasons),
            "failed_expectation_ids": sorted(failed or []),
            "unknown_expectation_ids": sorted(unknown or []),
            "evidence": evidence,
            "stale_reasons": sorted(stale_reasons or []),
            # tri-state deterministic-QC input to the acceptance gate (H, WP0):
            # {"status": pass|blocked|unavailable, "reason": <code|None>} for the
            # states that consult it, else None. Never invisible.
            "qc": qc,
            # J (WP5): non-None when expectation compilation itself failed.
            "expectation_compile_error": expectation_compile_error,
        }

    # 1. not_reviewable — no selected take, or its media file is missing.
    cur_path, cur_sha = _current_selected_media(project, shot_id)
    if cur_sha is None:
        return result("not_reviewable",
                      reasons=["no selected take, or its media file is missing"])

    # J (WP5). expectation compilation FAILED — never collapse a malformed
    # quality/continuity block into a vacuous "nothing was promised" pass. We
    # cannot derive what the author promised, so we cannot accept: surface the
    # compile error and hold the shot in the honest non-accepting `unknown`.
    if compile_error is not None:
        return result("unknown",
                      reasons=["expectation compilation failed — the shot's promises "
                               "cannot be derived, so acceptance is withheld: "
                               + compile_error],
                      expectation_compile_error=compile_error)

    # 2. no_explicit_expectations — nothing was promised.
    if not expectations:
        return result("no_explicit_expectations",
                      reasons=["the shot declares no must_show / avoid / continuity.locks"])

    # gather this subject's v2 evidence (file order).
    v2_records = [
        r for r in read_v2_records(project)[0]
        if (r.get("subject") or {}).get("kind") == "shot"
        and (r.get("subject") or {}).get("id") == shot_id
    ]

    # 3. unreviewed / legacy_reviewed — no v2 evidence at all.
    if not v2_records:
        if _has_legacy_evidence(project, shot_id):
            return result("legacy_reviewed",
                          reasons=["only legacy (pre-v2) evidence exists — it never "
                                   "satisfies v2 acceptance; re-review via manju qc brief"])
        return result("unreviewed", reasons=["no v2 review evidence for this subject"])

    # the evidence actually used = the LATEST v2 record still bound to CURRENT
    # state (re-verified now, not trusting the intake-time label).
    bound = None
    for r in v2_records:
        if not _live_failures(project, shot_id, r):
            bound = r  # file order => last current-bound wins

    # 4. stale — v2 evidence exists but none of it is still current-bound.
    if bound is None:
        latest = v2_records[-1]
        failures = _live_failures(project, shot_id, latest)
        return result("stale",
                      reasons=["v2 evidence exists but its binding no longer matches "
                               "current state: " + ", ".join(failures)],
                      stale_reasons=failures, evidence=_evidence_view(latest))

    observations = bound.get("observations") or []
    findings = bound.get("findings") or []
    results = diff(expectations, observations)
    failed = [eid for eid, v in results.items() if v == "FAIL"]
    unknown = [eid for eid, v in results.items() if v == "UNKNOWN"]
    has_blocker = any(isinstance(f, dict) and f.get("level") == "blocker" for f in findings)
    evidence = _evidence_view(bound)

    # 5. rejected — any FAIL on a required expectation, or any blocker finding.
    if failed or has_blocker:
        reasons = []
        if failed:
            reasons.append("required expectation(s) failed: " + ", ".join(sorted(failed)))
        if has_blocker:
            reasons.append("a blocker finding was reported by the reviewer")
        return result("rejected", reasons=reasons, failed=failed, unknown=unknown,
                      evidence=evidence)

    # 6. unknown — no FAIL, but at least one required expectation UNKNOWN.
    if unknown:
        return result("unknown",
                      reasons=["required expectation(s) unresolved (uncertain / "
                               "not evaluated / unobserved): " + ", ".join(sorted(unknown))],
                      unknown=unknown, evidence=evidence)

    # 7. Deterministic-QC gate (tri-state PASS / BLOCKED / UNAVAILABLE). Every
    #    required expectation is already a current-bound PASS with no blocker, so
    #    the machine tier is the last word before acceptance:
    #      BLOCKED     — a policy-blocking error names THIS shot => rejected;
    #      UNAVAILABLE — QC raised / missing / unreadable => we CANNOT confirm the
    #                    absence of blocks, so we never silently accept (H, WP0):
    #                    the honest non-accepting `unknown`, reason visible;
    #      PASS        — accepted.
    qc_status, qc_reason = _shot_qc_status(project, shot_id, qc_report)
    qc_view = {"status": qc_status, "reason": qc_reason}
    if qc_status == "blocked":
        return result("rejected",
                      reasons=["deterministic QC reports a policy-blocking error for "
                               "this shot (see manju qc) — cannot accept"],
                      evidence=evidence, qc=qc_view)
    if qc_status == "unavailable":
        return result("unknown",
                      reasons=["deterministic QC is unavailable (" + (qc_reason or "unknown")
                               + ") — cannot confirm this shot has no policy-blocking "
                               "error, so acceptance is withheld"],
                      unknown=unknown, evidence=evidence, qc=qc_view)
    return result("accepted",
                  reasons=["every required expectation has a current-bound PASS, no "
                           "blocker finding, and QC has no policy-blocking error"],
                  evidence=evidence, qc=qc_view)


# ----------------------------------------------------------- repair proposal


def repair_proposal(project: "Project", shot_id: str,
                    assurance: dict | None = None) -> dict | None:
    """A pure, read-only repair PROPOSAL for a rejected/unknown shot (else
    ``None``). It never generates, writes a source, or spends — the candidates
    are advice a human or agent must act on. Each candidate cites the precise
    expectation ids behind it."""
    a = assurance if assurance is not None else compute_assurance(project, shot_id)
    state = a.get("assurance_state")
    if state not in ("rejected", "unknown"):
        return None

    failed = list(a.get("failed_expectation_ids") or [])
    unknown = list(a.get("unknown_expectation_ids") or [])
    candidates: list[dict] = []
    if state == "rejected":
        reason = ("required expectation(s) failed: " + ", ".join(failed)
                  if failed else "a blocker finding was reported")
        candidates.append({"type": "redo", "reason": reason,
                           "requires_human_or_agent_patch": True})
    if unknown:
        candidates.append({
            "type": "re_review",
            "reason": "expectation(s) unknown/uncertain: " + ", ".join(unknown),
            "requires_human_or_agent_patch": True,
        })

    return {
        "subject": shot_id,
        "assurance_state": state,
        "failed_expectation_ids": failed,
        "unknown_expectation_ids": unknown,
        "recommended_skill": "repair-loop",
        "action_candidates": candidates,
        "do_not_execute_automatically": True,
    }


# ------------------------------------------------------------------ helpers


def _safe_compile(project: "Project", shot_id: str) -> tuple[dict, str | None]:
    """Compile the shot's expectations. Returns ``(expectation_set, None)`` on
    success, or ``({}, "<ErrType>: <msg>")`` when compilation RAISES — so the
    caller surfaces an honest compile-error state (J, WP5) instead of silently
    reading a malformed quality/continuity block as 'nothing was promised'."""
    try:
        return compile_expectations(project, shot_id), None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def _human_review_state(project: "Project", shot_id: str) -> str | None:
    """READ ShotStatus.review_state — assurance never writes it (three-state
    separation). None when the shot cannot be loaded."""
    try:
        return project.load_shot(shot_id).status.review_state
    except Exception:
        return None


def _record_binding_failures_now(project: "Project", shot_id: str,
                                 record: dict) -> list[str]:
    """Re-verify a stored record's bindings against CURRENT state — the same
    media/spec/expectations comparison intake did, recomputed now so a world
    move AFTER a bound intake also reads as stale."""
    failures: set[str] = set()
    cur_path, cur_sha = _current_selected_media(project, shot_id)
    if cur_sha is None or cur_sha != record.get("media_sha256") \
            or cur_path != record.get("media_project_path"):
        failures.add("media")
    if _current_spec_hash(project, shot_id) != record.get("spec_hash"):
        failures.add("spec")
    if _current_expectation_digest(project, shot_id) != record.get("expectation_digest"):
        failures.add("expectations")
    return sorted(failures)


def _live_failures(project: "Project", shot_id: str, record: dict) -> list[str]:
    """A record is current-bound iff this is empty. Unions a fresh re-verification
    with any intake-time ``binding_failures`` (a record stored binding-stale is
    stale forever, even if the world happened to move back)."""
    now = set(_record_binding_failures_now(project, shot_id, record))
    if record.get("binding") == "stale":
        now |= set(record.get("binding_failures") or [])
    return sorted(now)


def _evidence_view(record: dict) -> dict:
    """The compact evidence descriptor for the record actually used."""
    return {
        "packet_id": record.get("packet_id"),
        "media_sha256": record.get("media_sha256"),
        "reviewer": record.get("reviewer"),
        "observed_at": record.get("ts"),
    }


def _has_legacy_evidence(project: "Project", shot_id: str) -> bool:
    """Any legacy (schema-less) verdict record for this shot. ``_read_records``
    already skips v2 records, so a hit here is genuinely legacy."""
    records, _malformed = _read_records(project)
    return any(r.get("shot") == shot_id for r in records)


class _QCUnavailable:
    """Sentinel: the deterministic QC pass could not be produced (run_qc raised,
    or the report is missing/unreadable). DISTINCT from ``None``, which means
    'not run yet — compute it'. Threading this through :func:`assurance_for_all`
    lets one failed shared run_qc mark every shot's QC unavailable without each
    shot re-running (and re-failing) it, and without the old silent read of a
    failed pass as 'no policy block' (H, WP0)."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "QC_UNAVAILABLE"


QC_UNAVAILABLE = _QCUnavailable()


def _shot_qc_status(project: "Project", shot_id: str,
                    report: "QCReport | None" = None) -> tuple[str, str | None]:
    """Tri-state deterministic-QC input to the acceptance gate for THIS shot:

        "pass"        QC ran; no error-level item is attributed to this shot
        "blocked"     QC ran; an error-level (policy-blocking) item names this shot
        "unavailable" QC could not be produced (run_qc raised, or the report is
                      missing/unreadable) — the honest 'we cannot confirm the
                      absence of blocks' input; NEVER silently a pass (H, WP0)

    Returns ``(status, reason_code)`` — ``reason_code`` is a short machine token
    only when unavailable, else ``None``. Scoped to the shot so one shot's
    machine error never blocks another's acceptance (reuses QCReport's existing
    per-item error semantics)."""
    if report is QC_UNAVAILABLE:
        return "unavailable", "qc_run_failed"
    if report is None:
        try:
            from .checks import run_qc

            report = run_qc(project, None, extract_frames=False)
        except Exception:
            return "unavailable", "qc_run_failed"
    try:
        blocked = any(it.level == "error" and it.subject == shot_id
                      for it in report.items)
    except Exception:
        # a malformed/unreadable report is not evidence of "no blocks".
        return "unavailable", "qc_report_unreadable"
    return ("blocked" if blocked else "pass"), None


def assurance_for_all(project: "Project",
                      qc_report: "QCReport | None" = None) -> list[dict]:
    """Every shot's assurance in :meth:`Project.shot_ids` order — a convenience
    for surfaces (read-only, deterministic). The deterministic QC report is
    computed ONCE here and shared across every shot's acceptance gate (per-shot
    ``compute_assurance`` would otherwise re-run QC for each accepted shot);
    surfaces that already ran QC (the ``manju qc`` path) pass theirs in."""
    if qc_report is None:
        try:
            from .checks import run_qc

            qc_report = run_qc(project, None, extract_frames=False)
        except Exception:
            # the shared QC pass failed -> mark every shot's QC unavailable
            # (H, WP0) rather than the old silent None (which read as 'no
            # blocks') or letting each shot re-run and re-fail run_qc.
            qc_report = QC_UNAVAILABLE
    return [compute_assurance(project, sid, qc_report=qc_report)
            for sid in project.shot_ids()]
