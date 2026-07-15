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

Each :class:`JobRunner` is bound to exactly one ``project_id`` for its whole
lifetime (GUI repair plan 2026-07-15). Jobs carry that id; retry refuses a
mismatch; logs never silently cross projects.

Runner lifecycle (OPEN → CLOSING → CLOSED):

- ``submit`` only while OPEN; otherwise raises :class:`RunnerClosed`.
- ``shutdown`` cancels queued jobs (default), optional cooperative cancel of
  the running job, then posts a single exit sentinel — queued work never
  executes after shutdown begins.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "Job",
    "JobRunner",
    "RETRYABLE_KINDS",
    "RunnerClosed",
    "RunnerState",
    "ShutdownReport",
]

# Keep this many finished jobs around for the UI; older ones are dropped.
_HISTORY_CAP = 50

# Job kinds for which ``_build_retry_fn`` can rebuild the work closure (P1-9).
# Keep in lockstep with ``gui/server.py`` ``_build_retry_fn`` allow-list.
RETRYABLE_KINDS = frozenset({
    "build", "redo", "voice", "redo_batch", "voice_batch",
})

# C6: only kinds that actually honor should_cancel / cancel_event mid-run.
# Other kinds still accept cancel on queued jobs, but running cancel is a lie.
CANCELABLE_RUNNING_KINDS = frozenset({
    "build", "redo_batch", "voice_batch",
    # C24: single-shot voice threads should_cancel into GenericTtsProvider
    # poll when the adapter signature supports it (see gui/server.py).
    "voice",
    # C25: single-shot redo threads should_cancel into GenerationRequest
    # (cloud/ComfyUI poll) via redo_shot(..., should_cancel=...).
    "redo",
    # C26: kind strings must match JobRunner.submit() exactly
    # (was ingest_apply / series_sync / edit_preview — wrong aliases).
    "ingest_plan", "ingest", "series_sync_bible",
    "edit_preview_batch",
    # C31: voice_preview threads should_cancel into ttspreview synthesize.
    "voice_preview",
    # C33: export wraps packaging ffmpeg in cancel_scope(job.should_cancel).
    "export",
    # C34: repair wraps ffmpeg repair_ops in cancel_scope.
    "repair",
    # C46: run_qc samples should_cancel between major check batches.
    "qc",
    # C51: handle_rebuild threads should_cancel into redo + ffmpeg trim.
    "handle_rebuild",
    # series_new_episode deliberately omitted: fn does not sample should_cancel.
})

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

