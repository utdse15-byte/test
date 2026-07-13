"""Serialized job runner for the GUI (§1-⑦ revisited).

Mutating engine operations (build / redo / voice / qc / repair / batch kinds /
…) run in ONE worker thread, strictly FIFO. That is an honest model of the
engine's single-writer assumption: the build graph, the timeline compiler and
the SQLite ledger were designed for one writer at a time (§3, §5 value locks
guard *content*, not *processes*). The GUI therefore never runs two mutating
operations concurrently — a second click queues behind the first.

Jobs themselves are in-memory only (the live queue/state machine dies with the
server) — but round AA4 adds a durable, best-effort TRANSITION LOG,
``.manju/jobs.jsonl`` (one JSON line per submit/start/cancel/finish), so a GUI
crash/restart can honestly say "this job was still running when the GUI died,
its actual outcome is unknown" instead of silently forgetting it ever existed.
This log is operational history for THIS process's job queue, not the durable
collaboration log — that role stays events.jsonl/the run ledger, and the
result of a job is still whatever dict the engine call returned, so the
front-end renders the same payload the CLI would have printed with --json.

``jobs.jsonl`` schema — one JSON object per line, UTF-8, newest last::

    {"ts": "<iso8601 UTC>", "id": "<job id>", "kind": "<job kind>",
     "state": "queued|running|canceling|canceled|done|failed|interrupted",
     "params_summary": {...}, "error": "<str | null>"}

``params_summary`` is ``params`` with long lists/strings truncated (see
:func:`_summarize_params`) — this log is capped at :data:`_JOBS_LOG_CAP`
lines (rewritten to the newest N on every :class:`JobRunner` construction,
mirroring the simplest existing precedent for a disposable, single-writer,
single-process log — unlike ``events.jsonl``/``failures.jsonl``, which are
durable multi-process collaboration/ledger logs and get either no rotation or
a byte-size rotation behind a cross-process lock, ``jobs.jsonl`` is written by
exactly one process's one worker thread, so a plain startup cap is enough).
``state="interrupted"`` is synthetic — see :meth:`JobRunner.interrupted`.

**Cross-process honesty**: an MCP tool call or a CLI invocation (``manju
build`` run directly in a terminal) is a SEPARATE OS process with its own
Python interpreter — it never touches this module's in-memory queue or this
GUI's ``jobs.jsonl``. The GUI's job queue can never see, list, cancel, or
report on a build/redo/voice/qc run started via the CLI or an MCP tool call,
and vice versa: cancelling a GUI job has zero effect on a concurrent CLI
process (they are only ever kept from CORRUPTING data by the cross-process
``.manju/build.lock`` — see runtime/buildlock.py — never by shared visibility
into each other's progress or cancel state). A user running both at once sees
two independent queues/histories; this is a real limitation, not a bug to
paper over.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

__all__ = ["Job", "JobRunner"]

# Keep this many finished jobs around for the UI; older ones are dropped.
_HISTORY_CAP = 50

# jobs.jsonl (round AA4): disposable operational history, capped/rewritten to
# this many newest lines on every JobRunner construction — see module
# docstring for why this is a plain cap rather than a byte-size rotation.
_JOBS_LOG_FILE = "jobs.jsonl"
_JOBS_LOG_CAP = 500

# The 中文 honesty note stamped onto a job a past GUI process left dangling —
# see JobRunner._scan_interrupted and its own module-docstring cross-ref.
_INTERRUPTED_ERROR = (
    "GUI 上次退出时该任务仍在运行,实际结果未知——请核对产物后按需重试"
)

# round AA item 6: WHY an interrupted job's "重试" button is gone. jobs.jsonl
# only ever persists ``params_summary`` (:func:`_summarize_params`), not the
# original ``params`` — a batch kind's shot list beyond the first 8 is
# truncated to ``"...(+N more)"`` and long strings are cut at 200 chars, so
# resubmitting FROM this record in general would silently run a DIFFERENT
# (smaller) job than the one that was interrupted. That is worse than no
# retry at all, so :func:`_interrupted_job_dict` sets ``retryable: False``
# and carries this note instead — the honest fix is to go back to the page
# that started the job and re-initiate it there, where the full params are
# still available.
_INTERRUPTED_RETRY_NOTE = (
    "中断任务无法原样重试(参数摘要有损)——请从原页面重新发起"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _summarize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Compact ``params`` for a jobs.jsonl line — a batch kind's ``shots``
    list (redo_batch/voice_batch/…) could run to hundreds of ids, and this
    log is meant to stay a cheap, bounded operational trail, not a second
    copy of every request body. Never raises: an unsummarizable value
    degrades to its type name rather than breaking persistence."""
    out: dict[str, Any] = {}
    for k, v in (params or {}).items():
        try:
            if isinstance(v, (list, tuple)):
                seq = list(v)
                out[k] = seq if len(seq) <= 8 else seq[:8] + [f"...(+{len(seq) - 8} more)"]
            elif isinstance(v, str) and len(v) > 200:
                out[k] = v[:200] + "…(truncated)"
            elif isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
            elif isinstance(v, dict):
                out[k] = f"<dict:{len(v)} keys>"
            else:
                out[k] = str(type(v).__name__)
        except Exception:
            out[k] = "<unrepresentable>"
    return out


