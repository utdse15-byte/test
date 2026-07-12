"""`manju perf` — the run performance report (§8.6 可观测性, DERIVED VIEW).

§8.6 lists what a single-user local build MAY surface for observability —
stage duration, cache hit rate, provider time, render time, QC time, cost,
disk usage, slowest node, error categories, a performance report. This module
is that report, and it is a PURE DERIVED VIEW over evidence that already
exists: the single ``events.jsonl`` attempt stream (DR03C, see
``build.attempts``). It adds NO instrumentation — no timers, no counters, no
new event kinds — and the engine never reads it back (§8.7 原则:
"性能提示不是调度事实" — a performance hint is never a scheduling fact). The
enforceable form of that pin is that nothing under ``build/`` imports this
module; only the CLI does, lazily.

Recorded sources it reads (all already on the stream):

* ``duration_ms`` — every terminal ``stage_attempt`` carries the monotonic
  wall time of that attempt (``attempts.AttemptHandle._emit``). This is the
  ONLY duration source; the report never re-times anything.
* ``stage`` / ``state`` — the three emitted stages (``generate``, ``render``
  and the run-level ``build``/``run`` envelope) and the 11 attempt states.
* ``SKIPPED_CACHE_HIT`` — a first-class recorded state (emitted by
  ``build.graph`` for a reused final AND for a per-shot cache/manual skip), so
  a cache hit is DERIVABLE per stage; it is not estimated.
* ``executor.kind == "provider"`` — set by ``providers.registry`` on every
  provider generation attempt, so provider vs local time is DERIVABLE where the
  rows distinguish; render / cache-skip attempts carry no provider executor.
* ``cost`` — the attempt cost block (actual|estimated, never double-counted;
  the money rule is ``attempts._cost_of``, reused here so the numbers can
  never drift from the RunManifest).
* ``unit.shot`` — per-shot rollup of duration + cost.
* ``failure.category`` / ``failure.code`` — error-category grouping.
* ``outputs[].bytes`` — recorded output size (a HONEST proxy, surfaced as
  ``recorded_output_bytes``; it is NOT filesystem disk usage — see below).

HONESTY: a §8.6 metric with no recorded source is listed in ``unavailable``
with the missing-source name and is NEVER estimated. Two metrics are
structurally unavailable on today's stream:

* ``qc_time`` — QC runs are referenced only as ``evidence_refs`` (report paths)
  on the run-level attempt; no ``stage="qc"`` attempt is ever emitted, so there
  is no recorded QC duration to sum.
* ``disk_usage`` — only per-output ``bytes`` are recorded; a true disk-usage
  figure (project size, proxy/cache/intermediate footprint, dedup-aware) would
  require a live filesystem stat, which a deterministic derived view must not
  read. ``recorded_output_bytes`` is surfaced as the honest recorded proxy.

DETERMINISM: the report is a projection of ``build.attempts.build_run_manifest``
(the canonical evidence projection) plus aggregates computed from the manifest's
attempt records. It surfaces NO wall-clock field — the manifest's
``generated_at`` is deliberately dropped and wall duration is parsed from the
recorded ``started_at``/``ended_at`` stamps (never ``ts``, never ``now()``) — so
the output is byte-stable for a given ``events.jsonl``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..build.attempts import (
    EVENTS_FILE,
    FAILED,
    SKIPPED_CACHE_HIT,
    SUCCEEDED,
    _cost_of,  # the ONE money rule (prefer actual, never double-count) — reused
    _root,
    build_run_manifest,
)

# How many slowest stages / attempts the report surfaces (highest duration
# first). A local single-user report — a short, scannable list, not a trace.
_SLOWEST_N = 5

# The run-level envelope attempt (its duration_ms is ~the whole wall time, and
# its span IS the wall duration). It is EXCLUDED from every per-stage / compute
# aggregation so the wall envelope never double-counts the stages inside it.
_ENVELOPE_STAGE = "build"
_ENVELOPE_ACTION = "run"

# The §8.6 metrics that have no recorded source on today's attempt stream. Each
# is listed honestly with the missing-source name — NEVER estimated. This list
# is structural (independent of any one run), so it is emitted even for an
# empty / not-found report.
_UNAVAILABLE: list[dict[str, str]] = [
    {
        "metric": "qc_time",
        "missing_source": (
            "no timed stage=\"qc\" attempt is emitted on events.jsonl — QC runs "
            "are referenced only via evidence_refs (report paths) on the "
            "run-level attempt, never timed on the stream"
        ),
    },
    {
        "metric": "disk_usage",
        "missing_source": (
            "no filesystem disk-usage measurement is recorded in the attempt "
            "evidence; only per-output bytes are recorded (surfaced as "
            "recorded_output_bytes), which is NOT project/cache/proxy disk usage"
        ),
    },
]


def _is_envelope(rec: dict) -> bool:
    """The single run-level terminal attempt (stage=build, action=run)."""
    return rec.get("stage") == _ENVELOPE_STAGE and rec.get("action") == _ENVELOPE_ACTION


def _parse_ts(value: Any) -> datetime | None:
    """Parse a recorded ISO stamp (``_now_iso`` format, tz-aware, seconds
    precision) tolerantly — a missing / malformed stamp yields ``None`` and
    simply does not participate in the wall window."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _duration_ms(rec: dict) -> int | None:
    """The recorded ``duration_ms`` for one attempt, or ``None`` when the row
    carries none (legacy / foreign) — never invented."""
    v = rec.get("duration_ms")
    if isinstance(v, bool):  # bool is an int subclass; a stray bool is not a duration
        return None
    if isinstance(v, (int, float)):
        return int(v)
    return None


