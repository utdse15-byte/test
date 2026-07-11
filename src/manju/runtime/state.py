"""Runtime state — SQLite that holds only *rebuildable* state (§1-⑥, §3, §8).

Three tables, all disposable:

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
- ``intents`` (goal 27) — a PRE-SUBMIT breadcrumb written BEFORE the paid
  ``submit()`` call, closing the one window ``jobs`` cannot cover: a job id
  does not exist yet, so a crash between "we are about to call submit()" and
  "submit() returned a job id" leaves NOTHING in ``jobs`` to resume — a
  restart would resubmit blindly, and the remote side may already have
  received (and be billing) the first attempt. An intent starts ``open``; a
  submit that returns cleanly (success OR a caught local exception) resolves
  it (``resolved`` / ``error``) in the SAME process. Only a genuine crash
  leaves it ``open`` forever — :meth:`dangling_intents` finds those so the
  caller can flag them instead of resubmitting blindly (§8.1 double-charge
  window closed).

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

That worst case is exactly what goal 27 narrows further: ``jobs``/``intents``
are best-effort for READS (§3, disposable, rebuildable) but a FAILURE TO WRITE
a pre-submit intent or a post-submit job id must never be silently swallowed —
see ``providers/base.py: CloudProvider.generate()`` for the loud (never blocks
generation, but never silent) handling.

Standard-library ``sqlite3`` only; WAL journaling; UTF-8; pathlib throughout.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
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
    provider      TEXT NOT NULL,
    remote_job_id TEXT NOT NULL,
    shot          TEXT NOT NULL,
    params        TEXT,
    status        TEXT NOT NULL,
    submitted_at  TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (provider, remote_job_id)
);
CREATE TABLE IF NOT EXISTS intents (
    id            TEXT PRIMARY KEY,
    provider      TEXT NOT NULL,
    shot          TEXT NOT NULL,
    params_hash   TEXT,
    ts            TEXT NOT NULL,
    remote_job_id TEXT,
    status        TEXT NOT NULL DEFAULT 'open'
);
"""

# DR06: the additive submission-identity/admission-state columns on the SAME
# intents table (ruling 4, following the #47 migration precedent). A UNIQUE
# index on submission_id (NULLs stay distinct so legacy/goal-27 rows never
# collide) is the SQLite-level guarantee behind the atomic DISPATCHING claim.
# ``state`` is the admission state machine (providers.submission.STATES);
# ``updated_ts`` is display-only. Legacy rows read state NULL -> UNKNOWN_LEGACY.
_INTENT_SUBMISSION_COLUMNS = (
    ("submission_id", "TEXT"),
    ("request_digest", "TEXT"),
    ("state", "TEXT"),
    ("updated_ts", "TEXT"),
)


