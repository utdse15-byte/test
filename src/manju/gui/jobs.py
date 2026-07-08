"""Serialized job runner for the GUI (§1-⑦ revisited).

Mutating engine operations (build / redo / voice / qc / repair) run in ONE
worker thread, strictly FIFO. That is an honest model of the engine's
single-writer assumption: the build graph, the timeline compiler and the
SQLite ledger were designed for one writer at a time (§3, §5 value locks
guard *content*, not *processes*). The GUI therefore never runs two mutating
operations concurrently — a second click queues behind the first.

Jobs are in-memory only (the runner dies with the server); durable history
lives where it always did — events.jsonl and the run ledger. The result of a
job is whatever dict the engine call returned, so the front-end renders the
same payload the CLI would have printed with --json.
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

__all__ = ["Job", "JobRunner"]

# Keep this many finished jobs around for the UI; older ones are dropped.
_HISTORY_CAP = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str  # "build" | "redo" | "voice" | "qc" | "repair"
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


class JobRunner:
    """One daemon worker thread; submit() returns immediately with a Job."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple[Job, Callable[[Job], dict[str, Any]]] | None]" = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # insertion order, oldest first
        self._lock = threading.Lock()
        self._rev = 0  # bumps on submit and on completion — cheap change signal
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
        self._queue.put((job, fn))
        return job

    def cancel(self, job_id: str) -> Job | None:
        """Cancel a queued or running job (goal A: honest job cancellation).

        - **queued**: marked ``canceled`` immediately — the worker skips it
          when it is dequeued (see :meth:`_run`), so it NEVER runs and never
          spends anything.
        - **running**: the cancel flag is set and the state moves to
          ``canceling``; the job's own work function observes the flag at
          ITS checkpoints (today: ``run_build(should_cancel=...)``) and
          unwinds honestly, landing on ``canceled``. Cancellation is
          cooperative, never forced — a work function that does not check
          the flag simply finishes on its own (lands on ``done``/``failed``
          as usual; the cancel request had no effect beyond being recorded).
        - **already terminal** (done/failed/canceled) or **unknown id**: a
          no-op — returns the job (or ``None`` for an unknown id) unchanged,
          so a stale double-click from the UI is harmless.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state == "queued":
                job.state = "canceled"
                job.error = "已取消:排队中被取消,从未运行,未产生任何花费"
                job.finished = _now()
                self._rev += 1
            elif job.state == "running":
                job.cancel_event.set()
                job.state = "canceling"
                self._rev += 1
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
                    continue
                job.state = "running"
                job.started = _now()
                self._rev += 1  # queued->running must move the fingerprint too
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