def _shot_of(rec: dict) -> str | None:
    unit = rec.get("unit")
    if isinstance(unit, dict):
        shot = unit.get("shot")
        if shot is not None:
            return str(shot)
    return None


def _is_provider(rec: dict) -> bool:
    ex = rec.get("executor")
    return isinstance(ex, dict) and ex.get("kind") == "provider"


def _provider_id(rec: dict) -> str | None:
    ex = rec.get("executor")
    if isinstance(ex, dict):
        pid = ex.get("provider_id")
        if pid is not None:
            return str(pid)
    return None


def _empty_report(run_id: str | None, terminal_status: str) -> dict:
    """A structured, never-crashing empty report — every key present with an
    empty / zero value, and the structural ``unavailable`` list still filled
    (those metrics are unavailable regardless of whether the run exists)."""
    return {
        "run_id": run_id,
        "found": False,
        "terminal_status": terminal_status,
        "command": None,
        "target": None,
        "mode": None,
        "attempt_count": 0,
        "malformed_lines": 0,
        "wall": {"ms": None, "started_at": None, "ended_at": None, "source": None},
        "stages": [],
        "slowest_stages": [],
        "slowest_attempts": [],
        "time_split": {
            "provider_ms": 0,
            "local_ms": 0,
            "render_ms": 0,
            "provider_attempts": 0,
            "local_attempts": 0,
            "attempts_missing_duration": 0,
            "by_provider": [],
            "note": (
                "provider = executor.kind=='provider'; local = every other "
                "non-envelope attempt; the run-level build/run envelope is excluded"
            ),
        },
        "cache": {"hits": 0, "eligible": 0, "rate": None, "by_stage": [],
                  "source": "SKIPPED_CACHE_HIT state (recorded per-stage)"},
        "cost": {"totals": [], "by_provider": [], "by_shot": [],
                 "source": "attempt cost blocks (actual|estimated, never double-counted)"},
        "by_shot": [],
        "errors": {"count": 0, "by_category": [],
                   "source": "failure.category / failure.code"},
        "recorded_output_bytes": 0,
        "unavailable": [dict(u) for u in _UNAVAILABLE],
    }


def _latest_run_id(project: Any) -> str | None:
    """The most recently written run on the stream: the ``run_id`` of the LAST
    parseable event line that carries one. events.jsonl is strictly append
    order under the events flock (and builds are serialized by build_lock), so
    file order IS run order — the last run_id seen is the latest run. Format
    independent (never assumes the run_id embeds a sortable timestamp) and
    deterministic given the file."""
    import json

    path = _root(project) / EVENTS_FILE
    if not path.exists():
        return None
    latest: str | None = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue  # torn line — never the authority for "latest"
            detail = rec.get("detail") if isinstance(rec, dict) else None
            if isinstance(detail, dict) and detail.get("run_id"):
                latest = str(detail["run_id"])
    return latest


