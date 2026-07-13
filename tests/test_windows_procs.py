"""W1 Windows process semantics (MANJU_WINDOWS_ONLY_LEAN_V3 §3.4/§3.5).

The single Windows-lethal defect at HEAD: ``BuildLock``'s staleness probe
``_pid_alive`` used ``os.kill(pid, 0)`` — which on Windows is NOT a probe.
CPython routes any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT through
``TerminateProcess``, so a second ``manju`` invocation checking whether the
lock holder is alive would KILL the live build it was politely queuing behind.
The fix dispatches to an OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) +
GetExitCodeProcess probe on ``os.name == "nt"`` — the POSIX path is
byte-identical (§3.4: 不要无证据重写现有锁 — the lock file mechanism is
untouched, only the liveness probe grows a Windows branch).

§3.5: ``local_cmd``'s timeout reaper degrades on Windows from "kill the whole
process group" to "kill the direct child only" — a wrapper's grandchildren
(the leaked GPU worker of F1) survive. The Windows branch must reap the TREE:
``taskkill /PID <pid> /T /F`` (the documented cancel order: graceful → wait →
taskkill), keeping the POSIX ``killpg`` path byte-identical.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import types

import pytest

import manju.runtime.buildlock as buildlock
import manju.providers.local_cmd as local_cmd


# --------------------------------------------------------------------------
# §3.4 — the liveness probe must never kill what it probes
# --------------------------------------------------------------------------


def test_pid_probe_leaves_live_child_alive():
    """Probing a LIVE process must return True and leave it running.

    Green on POSIX at HEAD (kill(pid, 0) really is a probe there); RED on
    Windows at HEAD, where os.kill(pid, 0) TerminateProcess-es the child.
    Runs everywhere — this is the regression tripwire for the Windows CI leg.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert buildlock._pid_alive(child.pid) is True
        time.sleep(0.3)  # give a wrongful TerminateProcess time to land
        assert child.poll() is None, "the probe must never kill the probed process"
    finally:
        child.kill()
        child.wait(timeout=10)
    assert buildlock._pid_alive(child.pid) is False


def test_pid_probe_windows_dispatch_never_calls_os_kill(monkeypatch):
    """On ``os.name == "nt"`` the probe must not touch ``os.kill`` at all —
    there is no safe signal-0 semantics to fall back on."""
    killed: list[tuple[int, int]] = []
    real_kill = os.kill

    def spy(pid, sig):
        killed.append((pid, sig))
        return real_kill(pid, sig)

    monkeypatch.setattr(buildlock.os, "kill", spy)
    monkeypatch.setattr(buildlock, "_IS_WINDOWS", True, raising=True)
    result = buildlock._pid_alive(os.getpid())
    assert isinstance(result, bool)
    assert killed == [], "os.kill must never run on the Windows branch"


class _StubKernel32:
    """Just enough of kernel32 for the probe: OpenProcess handle/0,
    GetExitCodeProcess writing an exit code, CloseHandle bookkeeping."""

    def __init__(self, handle: int, exit_code: int | None = None):
        self._handle = handle
        self._exit_code = exit_code
        self.closed: list[int] = []

    def OpenProcess(self, access, inherit, pid):
        return self._handle

    def GetExitCodeProcess(self, handle, code_ref):
        if self._exit_code is None:
            return 0  # probe failure — err-safe callers treat as alive
        code_ref._obj.value = self._exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_pid_alive_windows_probe_semantics(monkeypatch):
    """The four probe outcomes: STILL_ACTIVE → alive; exited → dead;
    OpenProcess denied (err 5: exists, not ours) → alive; OpenProcess
    invalid-parameter (err 87: no such pid) → dead."""
    import ctypes

    STILL_ACTIVE = 259

    stub = _StubKernel32(handle=1234, exit_code=STILL_ACTIVE)
    monkeypatch.setattr(buildlock, "_win_kernel32", lambda: stub)
    assert buildlock._pid_alive_windows(4242) is True
    assert stub.closed == [1234], "the process handle must be closed"

    stub = _StubKernel32(handle=1234, exit_code=0)
    monkeypatch.setattr(buildlock, "_win_kernel32", lambda: stub)
    assert buildlock._pid_alive_windows(4242) is False

    stub = _StubKernel32(handle=0)
    monkeypatch.setattr(buildlock, "_win_kernel32", lambda: stub)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    assert buildlock._pid_alive_windows(4242) is True  # exists, not ours

    stub = _StubKernel32(handle=0)
    monkeypatch.setattr(buildlock, "_win_kernel32", lambda: stub)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)
    assert buildlock._pid_alive_windows(4242) is False  # no such pid


def test_pid_probe_windows_errors_count_as_alive(monkeypatch):
    """Err-safe philosophy unchanged (buildlock docstring): a probe that
    cannot decide must say ALIVE — a false 'alive' only delays stealing until
    the mtime backstop; a false 'dead' would kill a live builder's lock."""

    def boom():
        raise OSError("no kernel32 here")

    monkeypatch.setattr(buildlock, "_win_kernel32", boom)
    assert buildlock._pid_alive_windows(4242) is True


# --------------------------------------------------------------------------
# §3.5 — timeout reaping must clean the WHOLE tree on Windows too
# --------------------------------------------------------------------------