_LEGACY_UNSCOPED_NOTE = (
    "旧版本任务，项目归属未记录——"
    + "无法原样重试(参数摘要有损)——请从原页面重新发起"
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


class RunnerState(Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class RunnerClosed(RuntimeError):
    """Raised when :meth:`JobRunner.submit` is called after shutdown began."""


@dataclass(frozen=True)
class ShutdownReport:
    """Structured result of :meth:`JobRunner.shutdown` (never silent timeout)."""

    stopped: bool
    canceled_queued: tuple[str, ...]
    running_job_id: str | None


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
    # Fixed at submit from the runner's project_id; callers cannot override.
    project_id: str = ""
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
            "project_id": self.project_id,
            # convenience for the GUI: whether a cancel/retry click is even
            # meaningful right now, without the client re-deriving the rule.
            # C6: queued always cancelable; running only when the work fn
            # cooperates (else UI 取消中… then done = spend honesty lie).
            "cancelable": (
                self.state == "queued"
                or (self.state == "running" and self.kind in CANCELABLE_RUNNING_KINDS)
            ),
            # P1-9: only kinds _build_retry_fn actually supports — avoid a
            # dead-end "重试" button that always 400s for qc/export/repair/…
            "retryable": (
                self.state in ("failed", "canceled")
                and self.kind in RETRYABLE_KINDS
            ),
        }

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running", "canceling")

    def should_cancel(self) -> bool:
        """Bound-method convenience for callers that want a bare
        ``Callable[[], bool]`` (e.g. ``run_build(should_cancel=job.should_cancel)``)
        instead of reaching into ``job.cancel_event.is_set``."""
        return self.cancel_event.is_set()


def _interrupted_job_dict(
    rec: dict[str, Any],
    *,
    created: str | None = None,
    started: str | None = None,
    finished: str | None = None,
    legacy_unscoped: bool = False,
) -> dict[str, Any]:
    """A jobs.jsonl record (freshly detected as dangling, OR already
    ``state="interrupted"`` from an earlier construction) rendered in the
    SAME shape :meth:`Job.to_dict` produces (plus ``note`` / legacy flags),
    so the GUI queue panel can render a dangling job with almost the exact
    same row renderer as a live one — it is read-only data, never a live
    :class:`Job` (see :meth:`JobRunner.interrupted`)."""
    note = _LEGACY_UNSCOPED_NOTE if legacy_unscoped else _INTERRUPTED_RETRY_NOTE
    return {
        "id": rec.get("id"),
        "kind": rec.get("kind"),
        "params": rec.get("params_summary"),
        "state": "interrupted",
        "progress": None,
        "error": rec.get("error") or _INTERRUPTED_ERROR,
        "result": None,
        "created": created,
        "started": started,
        "finished": finished if finished is not None else rec.get("ts"),
        "retry_of": None,
        "project_id": rec.get("project_id") or "",
        "cancelable": False,
        "retryable": False,
        "legacy_unscoped": legacy_unscoped,
        "note": note,
    }


class JobRunner:
    """One daemon worker thread; submit() returns immediately with a Job.

    Bound to a single ``project_id`` for its lifetime. ``runtime_dir`` is the
    project's ``.manju`` directory (or ``None`` to disable jobs.jsonl).
    """

    def __init__(
        self,
        runtime_dir: "Path | str | None" = None,
        *,
        project_id: str = "",
    ) -> None:
        self.project_id = project_id or ""
        self._queue: "queue.Queue[tuple[Job, Callable[[Job], dict[str, Any]]] | None]" = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # insertion order, oldest first
        self._lock = threading.Lock()
        self._state = RunnerState.OPEN
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

        Raises :class:`RunnerClosed` if the runner is closing or closed.
        ``project_id`` is always taken from the runner — callers cannot set it.
        """
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            params=params,
            retry_of=retry_of,
            project_id=self.project_id,
        )
        with self._lock:
            if self._state is not RunnerState.OPEN:
                raise RunnerClosed("job runner is closing/closed")
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
          ITS checkpoints and unwinds honestly, landing on ``canceled``.
        - **already terminal** or **unknown id**: no-op.
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
        """Jobs a PAST GUI process left dangling — see module docstring."""
        return list(self._interrupted)

    def busy(self) -> bool:
        with self._lock:
            return any(self._jobs[i].active for i in self._order)

    def state(self) -> RunnerState:
        with self._lock:
            return self._state

    @property
    def worker_alive(self) -> bool:
        """Whether the daemon worker thread is still running."""
        return self._worker.is_alive()

    def shutdown(
        self,
        timeout: float | None = 5.0,
        *,
        cancel_queued: bool = True,
        cancel_running: bool = False,
    ) -> ShutdownReport:
        """Stop the worker. Cancels queued jobs by default so they never run.

        Idempotent: a second call waits again / reports current state.
        Returns a structured :class:`ShutdownReport` (``stopped=False`` if
        the worker is still alive after ``timeout``).
        """
        canceled: list[str] = []
        running_id: str | None = None
        with self._lock:
            if self._state is RunnerState.CLOSED:
                return ShutdownReport(
                    stopped=True, canceled_queued=(), running_job_id=None)
            first_close = self._state is RunnerState.OPEN
            self._state = RunnerState.CLOSING
            if first_close and cancel_queued:
                for jid in list(self._order):
                    job = self._jobs[jid]
                    if job.state == "queued":
                        job.state = "canceled"
                        job.error = "已取消:服务器关闭时取消排队任务,从未运行"
                        job.finished = _now()
                        canceled.append(jid)
                        self._rev += 1
            if cancel_running:
                for jid in self._order:
                    job = self._jobs[jid]
                    if job.state in ("running", "canceling"):
                        job.cancel_event.set()
                        if job.state == "running":
                            job.state = "canceling"
                            self._rev += 1
            for jid in self._order:
                if self._jobs[jid].state in ("running", "canceling"):
                    running_id = jid
                    break

        for jid in canceled:
            job = self.get(jid)
            if job is not None:
                self._persist(job)
        if cancel_running and running_id:
            job = self.get(running_id)
            if job is not None:
                self._persist(job)

        if first_close:
            # Exactly one exit sentinel — only on the OPEN→CLOSING transition.
            self._queue.put(None)

        if timeout is None:
            self._worker.join()
        else:
            self._worker.join(float(timeout))
        stopped = not self._worker.is_alive()
        if stopped:
            with self._lock:
                self._state = RunnerState.CLOSED
        return ShutdownReport(
            stopped=stopped,
            canceled_queued=tuple(canceled),
            running_job_id=running_id,
        )

    # --------------------------------------------------------------- worker

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                with self._lock:
                    self._state = RunnerState.CLOSED
                return
            job, fn = item
            with self._lock:
                if job.state == "canceled":
                    # canceled while still queued (cancel()/shutdown) — never runs
                    continue
                job.state = "running"
                job.started = _now()
                self._rev += 1  # queued->running must move the fingerprint too
            self._persist(job)
            try:
                result = fn(job)
                # goal A: some engine calls never RAISE for cancellation —
                # a dict result that self-reports canceled=True is ALSO cancel.
                # R2-P2-1: publish terminal fields under _lock (no half-read).
                with self._lock:
                    job.result = result
                    if isinstance(result, dict) and result.get("canceled") is True:
                        job.state = "canceled"
                        errs = result.get("errors")
                        job.error = (
                            (errs[0] if isinstance(errs, list) and errs else None)
                            or "已取消"
                        )
                    else:
                        job.state = "done"
                        # C7: cancel was clicked but work finished anyway —
                        # honest note so UI never pretends spend stopped mid-run.
                        if job.cancel_event.is_set():
                            job.error = (
                                "取消请求未中断:任务已完成"
                                "(该类型不支持中途取消或已越过取消点)"
                            )
                    job.finished = _now()
                    self._rev += 1
            except Exception as exc:  # any engine failure -> one-line finding
                with self._lock:
                    job.state = "canceled" if job.cancel_event.is_set() else "failed"
                    job.error = " ".join(str(exc).split()) or exc.__class__.__name__
                    job.finished = _now()
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
        """Append one jobs.jsonl transition line for ``job``'s CURRENT state."""
        if self._jobs_log_path is None:
            return
        record = {
            "ts": _now(),
            "id": job.id,
            "kind": job.kind,
            "state": job.state,
            "params_summary": _summarize_params(job.params),
            "error": job.error,
            "project_id": job.project_id,
            "created": job.created,
            "started": job.started,
            "finished": job.finished,
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
                    # collaboration log.
        except OSError:
            pass  # operational history only — never blocks a job

    def _rewrite_log(self, records: list[dict[str, Any]]) -> None:
        """Cap/rotate: replace jobs.jsonl with exactly ``records``."""
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
        """On construction: read jobs.jsonl, cap it, find dangling jobs.

        For each job id, keep first and last records so interrupted entries
        recover ``created`` / ``started`` / ``finished`` timestamps instead
        of all-null created (which sorted ahead of live running jobs).
        """
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
                continue  # a torn write must not brick the scan

        if len(records) > _JOBS_LOG_CAP:
            records = records[-_JOBS_LOG_CAP:]
            self._rewrite_log(records)

        first_by_id: dict[str, dict[str, Any]] = {}
        last_by_id: dict[str, dict[str, Any]] = {}
        first_running_ts: dict[str, str] = {}
        order: list[str] = []
        for rec in records:
            jid = rec.get("id")
            if not jid:
                continue
            if jid not in first_by_id:
                order.append(jid)
                first_by_id[jid] = rec
            last_by_id[jid] = rec
            if rec.get("state") == "running" and jid not in first_running_ts:
                ts = rec.get("ts") or rec.get("started")
                if ts:
                    first_running_ts[jid] = str(ts)

        interrupted: list[dict[str, Any]] = []
        new_records: list[dict[str, Any]] = []
        for jid in order:
            rec = last_by_id[jid]
            state = rec.get("state")
            first = first_by_id[jid]
            created = first.get("created") or first.get("ts")
            started = first_running_ts.get(jid) or rec.get("started")
            pid = rec.get("project_id") or first.get("project_id") or ""
            legacy = not bool(pid)
            if state in ("queued", "running", "canceling"):
                synth = {
                    "ts": _now(),
                    "id": jid,
                    "kind": rec.get("kind"),
                    "state": "interrupted",
                    "params_summary": rec.get("params_summary"),
                    "error": _INTERRUPTED_ERROR,
                    "project_id": pid,
                    "created": created,
                    "started": started,
                    "finished": _now(),
                }
                interrupted.append(_interrupted_job_dict(
                    synth,
                    created=created,
                    started=started,
                    finished=synth["finished"],
                    legacy_unscoped=legacy,
                ))
                new_records.append(synth)
            elif state == "interrupted":
                finished = rec.get("finished") or rec.get("ts")
                interrupted.append(_interrupted_job_dict(
                    rec,
                    created=rec.get("created") or created,
                    started=rec.get("started") or started,
                    finished=finished,
                    legacy_unscoped=legacy,
                ))

        if new_records:
            self._append_records(new_records)
        return interrupted
