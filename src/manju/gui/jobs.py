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
    state: str = "queued"  # queued | running | done | failed
    progress: str | None = None  # coarse phase ("render:final"…), advisory
    error: str | None = None
    result: dict[str, Any] | None = None
    created: str = field(default_factory=_now)
    started: str | None = None
    finished: str | None = None

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
        }

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")


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
               fn: Callable[[Job], dict[str, Any]]) -> Job:
        """``fn`` receives the Job so long engine calls can publish coarse
        progress (``job.progress``) while they run."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._rev += 1
            self._trim_locked()
        self._queue.put((job, fn))
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
            job.state = "running"
            job.started = _now()
            with self._lock:
                self._rev += 1  # queued->running must move the fingerprint too
            try:
                job.result = fn(job)
                job.state = "done"
            except Exception as exc:  # any engine failure -> one-line finding
                job.state = "failed"
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