def test_local_cmd_kill_tree_uses_taskkill_on_windows(monkeypatch):
    """On Windows the group-kill must become ``taskkill /PID <pid> /T /F``
    (tree + force) — the direct-child-only degrade leaves grandchildren
    running (the F1 leak, unreapable at HEAD on Windows)."""
    recorded: list[list[str]] = []

    def record_run(cmd, *a, **kw):
        recorded.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    kills: list[str] = []
    fake_proc = types.SimpleNamespace(pid=43210, kill=lambda: kills.append("kill"))

    monkeypatch.setattr(local_cmd.subprocess, "run", record_run)
    monkeypatch.setattr(local_cmd, "_IS_WINDOWS", True, raising=True)
    local_cmd._kill_process_group(fake_proc)

    taskkills = [c for c in recorded if c and c[0] == "taskkill"]
    assert taskkills, f"expected a taskkill invocation, got {recorded!r}"
    assert taskkills[0] == ["taskkill", "/PID", "43210", "/T", "/F"]


# --------------------------------------------------------------------------
# §3.4 — the events-ledger append lock must WORK on Windows, not go dark
# --------------------------------------------------------------------------
#
# DECISIONS #20 (pre-Windows-plan) chose "no msvcrt lock — Windows appends
# refuse/drop". Under MANJU_WINDOWS_ONLY_LEAN_V3 Windows is the primary
# platform: an evidence ledger that silently drops EVERY best-effort record
# and raises on every required one contradicts "text is truth" where it now
# matters most. The reversal is deliberate, minimal (an ``msvcrt.locking``
# byte-0 lock on the SAME sibling lock file, same no-unlocked-write policy)
# and recorded in DECISIONS.


class _StubMsvcrt:
    """Just enough of msvcrt for events_lock: LK_NBLCK/LK_UNLCK bookkeeping,
    optionally refusing every lock attempt to simulate contention."""

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self, contended: bool = False):
        self.contended = contended
        self.calls: list[tuple[int, int]] = []

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, nbytes))
        if mode == self.LK_NBLCK and self.contended:
            raise OSError(13, "locked (simulated contention)")


def test_events_append_uses_msvcrt_lock_when_fcntl_missing(tmp_path, monkeypatch):
    """No fcntl + msvcrt present (i.e. Windows): a REQUIRED append must lock
    via msvcrt and land the record — never raise no_reliable_lock."""
    import manju.core.events as events

    stub = _StubMsvcrt()
    monkeypatch.setattr(events, "fcntl", None)
    monkeypatch.setattr(events, "msvcrt", stub, raising=False)

    ok = events.append_jsonl_line(
        tmp_path, {"actor": "engine", "action": "test"}, durable=False, required=True
    )
    assert ok is True
    assert (tmp_path / "events.jsonl").exists()
    modes = [m for (m, _n) in stub.calls]
    assert stub.LK_NBLCK in modes, "the append must take the msvcrt byte lock"
    assert stub.LK_UNLCK in modes, "the append must release the msvcrt byte lock"


def test_events_append_contention_still_never_writes_unlocked(tmp_path, monkeypatch):
    """WP1 policy unchanged on the Windows branch: lock timeout → required
    raises lock_timeout; best-effort drops. NEVER an unlocked write."""
    import manju.core.events as events

    stub = _StubMsvcrt(contended=True)
    monkeypatch.setattr(events, "fcntl", None)
    monkeypatch.setattr(events, "msvcrt", stub, raising=False)
    monkeypatch.setattr(events, "DEFAULT_LOCK_TIMEOUT_S", 0.05)

    with pytest.raises(events.EvidenceWriteError) as exc:
        events.append_jsonl_line(
            tmp_path, {"actor": "engine", "action": "test"}, durable=False, required=True
        )
    assert exc.value.reason == "lock_timeout"
    ok = events.append_jsonl_line(
        tmp_path, {"actor": "engine", "action": "test"}, durable=False, required=False
    )
    assert ok is False
    assert not (tmp_path / "events.jsonl").exists(), "never write unlocked"


def test_events_append_refuses_when_no_lock_primitive_exists(tmp_path, monkeypatch):
    """Neither fcntl nor msvcrt (exotic platform): the pre-W1 refuse/drop
    contract stays byte-identical."""
    import manju.core.events as events

    monkeypatch.setattr(events, "fcntl", None)
    monkeypatch.setattr(events, "msvcrt", None, raising=False)

    with pytest.raises(events.EvidenceWriteError):
        events.append_jsonl_line(
            tmp_path, {"actor": "engine", "action": "test"}, durable=False, required=True
        )
    ok = events.append_jsonl_line(
        tmp_path, {"actor": "engine", "action": "test"}, durable=False, required=False
    )
    assert ok is False
    assert not (tmp_path / "events.jsonl").exists()


def test_local_cmd_posix_group_kill_unchanged():
    """The POSIX path stays byte-identical: a dead fake pid falls through the
    group-kill attempt into the direct-child kill, exactly as at HEAD."""
    if not (hasattr(os, "killpg") and hasattr(os, "getpgid")):
        pytest.skip("POSIX process groups required")
    kills: list[str] = []
    fake_proc = types.SimpleNamespace(pid=2**22 + 12345, kill=lambda: kills.append("kill"))
    local_cmd._kill_process_group(fake_proc)
    assert kills == ["kill"]