@dataclass
class Job:
    id: str
    kind: str  # e.g. "build" | "redo" | "voice" | "qc" | "repair" | … — the
               # runner itself never enumerates kinds (kind-agnostic by design)
    params: dict[str, Any]
    state: str = "queued"  # queued | running | canceling | canceled | done | failed
    progress: str | None = None  # coarse phase ("render:final"…), advisory
    error: str | None = None
    result: dict[str, Any] | None = None
    created: str = field(default_factory=_now)
    started: str | None = None
    finished: str | None = None
    # goal B (retry): the job id this one was retried FROM, or None for a
    # fresh submit — lineage the queue panel renders as "retry of #<id>".
    retry_of: str | None = None
    # goal A (cancel): the cooperative cancel flag. A work function that wants
    # to be cancelable is handed this Job (submit()'s contract already passes
    # it) and checks ``job.cancel_event.is_set`` (or the job's own
    # ``should_cancel`` bound method below) at its own checkpoints — see
    # build/graph.py: run_build(should_cancel=...). Excluded from dataclass
    # eq/repr: a threading.Event has no meaningful equality/repr for job
    # comparisons or logs.
    cancel_event: threading.Event = field(default_factory=threading.Event,
                                          repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "params": self.params,
            "state": self.state,
            "progress": self.progress,
            "error": self.error,
            "result": self.result,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "retry_of": self.retry_of,
            # convenience for the GUI: whether a cancel/retry click is even
            # meaningful right now, without the client re-deriving the rule.
            "cancelable": self.state in ("queued", "running"),
            "retryable": self.state in ("failed", "canceled"),
        }

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running", "canceling")

    def should_cancel(self) -> bool:
        """Bound-method convenience for callers that want a bare
        ``Callable[[], bool]`` (e.g. ``run_build(should_cancel=job.should_cancel)``)
        instead of reaching into ``job.cancel_event.is_set``."""
        return self.cancel_event.is_set()


def _interrupted_job_dict(rec: dict[str, Any]) -> dict[str, Any]:
    """A jobs.jsonl record (freshly detected as dangling, OR already
    ``state="interrupted"`` from an earlier construction) rendered in the
    SAME shape :meth:`Job.to_dict` produces (plus one extra key, ``note``),
    so the GUI queue panel can render a dangling job with almost the exact
    same row renderer as a live one — it is read-only data, never a live
    :class:`Job` (see :meth:`JobRunner.interrupted`).

    ``retryable`` is always ``False`` here (round AA item 6): unlike a
    ``failed``/``canceled`` live :class:`Job`, whose ``params`` dict is the
    exact one it ran with, this record only ever has ``params_summary`` —
    already lossy on write (:func:`_summarize_params`) — so a resubmit built
    from it could silently run a smaller/different job than the one that was
    interrupted. ``note`` carries the 中文 explanation the queue panel shows
    in place of a retry button (:data:`_INTERRUPTED_RETRY_NOTE`)."""
    return {
        "id": rec.get("id"),
        "kind": rec.get("kind"),
        "params": rec.get("params_summary"),
        "state": "interrupted",
        "progress": None,
        "error": rec.get("error") or _INTERRUPTED_ERROR,
        "result": None,
        "created": None,
        "started": None,
        "finished": rec.get("ts"),
        "retry_of": None,
        "cancelable": False,
        "retryable": False,
        "note": _INTERRUPTED_RETRY_NOTE,
    }


