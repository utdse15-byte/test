"""AI_IDE_07C — approved release baseline + composed release assessment.

The one real gap 07C fills: a human can bless the EXACT immutable bytes of a
final as the "current release baseline"; ``compare`` and ``exports`` then read
that pointer and fold the evidence that ALREADY exists into a blocking /
regression / next-safe-action view.

Deliberate non-goals (contract §1, §11): NO new store, NO new public schema, NO
new event log, NO planner/DAG/readiness subsystem, NO second verification log.

- The baseline is a narrow new ``kind`` ("release_baseline_approved") appended
  to the EXISTING human-verification envelope — ``reports/verifications.jsonl``,
  the same append-only log ``exportstatus.mark_verified`` writes draft
  verifications to. It binds exact artifact bytes (path + sha256 + size +
  final_key). It is never a hand-editable ``release-baseline.json`` fact file.

- The release assessment is an INSTANT derived composition. It owns no truth; it
  CALLS the subsystems that do and folds their answers:
    exports status ....... :func:`exportstatus.deliverables`  (delivery owner)
    final regression ..... :func:`compare.compare_finals`     (the ONLY diff engine)
    QC acceptance ........ :func:`qc.assurance.assurance_for_all`
    run terminal honesty . :func:`attempts.build_run_manifest`
    paid submissions ..... ``RuntimeState.unresolved_submissions`` + chain verify
    action safety ........ ``mcp.policy`` TOOL_DEFS (never a hand-written table)
  Nothing here re-implements any of them. Deleting or not materializing the
  assessment can never affect build / cache / resume — it is read-only and
  writes nothing.

Fail-closed everywhere the evidence is missing/ambiguous (contract §7-9): an
INCOMPLETE run, an OUTCOME_UNKNOWN submission, a corrupt evidence chain, an
unavailable/stale/rejected QC, and a stale/missing/tampered current final all
block; a first release with no baseline is NOT a technical failure.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.hashing import hash_file, hash_value
from ..qc.assurance import assurance_for_all
from .attempts import build_run_manifest
from .compare import compare_finals, resolve_final
from .exportstatus import VERIFICATIONS_FILE, VERIFICATIONS_LOCK, Freshness, deliverables
from .shotpackage import project_revision

# The narrow new event kind — a value INSIDE the existing verification envelope,
# not a new file/store/schema (contract §5.1).
BASELINE_KIND = "release_baseline_approved"

# baseline resolution states (contract §5.3 / §7.2)
VALID = "VALID"
NO_BASELINE = "NO_BASELINE"
DAMAGED = "DAMAGED"          # artifact bytes missing or hash no longer matches
CORRUPT = "CORRUPT"          # the baseline event itself is malformed/incomplete
# POST_COMPLETION WP2 §4.2 tri-state: the verification log PRESENT but unreadable
# is NOT the same as absent — it must fail closed, never degrade to NO_BASELINE.
EVIDENCE_UNAVAILABLE = "BASELINE_EVIDENCE_UNAVAILABLE"

# regression review verdicts (contract §6.2)
UNCHANGED = "UNCHANGED"
CHANGED = "CHANGED_REQUIRES_REVIEW"


class BaselineError(RuntimeError):
    """A baseline approval could not be made (blocked, not durable, or denied).
    ``str()`` carries no secret, no Authorization and no signed URL (§13)."""


# --------------------------------------------------------------- envelope I/O


def _verifications_path(project: Any) -> Path:
    return project.reports_dir / VERIFICATIONS_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event_id(event: dict) -> str:
    """A deterministic, self-verifying id: the canonical hash of the event
    payload minus the id field itself."""
    return hash_value({k: v for k, v in event.items() if k != "event_id"})


def _read_baseline_events(project: Any, target: str = "final"
                          ) -> tuple[list[dict], int, str]:
    """(events, malformed_line_count, io_state) — every ``release_baseline_approved``
    record for ``target`` in append order. Torn JSON lines are skipped exactly
    like the events log (§3): a malformed line NEVER swallows the valid events
    around it (test §9.1.5).

    POST_COMPLETION WP2 §4.2 tri-state ``io_state``:

    - ``ABSENT``   — the log does not exist yet (a first release, NO_BASELINE);
    - ``READABLE`` — the log was read (events/malformed are meaningful);
    - ``UNREADABLE``— the log exists but ``read_text`` raised (fail closed as
      ``BASELINE_EVIDENCE_UNAVAILABLE``; NEVER equated to ABSENT/NO_BASELINE)."""
    path = _verifications_path(project)
    events: list[dict] = []
    malformed = 0
    if not path.exists():
        return events, malformed, "ABSENT"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events, malformed, "UNREADABLE"
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if (isinstance(rec, dict) and rec.get("kind") == BASELINE_KIND
                and rec.get("target", "final") == target):
            events.append(rec)
    return events, malformed, "READABLE"


def _append_baseline_event(project: Any, event: dict) -> None:
    """Append ONE baseline event durably to the existing verification log.

    WP2 §4.4: routed through the ONE shared cross-process append coordinator
    (``core.events.append_jsonl_line`` against ``verifications.jsonl`` under the
    ``verifications.lock`` sibling) — the SAME locked helper draft verification
    uses. write → flush → fsync under the exclusive flock; on ANY failure the
    partial append is rolled back INSIDE the lock, so a failed durable write is
    NEVER reported as a successful approval (§5.2.7 / §9.1.6) AND a concurrent
    writer's committed bytes are never truncated."""
    from ..core.events import EvidenceWriteError, append_jsonl_line

    _verifications_path(project).parent.mkdir(parents=True, exist_ok=True)
    try:
        append_jsonl_line(project.reports_dir, event, durable=True, required=True,
                          file_name=VERIFICATIONS_FILE, lock_name=VERIFICATIONS_LOCK)
    except EvidenceWriteError as exc:
        raise BaselineError(
            f"durable append failed ({exc.reason}); approval NOT recorded"
        ) from exc


