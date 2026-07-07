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
          "total": float,                # summed cost across all runs
          "currency": str | None,        # the single spend currency, or None
                                         #   when mixed / none recorded
          "budget_limit": float | None,  # project.yaml budget.limit
          "estimated_total": float | None,  # sum of non-None run estimates, or
                                            #   None when no estimate exists
          "delta": float | None,         # total - estimated_total (None if no
                                         #   estimate exists) — spend-delta column
          "by_provider": [{"provider", "runs": int, "cost": float}, ...],  # cost desc
          "by_shot":     [{"shot", "runs": int, "cost": float}, ...],      # cost desc
          "recent": [{"ts","shot","provider","take","cost","currency","status",
                      "estimated_cost"}, ...],  # estimated_cost may be None
          "source": "ledger" | "sidecars" | "empty",
        }

    ``recent`` is newest first, capped at the 20 most recent runs.
    ``estimated_total``/``delta`` are populated only when at least one ledger row
    carries an estimate; the sidecar fallback (§3) leaves both ``None`` because
    sidecars record the actual cost only.

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

    # Spend delta: sum the estimates actually present (rows predating the feature
    # carry None), then compare against the actual total. Both stay None when no
    # row carries an estimate, so the delta column simply doesn't render.
    est_values = [r["estimated_cost"] for r in rows if r["estimated_cost"] is not None]
    if est_values:
        estimated_total: float | None = float(sum(est_values))
        delta: float | None = float(total) - estimated_total
    else:
        estimated_total = None
        delta = None

    return {
        "total": float(total),
        "currency": currency,
        "budget_limit": None,  # filled in by spend_report
        "estimated_total": estimated_total,
        "delta": delta,
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
    currencies: set[str] = set()
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
                currencies.add(currency)
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
            "budget_limit": None,
            "estimated_total": None,
            "delta": None,
            "by_provider": [],
            "by_shot": [],
            "recent": [],
            "source": "empty",
        }

    recent.sort(key=lambda r: r["ts"] or "", reverse=True)
    return {
        "total": total,
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "budget_limit": None,  # filled in by spend_report
        # sidecars never carry the pre-flight estimate, so the delta is None.
        "estimated_total": None,
        "delta": None,
        "by_provider": _aggregate(provider_pairs, "provider"),
        "by_shot": _aggregate(shot_pairs, "shot"),
        "recent": recent[:_RECENT_LIMIT],
        "source": "sidecars",
    }
