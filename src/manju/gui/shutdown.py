"""Coordinated GUI application shutdown (one quit thread, structured reports).

``cancel_running`` is *bounded*: if a job ignores cooperative cancel, the
coordinator reports ``shutdown_state=stuck`` and keeps HTTP readable instead
of hanging forever on an unbounded join.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from .jobs import JobRunner, ShutdownReport

__all__ = ["AppShutdownCoordinator", "QuitResult"]

_log = logging.getLogger("manju.gui.shutdown")

# How long cancel_running waits for a cooperative job to finish before stuck.
# Override with MANJU_QUIT_CANCEL_TIMEOUT for tests.
_CANCEL_RUNNING_TIMEOUT_S = float(os.environ.get("MANJU_QUIT_CANCEL_TIMEOUT", "60"))
# after_current: long jobs may run a while; still poll so status stays fresh.
_AFTER_CURRENT_POLL_S = 0.25
_CANCEL_POLL_S = 0.25


@dataclass(frozen=True)
class QuitResult:
    ok: bool
    code: str
    mode: str
    shutdown_report: dict[str, Any] | None
    status: dict[str, Any]


class AppShutdownCoordinator:
    """Exactly one background quit thread per process lifetime."""

    def __init__(self, server: Any) -> None:
        self._server = server
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._mode: str | None = None
        self._last_report: ShutdownReport | None = None
        self._stuck = False
        self._stuck_message = ""

    @property
    def mode(self) -> str | None:
        return self._mode

    @property
    def in_progress(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    @property
    def stuck(self) -> bool:
        return self._stuck

    def request(self, mode: str = "after_current") -> dict[str, Any]:
        if mode not in ("after_current", "cancel_running"):
            mode = "after_current"
        with self._lock:
            already = self._server.closing.is_set()
            self._server.closing.set()
            self._mode = mode
            if self._thread is not None and self._thread.is_alive():
                return self._payload(
                    code="quit_already_in_progress" if not self._stuck else "quit_stuck",
                    mode=self._mode or mode,
                    report=self._last_report,
                )
            runner: JobRunner = self._server.runner
            report = runner.shutdown(
                timeout=0,
                cancel_queued=True,
                cancel_running=(mode == "cancel_running"),
            )
            self._last_report = report
            self._stuck = False
            self._stuck_message = ""
            thr = threading.Thread(
                target=self._finish,
                name="manju-gui-quit",
                daemon=False,
            )
            self._thread = thr
            thr.start()
            return self._payload(
                code="quit_started" if not already else "quit_already_in_progress",
                mode=mode,
                report=report,
            )

    def _payload(
        self,
        *,
        code: str,
        mode: str,
        report: ShutdownReport | None,
    ) -> dict[str, Any]:
        sr = None
        if report is not None:
            sr = {
                "stopped": report.stopped,
                "canceled_queued": list(report.canceled_queued),
                "running_job_id": report.running_job_id,
            }
        status = self._server.app_status()
        if self._stuck:
            status = dict(status)
            status["shutdown_state"] = "stuck"
            status["message"] = self._stuck_message or (
                "当前任务没有响应取消请求")
        return {
            "ok": True,
            "code": code,
            "mode": mode,
            "shutdown_report": sr,
            "status": status,
            "shutdown_state": "stuck" if self._stuck else (
                "closing" if self.in_progress else "done"),
        }

    def _wait_runner(self, runner: JobRunner, *, timeout: float | None) -> ShutdownReport:
        """Poll join until stopped or deadline (never unbounded hang)."""
        mode = self._mode or "after_current"
        # Re-issue cancel flags each poll for cancel_running.
        if mode == "cancel_running":
            runner.shutdown(timeout=0, cancel_queued=True, cancel_running=True)

        if timeout is None:
            # after_current: wait for natural finish, but still poll so we can
            # re-check cancel flags if mode was upgraded (not used today).
            while runner.worker_alive:
                report = runner.shutdown(
                    timeout=_AFTER_CURRENT_POLL_S,
                    cancel_queued=True,
                    cancel_running=False,
                )
                self._last_report = report
                if report.stopped:
                    return report
            return ShutdownReport(
                stopped=True,
                canceled_queued=(),
                running_job_id=None,
            )

        deadline = time.monotonic() + float(timeout)
        while runner.worker_alive:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            report = runner.shutdown(
                timeout=min(_CANCEL_POLL_S, remaining),
                cancel_queued=True,
                cancel_running=(mode == "cancel_running"),
            )
            self._last_report = report
            if report.stopped:
                return report
        # Final snapshot
        stopped = not runner.worker_alive
        if stopped:
            with getattr(runner, "_lock", threading.Lock()):
                # state may already be CLOSED by worker
                pass
        running_id = None
        try:
            for job in runner.list():
                if job.state in ("running", "canceling"):
                    running_id = job.id
                    break
        except Exception:
            pass
        report = ShutdownReport(
            stopped=stopped,
            canceled_queued=(),
            running_job_id=running_id,
        )
        self._last_report = report
        return report

    def _finish(self) -> None:
        try:
            time.sleep(0.2)  # flush /api/app/quit response
            runner: JobRunner = self._server.runner
            mode = self._mode or "after_current"
            timeout = None if mode == "after_current" else _CANCEL_RUNNING_TIMEOUT_S
            report = self._wait_runner(runner, timeout=timeout)
            self._last_report = report

            if not report.stopped:
                if mode == "cancel_running":
                    self._stuck = True
                    self._stuck_message = (
                        "当前任务没有响应取消请求 — 写操作已拒绝，"
                        "状态/日志仍可读；可继续等待或强制结束进程")
                    _log.error(
                        "gui shutdown STUCK: worker alive after %.0fs cancel_running "
                        "(job=%s)", _CANCEL_RUNNING_TIMEOUT_S, report.running_job_id)
                    # Do NOT server_close while worker may still write project files.
                    return
                _log.warning(
                    "gui shutdown: after_current still running (job=%s) — "
                    "keeping wait (should not reach here)", report.running_job_id)
                report = self._wait_runner(runner, timeout=None)

            try:
                self._server.shutdown()
            except Exception as exc:
                _log.warning("gui HTTP shutdown(): %s", exc)
            try:
                self._server.server_close()
            except Exception as exc:
                _log.warning("gui server_close(): %s", exc)
            _log.info("gui shutdown complete (mode=%s stopped=%s)",
                      mode, report.stopped)
        except Exception:
            _log.exception("gui AppShutdownCoordinator._finish failed")
