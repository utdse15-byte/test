"""Deterministic ffmpeg-cancellation unit tests with a FAKE process (P1 item 9).

These use a fake Popen — no real ffmpeg — so they run everywhere and pin the
cancellation CONTRACT precisely: cancel is requested, terminate precedes kill,
the phase stops (MediaCanceled), no final asset is published, and child
processes are reaped first on Windows. A separate, skip-guarded integration
test exercises a real ffmpeg (see the bottom of this file).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time

import pytest

import manju.media.ffmpeg as ff


class FakeProc:
    """A subprocess that never exits on its own, so the cancel poll loop always
    reaches its cancel/timeout branch. Records the ordered teardown calls."""

    def __init__(self, *, survive_terminate: bool = False) -> None:
        self.pid = 4242
        self.returncode = None
        self.events: list[str] = []
        self._survive_terminate = survive_terminate
        self._killed = False

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")
        self._killed = True
        self.returncode = -9

    def communicate(self, timeout=None):
        self.events.append("communicate_grace" if timeout is not None else "communicate")
        if timeout is not None and self._survive_terminate and not self._killed:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        if self.returncode is None:
            self.returncode = 0
        return ("", "")


def _patch_popen(monkeypatch, proc: FakeProc) -> None:
    monkeypatch.setattr(ff.subprocess, "Popen", lambda *a, **k: proc)


def _always_cancel() -> bool:
    return True


# --------------------------------------------------------------------------- #
# The cancellation contract
# --------------------------------------------------------------------------- #

def test_cancel_is_requested_and_phase_stops(monkeypatch) -> None:
    proc = FakeProc()
    _patch_popen(monkeypatch, proc)
    calls = {"n": 0}

    def check() -> bool:
        calls["n"] += 1
        return True

    with pytest.raises(ff.MediaCanceled):
        ff._run_ffmpeg_cancelable(
            ["ffmpeg", "-i", "in.mp4", "out.mp4"], project=None, subject="s",
            step="render", log_name="render", timeout=None, check=check)
    assert calls["n"] >= 1                 # cancellation was actually requested
    assert "terminate" in proc.events      # the process was signalled


def test_terminate_precedes_kill_when_process_survives_sigterm(monkeypatch) -> None:
    proc = FakeProc(survive_terminate=True)
    _patch_popen(monkeypatch, proc)
    with pytest.raises(ff.MediaCanceled):
        ff._run_ffmpeg_cancelable(
            ["ffmpeg"], project=None, subject=None, step="render",
            log_name="render", timeout=None, check=_always_cancel)
    # terminate FIRST, kill only after the grace period expires.
    assert proc.events == ["terminate", "communicate_grace", "kill", "communicate"]
    assert proc.events.index("terminate") < proc.events.index("kill")


def test_no_kill_when_sigterm_is_enough(monkeypatch) -> None:
    proc = FakeProc(survive_terminate=False)
    _patch_popen(monkeypatch, proc)
    with pytest.raises(ff.MediaCanceled):
        ff._run_ffmpeg_cancelable(
            ["ffmpeg"], project=None, subject=None, step="render",
            log_name="render", timeout=None, check=_always_cancel)
    assert proc.events == ["terminate", "communicate_grace"]
    assert "kill" not in proc.events


def test_child_processes_reaped_before_terminate_on_windows(monkeypatch) -> None:
    proc = FakeProc()
    _patch_popen(monkeypatch, proc)
    monkeypatch.setattr(ff, "_IS_WINDOWS", True)
    monkeypatch.setattr(ff, "_taskkill_tree", lambda p: p.events.append("taskkill_tree"))
    with pytest.raises(ff.MediaCanceled):
        ff._run_ffmpeg_cancelable(
            ["ffmpeg"], project=None, subject=None, step="render",
            log_name="render", timeout=None, check=_always_cancel)
    # the whole tree is killed BEFORE the direct terminate (its /T reach note).
    assert proc.events[0] == "taskkill_tree"
    assert proc.events.index("taskkill_tree") < proc.events.index("terminate")


def test_no_final_asset_published_on_cancel(tmp_path, monkeypatch) -> None:
    _patch_popen(monkeypatch, FakeProc())
    dest = tmp_path / "final_v1.mp4"
    ev = threading.Event()
    ev.set()
    with pytest.raises(ff.MediaCanceled):
        with ff.atomic_output(dest) as tmp:
            ff.run_ffmpeg(["-i", "in.mp4", str(tmp)], project=None, cancel_event=ev)
    assert not dest.exists()                      # nothing published at the trusted path
    assert not list(tmp_path.glob(".*tmp*"))       # and no truncated temp left behind


def test_default_path_used_when_no_cancel_check(monkeypatch) -> None:
    # With no cancel check active, the blocking subprocess.run path is used and
    # the cancelable Popen loop is never entered.
    used = {"popen": False, "run": False}

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(ff.subprocess, "run",
                        lambda *a, **k: used.__setitem__("run", True) or _Done())
    monkeypatch.setattr(ff.subprocess, "Popen",
                        lambda *a, **k: used.__setitem__("popen", True))
    ff.run_ffmpeg(["-i", "in.mp4", "out.mp4"], project=None)  # no cancel_event
    assert used["run"] is True and used["popen"] is False


# --------------------------------------------------------------------------- #
# Job-status transitions exactly once on an ffmpeg cancel
# --------------------------------------------------------------------------- #

def test_runner_canceled_state_is_terminal_and_single(monkeypatch) -> None:
    from manju.gui.jobs import JobRunner

    r = JobRunner()
    try:
        def fn(job):
            job.cancel_event.set()
            raise ff.MediaCanceled("已取消:ffmpeg 进程已终止")

        job = r.submit("export", {}, fn)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and r.get(job.id).state != "canceled":
            time.sleep(0.01)
        done = r.get(job.id)
        assert done.state == "canceled"
        finished = done.finished
        # It does not flip again: state + finished stay put across further polls.
        time.sleep(0.1)
        again = r.get(job.id)
        assert again.state == "canceled"
        assert again.finished == finished
    finally:
        r.shutdown(timeout=2.0)


# --------------------------------------------------------------------------- #
# Real ffmpeg integration — skipped with a precise reason when unsupported
# --------------------------------------------------------------------------- #

@pytest.mark.ffmpeg
def test_real_ffmpeg_cancel_integration(tmp_path) -> None:
    """Start a genuinely long real-ffmpeg encode, cancel it mid-run, and confirm
    the process exits and no valid final is published. Skipped with an
    actionable reason (never a hidden pass) when ffmpeg is absent."""
    if shutil.which("ffmpeg") is None:
        pytest.skip(
            "real ffmpeg cancel integration needs ffmpeg on PATH — the Windows "
            "hard gate installs the pinned 6.1.1 (choco); install ffmpeg locally "
            "to run this (macOS `brew install ffmpeg`, Ubuntu `apt install ffmpeg`)."
        )

    dest = tmp_path / "final_v1.mp4"
    ev = threading.Event()

    def _cancel_soon() -> None:
        time.sleep(0.4)
        ev.set()

    t = threading.Thread(target=_cancel_soon, daemon=True)
    t.start()
    with pytest.raises(ff.MediaCanceled):
        with ff.atomic_output(dest) as tmp:
            # a 60s synthetic encode — far longer than the ~0.4s to cancel.
            ff.run_ffmpeg(
                ["-f", "lavfi", "-i", "testsrc=duration=60:size=320x240:rate=25",
                 "-t", "60", str(tmp)],
                project=None, cancel_event=ev, timeout=None)
    t.join(timeout=2.0)
    assert not dest.exists()  # incomplete output is never a valid final