class JobRunner:
    """One daemon worker thread; submit() returns immediately with a Job."""

    def __init__(self, runtime_dir: "Path | str | None" = None) -> None:
        """``runtime_dir`` (round AA4) is the project's ``.manju`` directory
        (``Project.runtime_dir`` — threaded in from gui/server.py's
        construction site). ``None`` (the default — every bare ``JobRunner()``
        a test constructs, and any server started unbound with no project
        yet) disables jobs.jsonl persistence entirely: no file is ever
        written or read, and :meth:`interrupted` always returns ``[]``. This
        keeps the runner usable standalone (tests, an unbound workspace
        picker) exactly as before this round."""
        self._queue: "queue.Queue[tuple[Job, Callable[[Job], dict[str, Any]]] | None]" = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # insertion order, oldest first
        self._lock = threading.Lock()
        self._rev = 0  # bumps on submit and on completion — cheap change signal
        self._jobs_log_path: "Path | None" = (
            Path(runtime_dir) / _JOBS_LOG_FILE if runtime_dir is not None else None
        )
        self._persist_lock = threading.Lock()
        # goal: interrupted-job honesty — computed ONCE at construction, from
        # whatever jobs.jsonl says about the PREVIOUS process's queue. Never
        # touches self._jobs (these are not live Jobs — see interrupted()).
        self._interrupted: list[dict[str, Any]] = self._scan_interrupted()
        self._worker = threading.Thread(target=self._run, name="manju-gui-jobs", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ api

    def submit(self, kind: str, params: dict[str, Any],
               fn: Callable[[Job], dict[str, Any]], *,
               retry_of: str | None = None) -> Job:
        """``fn`` receives the Job so long engine calls can publish coarse
        progress (``job.progress``) while they run, and check
        ``job.should_cancel()`` / ``job.cancel_event`` at their own
        checkpoints if they support cooperative cancellation (goal A).

        ``retry_of`` (goal B) tags this as a FRESH re-submit of a failed/
        canceled job — a new id, same kind/params, lineage recorded so the
        queue panel can render "retry of #<id>". JobRunner itself never
        re-derives ``fn`` from a kind — the caller (gui/server.py) rebuilds
        the SAME closure a fresh submit of this kind would use, re-running
        that kind's own validation in the process (goal: refuse a retry
        whose params no longer validate)."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params, retry_of=retry_of)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._rev += 1
            self._trim_locked()
        # persisted BEFORE the job is even queued for execution, so the
        # "queued" line in jobs.jsonl always precedes its own "running" line.
        self._persist(job)
        self._queue.put((job, fn))
        return job

    def cancel(self, job_id: str) -> Job | None:
        """Cancel a queued or running job (goal A: honest job cancellation).

        - **queued**: marked ``canceled`` immediately — the worker skips it
          when it is dequeued (see :meth:`_run`), so it NEVER runs and never
          spends anything.
        - **running**: the cancel flag is set and the state moves to
          ``canceling``; the job's own work function observes the flag at
          ITS checkpoints (today: ``run_build(should_cancel=...)`` and every
          batch kind's between-item checkpoint) and unwinds honestly,
          landing on ``canceled``. Cancellation is cooperative, never forced
          — a work function that does not check the flag simply finishes on
          its own (lands on ``done``/``failed`` as usual; the cancel request
          had no effect beyond being recorded).
        - **already terminal** (done/failed/canceled) or **unknown id**: a
          no-op — returns the job (or ``None`` for an unknown id) unchanged,
          so a stale double-click from the UI is harmless.
        """
        changed = False
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state == "queued":
                job.state = "canceled"
                job.error = "已取消:排队中被取消,从未运行,未产生任何花费"
                job.finished = _now()
                self._rev += 1
                changed = True
            elif job.state == "running":
                job.cancel_event.set()
                job.state = "canceling"
                self._rev += 1
                changed = True
        if changed:  # a no-op cancel (already terminal) writes no extra line
            self._persist(job)
        return job

    def revision(self) -> int:
        with self._lock:
            return self._rev

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        """Newest first — what the UI shows."""
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order)]

    def interrupted(self) -> list[dict[str, Any]]:
        """Jobs a PAST GUI process left dangling — non-terminal (queued/
        running/canceling) the moment this process's jobs.jsonl tail was last
        written, meaning the previous process died (crash or plain restart)
        before that job ever reached done/failed/canceled. Its real outcome
        (did the ffmpeg encode finish? did the provider call land?) is
        genuinely unknown — see :data:`_INTERRUPTED_ERROR`.

        Computed ONCE at construction (see :meth:`_scan_interrupted`); these
        are read-only, :meth:`Job.to_dict`-shaped dicts, NOT live
        :class:`Job` objects — they are never resurrected into the runnable
        queue (cancel()/get() do not see them). Unlike a normal failed/
        canceled job, one of these is NOT retryable (round AA item 6):
        jobs.jsonl only ever recorded this job's ``params_summary``, a
        LOSSY compaction (:func:`_summarize_params` truncates long lists/
        strings), so ``gui/server.py``'s ``_act_jobs_retry`` refuses a
        resubmit built from it (a 中文 4xx, never a silent
        smaller-than-intended re-run) — every dict here carries
        ``retryable=False`` plus a ``note`` explaining that, and the queue
        panel points the human back at the page that started the job
        instead of offering a retry button."""
        return list(self._interrupted)

    def busy(self) -> bool:
        with self._lock:
            return any(self._jobs[i].active for i in self._order)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the worker after the current job (tests / server close)."""
        self._queue.put(None)
        self._worker.join(timeout)

    # --------------------------------------------------------------- worker

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job, fn = item
            with self._lock:
                if job.state == "canceled":
                    # goal A: canceled while still queued (cancel() ran
                    # before the worker dequeued it) — it never runs, so
                    # there is nothing to unwind and nothing was spent.
                    # cancel() already persisted this transition; no second
                    # line needed here.
                    continue
                job.state = "running"
                job.started = _now()
                self._rev += 1  # queued->running must move the fingerprint too
            self._persist(job)
            try:
                job.result = fn(job)
                # goal A: some engine calls (build/graph.py: run_build) never
                # RAISE for a cancellation — like BuildLocked, a canceled
                # build is a clean, honest result envelope
                # (BuildResult(ok=False, canceled=True, errors=[...])), not an
                # exception. A dict result that self-reports canceled=True is
                # therefore ALSO cancellation, even though fn() returned
                # normally — checked generically (a bare dict key, not an
                # engine type) so this runner stays kind-agnostic (module
                # docstring: it never imports engine code).
                if isinstance(job.result, dict) and job.result.get("canceled") is True:
                    job.state = "canceled"
                    errs = job.result.get("errors")
                    job.error = (errs[0] if isinstance(errs, list) and errs else None) or "已取消"
                else:
                    job.state = "done"
            except Exception as exc:  # any engine failure -> one-line finding
                # goal A: an exception raised while the cancel flag was set is
                # the work function's own honest cancellation unwind (e.g. a
                # provider/ffmpeg-level cancel that DOES raise) — report it as
                # "canceled", not "failed", regardless of the exact exception
                # type each job kind happens to raise (kind-agnostic by
                # design, see the module docstring: this runner never imports
                # engine code).
                job.state = "canceled" if job.cancel_event.is_set() else "failed"
                job.error = " ".join(str(exc).split()) or exc.__class__.__name__
            finally:
                job.finished = _now()
                with self._lock:
                    self._rev += 1
            self._persist(job)

    def _trim_locked(self) -> None:
        """Drop the oldest FINISHED jobs beyond the cap (never active ones)."""
        while len(self._order) > _HISTORY_CAP:
            for i, jid in enumerate(self._order):
                if not self._jobs[jid].active:
                    del self._jobs[jid]
                    del self._order[i]
                    break
            else:  # everything active (absurd, but never spin)
                return

    # ------------------------------------------------------- jobs.jsonl (AA4)

    def _persist(self, job: Job) -> None:
        """Append one jobs.jsonl transition line for ``job``'s CURRENT state
        — called at submit/start/cancel/finish. Best-effort: a disk hiccup
        must never break the in-memory job queue (mirrors
        ``core.events.append_event``'s stance, just scoped to this process's
        disposable operational history rather than the durable collaboration
        log — see the module docstring)."""
        if self._jobs_log_path is None:
            return
        record = {
            "ts": _now(),
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "params_summary": _summarize_params(job.params),
            "error": job.error,
        }
        self._append_records([record])

    def _append_records(self, records: list[dict[str, Any]]) -> None:
        if self._jobs_log_path is None or not records:
            return
        try:
            with self._persist_lock:
                self._jobs_log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._jobs_log_path, "a", encoding="utf-8") as f:
                    for rec in records:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    # G6: NO os.fsync — jobs.jsonl is self-declared disposable
                    # operational history (module docstring), not the durable
                    # collaboration log. submit() persists on the POST/click
                    # thread before the 202, so an fsync there put disk-sync
                    # latency on the user's click path for a rewritten-capped,
                    # rebuild-irrelevant log. flush() keeps the bytes honest;
                    # durability of THIS file is explicitly not promised.
        except OSError:
            pass  # operational history only — never blocks a job (module docstring)

    def _rewrite_log(self, records: list[dict[str, Any]]) -> None:
        """Cap/rotate: replace jobs.jsonl with exactly ``records`` (the newest
        :data:`_JOBS_LOG_CAP` lines) — see the module docstring for why a
        plain startup rewrite is enough for this single-process log."""
        if self._jobs_log_path is None:
            return
        try:
            self._jobs_log_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._jobs_log_path.with_name(self._jobs_log_path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp, self._jobs_log_path)
        except OSError:
            pass  # a rotation hiccup must never block GUI startup

    def _scan_interrupted(self) -> list[dict[str, Any]]:
        """On construction: read jobs.jsonl, cap it to the newest
        :data:`_JOBS_LOG_CAP` lines (rewriting the file if it was longer),
        then find every job id whose LAST record is still non-terminal — a
        previous GUI process died with that job queued/running/canceling.

        Each freshly-found dangling job gets exactly ONE synthetic
        ``state="interrupted"`` record appended to jobs.jsonl, so a LATER
        construction (another GUI restart) recognizes it as already-flagged
        and does not append a second one — but it STILL surfaces in this
        construction's return value (and every later one, via the
        ``elif`` branch below), so the job stays visible in the queue panel
        across restarts until the user retries/dismisses it, rather than
        silently vanishing after the first restart that notices it."""
        if self._jobs_log_path is None or not self._jobs_log_path.exists():
            return []
        try:
            raw_lines = self._jobs_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        records: list[dict[str, Any]] = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn write must not brick the scan (mirrors events.py)

        if len(records) > _JOBS_LOG_CAP:
            records = records[-_JOBS_LOG_CAP:]
            self._rewrite_log(records)

        last_by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for rec in records:
            jid = rec.get("id")
            if not jid:
                continue
            if jid not in last_by_id:
                order.append(jid)
            last_by_id[jid] = rec

        interrupted: list[dict[str, Any]] = []
        new_records: list[dict[str, Any]] = []
        for jid in order:
            rec = last_by_id[jid]
            state = rec.get("state")
            if state in ("queued", "running", "canceling"):
                synth = {
                    "ts": _now(),
                    "id": jid,
                    "kind": rec.get("kind"),
                    "state": "interrupted",
                    "params_summary": rec.get("params_summary"),
                    "error": _INTERRUPTED_ERROR,
                }
                interrupted.append(_interrupted_job_dict(synth))
                new_records.append(synth)
            elif state == "interrupted":
                interrupted.append(_interrupted_job_dict(rec))
            # any other terminal state (done/failed/canceled) -> not dangling,
            # nothing to surface.

        if new_records:
            self._append_records(new_records)
        return interrupted