def run_performance(project: Any, run_id: str | None = None) -> dict:
    """The run performance report for ONE run (§8.6), a pure derived view.

    ``run_id=None`` reports the latest run (last on the stream). A run id with
    no events yields a structured empty report (``found: False``,
    ``terminal_status: NOT_FOUND``) — never a crash. ``project`` may be a
    ``Project`` or a raw path (read-only; mirrors ``build.attempts``).

    The report is deterministic for a given ``events.jsonl``: it derives from
    ``build_run_manifest`` (dropping its wall-clock ``generated_at``) plus
    aggregates over the manifest's attempt records, and reads no wall clock.
    See the module docstring for the recorded-source map and the two
    structurally ``unavailable`` metrics.
    """
    if run_id is None:
        run_id = _latest_run_id(project)
        if run_id is None:
            return _empty_report(None, "NOT_FOUND")

    manifest = build_run_manifest(project, run_id)
    if manifest.get("terminal_status") == "NOT_FOUND":
        return _empty_report(run_id, "NOT_FOUND")

    records: list[dict] = list(manifest.get("attempts") or [])
    # non-envelope attempts drive every per-stage / compute aggregation.
    work = [r for r in records if not _is_envelope(r)]

    # ---- wall duration ----------------------------------------------------
    # Prefer the run-level envelope's own span (started_at -> ended_at); else
    # the min/max of the stage attempts' started_at/ended_at. NEVER `ts` (that
    # is the write stamp, not the work span) and never `now()`.
    wall = _wall(records)

    # ---- per-stage duration + status counts -------------------------------
    stages = _stages(work)

    # ---- slowest stages / attempts ---------------------------------------
    slowest_stages = [
        {"stage": s["stage"], "total_ms": s["total_ms"]}
        for s in sorted(stages, key=lambda s: s["total_ms"], reverse=True)
        if s["total_ms"] > 0
    ][:_SLOWEST_N]
    slowest_attempts = _slowest_attempts(work)

    # ---- provider vs local time split ------------------------------------
    time_split = _time_split(work)

    # ---- cache hit rate (derived from SKIPPED_CACHE_HIT) ------------------
    cache = _cache(work)

    # ---- cost: totals reuse the manifest; per-provider/shot recomputed ----
    cost = _cost(work, manifest)

    # ---- per-shot rollup --------------------------------------------------
    by_shot = _by_shot(work)

    # ---- error categories -------------------------------------------------
    errors = _errors(manifest.get("failures") or [])

    # ---- recorded output bytes (honest proxy, NOT disk usage) -------------
    recorded_output_bytes = _output_bytes(records)

    return {
        "run_id": run_id,
        "found": True,
        "terminal_status": manifest.get("terminal_status"),
        "command": manifest.get("command"),
        "target": manifest.get("target"),
        "mode": manifest.get("mode"),
        "attempt_count": manifest.get("attempt_count", len(records)),
        "malformed_lines": manifest.get("malformed_lines", 0),
        "wall": wall,
        "stages": stages,
        "slowest_stages": slowest_stages,
        "slowest_attempts": slowest_attempts,
        "time_split": time_split,
        "cache": cache,
        "cost": cost,
        "by_shot": by_shot,
        "errors": errors,
        "recorded_output_bytes": recorded_output_bytes,
        "unavailable": [dict(u) for u in _UNAVAILABLE],
    }


# ------------------------------------------------------------- aggregations


def _wall(records: list[dict]) -> dict:
    envelope = next((r for r in records if _is_envelope(r)), None)
    if envelope is not None:
        start = _parse_ts(envelope.get("started_at"))
        end = _parse_ts(envelope.get("ended_at"))
        if start is not None and end is not None:
            return {"ms": _span_ms(start, end),
                    "started_at": envelope.get("started_at"),
                    "ended_at": envelope.get("ended_at"),
                    "source": "run_envelope"}
    starts = [s for s in (_parse_ts(r.get("started_at")) for r in records) if s]
    ends = [e for e in (_parse_ts(r.get("ended_at")) for r in records) if e]
    if starts and ends:
        earliest, latest = min(starts), max(ends)
        # pair the min/max with their source strings (re-find, cheap + honest)
        started_at = min((r.get("started_at") for r in records
                          if _parse_ts(r.get("started_at")) == earliest), default=None)
        ended_at = max((r.get("ended_at") for r in records
                        if _parse_ts(r.get("ended_at")) == latest), default=None)
        return {"ms": _span_ms(earliest, latest), "started_at": started_at,
                "ended_at": ended_at, "source": "event_span"}
    return {"ms": None, "started_at": None, "ended_at": None, "source": None}


