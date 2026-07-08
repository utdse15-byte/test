"""`manju spend` — accumulated-spend visibility (§8.3 事后逐笔记账, made visible).

§8.3 成本护栏 stacks three guardrails over "every cloud build may spend real
money":

    事前  `manju build --dry-run` — per-task cost estimate *before* committing
    事中  project.yaml `budget.limit` — cumulative spend trips the queue to
          waiting_user *while* running
    事后  逐笔记账 — every call is booked into the run ledger (provider, cost,
          currency, remote_job_id) *after* the fact; the take sidecar carries
          that call's cost too

The first two layers guard before/while the money moves. This module is the read
side of the third — the *事后* (after-the-fact) ledger — turned into a structured
report: the running total, grouped by provider and by shot, plus the most recent
runs.

`spend` is the MONEY view; `manju tasks` (round Q) is the JOB/QUEUE view. They
are deliberately NOT the same command: `tasks` answers "what ran / what is still
in flight", `spend` answers "where did the money go". Both read the same
disposable ledger and share the same query layer — the ledger path here derives
its per-provider / per-shot breakdowns from ``RuntimeState.cost_by_provider`` /
``cost_by_shot`` (the same helper the `tasks` footer uses), so the two commands
can never drift on the numbers.

Estimate-vs-actual (COMPETITIVE-UX-STUDY "spend delta"): the ledger now also
persists each run's pre-flight `estimated_cost` (the *事前* figure that used to
vanish after the build). When any ledger row carries one, the report surfaces
`estimated_total` (their sum) and `delta` (`total - estimated_total`) — the
column that calibrates trust in the ask_before gate — and each recent row carries
its own `estimated_cost`. Sidecars record only the ACTUAL cost, so the §3 sidecar
fallback leaves all three honestly `None`.

§3 disposability: that ledger lives in `.manju/state.sqlite`, which may be
deleted at any moment and rebuilt from text + media. So every SQLite access here
is best-effort — any failure (or a ledger with no rows yet) silently degrades to
deriving spend from the take sidecars on disk (`sidecar.remote.cost`), exactly the
§3 rebuild source `manju status` falls back to. The ledger is authoritative only
while it actually holds rows; otherwise the sidecars are; when neither carries
data the report is ``"empty"``. Spend visibility never raises — a disposable cache
must not be able to break a read.
"""

from __future__ import annotations

from typing import Any

from ..core.container import Project

# How many of the most recent runs the report surfaces (newest first).
_RECENT_LIMIT = 20


def spend_report(project: Project) -> dict:
    """Structured accumulated-spend report for one project (§8.3 事后).

    Shape::

        {
          "total": float,                # summed cost across all runs — a RAW
                                         #   cross-currency sum when mixed (goal 79:
                                         #   kept for backward compat; use by_currency
                                         #   for an honest breakdown)
          "currency": str | None,        # the single spend currency, or None
                                         #   when mixed / none recorded
          "by_currency": [{"currency", "cost": float, "runs": int}, ...],  # cost desc
                                         # (goal 79) — never merges different
                                         # currencies into one number; ALWAYS use
                                         # this to display spend, not total+currency
          "budget_limit": float | None,  # project.yaml budget.limit
          "estimated_total": float | None,  # sum of estimates over the COVERED
                                            #   rows only (goal 67), or None when
                                            #   no row carries one
          "delta": float | None,         # (actual - estimated) over ONLY the rows
                                         #   that carry an estimate (goal 67) — never
                                         #   "all actual" minus "some estimated"
          "delta_coverage": str | None,  # "覆盖 N/M 条记录" (goal 67), None with delta
          "by_provider": [{"provider", "runs": int, "cost": float}, ...],  # cost desc
          "by_shot":     [{"shot", "runs": int, "cost": float}, ...],      # cost desc
          "recent": [{"ts","shot","provider","take","cost","currency","status",
                      "estimated_cost"}, ...],  # estimated_cost may be None
          "source": "ledger" | "sidecars" | "empty",
        }

    ``recent`` is newest first, capped at the 20 most recent runs.
    ``estimated_total``/``delta``/``delta_coverage`` are populated only when at
    least one ledger row carries an estimate; the sidecar fallback (§3) leaves
    all three ``None`` because sidecars record the actual cost only.

    The run ledger (``RuntimeState``) is authoritative whenever it holds rows.
    Because that SQLite state is disposable (§3), every ledger access is wrapped
    in try/except: any failure — or an empty/never-populated ledger — degrades to
    deriving spend from the take sidecars on disk (the same §3 rebuild source
    ``manju status`` uses). ``"empty"`` when neither carries data.
    """
    try:
        budget_limit = project.load_config().budget.limit
    except Exception:
        budget_limit = None

    report = _from_ledger(project)
    if report is None:
        report = _from_sidecars(project)
    report["budget_limit"] = budget_limit
    return report


def _aggregate(pairs: list[tuple[Any, float]], key_name: str) -> list[dict]:
    """Group ``(key, cost)`` pairs into ``[{key_name, "runs", "cost"}, ...]``,
    sorted by cost descending (stable — ties keep first-seen order). Used for the
    sidecar fallback, where there is no ledger to GROUP BY."""
    acc: dict[Any, list] = {}
    for key, cost in pairs:
        slot = acc.setdefault(key, [0, 0.0])
        slot[0] += 1
        slot[1] += float(cost or 0.0)
    rows = [{key_name: key, "runs": n, "cost": cost} for key, (n, cost) in acc.items()]
    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows


def _from_ledger(project: Project) -> dict | None:
    """Build the report from the run ledger, or ``None`` when the ledger is
    unavailable (§3 disposable — any SQLite failure) or holds no rows yet.

    The per-provider / per-shot breakdowns come from
    ``RuntimeState.cost_by_provider`` / ``cost_by_shot`` — the SAME query layer
    the `tasks` footer uses, so the money view and the job view never disagree on
    the numbers (they are two lenses over one ledger). All SQLite access is inside
    the try/except; the pure-Python delta math that follows operates on
    already-materialized rows, so a genuine bug there is not swallowed as "ledger
    unavailable".
    """
    try:
        from ..runtime.state import RuntimeState

        with RuntimeState(project.root) as state:
            n = state.count_runs()
            if n == 0:
                return None  # ledger present but empty -> fall back to sidecars
            rows = state.run_log(n)  # every row, newest first
            total, currency = state.total_cost()
            by_currency = state.total_cost_by_currency()  # goal 79
            by_provider = [
                {"provider": r["provider"], "runs": r["runs"], "cost": r["cost"]}
                for r in state.cost_by_provider()
            ]
            by_shot = [
                {"shot": r["shot"], "runs": r["runs"], "cost": r["cost"]}
                for r in state.cost_by_shot()
            ]
    except Exception:
        return None

    # Spend delta (goal 67): comparing "actual total over ALL rows" against
    # "estimated total over only the rows that carry one" is not a real delta
    # when some rows predate the estimate feature (or a provider never wrote
    # one) — it silently mixes an all-rows figure with a subset figure. Fixed:
    # both sides of the subtraction are restricted to the SAME covered subset;
    # `total` itself is untouched (still the true all-rows actual).
    covered = [r for r in rows if r["estimated_cost"] is not None]
    if covered:
        estimated_total: float | None = float(sum(r["estimated_cost"] for r in covered))
        actual_covered = float(sum(r["cost"] for r in covered))
        delta: float | None = actual_covered - estimated_total
        delta_coverage = f"覆盖 {len(covered)}/{len(rows)} 条记录"
    else:
        estimated_total = None
        delta = None
        delta_coverage = None

    return {
        "total": float(total),
        "currency": currency,
        # goal 79: the honest per-currency breakdown — display ALL currencies
        # (e.g. 12 CNY + 2 USD) rather than a single merged/mislabeled number.
        "by_currency": by_currency,
        "budget_limit": None,  # filled in by spend_report
        "estimated_total": estimated_total,
        "delta": delta,
        # goal 67: how much of the ledger the delta actually covers — a delta
        # over 3/10 records is a different claim than one over 10/10.
        "delta_coverage": delta_coverage,
        "by_provider": by_provider,
        "by_shot": by_shot,
        "recent": [
            {
                "ts": r["ts"],
                "shot": r["shot"],
                "provider": r["provider"],
                "take": r["take"],
                "cost": r["cost"],
                "currency": r["currency"],
                "status": r["status"],
                "estimated_cost": r["estimated_cost"],
            }
            for r in rows[:_RECENT_LIMIT]
        ],
        "source": "ledger",
    }


def _from_sidecars(project: Project) -> dict:
    """Derive spend from the take sidecars on disk — the §3 rebuild source and
    the fallback when the ledger has no rows.

    Walks takes exactly as ``build/status.py`` does (``sidecar.remote.cost``),
    counting one run per take on disk (mirroring ``RuntimeState.rebuild``, which
    writes one succeeded row per take). Sidecars carry ``created_at``, so
    ``recent`` is populated from it; ``"empty"`` when there are no takes at all.
    """
    provider_pairs: list[tuple[Any, float]] = []
    shot_pairs: list[tuple[Any, float]] = []
    recent: list[dict] = []
    currency_totals: dict[str | None, float] = {}
    total = 0.0
    takes_seen = 0

    for shot_id in project.shot_ids():
        for take in project.takes(shot_id):
            takes_seen += 1
            sidecar = take.sidecar
            remote = sidecar.remote
            cost = 0.0
            currency: str | None = None
            if remote is not None and remote.cost:
                cost = float(remote.cost)
                currency = remote.currency
                currency_totals[currency] = currency_totals.get(currency, 0.0) + cost
            total += cost
            provider_pairs.append((sidecar.provider, cost))
            shot_pairs.append((shot_id, cost))
            recent.append(
                {
                    "ts": sidecar.created_at,
                    "shot": shot_id,
                    "provider": sidecar.provider,
                    "take": take.name,
                    "cost": cost,
                    "currency": currency,
                    "status": "succeeded",
                    # sidecars record ACTUAL cost only — no pre-flight estimate.
                    "estimated_cost": None,
                }
            )

    if takes_seen == 0:
        return {
            "total": 0.0,
            "currency": None,
            "by_currency": [],
            "budget_limit": None,
            "estimated_total": None,
            "delta": None,
            "delta_coverage": None,
            "by_provider": [],
            "by_shot": [],
            "recent": [],
            "source": "empty",
        }

    recent.sort(key=lambda r: r["ts"] or "", reverse=True)
    by_currency = [
        {"currency": cur, "cost": round(cost, 6)}
        for cur, cost in sorted(currency_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {
        "total": total,
        "currency": next(iter(currency_totals)) if len(currency_totals) == 1 else None,
        # goal 79: the honest per-currency breakdown, same shape as the ledger path.
        "by_currency": by_currency,
        "budget_limit": None,  # filled in by spend_report
        # sidecars never carry the pre-flight estimate, so the delta is None.
        "estimated_total": None,
        "delta": None,
        "delta_coverage": None,
        "by_provider": _aggregate(provider_pairs, "provider"),
        "by_shot": _aggregate(shot_pairs, "shot"),
        "recent": recent[:_RECENT_LIMIT],
        "source": "sidecars",
    }
