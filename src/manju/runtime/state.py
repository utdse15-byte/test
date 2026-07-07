"""Runtime state — SQLite that holds only *rebuildable* state (§1-⑥, §3, §8).

Two tables, both disposable:

- ``runs`` — the per-call cost ledger and run log (§8.3). One row per generation
  attempt (succeeded or failed), carrying cost/currency, failure kind, the
  ``remote_job_id`` and the take(s) produced. It also persists the pre-flight
  ``estimated_cost`` (the *事前* dry-run figure), so ``manju spend`` can show
  estimate-vs-actual — the delta that calibrates trust in the ask_before gate.
  ``manju status`` reads it to show the project's accumulated spend.
- ``jobs`` — submitted-but-not-yet-finished remote jobs (§8.1). The
  ``remote_job_id`` is written the instant ``submit`` returns, so a process that
  is killed / loses network / reboots resumes *polling* the same job instead of
  resubmitting — an interrupted long task never double-charges (§8.1, §14).

§3 discipline and the one honest caveat
----------------------------------------
Everything the engine needs long-term lives in text + media: a *finished* job
always leaves a take sidecar carrying ``remote.job_id``, ``cost`` and
``currency`` (§4.2), so :meth:`RuntimeState.rebuild` can re-derive the whole run
ledger from disk. The single piece of state that is NOT recoverable from text +
media is a job still *in flight* when ``.manju/`` is lost — it never got as far
as writing a sidecar. The accepted worst case (§3) is therefore exactly one
resubmission of an in-flight job after a runtime-directory loss: bounded, cheap,
and far preferable to maintaining a second source of truth that could drift.
:meth:`rebuild` accordingly leaves the ``jobs`` table alone except for pruning
pending jobs older than seven days, which are treated as stale.

Standard-library ``sqlite3`` only; WAL journaling; UTF-8; pathlib throughout.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # avoid an import cycle; only needed for type hints
    from ..core.container import Project

STALE_PENDING_JOB_DAYS = 7  # §3: pending jobs older than this are pruned on rebuild
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    shot          TEXT,
    provider      TEXT,
    params        TEXT,
    status        TEXT NOT NULL,
    failure_kind  TEXT,
    cost          REAL NOT NULL DEFAULT 0,
    currency      TEXT,
    remote_job_id TEXT,
    take          TEXT,
    error         TEXT,
    estimated_cost REAL,
    failure_id    TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    remote_job_id TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    shot          TEXT NOT NULL,
    params        TEXT,
    status        TEXT NOT NULL,
    submitted_at  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


def _dumps(value: Any) -> str | None:
    """Canonical JSON for a params dict (or None). Non-ASCII kept, keys sorted
    so the same params always serialize identically."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class RuntimeState:
    """SQLite-backed runtime bookkeeping for one project.

    Usage as a context manager closes the connection on exit::

        with RuntimeState(project.root) as state:
            state.record_run(shot="S001", provider="cloud_x", status="succeeded")

    ``now_fn`` is an injectable zero-arg clock returning a timezone-aware
    ``datetime`` (defaults to ``datetime.now(timezone.utc)``); all timestamps
    are stored as ISO-8601 UTC strings.
    """

    def __init__(
        self,
        project_root: Path | str,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.root = Path(project_root)
        self.db_path = self.root / ".manju" / "state.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._now: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    # ---------------------------------------------------------- lifecycle

    def _create_schema(self) -> None:
        with self._conn:  # idempotent: CREATE TABLE IF NOT EXISTS
            self._conn.executescript(_SCHEMA)
        # Forward-upgrade a ledger created before ``estimated_cost`` existed
        # (rows written by round-Q code have the old column list). Idempotent:
        # on a fresh/already-upgraded DB the column is present and sqlite raises
        # "duplicate column name", which we swallow. The ledger is §3-disposable,
        # so a failed upgrade degrades (no estimates) but never raises.
        for column, coltype in (("estimated_cost", "REAL"), ("failure_id", "TEXT")):
            try:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {coltype}")
            except sqlite3.Error:
                pass

    def _ts(self) -> str:
        return self._now().isoformat()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "RuntimeState":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False

    # --------------------------------------------------------------- runs

    def record_run(
        self,
        *,
        shot: str | None,
        provider: str | None,
        status: str,
        params: dict | None = None,
        failure_kind: str | None = None,
        cost: float = 0.0,
        currency: str | None = None,
        remote_job_id: str | None = None,
        take: str | None = None,
        error: str | None = None,
        estimated_cost: float | None = None,
        failure_id: str | None = None,
    ) -> int:
        """Append one ledger row (§8.3); return its autoincrement id.

        ``estimated_cost`` is the pre-flight *事前* figure for this call, stored
        so ``manju spend`` can show estimate-vs-actual. It stays ``None`` (honest)
        when no estimate was available — never coerced to 0.

        ``failure_id`` cross-references the structured record in
        ``reports/failures.jsonl`` (goal 10) so the ledger's one-line ``error``
        (what ``manju tasks`` shows) and the full failure evidence never drift —
        the reason surfaced in the JOB view matches the failure record exactly."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs "
                "(ts, shot, provider, params, status, failure_kind, cost, "
                " currency, remote_job_id, take, error, estimated_cost, failure_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._ts(),
                    shot,
                    provider,
                    _dumps(params),
                    status,
                    failure_kind,
                    float(cost or 0.0),
                    currency,
                    remote_job_id,
                    take,
                    error,
                    None if estimated_cost is None else float(estimated_cost),
                    failure_id,
                ),
            )
        return int(cur.lastrowid)

    def run_log(self, n: int = 50) -> list[dict]:
        """Most recent runs first (§8.3)."""
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(n),)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_runs(self) -> int:
        """Total ledger rows via SELECT COUNT(*) — `status` (§8.3, §10) and
        `spend` need the number of runs, not the rows themselves (avoids
        fetching up to 100k rows just to len() them)."""
        row = self._conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()
        return int(row["c"])

    def total_cost(self) -> tuple[float, str | None]:
        """``(sum of runs.cost, currency)``. Currency is the single currency in
        use, or ``None`` when mixed or none is recorded (§8.3)."""
        total = self._conn.execute(
            "SELECT COALESCE(SUM(cost), 0.0) AS total FROM runs"
        ).fetchone()["total"]
        codes = [
            r["currency"]
            for r in self._conn.execute(
                "SELECT DISTINCT currency FROM runs WHERE currency IS NOT NULL"
            ).fetchall()
        ]
        currency = codes[0] if len(codes) == 1 else None
        return (float(total or 0.0), currency)

    def cost_by_provider(self) -> list[dict]:
        """Per-provider spend, highest first — the breakdown behind
        :meth:`total_cost` (their costs sum to ``total_cost()[0]``). Each row is
        ``{provider, cost, currency, runs}``; ``currency`` is ``None`` when that
        provider recorded more than one currency (§8.3), mirroring total_cost."""
        rows = self._conn.execute(
            "SELECT provider, "
            "       COALESCE(SUM(cost), 0.0) AS cost, "
            "       COUNT(*) AS runs, "
            "       COUNT(DISTINCT currency) AS ccount, "
            "       MAX(currency) AS currency "
            "FROM runs GROUP BY provider ORDER BY cost DESC, provider"
        ).fetchall()
        return [
            {
                "provider": r["provider"],
                "cost": float(r["cost"] or 0.0),
                "runs": int(r["runs"]),
                "currency": r["currency"] if r["ccount"] == 1 else None,
            }
            for r in rows
        ]

    def cost_by_shot(self) -> list[dict]:
        """Per-shot spend, highest first — the money view's by-shot breakdown
        (`manju spend`), the sibling of :meth:`cost_by_provider` (their costs
        both sum to ``total_cost()[0]``). Each row is ``{shot, cost, runs}``.
        One SQL GROUP BY so `spend` shares this query layer with `tasks` rather
        than re-aggregating the run log in Python (§8.3)."""
        rows = self._conn.execute(
            "SELECT shot, "
            "       COALESCE(SUM(cost), 0.0) AS cost, "
            "       COUNT(*) AS runs "
            "FROM runs GROUP BY shot ORDER BY cost DESC, shot"
        ).fetchall()
        return [
            {"shot": r["shot"], "cost": float(r["cost"] or 0.0), "runs": int(r["runs"])}
            for r in rows
        ]

    # --------------------------------------------------------------- jobs

    def open_job(
        self,
        remote_job_id: str,
        *,
        provider: str,
        shot: str,
        params: dict | None = None,
    ) -> None:
        """Persist a submitted job as ``pending`` (§8.1 "提交成功即写入").

        ``INSERT OR REPLACE`` so a re-submit of the same id is idempotent."""
        ts = self._ts()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO jobs "
                "(remote_job_id, provider, shot, params, status, submitted_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (remote_job_id, provider, shot, _dumps(params), ts, ts),
            )

    def close_job(self, remote_job_id: str, status: str) -> None:
        """Mark a job terminal (``"succeeded"`` | ``"failed"``); it drops out of
        :meth:`pending_jobs` but the row is kept for the record."""
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE remote_job_id = ?",
                (status, self._ts(), remote_job_id),
            )

    def pending_jobs(
        self, *, shot: str | None = None, provider: str | None = None
    ) -> list[dict]:
        """Still-pending jobs, newest first, optionally filtered (§8.1)."""
        sql = "SELECT * FROM jobs WHERE status = 'pending'"
        args: list[Any] = []
        if shot is not None:
            sql += " AND shot = ?"
            args.append(shot)
        if provider is not None:
            sql += " AND provider = ?"
            args.append(provider)
        sql += " ORDER BY submitted_at DESC, rowid DESC"
        rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ rebuild

    def rebuild(self, project: "Project") -> dict:
        """Rebuild disposable state from disk (§3).

        Wipes ``runs`` entirely and re-derives one ``succeeded`` row per take
        from every take sidecar on disk: provider and cost/currency/
        remote_job_id come from ``sidecar.remote`` when present, the take name
        and the sidecar's ``created_at`` supply the rest. The ``jobs`` table is
        left alone except that pending jobs older than seven days are pruned as
        stale (see the module docstring for why in-flight jobs are the one piece
        of state not recoverable from text + media).

        Returns ``{"runs": <count>, "pending_jobs": <count after pruning>}``.
        """
        with self._conn:
            self._conn.execute("DELETE FROM runs")
            run_count = 0
            for shot_id in project.shot_ids():
                for take in project.takes(shot_id):
                    sidecar = take.sidecar
                    remote = sidecar.remote
                    cost = 0.0
                    currency: str | None = None
                    job_id: str | None = None
                    if remote is not None:
                        job_id = remote.job_id
                        if remote.cost is not None:
                            cost = float(remote.cost)
                            currency = remote.currency
                    self._conn.execute(
                        "INSERT INTO runs "
                        "(ts, shot, provider, params, status, failure_kind, cost, "
                        " currency, remote_job_id, take, error, estimated_cost) "
                        # estimated_cost is NULL: sidecars carry the ACTUAL cost
                        # only, never the pre-flight estimate — honest (§3).
                        "VALUES (?, ?, ?, ?, 'succeeded', NULL, ?, ?, ?, ?, NULL, NULL)",
                        (
                            sidecar.created_at or self._ts(),
                            shot_id,
                            sidecar.provider,
                            _dumps(dict(sidecar.params) or None),
                            cost,
                            currency,
                            job_id,
                            take.name,
                        ),
                    )
                    run_count += 1

            cutoff = (self._now() - timedelta(days=STALE_PENDING_JOB_DAYS)).isoformat()
            self._conn.execute(
                "DELETE FROM jobs WHERE status = 'pending' AND submitted_at < ?",
                (cutoff,),
            )
            pending = self._conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status = 'pending'"
            ).fetchone()["c"]

        return {"runs": run_count, "pending_jobs": int(pending)}