def _span_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))


def _stages(work: list[dict]) -> list[dict]:
    acc: dict[str, dict] = {}
    for r in work:
        stage = str(r.get("stage") or "?")
        slot = acc.setdefault(stage, {"stage": stage, "attempts": 0, "total_ms": 0,
                                      "min_ms": None, "max_ms": None,
                                      "status_counts": {}, "missing_duration": 0})
        slot["attempts"] += 1
        state = str(r.get("state") or "?")
        slot["status_counts"][state] = slot["status_counts"].get(state, 0) + 1
        d = _duration_ms(r)
        if d is None:
            slot["missing_duration"] += 1
            continue
        slot["total_ms"] += d
        slot["min_ms"] = d if slot["min_ms"] is None else min(slot["min_ms"], d)
        slot["max_ms"] = d if slot["max_ms"] is None else max(slot["max_ms"], d)
    rows = list(acc.values())
    for slot in rows:
        timed = slot["attempts"] - slot["missing_duration"]
        slot["mean_ms"] = round(slot["total_ms"] / timed, 3) if timed else None
        # stable, sorted status_counts for byte-deterministic output
        slot["status_counts"] = dict(sorted(slot["status_counts"].items()))
    # deterministic order: slowest first, then stage name for ties
    rows.sort(key=lambda s: (-s["total_ms"], s["stage"]))
    return rows


def _slowest_attempts(work: list[dict]) -> list[dict]:
    timed = [r for r in work if _duration_ms(r) is not None]
    # slowest first; tie-break by (stage, attempt_id) for determinism
    timed.sort(key=lambda r: (-(_duration_ms(r) or 0), str(r.get("stage") or ""),
                              str(r.get("attempt_id") or "")))
    out: list[dict] = []
    for r in timed[:_SLOWEST_N]:
        row = {"attempt_id": r.get("attempt_id"), "stage": r.get("stage"),
               "state": r.get("state"), "duration_ms": _duration_ms(r)}
        shot = _shot_of(r)
        if shot is not None:
            row["shot"] = shot
        out.append(row)
    return out


def _time_split(work: list[dict]) -> dict:
    provider_ms = local_ms = render_ms = 0
    provider_attempts = local_attempts = missing = 0
    by_provider: dict[str, dict] = {}
    for r in work:
        d = _duration_ms(r)
        if d is None:
            missing += 1
        if _is_provider(r):
            provider_attempts += 1
            if d is not None:
                provider_ms += d
                pid = _provider_id(r) or "?"
                slot = by_provider.setdefault(
                    pid, {"provider_id": pid, "attempts": 0, "total_ms": 0})
                slot["attempts"] += 1
                slot["total_ms"] += d
        else:
            local_attempts += 1
            if d is not None:
                local_ms += d
        if str(r.get("stage")) == "render" and d is not None:
            render_ms += d
    by_provider_rows = sorted(by_provider.values(),
                              key=lambda s: (-s["total_ms"], s["provider_id"]))
    return {
        "provider_ms": provider_ms,
        "local_ms": local_ms,
        "render_ms": render_ms,
        "provider_attempts": provider_attempts,
        "local_attempts": local_attempts,
        "attempts_missing_duration": missing,
        "by_provider": by_provider_rows,
        "note": (
            "provider = executor.kind=='provider'; local = every other "
            "non-envelope attempt; the run-level build/run envelope is excluded"
        ),
    }


