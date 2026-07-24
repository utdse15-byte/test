"""Coordinated GUI application shutdown (one quit thread, structured reports).

BOTH modes are bounded (GUI-SHUTDOWN-P1-001): if a job ignores cooperative
cancel — or simply never returns, which is what a wedged provider HTTP call
looks like — the coordinator reports ``shutdown_state=stuck`` and keeps HTTP
readable instead of hanging forever on an unbounded join. ``after_current``
gets the longer ceiling of the two (a real render legitimately runs for
hours), but it does get one: "wait for the job" must not mean "wait forever".

The quit thread is a DAEMON. It used to be non-daemon, which meant the
interpreter itself joined it at exit: clicking 退出 (or pressing Ctrl-C) while
a wedged job held the worker never returned the prompt, and Task Manager was
the only way out — on the owner's primary platform, where the GUI is usually
the ``--app`` window. Daemon is safe here because nothing in ``_finish``
writes project truth: the job worker owns that, and this thread only waits on
it and closes sockets.
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
# GUI-SHUTDOWN-P1-001: after_current's ceiling. Generous on purpose — the
# whole point of the mode is "let the current job finish", and a final render
# can legitimately take a long time — but FINITE, because "the provider call
# never returns" is indistinguishable from "still working" to this thread, and
# an infinite wait was reported as neither done nor stuck, forever. On expiry
# the job is NOT killed: the coordinator reports stuck (writes already
# refused, status/logs still readable) and the human can escalate to
# cancel_running or end the process. Override with
# MANJU_QUIT_AFTER_CURRENT_TIMEOUT for tests.
_AFTER_CURRENT_TIMEOUT_S = float(
    os.environ.get("MANJU_QUIT_AFTER_CURRENT_TIMEOUT", "3600"))
# after_current: long jobs may run a while; still poll so status stays fresh.
_AFTER_CURRENT_POLL_S = 0.25
_CANCEL_POLL_S = 0.25


def _timeout_for(mode: str) -> float:
    """The wait ceiling for ``mode`` — one owner for the two constants."""
    return (_CANCEL_RUNNING_TIMEOUT_S if mode == "cancel_running"
            else _AFTER_CURRENT_TIMEOUT_S)


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
            upgraded = (mode == "cancel_running" and self._mode == "after_current")
            self._mode = mode
            if self._thread is not None and self._thread.is_alive():
                if upgraded:
                    # 「等它跑完再退」升级为「取消并退出」必须真的取消:光改
                    # self._mode 只换了应答里的标签,运行中的任务会继续跑完。
                    # runner.shutdown 是幂等的,cancel_running 分支在任何一次
                    # 调用都会置 cancel 标志(见 jobs.py)。
                    self._last_report = self._server.runner.shutdown(
                        timeout=0, cancel_queued=True, cancel_running=True)
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
                # GUI-SHUTDOWN-P1-001: daemon — see the module docstring. A
                # non-daemon quit thread is joined by the interpreter, so a
                # wedged job made 退出/Ctrl-C hang the whole process.
                daemon=True,
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

    def _wait_runner(self, runner: JobRunner, *, timeout: float | None = None
                     ) -> ShutdownReport:
        """Poll join until stopped or deadline (never unbounded hang).

        GUI-SHUTDOWN-P1-002: ``mode`` is re-read on EVERY pass, not once.
        ``request()`` can upgrade after_current → cancel_running while this
        loop is already running; reading the mode once meant the loop kept
        polling with ``cancel_running=False`` against the after_current
        ceiling, so the 60s cancel deadline was never established and the
        状态 stayed ``closing`` instead of ever reaching ``stuck`` — the
        upgrade looked like it did nothing. An upgrade now re-issues the
        cancel flags and restarts the deadline at the new mode's ceiling.

        ``timeout`` pins the initial ceiling (tests / explicit callers);
        omitted, it comes from the current mode.
        """
        mode = self._mode or "after_current"
        deadline = time.monotonic() + float(
            timeout if timeout is not None else _timeout_for(mode))
        # Re-issue cancel flags each poll for cancel_running.
        if mode == "cancel_running":
            runner.shutdown(timeout=0, cancel_queued=True, cancel_running=True)

        while runner.worker_alive:
            current = self._mode or "after_current"
            if current != mode:
                mode = current
                deadline = time.monotonic() + _timeout_for(mode)
                if mode == "cancel_running":
                    runner.shutdown(
                        timeout=0, cancel_queued=True, cancel_running=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            poll = (_CANCEL_POLL_S if mode == "cancel_running"
                    else _AFTER_CURRENT_POLL_S)
            report = runner.shutdown(
                timeout=min(poll, remaining),
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
            # GUI-SHUTDOWN-P1-002: no `mode` snapshot is taken before the
            # wait. _wait_runner tracks upgrades itself, and the mode is
            # re-read AFTER it returns so the stuck report describes the mode
            # that actually expired.
            report = self._wait_runner(runner)
            self._last_report = report
            mode = self._mode or "after_current"

            if not report.stopped:
                # GUI-SHUTDOWN-P1-001: both modes end here. after_current used
                # to fall through to an unbounded re-wait, so a wedged job left
                # the coordinator neither done nor stuck, forever.
                self._stuck = True
                self._stuck_message = (
                    "当前任务没有响应取消请求 — 写操作已拒绝，"
                    "状态/日志仍可读；可继续等待或强制结束进程"
                    if mode == "cancel_running" else
                    "当前任务仍未结束 — 写操作已拒绝，状态/日志仍可读；"
                    "可选择「取消并退出」或强制结束进程")
                _log.error(
                    "gui shutdown STUCK: worker alive after %.0fs %s (job=%s)",
                    _timeout_for(mode), mode, report.running_job_id)
                # Do NOT server_close while worker may still write project files.
                return

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
