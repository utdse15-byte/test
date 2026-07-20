"""Local owner-friction usage report (Priority 2 item 11).

Derives a LOCAL-ONLY report from the operational history a project already
keeps — ``.manju/jobs.jsonl`` (job transitions) and ``reports/failures.jsonl``
(structured failures). No telemetry, no network, no build effect: this reads
existing local data and returns a plain dict for ``manju doctor --usage-report``
to render. It is a derived VIEW — never runtime truth, never a build input.

Honesty rule: a metric that the locally-persisted history does not actually
carry is reported as ``None`` with a short ``notes`` reason, never fabricated.
``jobs.jsonl`` persists a compact transition trail (id/kind/state/timestamps),
so durations, per-kind state counts and cancellation counts are exact; retry
lineage, waiting-user timing and billing disposition are not in that trail and
are reported as unavailable rather than guessed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import jobkinds


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # naive legacy stamp → UTC — mixing naive and aware datetimes in a
        # subtraction raises TypeError and would crash the derived report
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    k = (len(sorted_vals) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return round(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac, 2)


_TERMINAL = {"done", "failed", "canceled", "interrupted"}


def _failure_category(cause: str) -> str:
    c = (cause or "").lower()
    if "rate" in c or "429" in c or "限流" in c:
        return "rate_limit"
    if "timeout" in c or "超时" in c:
        return "timeout"
    if "content" in c or "rejected" in c or "审核" in c or "nsfw" in c:
        return "content_rejected"
    if "ffmpeg" in c or "ffprobe" in c:
        return "ffmpeg"
    if "proxy" in c or "代理" in c:
        return "proxy"
    if "path" in c or "路径" in c:
        return "path"
    return "other"


def usage_report(project: Any) -> dict[str, Any]:
    """Compute the local usage report for ``project`` (or a bare root path)."""
    root = Path(getattr(project, "root", project))
    jobs = _read_jsonl(root / ".manju" / "jobs.jsonl")
    failures = _read_jsonl(root / "reports" / "failures.jsonl")

    # Keep the LAST transition line per job id (its terminal state).
    last_by_id: dict[str, dict[str, Any]] = {}
    for rec in jobs:
        jid = rec.get("id")
        if isinstance(jid, str):
            last_by_id[jid] = rec

    durations: dict[str, list[float]] = {}
    state_counts: dict[str, dict[str, int]] = {}
    cancellations = 0
    possibly_billed = 0
    paid = jobkinds.paid_kinds()

    for rec in last_by_id.values():
        kind = rec.get("kind") or "?"
        state = rec.get("state") or "?"
        state_counts.setdefault(kind, {})
        state_counts[kind][state] = state_counts[kind].get(state, 0) + 1
        if state == "canceled":
            cancellations += 1
            if kind in paid:
                possibly_billed += 1  # upper bound: a paid kind that canceled
        if state in _TERMINAL:
            start = _parse_ts(rec.get("started"))
            end = _parse_ts(rec.get("finished"))
            if start and end and end >= start:
                durations.setdefault(kind, []).append((end - start).total_seconds())

    duration_by_kind: dict[str, dict[str, Any]] = {}
    for kind, vals in durations.items():
        vals.sort()
        duration_by_kind[kind] = {
            "count": len(vals),
            "p50_s": _percentile(vals, 0.50),
            "p95_s": _percentile(vals, 0.95),
        }

    failure_categories: dict[str, int] = {}
    for f in failures:
        cat = _failure_category(str(f.get("cause", "")))
        failure_categories[cat] = failure_categories.get(cat, 0) + 1

    qc_failures = state_counts.get("qc", {}).get("failed", 0)

    return {
        "source": {
            "jobs_jsonl": (root / ".manju" / "jobs.jsonl").exists(),
            "failures_jsonl": (root / "reports" / "failures.jsonl").exists(),
        },
        "jobs_analyzed": len(last_by_id),
        "duration_by_kind": duration_by_kind,
        "state_counts_by_kind": state_counts,
        "cancellations": {
            "total": cancellations,
            # jobs.jsonl does not persist the billing disposition, so these are
            # not derivable from it; item 8's disposition lives on the live Job.
            "confirmed": None,
            "unknown": None,
            "notes": "confirmed/unknown split needs the live Job.billing record",
        },
        "possibly_billed_more_than_once": possibly_billed,
        "possibly_billed_notes": "upper bound: paid kinds that canceled (remote may have billed)",
        "provider_failure_categories": failure_categories,
        "qc_failure_count": qc_failures,
        "retry_count_by_operation": None,
        "waiting_user": None,
        "cache_hit_rate": None,
        "unavailable_notes": (
            "retry lineage, waiting_user timing and cache-hit rate are not in "
            "jobs.jsonl/failures.jsonl; they need richer local history to report honestly"
        ),
        "telemetry": "none — local-only, no network transmission",
    }