def _dumps(value: Any) -> str | None:
    """Canonical JSON for a params dict (or None). Non-ASCII kept, keys sorted
    so the same params always serialize identically."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class MalformedSubmissionEvidence(RuntimeError):
    """FINAL_ACCEPTANCE F1 — the submission evidence stream contains torn or
    unparseable lines. A torn line has no recoverable submission_id, so it
    could belong to ANY submission: a strict consult must treat the whole
    stream as unverifiable (``submission_recovery_unavailable``, stage
    ``evidence_malformed``) rather than as empty history. Not a schema — an
    internal control-flow signal between the strict restore and its callers."""


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
        self._migrate_legacy_jobs_table()  # #47: must run BEFORE CREATE TABLE IF NOT EXISTS
        with self._conn:  # idempotent: CREATE TABLE IF NOT EXISTS
            self._conn.executescript(_SCHEMA)
        # Forward-upgrade a ledger created before ``estimated_cost`` existed
        # (rows written by round-Q code have the old column list). Idempotent:
        # on a fresh/already-upgraded DB the column is present and sqlite raises
        # "duplicate column name", which we swallow. The ledger is §3-disposable,
        # so a failed upgrade degrades (no estimates) but never raises.
        # DR03C: ``attempt_id`` cross-references the stage_attempt event that
        # produced this take (events.jsonl is the attempt HISTORY; this column is
        # only a convenience index into it). Same idempotent additive migration
        # precedent as #47 / estimated_cost above — a fresh/upgraded DB already
        # has the column and sqlite raises "duplicate column name", swallowed.
        for column, coltype in (("estimated_cost", "REAL"), ("failure_id", "TEXT"),
                                ("attempt_id", "TEXT")):
            try:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {coltype}")
            except sqlite3.Error:
                pass
        # DR06: additively grow the intents table with the submission columns
        # (same idempotent "duplicate column name -> swallow" precedent), then
        # the UNIQUE index behind the atomic claim. A pre-DR06 DB gains empty
        # columns (its rows read state UNKNOWN_LEGACY); a fresh/upgraded DB
        # no-ops. §3-disposable: a failed upgrade degrades, never raises.
        for column, coltype in _INTENT_SUBMISSION_COLUMNS:
            try:
                with self._conn:
                    self._conn.execute(f"ALTER TABLE intents ADD COLUMN {column} {coltype}")
            except sqlite3.Error:
                pass
        try:
            with self._conn:
                self._conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_intents_submission "
                    "ON intents(submission_id)")
        except sqlite3.Error:
            pass

    def _migrate_legacy_jobs_table(self) -> None:
        """#47: ``jobs`` used to key on ``remote_job_id`` ALONE. Two providers
        that happen to return the same job id (both are free to mint their own
        id namespace) would silently replace each other's pending row —
        ``INSERT OR REPLACE`` on a single-column PK cannot tell them apart.
        The fix is a compound PRIMARY KEY ``(provider, remote_job_id)``.

        SQLite cannot ALTER a PRIMARY KEY in place, so a legacy table (created
        by code before this round) is renamed aside — never dropped outright,
        so the raw rows are inspectable — and ``CREATE TABLE IF NOT EXISTS``
        (right after this call) mints a fresh, empty, correctly-keyed table.
        This is the SAME accepted worst case the module docstring already
        names for any ``.manju/`` loss: at most one resubmission of a job that
        was genuinely in flight at migration time — never silently-wrong
        provider attribution, which is what the old schema risked. The §3
        rebuild path (``manju rebuild-index`` / :meth:`rebuild`) is exactly
        the recovery a user runs after this — it re-derives ``runs`` from take
        sidecars and prunes/reports ``jobs`` against the fresh table, so a
        legacy DB comes back to a consistent (if pending-jobs-emptied) state
        with no manual SQL required."""
        try:
            cols = self._conn.execute("PRAGMA table_info(jobs)").fetchall()
        except sqlite3.Error:
            return
        if not cols:
            return  # no jobs table yet (fresh DB) — CREATE TABLE below mints it
        pk_cols = {c["name"] for c in cols if c["pk"]}
        if pk_cols == {"provider", "remote_job_id"}:
            return  # already the round-W shape
        try:
            with self._conn:
                self._conn.execute(
                    f"ALTER TABLE jobs RENAME TO jobs_legacy_{int(time.time())}"
                )
        except sqlite3.Error:
            pass  # disposable state (§3): a failed migration must never raise

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
        attempt_id: str | None = None,
    ) -> int:
        """Append one ledger row (§8.3); return its autoincrement id.

        ``estimated_cost`` is the pre-flight *事前* figure for this call, stored
        so ``manju spend`` can show estimate-vs-actual. It stays ``None`` (honest)
        when no estimate was available — never coerced to 0.

        ``failure_id`` cross-references the structured record in
        ``reports/failures.jsonl`` (goal 10) so the ledger's one-line ``error``
        (what ``manju tasks`` shows) and the full failure evidence never drift —
        the reason surfaced in the JOB view matches the failure record exactly.

        ``attempt_id`` (DR03C) cross-references the ``stage_attempt`` event in
        events.jsonl that produced this take, so ``manju tasks`` can surface the
        same attempt identity the evidence stream carries. ``None`` for legacy
        rows and for any run recorded outside a build's evidence context."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs "
                "(ts, shot, provider, params, status, failure_kind, cost, "
                " currency, remote_job_id, take, error, estimated_cost, failure_id, "
                " attempt_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    attempt_id,
                ),
            )
        return int(cur.lastrowid)

    def run_log(self, n: int = 50) -> list[dict]:
        """Most recent runs first (§8.3)."""
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(n),)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: int) -> dict | None:
        """One ledger row by its autoincrement id, or ``None`` (goal: CLI
        ``manju tasks retry <id>`` — the run ledger has no LIVE job registry
        the way the GUI's JobRunner does, so retry is sourced from this row's
        recorded shot/provider/params rather than a re-enqueued job)."""
        row = self._conn.execute(
            "SELECT * FROM runs WHERE id = ?", (int(run_id),)
        ).fetchone()
        return dict(row) if row is not None else None

    def count_runs(self) -> int:
        """Total ledger rows via SELECT COUNT(*) — `status` (§8.3, §10) and
        `spend` need the number of runs, not the rows themselves (avoids
        fetching up to 100k rows just to len() them)."""
        row = self._conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()
        return int(row["c"])

    def total_cost(self) -> tuple[float, str | None]:
        """``(sum of runs.cost, currency)``. Currency is the single currency in
        use, or ``None`` when mixed or none is recorded (§8.3).

        The float itself is a RAW cross-currency sum when multiple currencies
        are in play (e.g. 10 CNY + 2 USD sums to a meaningless 12) — kept only
        for backward compatibility with callers that need a single number.
        Use :meth:`total_cost_by_currency` (goal 79) for an honest breakdown
        that never merges different currencies into one total."""
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

    def total_cost_by_currency(self) -> list[dict]:
        """Per-currency totals (goal 79) — the honest replacement for a single
        summed number when spend spans multiple currencies: 10 CNY + 2 USD
        must never collapse into a meaningless "12". Highest cost first; a
        NULL currency (a row that never recorded one) groups under ``None``."""
        rows = self._conn.execute(
            "SELECT currency, COALESCE(SUM(cost), 0.0) AS cost, COUNT(*) AS runs "
            "FROM runs GROUP BY currency ORDER BY cost DESC"
        ).fetchall()
        return [
            {"currency": r["currency"], "cost": float(r["cost"] or 0.0),
             "runs": int(r["runs"])}
            for r in rows
        ]

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

        ``INSERT OR REPLACE`` so a re-submit of the same id is idempotent —
        keyed on ``(provider, remote_job_id)`` (#47), so two providers that
        happen to mint the same job id string never collide."""
        ts = self._ts()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO jobs "
                "(provider, remote_job_id, shot, params, status, submitted_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (provider, remote_job_id, shot, _dumps(params), ts, ts),
            )

    def close_job(self, remote_job_id: str, status: str, *, provider: str) -> None:
        """Mark a job terminal (``"succeeded"`` | ``"failed"``); it drops out of
        :meth:`pending_jobs` but the row is kept for the record.

        ``provider`` is required (#47): closing by ``remote_job_id`` alone
        cannot tell two providers' same-string job ids apart, exactly the
        cross-provider collision the compound primary key exists to prevent."""
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? "
                "WHERE provider = ? AND remote_job_id = ?",
                (status, self._ts(), provider, remote_job_id),
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

    # -------------------------------------------------------------- intents

    def open_intent(self, *, provider: str, shot: str,
                    params_hash: str | None = None,
                    submission_id: str | None = None,
                    request_digest: str | None = None,
                    state: str | None = None) -> str:
        """Persist a PRE-SUBMIT intent (§8.1 double-submit window, goal 27):
        written BEFORE the paid ``submit()`` call so a crash between "we are
        about to spend money" and "we know the remote job id" leaves a
        breadcrumb — the interval :meth:`open_job` cannot cover because the
        job id does not exist yet. Returns a local intent id (never a remote
        one); pass it to :meth:`resolve_intent` once ``submit()`` returns (or
        raises).

        DR06 (additive): the paid cloud path also threads ``submission_id`` /
        ``request_digest`` / ``state`` (typically PREPARED) so the SAME row is
        the admission-state projection. Legacy/goal-27 callers omit them and get
        byte-identical behavior (state NULL -> UNKNOWN_LEGACY on read). This is
        the write whose FAILURE the paid path treats as fail-closed (ruling 5):
        it re-raises so the caller can refuse to submit."""
        intent_id = uuid.uuid4().hex
        ts = self._ts()
        with self._conn:
            self._conn.execute(
                "INSERT INTO intents (id, provider, shot, params_hash, ts, "
                "remote_job_id, status, submission_id, request_digest, state, "
                "updated_ts) VALUES (?, ?, ?, ?, ?, NULL, 'open', ?, ?, ?, ?)",
                (intent_id, provider, shot, params_hash, ts,
                 submission_id, request_digest, state, ts if state else None),
            )
        return intent_id

    # ------------------------------------------------ DR06 submission projection

    def claim_dispatching(self, submission_id: str) -> bool:
        """The ATOMIC admission claim (ruling 4): a single SQLite CAS
        ``UPDATE ... SET state='DISPATCHING' WHERE submission_id=? AND
        state='PREPARED'`` under a transaction, returning whether exactly one
        row changed. This is multi-process safe (SQLite row locking is the
        guarantee, NOT an in-memory lock): if two processes race the same
        PREPARED submission, exactly one sees rowcount==1 and proceeds to the
        paid submit; the loser sees False and must NOT submit."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE intents SET state = 'DISPATCHING', updated_ts = ? "
                "WHERE submission_id = ? AND state = 'PREPARED'",
                (self._ts(), submission_id),
            )
        return cur.rowcount == 1

    def set_submission_state(self, submission_id: str, new_state: str, *,
                             expected_state: str | None = None,
                             remote_job_id: str | None = None) -> bool:
        """Transition a submission's state (ruling 4). When ``expected_state`` is
        given the update is a CAS on it (returns whether it applied); otherwise
        it applies unconditionally to the named submission. ``remote_job_id`` is
        recorded when supplied (e.g. on ADMITTED). Returns whether a row changed.

        The caller validates transition LEGALITY via ``providers.submission``;
        this method is the persistence primitive."""
        sql = ("UPDATE intents SET state = ?, updated_ts = ?"
               + (", remote_job_id = ?" if remote_job_id is not None else "")
               + " WHERE submission_id = ?"
               + (" AND state = ?" if expected_state is not None else ""))
        args: list[Any] = [new_state, self._ts()]
        if remote_job_id is not None:
            args.append(remote_job_id)
        args.append(submission_id)
        if expected_state is not None:
            args.append(expected_state)
        with self._conn:
            cur = self._conn.execute(sql, args)
        return cur.rowcount == 1

    def get_submission(self, submission_id: str) -> dict | None:
        """One submission's intent row by submission_id, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM intents WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def submissions(self, *, shot: str | None = None, provider: str | None = None,
                    states: tuple[str, ...] | None = None,
                    request_digest: str | None = None) -> list[dict]:
        """Submission intent rows (state IS NOT NULL — i.e. DR06-minted, never a
        legacy goal-27 row), newest first, filtered by any of shot / provider /
        state set / request_digest. The one query behind the fresh-submit
        correlation (ruling 8), ``tasks`` unresolved view, and recovery."""
        sql = "SELECT * FROM intents WHERE state IS NOT NULL"
        args: list[Any] = []
        if shot is not None:
            sql += " AND shot = ?"
            args.append(shot)
        if provider is not None:
            sql += " AND provider = ?"
            args.append(provider)
        if request_digest is not None:
            sql += " AND request_digest = ?"
            args.append(request_digest)
        if states:
            sql += " AND state IN (%s)" % ",".join("?" * len(states))
            args.extend(states)
        sql += " ORDER BY updated_ts DESC, ts DESC, rowid DESC"
        return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def unresolved_submissions(self) -> list[dict]:
        """Every submission still needing resolution — state in
        DISPATCHING / ADMITTED / OUTCOME_UNKNOWN (ruling 4/11). Newest first.
        The projection ``manju tasks --json`` surfaces and ``rebuild`` restores."""
        from ..providers.submission import UNRESOLVED_STATES

        return self.submissions(states=tuple(sorted(UNRESOLVED_STATES)))

    def resolve_intent(self, intent_id: str, remote_job_id: str | None) -> None:
        """Fold a submit's outcome back onto its intent (goal 27): a
        ``remote_job_id`` closes the double-submit window cleanly
        (``resolved`` — :meth:`jobs`/:meth:`pending_jobs` owns resume from
        here); ``None`` means ``submit()`` raised BEFORE returning an id,
        handled locally in the SAME process (``error`` — safe, nothing to
        flag). An intent that never reaches this call (a genuine crash) stays
        ``open`` — see :meth:`dangling_intents`."""
        with self._conn:
            if remote_job_id:
                self._conn.execute(
                    "UPDATE intents SET remote_job_id = ?, status = 'resolved' "
                    "WHERE id = ?",
                    (remote_job_id, intent_id),
                )
            else:
                self._conn.execute(
                    "UPDATE intents SET status = 'error' WHERE id = ?",
                    (intent_id,),
                )

    def dangling_intents(
        self, *, shot: str | None = None, provider: str | None = None
    ) -> list[dict]:
        """Intents still ``open`` (goal 27) — a submit call that started and
        never resolved IN THIS PROCESS (the process died before
        :meth:`resolve_intent` could run). The remote side MAY have already
        received and be billing that attempt; a caller finding one here
        should flag it for human review rather than resubmit blindly.
        Newest first, optionally filtered."""
        sql = "SELECT * FROM intents WHERE status = 'open'"
        args: list[Any] = []
        if shot is not None:
            sql += " AND shot = ?"
            args.append(shot)
        if provider is not None:
            sql += " AND provider = ?"
            args.append(provider)
        sql += " ORDER BY ts DESC, rowid DESC"
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

        # DR06 (ruling 4): SQLite is a rebuildable projection of the events
        # truth — restore any UNRESOLVED submission (DISPATCHING / ADMITTED-
        # without-terminal / OUTCOME_UNKNOWN) from the submission_state event
        # stream so a .manju/ loss cannot silently forget an in-flight or
        # ambiguous paid submission. Best-effort: a read hiccup degrades to no
        # restore, never raises.
        restored = self._restore_submissions_from_events(project)
        return {"runs": run_count, "pending_jobs": int(pending),
                "submissions_restored": restored}

    def _restore_submissions_from_events(self, project: "Project", *,
                                         shot: str | None = None,
                                         provider: str | None = None,
                                         strict: bool = False) -> int:
        """Reconstruct each submission's state from the events stream and
        re-project the UNRESOLVED ones into the intents table. Events are the
        source of truth (a state change emits its event around/before the SQLite
        write — the §8.4 order), so events win on reconcile.

        P0 WP3 §5.2: the projected state is the tail of the LONGEST VALID PREFIX
        of the per-submission hash chain (``providers.submission.project_chain``)
        — NEVER "last event wins". A valid PREPARED→DISPATCHING prefix followed
        by a corrupt line restores DISPATCHING (the consult then blocks a fresh
        submit); a chain with NO valid prefix restores the
        RECOVERY_EVIDENCE_CORRUPT sentinel (blocks until `tasks
        attach-remote-job`/`abandon`). A corrupt tail can no longer forge a
        resolved state and silently unblock a resubmit.

        ``shot`` / ``provider`` (POST_COMPLETION WP1) scope the restore to one
        unit's chains — the SAME algorithm, an additive filter — so the paid
        consult recovers only the shot+provider it is about (correctness-first:
        a full-log read is acceptable, an unrelated unit is never blocked).
        ``strict=True`` propagates an evidence read/verify failure instead of
        swallowing it to 0, so :meth:`ensure_submission_projection` can classify
        it as ``submission_recovery_unavailable`` rather than an empty set."""
        try:
            from ..build.attempts import read_submission_events
            from ..providers.submission import (
                UNRESOLVED_STATES,
                project_chain,
            )
        except Exception:
            if strict:
                raise
            return 0
        try:
            records, _malformed = read_submission_events(project)
        except Exception:
            if strict:
                raise
            return 0
        # FINAL_ACCEPTANCE F1: a torn/unparseable line carries NO recoverable
        # submission_id — it could belong to ANY submission, so under a strict
        # consult the whole stream is untrustworthy evidence. Never interpreted
        # as empty history. (Non-strict rebuild still restores what parses: the
        # explicit recovery path must not be bricked, and the consult re-checks
        # the stream on the very next paid attempt anyway.)
        if strict and _malformed:
            raise MalformedSubmissionEvidence(
                f"{_malformed} torn/unparseable line(s) in the submission "
                f"evidence stream")
        # group per submission_id in FILE order (= emission/chain order)
        chains: dict[str, list[dict]] = {}
        for rec in records:
            sid = rec.get("submission_id")
            if sid:
                chains.setdefault(sid, []).append(rec)
        if shot is not None or provider is not None:
            # a submission's identity is stable across its chain; take it from
            # the first event that carries the field (a later event may omit it).
            def _match(events: list[dict]) -> bool:
                c_shot = next((e.get("shot") for e in events if e.get("shot")), None)
                c_prov = next((e.get("provider_id") for e in events
                               if e.get("provider_id")), None)
                if shot is not None and c_shot != shot:
                    return False
                if provider is not None and c_prov != provider:
                    return False
                return True

            chains = {sid: evs for sid, evs in chains.items() if _match(evs)}
        restored = 0
        with self._conn:
            for sid, events in chains.items():
                proj = project_chain(events)
                state = proj.get("state")
                if state is None or state not in UNRESOLVED_STATES:
                    continue  # resolved by a VALID prefix, or nothing to restore
                # row facts come from the VALID prefix only (never a corrupt
                # tail): the tail-most prefix event, plus the last remote_job_id
                # any prefix event carried. A sentinel (no valid prefix) takes
                # advisory identity fields from the first event — the consult
                # re-verifies the chain from evidence regardless.
                last_valid = proj.get("last_valid_index")
                prefix = events[: last_valid + 1] if last_valid is not None else []
                rec = prefix[-1] if prefix else events[0]
                remote_job_id = next(
                    (e.get("remote_job_id") for e in reversed(prefix)
                     if e.get("remote_job_id")), None)
                row = self._conn.execute(
                    "SELECT id FROM intents WHERE submission_id = ?", (sid,)
                ).fetchone()
                ts = rec.get("ts") or self._ts()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO intents (id, provider, shot, params_hash, ts, "
                        "remote_job_id, status, submission_id, request_digest, "
                        "state, updated_ts) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, "
                        "?, ?, ?)",
                        (uuid.uuid4().hex, rec.get("provider_id") or "?",
                         rec.get("shot") or "?", None, ts, remote_job_id,
                         sid, rec.get("request_digest"), state, ts),
                    )
                else:
                    self._conn.execute(
                        "UPDATE intents SET state = ?, remote_job_id = ?, "
                        "request_digest = ?, updated_ts = ? WHERE submission_id = ?",
                        (state, remote_job_id, rec.get("request_digest"),
                         ts, sid),
                    )
                restored += 1
        return restored

    def ensure_submission_projection(self, project: "Project", *,
                                     shot: str | None = None,
                                     provider: str | None = None) -> dict:
        """POST_COMPLETION WP1 — fold append-only submission EVIDENCE into the
        intents projection so a FRESH/empty ``state.sqlite`` can never be read as
        "no paid submission in flight" while ``events.jsonl`` still holds an
        unresolved/ambiguous chain (the double-charge gap).

        Discipline (contract §3.2/§3.3):

        - only touches evidence when the intents table has NO unresolved row for
          the scope — the genuinely-never-ran-paid case stays cheap;
        - reuses the SAME ``project_chain`` restore machinery as
          :meth:`rebuild` (no second ledger, no second algorithm); a terminal
          chain is NOT resurrected as unresolved, an unrelated shot/provider is
          never touched (the scope filter);
        - an evidence read/verify failure fail-closes as
          ``submission_recovery_unavailable`` — NEVER an empty set.

        Consumed identically by the paid consult (``_resolve_resume``) and the
        07C release gate (``build.baseline._submission_blockers``). Returns::

            {"status": "ok", "unresolved": [rows]}
            {"status": "recovery_unavailable", "stage": ..., "error": ...,
             "unresolved": []}
        """
        from ..providers.submission import UNRESOLVED_STATES

        states = tuple(sorted(UNRESOLVED_STATES))
        try:
            existing = self.submissions(shot=shot, provider=provider, states=states)
        except Exception as exc:
            return {"status": "recovery_unavailable", "stage": "state_query",
                    "error": type(exc).__name__, "unresolved": []}
        if existing:
            # already projected (or the DB is authoritative for this scope) — the
            # existing consult/gate logic verifies each chain from here.
            return {"status": "ok", "unresolved": existing}
        try:
            self._restore_submissions_from_events(
                project, shot=shot, provider=provider, strict=True)
            after = self.submissions(shot=shot, provider=provider, states=states)
        except MalformedSubmissionEvidence as exc:
            # F1: torn evidence is never read as empty history — fail closed
            # with the honest stage so the consult/gate can say WHY.
            return {"status": "recovery_unavailable", "stage": "evidence_malformed",
                    "error": type(exc).__name__, "unresolved": []}
        except Exception as exc:
            return {"status": "recovery_unavailable", "stage": "evidence_projection",
                    "error": type(exc).__name__, "unresolved": []}
        return {"status": "ok", "unresolved": after}
