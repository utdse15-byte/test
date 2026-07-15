"""Coordinated GUI application shutdown (one quit thread, structured reports).

HTTP handlers must not block waiting for the job runner. All quit requests
funnel through :class:`AppShutdownCoordinator`, which:

- is idempotent (second quit returns current progress),
- cancels queued jobs immediately,
- optionally cooperative-cancels the running job,
- waits for the runner in a single background thread,
- only then stops the HTTP server,
- never swallows timeouts (logs ``stopped=False``).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .jobs import JobRunner, RunnerState, ShutdownReport

__all__ = ["AppShutdownCoordinator", "QuitResult"]

_log = logging.getLogger("manju.gui.shutdown")


class _ServerHooks(Protocol):
    """Minimal surface the coordinator needs from GuiServer."""

    closing: threading.Event
    app_mode: bool

    def app_status(self) -> dict[str, Any]: ...
    def runner_for_quit(self) -> JobRunner: ...
    def stop_http(self) -> None: ...


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

    @property
    def mode(self) -> str | None:
        return self._mode

    @property
    def in_progress(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def request(self, mode: str = "after_current") -> dict[str, Any]:
        if mode not in ("after_current", "cancel_running"):
            mode = "after_current"
        with self._lock:
            already = self._server.closing.is_set()
            self._server.closing.set()
            self._mode = mode
            if self._thread is not None and self._thread.is_alive():
                return self._payload(
                    code="quit_already_in_progress",
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
        return {
            "ok": True,
            "code": code,
            "mode": mode,
            "shutdown_report": sr,
            "status": self._server.app_status(),
        }

    def _finish(self) -> None:
        try:
            # Let the /api/app/quit response flush before tearing down HTTP.
            time.sleep(0.2)
            runner: JobRunner = self._server.runner
            mode = self._mode or "after_current"
            report = runner.shutdown(
                timeout=None,
                cancel_queued=True,
                cancel_running=(mode == "cancel_running"),
            )
            self._last_report = report
            if not report.stopped:
                _log.warning(
                    "gui shutdown: runner still alive after wait "
                    "(running=%s canceled_queued=%s)",
                    report.running_job_id, report.canceled_queued)
                report = runner.shutdown(
                    timeout=30.0, cancel_queued=True, cancel_running=True)
                self._last_report = report
                if not report.stopped:
                    _log.error(
                        "gui shutdown: runner did not stop; forcing server_close "
                        "(state=%s)", runner.state())
            try:
                self._server.shutdown()  # ThreadingHTTPServer.shutdown
            except Exception as exc:
                _log.warning("gui HTTP shutdown(): %s", exc)
            try:
                self._server.server_close()
            except Exception as exc:
                _log.warning("gui server_close(): %s", exc)
            if report.stopped:
                _log.info("gui shutdown complete (mode=%s)", mode)
            else:
                _log.error("gui shutdown finished with live worker (mode=%s)", mode)
        except Exception:
            _log.exception("gui AppShutdownCoordinator._finish failed")