# ----------------------------------------------------------- sidecar helpers


def _read_key_full(final_path: Path) -> dict | None:
    sidecar = final_path.with_suffix(".key.json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_hash(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    try:
        return hash_file(path)
    except OSError:
        return None


def _current_final_key(project: Any) -> tuple[str | None, str]:
    """The CURRENT recomputed final content key — reuses exportstatus's own
    gather (the SINGLE staleness recompile), never a second key formula."""
    from . import exportstatus as ES

    try:
        ctx = ES._gather(project)
        return ctx.final_key, ctx.timeline_note or ""
    except Exception as exc:  # empty/uncompilable project → cannot prove freshness
        return None, " ".join(str(exc).split())[:120]


# --------------------------------------------------------------- baseline API


def current_baseline(project: Any, target: str = "final") -> dict:
    """Resolve the current release baseline for ``target``.

    The latest valid approval event wins (append-only; history is never
    rewritten). The result is bound to the EXACT bytes that were approved — a
    missing or hash-mismatched artifact is ``DAMAGED`` and is NEVER silently
    re-pointed at a newer final (contract §3, §5.3, tests §9.1.2/3)."""
    events, malformed, io_state = _read_baseline_events(project, target)
    if io_state == "UNREADABLE":
        # WP2 §4.2: present-but-unreadable is fail-closed, never NO_BASELINE.
        return {"status": EVIDENCE_UNAVAILABLE, "target": target,
                "reason": "the release-baseline verification log is unreadable"}
    if not events:
        if malformed:
            # WP2 §4.2: a malformed baseline line must NOT be equated to
            # NO_BASELINE — the evidence is corrupt, not absent.
            return {"status": CORRUPT, "target": target,
                    "reason": f"baseline evidence has {malformed} malformed line(s) "
                              "and no valid baseline event",
                    "malformed_count": malformed}
        return {"status": NO_BASELINE, "target": target}

    latest = events[-1]
    art = latest.get("artifact") or {}
    path = art.get("path")
    sha = art.get("sha256")
    event_id = latest.get("event_id")
    if not (isinstance(path, str) and path and isinstance(sha, str) and sha):
        return {"status": CORRUPT, "target": target, "event_id": event_id,
                "reason": "baseline event is missing its artifact path/sha256 binding"}
    # WP2 §4.3 self-verification: recompute the deterministic event_id over the
    # STORED payload; a mismatch means the event was tampered with (a field
    # changed while its id was left untouched) — CORRUPT, never trusted as VALID.
    if not (isinstance(event_id, str) and event_id and _event_id(latest) == event_id):
        return {"status": CORRUPT, "target": target, "event_id": event_id,
                "reason": "baseline event failed self-verification "
                          "(recomputed event_id does not match the stored id)"}

    result = {
        "status": VALID,
        "target": target,
        "event_id": event_id,
        "artifact": {"path": path, "sha256": sha, "bytes": art.get("bytes"),
                     "final_key": art.get("final_key")},
        "artifact_sha256": sha,
        "source_revision": latest.get("source_revision"),
        "run_id": latest.get("run_id"),
        "reason": latest.get("reason"),
        "approved_at": latest.get("ts"),
        "supersedes_event_id": latest.get("supersedes_event_id"),
        "risk_accepted": bool(latest.get("risk_accepted")),
        "known_blockers": latest.get("known_blockers") or [],
    }
    if malformed:
        # FINAL_ACCEPTANCE F5: torn lines ride along even when the latest event
        # is valid — a torn line could BE the superseding approval, so the
        # release assessment turns this into a blocking evidence blocker (the
        # display status itself stays whatever the valid event proves).
        result["malformed_count"] = malformed
    try:
        abspath = project.resolve(path)
    except Exception:
        # FINAL_ACCEPTANCE F5: NEVER fall back to a raw root-join — an
        # absolute or ../-escaping stored path would resolve OUTSIDE the
        # project and could even read back VALID against foreign bytes. An
        # unresolvable approved path is damage, full stop.
        result["status"] = DAMAGED
        result["damage"] = ("the approved artifact path cannot be resolved "
                            "inside the project (fail closed)")
        return result
    if not abspath.exists():
        result["status"] = DAMAGED
        result["damage"] = "artifact bytes missing at the approved path"
        return result
    current_sha = _safe_hash(abspath)
    if current_sha is None:
        result["status"] = DAMAGED
        result["damage"] = "artifact bytes unreadable"
    elif current_sha != sha:
        result["status"] = DAMAGED
        result["damage"] = "artifact bytes changed since approval (sha256 mismatch)"
    return result


def approve_baseline(project: Any, final_ref: str | None = None, *, reason: str = "",
                     actor_kind: str = "human", unattended: bool = False,
                     accept_known_risk: bool = False, target: str = "final") -> dict:
    """Approve a final's exact bytes as the current release baseline (§5.2).

    Human-CLI action ONLY: an unattended/agent caller is refused. Verifies the
    artifact is readable and carries a content-key sidecar, runs the SAME
    technical gate the assessment uses, and by default REFUSES if a blocker
    stands. ``accept_known_risk`` (human, explicit) overrides but records the
    blockers + the reason into the event. Appends durably to the existing
    verification log; only a successful append returns success. Never mutates
    source, never mints a final, never commits."""
    if unattended or actor_kind != "human":
        raise BaselineError(
            "baseline approval is an explicit human action; an unattended/agent "
            "caller may not approve (contract §1.5/§5.3)")

    if final_ref:
        try:
            final_path = resolve_final(project, final_ref)
        except Exception as exc:
            raise BaselineError(str(exc)) from exc
    else:
        final_path = project.newest_final_path()
        if final_path is None:
            raise BaselineError("no final to approve — build one first (renders/final is empty)")

    if not final_path.exists() or _size(final_path) == 0:
        raise BaselineError("the final to approve is missing or empty (fail closed)")
    key_data = _read_key_full(final_path)
    if not key_data or not key_data.get("final_key"):
        raise BaselineError(
            "the final has no content-key sidecar (render may be incomplete) — cannot approve")

    sha = _safe_hash(final_path)
    if sha is None:
        raise BaselineError("the final bytes are unreadable — cannot approve")

    # the same technical (non-regression) gate the assessment applies
    blockers = _technical_blockers(project, final_path)
    blocking = [b for b in blockers if b.get("blocking")]
    if blocking and not accept_known_risk:
        raise BaselineError(_approval_blocked_message(blocking))

    prior = current_baseline(project, target)
    supersedes = prior.get("event_id") if prior.get("status") in (VALID, DAMAGED, CORRUPT) else None

    event: dict[str, Any] = {
        "kind": BASELINE_KIND,
        "target": target,
        "ts": _now_iso(),
        "artifact": {
            "path": project.relpath(final_path),
            "sha256": sha,
            "bytes": _size(final_path),
            "final_key": key_data.get("final_key"),
        },
        "source_revision": project_revision(project),
        "run_id": key_data.get("run_id"),
        "assurance_digest": _assurance_digest(project),
        "technical_report_digest": key_data.get("output_sha256"),
        "actor": {"kind": "human"},
        "reason": reason or "",
        "supersedes_event_id": supersedes,
    }
    if accept_known_risk and blocking:
        event["risk_accepted"] = True
        event["known_blockers"] = [{"code": b["code"], "scope": b.get("scope")} for b in blocking]
        event["risk_reason"] = reason or ""
    event["event_id"] = _event_id(event)

    _append_baseline_event(project, event)  # raises → approval NOT reported as success
    return {"approved": True, "event": event,
            "risk_accepted_blockers": sorted({b["code"] for b in blocking}) if accept_known_risk else []}


def _assurance_digest(project: Any) -> str | None:
    """A stable digest of the current per-shot acceptance — evidence-only, best
    effort (never blocks approval on its own)."""
    try:
        rows = assurance_for_all(project)
        return hash_value([[(a.get("subject") or {}).get("id"), a.get("assurance_state")]
                           for a in rows])
    except Exception:
        return None


# ----------------------------------------------------- baseline-aware compare


def _final_name(project: Any, ref: str | None) -> str:
    if ref:
        return resolve_final(project, ref).stem
    newest = project.newest_final_path()
    if newest is None:
        raise BaselineError("no current final to compare (renders/final is empty)")
    return newest.stem


def compare_against_baseline(project: Any, candidate: str | None = None) -> dict:
    """Diff the candidate final against the approved baseline (§6). Internally
    calls the SAME :func:`compare.compare_finals` service — baseline is always
    the ``a`` (reference) side, candidate the ``b`` side, so the order can never
    invert (test §9.2.12). Additive-only fields are layered on; no
    ``RegressionComparisonV1`` object is created."""
    base = current_baseline(project)
    if base["status"] != VALID:
        out = {
            "comparison_mode": "APPROVED_BASELINE",
            "baseline": {"status": base["status"]},
            "candidate": {"artifact_sha256": _safe_hash(project.newest_final_path())},
            "review_status": base["status"],
            "note": f"baseline {base['status']} — nothing to diff against",
        }
        if base.get("event_id"):
            out["baseline"]["event_id"] = base["event_id"]
        return out

    base_name = Path(base["artifact"]["path"]).stem
    cand_name = _final_name(project, candidate)
    diff = compare_finals(project, base_name, cand_name)  # the ONE diff engine

    cand_sha = _safe_hash(project.final_dir / f"{cand_name}.mp4")
    diff["comparison_mode"] = "APPROVED_BASELINE"
    diff["baseline"] = {"status": VALID, "event_id": base["event_id"],
                        "artifact_sha256": base["artifact_sha256"]}
    diff["candidate"] = {"name": cand_name, "artifact_sha256": cand_sha}
    diff["review_status"] = (UNCHANGED if (cand_sha and cand_sha == base["artifact_sha256"])
                             else CHANGED)
    return diff


# ------------------------------------------------------ blocker construction


# blocker code → the existing command + MCP tool name it maps onto (contract
# §8.1). The command→tool map is allowed; the SAFETY VALUES come from policy.
_BLOCKER_ACTIONS: dict[str, dict[str, str | None]] = {
    "CURRENT_FINAL_MISSING": {"command": "manju build --dry-run", "tool": "build"},
    "CURRENT_FINAL_STALE": {"command": "manju build --dry-run", "tool": "build"},
    "CURRENT_FINAL_HASH_MISMATCH": {"command": "manju build", "tool": "build"},
    "CURRENT_FINAL_UNVERIFIABLE": {"command": "manju build", "tool": "build"},
    "RUN_NOT_PROVEN": {"command": "manju build", "tool": "build"},
    "RUN_INCOMPLETE": {"command": "manju tasks manifest", "tool": None},
    "RUN_FAILED": {"command": "manju tasks manifest", "tool": None},
    "RUN_CANCELED": {"command": "manju tasks manifest", "tool": None},
    "RUN_WAITING_USER": {"command": "manju tasks manifest", "tool": None},
    "RUN_NOT_FOUND": {"command": "manju tasks manifest", "tool": None},
    "ATTEMPT_EVIDENCE_CORRUPT": {"command": "manju tasks manifest", "tool": None},
    "SUBMISSION_OUTCOME_UNKNOWN": {"command": "manju tasks", "tool": None},
    "SUBMISSION_RECOVERY_UNAVAILABLE": {"command": "manju tasks", "tool": None},
    "QC_UNAVAILABLE": {"command": "manju qc brief", "tool": "qc_brief"},
    "QC_STALE": {"command": "manju qc brief", "tool": "qc_brief"},
    "QC_REJECTED": {"command": "manju director suggest", "tool": "director_suggest"},
    "REQUIRED_EXPORT_MISSING": {"command": "manju export", "tool": "export"},
    "REQUIRED_EXPORT_STALE": {"command": "manju export", "tool": "export"},
    "REQUIRED_EXPORT_PROBLEM": {"command": "manju export", "tool": "export"},
    "BASELINE_DAMAGED": {"command": "manju exports --baseline", "tool": None},
    "BASELINE_EVIDENCE_CORRUPT": {"command": "manju exports --baseline", "tool": None},
    "BASELINE_EVIDENCE_UNAVAILABLE": {"command": "manju exports --baseline", "tool": None},
    "REGRESSION_REVIEW_REQUIRED": {"command": "manju compare --against-baseline", "tool": None},
}


def _action_safety(tool_name: str | None) -> dict[str, Any]:
    """The action's safety metadata READ from mcp.policy's declared tool policy
    (§8.2) — never a hand-written allowlist. A CLI-only action (no MCP tool) is
    a human action: not auto-runnable, confirmation required."""
    from ..mcp import policy as P
    from ..mcp.tools import TOOL_DEFS

    if not tool_name:
        return {"fix_owner": "human", "safe_to_auto_run": False,
                "requires_confirmation": True, "may_network": False, "may_spend": False}
    pol = {t["name"]: t["policy"] for t in TOOL_DEFS}.get(tool_name)
    if pol is None:
        return {"fix_owner": "human", "safe_to_auto_run": False,
                "requires_confirmation": True, "may_network": False, "may_spend": False}
    may_spend = pol["spend"] != P.NEVER
    may_network = pol["network"] != P.NEVER
    unattended = pol["unattended"]
    requires_confirmation = unattended != P.ALLOW
    safe_to_auto_run = (unattended == P.ALLOW) and not may_spend and not may_network
    if may_spend or unattended == P.DENY:
        fix_owner = "human"
    elif safe_to_auto_run:
        fix_owner = "host_agent"
    else:
        fix_owner = "human"
    return {"fix_owner": fix_owner, "safe_to_auto_run": safe_to_auto_run,
            "requires_confirmation": requires_confirmation,
            "may_network": may_network, "may_spend": may_spend}


def _blocker(code: str, scope: str, detail: str, *, blocking: bool = True,
             requires_human_review: bool = False, warning: bool = False,
             owner: str | None = None) -> dict:
    tool = (_BLOCKER_ACTIONS.get(code) or {}).get("tool")
    safety = _action_safety(tool)
    return {
        "code": code,
        "scope": scope,
        "detail": detail,
        "evidence_refs": [],
        "owner": owner or safety["fix_owner"],
        "safe_to_auto_run": safety["safe_to_auto_run"],
        "blocking": bool(blocking),
        "requires_human_review": bool(requires_human_review),
        "warning": bool(warning),
    }


def _final_health(project: Any, final_path: Path | None) -> list[dict]:
    if final_path is None or not final_path.exists():
        return [_blocker("CURRENT_FINAL_MISSING", "final",
                         "no final has been rendered (renders/final is empty)")]
    if _size(final_path) == 0:
        return [_blocker("CURRENT_FINAL_HASH_MISMATCH", "final", "final is 0 bytes")]
    key_data = _read_key_full(final_path)
    if not key_data or not key_data.get("final_key"):
        return [_blocker("CURRENT_FINAL_HASH_MISMATCH", "final",
                         "missing content-key sidecar (render may be incomplete)")]
    out: list[dict] = []
    # FINAL_ACCEPTANCE F3: the final's bytes must be PROVABLE — a sidecar with
    # no output_sha256 proves nothing, unhashable bytes prove nothing, and a
    # recorded-vs-actual mismatch proves tampering. Only mismatch blocked
    # before; the two unprovable cases silently passed.
    recorded = key_data.get("output_sha256")
    actual = _safe_hash(final_path)
    if not recorded:
        out.append(_blocker("CURRENT_FINAL_UNVERIFIABLE", "final",
                            "the sidecar records no output_sha256 — the final's "
                            "bytes cannot be proven (fail closed)"))
    elif actual is None:
        out.append(_blocker("CURRENT_FINAL_UNVERIFIABLE", "final",
                            "the final's bytes could not be hashed — nothing "
                            "proves they are the rendered ones (fail closed)"))
    elif actual != recorded:
        out.append(_blocker("CURRENT_FINAL_HASH_MISMATCH", "final",
                            "final bytes changed since render (sidecar output_sha256 mismatch)"))
    cur_key, note = _current_final_key(project)
    if cur_key is None:
        out.append(_blocker("CURRENT_FINAL_STALE", "final",
                            "cannot recompute the current content key to prove freshness: "
                            + (note or "unavailable")))
    elif key_data.get("final_key") != cur_key:
        out.append(_blocker("CURRENT_FINAL_STALE", "final",
                            "content key differs from current specs — rebuild to refresh"))
    return out


# WP2 §4.5: the run-manifest terminal_status → (blocker code, detail) map. The
# two success statuses (COMPLETED / COMPLETED_WITH_WARNINGS) are deliberately
# absent — they pass.
_RUN_STATUS_BLOCKERS: dict[str, tuple[str, str]] = {
    "INCOMPLETE": ("RUN_INCOMPLETE",
                   "the run that produced this final never reached a terminal state"),
    "FAILED": ("RUN_FAILED",
               "the run that produced this final terminated FAILED"),
    "CANCELED": ("RUN_CANCELED",
                 "the run that produced this final was CANCELED"),
    "WAITING_USER": ("RUN_WAITING_USER",
                     "the run that produced this final is WAITING_USER — not a terminal success"),
    "NOT_FOUND": ("RUN_NOT_FOUND",
                  "this final claims a run_id with no run evidence at all (NOT_FOUND)"),
}


def _run_blockers(project: Any, final_path: Path | None) -> list[dict]:
    if final_path is None or not final_path.exists():
        return []  # no final at all — _final_health owns CURRENT_FINAL_MISSING
    key_data = _read_key_full(final_path)
    run_id = (key_data or {}).get("run_id")
    if not run_id:
        # FINAL_ACCEPTANCE F2 (reverses the POST_COMPLETION skip): the ABSENCE
        # of a run linkage is itself the signal — no sidecar schema marker is
        # needed. A final nobody can tie to a completed run is not silently a
        # success; a genuine legacy/manual final passes ONLY through the
        # explicit human --accept-known-risk approval (exact-SHA bound,
        # append-only on the verification log).
        return [_blocker("RUN_NOT_PROVEN", "final",
                         "the final's sidecar carries no run_id — no run "
                         "evidence proves how it was produced (fail closed); "
                         "rebuild, or approve with explicit human risk "
                         "acceptance for a legacy/manual final", owner="human")]
    try:
        manifest = build_run_manifest(project, run_id)
    except Exception:
        return [_blocker("ATTEMPT_EVIDENCE_CORRUPT", f"run:{run_id}",
                         "the run manifest could not be derived (fail closed)", owner="human")]
    out: list[dict] = []
    # WP2 §4.5 run gate: ONLY COMPLETED / COMPLETED_WITH_WARNINGS pass. Every
    # honest non-success terminal — INCOMPLETE / FAILED / CANCELED / WAITING_USER
    # — and a run whose evidence is missing entirely (NOT_FOUND) blocks: a final
    # produced by an interrupted / failed / canceled / waiting run must never be
    # blessed as a release baseline. HEAD mapped INCOMPLETE alone.
    status = manifest.get("terminal_status")
    if status in _RUN_STATUS_BLOCKERS:
        code, detail = _RUN_STATUS_BLOCKERS[status]
        out.append(_blocker(code, f"run:{run_id}", detail, owner="human"))
    elif status not in ("COMPLETED", "COMPLETED_WITH_WARNINGS"):
        # any UNKNOWN_LEGACY / unmapped status is not a PROVEN success → block.
        out.append(_blocker("RUN_INCOMPLETE", f"run:{run_id}",
                            f"the run terminal status {status!r} is not a proven success",
                            owner="human"))
    for f in (manifest.get("failures") or []):
        if f.get("code") == "submission_recovery_unavailable":
            out.append(_blocker("SUBMISSION_RECOVERY_UNAVAILABLE",
                                f"attempt:{f.get('attempt_id')}",
                                "a paid-submission recovery consult failed; outcome unverifiable",
                                owner="human"))
    return out


def _submission_blockers(project: Any) -> list[dict]:
    from ..providers import submission as Sub
    from ..runtime.state import RuntimeState
    from .attempts import read_submission_events

    sqlite_exists = (project.root / ".manju" / "state.sqlite").exists()
    # WP1 / WP2 §4.1: append-only submission EVIDENCE is the fact source — a
    # fresh/empty OR absent state.sqlite must NEVER be read as "no remote side
    # effect" (HEAD returned [] the instant the ledger was missing/unavailable).
    # Probe the evidence first: this keeps the genuinely-never-ran-paid project
    # cheap AND ledger-free (test §9.3.26) — no evidence + no ledger ⇒ nothing to
    # gate, and we do NOT create the disposable ledger just to answer a read.
    try:
        sub_events, malformed = read_submission_events(project)
    except Exception:
        return [_blocker("SUBMISSION_RECOVERY_UNAVAILABLE", "project",
                         "paid-submission evidence is unreadable — outcome "
                         "unverifiable (fail closed)", owner="human")]
    # FINAL_ACCEPTANCE F1: a torn/unparseable line has no recoverable
    # submission_id — it could BE a paid submission's record, so the stream can
    # never be read as clean/empty history. Blocks even when no parseable
    # submission event and no ledger exist (the torn line is the evidence).
    if malformed:
        return [_blocker("ATTEMPT_EVIDENCE_CORRUPT", "events",
                         f"the submission evidence stream has {malformed} torn/"
                         "unparseable line(s) — paid outcomes unverifiable "
                         "(fail closed)", owner="human")]
    if not sub_events and not sqlite_exists:
        return []
    # Consume the SAME WP1 projection/verification helper the paid consult uses
    # (never a second recovery algorithm): it folds evidence into the projection
    # when the ledger is fresh/empty, and fail-closes when the consult cannot run.
    try:
        with RuntimeState(project.root) as state:
            proj = state.ensure_submission_projection(project)
    except Exception:
        return [_blocker("SUBMISSION_RECOVERY_UNAVAILABLE", "project",
                         "the paid-submission projection could not be consulted "
                         "(fail closed)", owner="human")]
    if proj.get("status") == "recovery_unavailable":
        return [_blocker("SUBMISSION_RECOVERY_UNAVAILABLE", "project",
                         "paid-submission recovery consult failed — outcome "
                         "unverifiable (fail closed)", owner="human")]
    rows = proj.get("unresolved") or []
    out: list[dict] = []
    for row in rows:
        sid = row["submission_id"]
        shot = row.get("shot")
        scope = f"shot:{shot}" if shot else f"submission:{sid}"
        try:
            events, _ = read_submission_events(project, sid)
            ok, _broken = Sub.verify_chain(events)
        except Exception:
            ok = False
        state_norm = Sub.normalize_state(row.get("state"))
        if not ok or state_norm == Sub.RECOVERY_EVIDENCE_CORRUPT:
            out.append(_blocker("ATTEMPT_EVIDENCE_CORRUPT", scope,
                                "paid-submission evidence chain is corrupt (fail closed)",
                                owner="human"))
        else:
            out.append(_blocker("SUBMISSION_OUTCOME_UNKNOWN", scope,
                                "paid submission outcome is unresolved (never auto-resubmit)",
                                owner="human"))
    return out


def _qc_blockers(project: Any) -> list[dict]:
    try:
        rows = assurance_for_all(project)
    except Exception:
        return [_blocker("QC_UNAVAILABLE", "project",
                         "acceptance could not be derived (fail closed)")]
    out: list[dict] = []
    for a in rows:
        sid = (a.get("subject") or {}).get("id")
        scope = f"shot:{sid}" if sid else "project"
        state = a.get("assurance_state")
        qc = a.get("qc") or {}
        if qc.get("status") == "unavailable":
            out.append(_blocker("QC_UNAVAILABLE", scope,
                                qc.get("reason") or "deterministic QC is unavailable"))
        elif state == "rejected":
            out.append(_blocker("QC_REJECTED", scope,
                                "; ".join(a.get("reasons") or [])[:200] or "QC rejected"))
        elif state == "stale":
            out.append(_blocker("QC_STALE", scope,
                                "review evidence no longer matches the current take bytes"))
    return out


# Non-final deliverables that block when present-and-broken; a MISSING optional
# deliverable does not block first release (contract §7.4 required-deliverables),
# but an enabled-teaser that is missing does.
def _required_export_blockers(project: Any, rows: list) -> list[dict]:
    by_kind = {r.kind: r for r in rows}
    out: list[dict] = []
    for kind in ("srt", "ass", "otio", "jianying", "capcut", "cover", "teaser"):
        row = by_kind.get(kind)
        if row is None:
            continue
        fr = row.freshness
        scope = f"export:{kind}"
        if fr == Freshness.STALE:
            out.append(_blocker("REQUIRED_EXPORT_STALE", scope,
                                f"{kind} exists but is out of date — re-export"))
        elif fr == Freshness.PROBLEMATIC:
            out.append(_blocker("REQUIRED_EXPORT_PROBLEM", scope,
                                f"{kind} is broken: {row.basis}"[:200]))
        elif fr == Freshness.MISSING and kind == "teaser":
            # a teaser MISSING only blocks when packaging actually ENABLED it —
            # the deliverable row's basis says "未启用" when it is not (§7.4).
            if "未启用" not in (row.basis or ""):
                out.append(_blocker("REQUIRED_EXPORT_MISSING", scope,
                                    "an enabled teaser deliverable is missing"))
    return out


def _approval_blocked_message(blocking: list[dict]) -> str:
    """The refusal names every offender (TRISURFACE F-12): "resolve them" with
    only the code left the owner scanning the whole exports table to find WHICH
    deliverables were stale — the assessment knew all along."""
    offenders = ", ".join(
        f"{b['code']}[{b.get('scope', '?')}]" for b in
        sorted(blocking, key=lambda b: (b.get("code", ""), b.get("scope", ""))))
    return (f"approval blocked: {offenders} — resolve them, or pass "
            "--accept-known-risk (human-only) to record the risk and approve anyway")


def _technical_blockers(project: Any, final_path: Path | None,
                        rows: list | None = None) -> list[dict]:
    """The technical (non-regression) gate shared by approval and assessment.
    Composes the delivery / run / submission / QC subsystems — never re-derives
    any of them."""
    rows = rows if rows is not None else deliverables(project)
    out: list[dict] = []
    out += _final_health(project, final_path)
    out += _run_blockers(project, final_path)
    out += _submission_blockers(project)
    out += _qc_blockers(project)
    out += _required_export_blockers(project, rows)
    return out


# --------------------------------------------------------- release assessment


def _next_actions(blockers: list[dict]) -> list[dict]:
    """One next action per actionable blocker, mapped to an existing command,
    with safety metadata READ from the ToolPolicy (§8). De-duplicated by command.
    Never an executable step — a proposal/confirmation reference only."""
    out: list[dict] = []
    seen: set[str] = set()
    for b in blockers:
        if not (b.get("blocking") or b.get("requires_human_review")):
            continue
        spec = _BLOCKER_ACTIONS.get(b["code"])
        if not spec:
            continue
        command = spec["command"]
        if command in seen:
            continue
        seen.add(command)
        safety = _action_safety(spec.get("tool"))
        out.append({
            "command": command,
            "reason_code": b["code"],
            "tool": spec.get("tool"),
            "fix_owner": safety["fix_owner"],
            "safe_to_auto_run": safety["safe_to_auto_run"],
            "requires_confirmation": safety["requires_confirmation"],
            "may_network": safety["may_network"],
            "may_spend": safety["may_spend"],
        })
    return out


def release_assessment(project: Any, rows: list | None = None) -> dict:
    """The composed, instant, read-only release assessment (§7). Additive section
    for the exports JSON — not a separate file, not a new schema. Deterministic
    (no wall-clock fields) so two reads deep-equal; every path project-relative;
    no secret/Authorization/signed URL (§13, tests §9.3.26/27)."""
    rows = rows if rows is not None else deliverables(project)
    candidate_path = project.newest_final_path()
    candidate = {
        "path": project.relpath(candidate_path) if candidate_path else None,
        "sha256": _safe_hash(candidate_path),
    }

    blockers = _technical_blockers(project, candidate_path, rows=rows)

    base = current_baseline(project)
    if base["status"] == EVIDENCE_UNAVAILABLE:
        # WP2 §4.2: a present-but-unreadable baseline log is fail-closed — the
        # release cannot be proven safe against a baseline we cannot even read.
        blockers.append(_blocker("BASELINE_EVIDENCE_UNAVAILABLE", "baseline",
                                 base.get("reason") or "baseline evidence unavailable",
                                 owner="human"))
        regression = {"status": EVIDENCE_UNAVAILABLE, "compare_ref": None}
    elif base["status"] == DAMAGED:
        blockers.append(_blocker("BASELINE_DAMAGED", "baseline",
                                 base.get("damage") or "baseline artifact damaged", owner="human"))
        regression = {"status": DAMAGED, "compare_ref": None}
    elif base["status"] == CORRUPT:
        blockers.append(_blocker("BASELINE_EVIDENCE_CORRUPT", "baseline",
                                 base.get("reason") or "baseline event corrupt", owner="human"))
        regression = {"status": CORRUPT, "compare_ref": None}
    elif base["status"] == VALID:
        cand_sha = candidate["sha256"]
        if cand_sha and cand_sha == base.get("artifact_sha256"):
            regression = {"status": UNCHANGED, "compare_ref": "inline",
                          "baseline_event_id": base["event_id"]}
        else:
            regression = {"status": CHANGED, "compare_ref": "inline",
                          "baseline_event_id": base["event_id"]}
            blockers.append(_blocker(
                "REGRESSION_REVIEW_REQUIRED", "final",
                "candidate differs from the approved baseline — human review required",
                blocking=False, requires_human_review=True, owner="human"))
    else:  # NO_BASELINE — first release is not a technical failure (§7.3, test 23)
        regression = {"status": NO_BASELINE, "compare_ref": None}

    # FINAL_ACCEPTANCE F5: torn line(s) in the verification log block the
    # release EVEN beside a valid baseline event — a torn line could BE the
    # superseding approval. (The CORRUPT branch above already carries its own
    # evidence blocker; don't double-report.)
    if base.get("malformed_count") and base["status"] != CORRUPT:
        blockers.append(_blocker(
            "BASELINE_EVIDENCE_CORRUPT", "baseline",
            f"the verification log has {base['malformed_count']} torn/"
            "unparseable line(s) — the true latest approval is unprovable "
            "(fail closed)", owner="human"))

    hard = [b for b in blockers if b.get("blocking")]
    review = [b for b in blockers if b.get("requires_human_review")]
    # F3: a candidate whose bytes cannot be hashed is never ready, whatever
    # else holds — no byte proof, no release.
    ready = (candidate_path is not None) and (candidate.get("sha256") is not None) \
        and not hard and not review

    return {
        "candidate": candidate,
        "baseline": ({"status": base["status"], "event_id": base["event_id"]}
                     if base.get("event_id") else {"status": base["status"]}),
        "ready": ready,
        "blockers": blockers,
        "regression_review": regression,
        "next_actions": _next_actions(blockers),
    }