def _cache(work: list[dict]) -> dict:
    # A cache hit is a SKIPPED_CACHE_HIT attempt; the "eligible" denominator for
    # a stage's hit rate is the cache-decidable population there — the attempts
    # that either produced fresh (SUCCEEDED) or were skipped (SKIPPED_CACHE_HIT).
    by_stage: dict[str, dict] = {}
    hits = eligible = 0
    for r in work:
        state = r.get("state")
        if state not in (SUCCEEDED, SKIPPED_CACHE_HIT):
            continue
        stage = str(r.get("stage") or "?")
        slot = by_stage.setdefault(stage, {"stage": stage, "hits": 0, "eligible": 0})
        slot["eligible"] += 1
        eligible += 1
        if state == SKIPPED_CACHE_HIT:
            slot["hits"] += 1
            hits += 1
    rows = []
    for slot in sorted(by_stage.values(), key=lambda s: s["stage"]):
        slot["rate"] = round(slot["hits"] / slot["eligible"], 4) if slot["eligible"] else None
        rows.append(slot)
    return {
        "hits": hits,
        "eligible": eligible,
        "rate": round(hits / eligible, 4) if eligible else None,
        "by_stage": rows,
        "source": "SKIPPED_CACHE_HIT state (recorded per-stage)",
    }


def _cost(work: list[dict], manifest: dict) -> dict:
    # Totals reuse the manifest verbatim (single source of truth for the money
    # rule); per-provider / per-shot are recomputed with the SAME _cost_of rule.
    totals = [dict(c) for c in (manifest.get("costs") or [])]
    by_provider: dict[tuple, dict] = {}
    by_shot: dict[tuple, dict] = {}
    for r in work:
        amount, currency = _cost_of(r)
        if not amount:
            continue
        pid = _provider_id(r)
        if pid is not None:
            key = (pid, currency or "?")
            slot = by_provider.setdefault(
                key, {"provider_id": pid, "currency": currency, "amount": 0.0})
            slot["amount"] = round(slot["amount"] + amount, 6)
        shot = _shot_of(r)
        if shot is not None:
            key = (shot, currency or "?")
            slot = by_shot.setdefault(
                key, {"shot": shot, "currency": currency, "amount": 0.0})
            slot["amount"] = round(slot["amount"] + amount, 6)
    return {
        "totals": totals,
        "by_provider": sorted(by_provider.values(),
                              key=lambda s: (-s["amount"], s["provider_id"])),
        "by_shot": sorted(by_shot.values(), key=lambda s: (-s["amount"], s["shot"])),
        "source": "attempt cost blocks (actual|estimated, never double-counted)",
    }


def _by_shot(work: list[dict]) -> list[dict]:
    acc: dict[str, dict] = {}
    for r in work:
        shot = _shot_of(r)
        if shot is None:
            continue
        slot = acc.setdefault(shot, {"shot": shot, "attempts": 0, "total_ms": 0,
                                     "cost_amount": 0.0, "currency": None,
                                     "states": {}})
        slot["attempts"] += 1
        state = str(r.get("state") or "?")
        slot["states"][state] = slot["states"].get(state, 0) + 1
        d = _duration_ms(r)
        if d is not None:
            slot["total_ms"] += d
        amount, currency = _cost_of(r)
        if amount:
            slot["cost_amount"] = round(slot["cost_amount"] + amount, 6)
            slot["currency"] = slot["currency"] or currency
    rows = list(acc.values())
    for slot in rows:
        slot["states"] = dict(sorted(slot["states"].items()))
    rows.sort(key=lambda s: (-s["total_ms"], s["shot"]))
    return rows


def _errors(failures: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for f in failures:
        if not isinstance(f, dict):
            continue
        category = str(f.get("category") or "?")
        code = str(f.get("code") or "?")
        slot = by_category.setdefault(category, {"category": category, "count": 0,
                                                 "_codes": {}})
        slot["count"] += 1
        slot["_codes"][code] = slot["_codes"].get(code, 0) + 1
    rows = []
    for slot in sorted(by_category.values(), key=lambda s: (-s["count"], s["category"])):
        codes = [{"code": c, "count": n}
                 for c, n in sorted(slot.pop("_codes").items(), key=lambda kv: (-kv[1], kv[0]))]
        slot["codes"] = codes
        rows.append(slot)
    return {
        "count": sum(r["count"] for r in rows),
        "by_category": rows,
        "source": "failure.category / failure.code",
    }


def _output_bytes(records: list[dict]) -> int:
    total = 0
    for r in records:
        for out in (r.get("outputs") or []):
            if isinstance(out, dict):
                b = out.get("bytes")
                if isinstance(b, bool):
                    continue
                if isinstance(b, (int, float)):
                    total += int(b)
    return total
