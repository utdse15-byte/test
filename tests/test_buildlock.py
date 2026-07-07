"""Tests for manju.runtime.buildlock — the process-level build mutex.

Value locks (§5) guard content; BuildLock guards processes: one mutating
engine per project at a time. Fresh locks refuse with an actionable bilingual
message; stale locks (dead pid on our host, too-old mtime, corrupt file) are
stolen; release never deletes a lock that is not provably ours.

All time-based behavior is tuned via ``stale_after_s`` / ``heartbeat_interval_s``
so the suite stays fast (no fixed sleep over 0.2s).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from manju.runtime.buildlock import BuildLock, BuildLocked, build_lock

HOSTNAME = socket.gethostname()


def _write_holder(
    path: Path,
    *,
    pid: int = 0,
    hostname: str = HOSTNAME,
    actor: str = "ai",
    text: str | None = None,
) -> None:
    """Hand-write a lock file the way a foreign (possibly dead) process would.
    ``text`` overrides the JSON payload entirely, for the corrupt-file cases."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        text = json.dumps(
            {"pid": pid, "actor": actor, "started": "2026-07-05T00:00:00+00:00",
             "hostname": hostname}
        )
    path.write_text(text, encoding="utf-8")


def _dead_pid() -> int:
    """A pid that certainly refers to no live process: spawn a child that
    exits immediately, reap it, reuse its pid."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ------------------------------------------------------------ acquire/release


def test_acquire_release_roundtrip(tmp_project):
    lock = BuildLock(tmp_project.root, actor="human")
    lock.acquire()
    try:
        assert lock.path == tmp_project.runtime_dir / "build.lock"
        assert lock.path.exists()
        holder = json.loads(lock.path.read_text(encoding="utf-8"))
        assert holder["pid"] == os.getpid()
        assert holder["actor"] == "human"
        assert holder["hostname"] == HOSTNAME
        datetime.fromisoformat(holder["started"])  # valid ISO-8601 UTC stamp
    finally:
        lock.release()
    assert not lock.path.exists()


def test_context_manager_releases_on_exception(tmp_path):
    # bare tmp_path: .manju/ does not exist yet — acquire must create it.
    lock = BuildLock(tmp_path)
    with pytest.raises(ValueError, match="boom"):
        with lock:
            assert lock.path.exists()
            raise ValueError("boom")
    assert not lock.path.exists()


# ----------------------------------------------------------------- contention


def test_second_acquire_raises_buildlocked_with_holder_info(tmp_project):
    with build_lock(tmp_project.root, actor="human") as first:
        with pytest.raises(BuildLocked) as excinfo:
            BuildLock(tmp_project.root, actor="ai").acquire()
        exc = excinfo.value
        assert exc.holder["pid"] == os.getpid()
        assert exc.holder["actor"] == "human"
        msg = str(exc)
        assert str(os.getpid()) in msg
        assert "actor=human" in msg
        assert exc.holder["started"] in msg
        assert str(first.path) in msg  # points at the exact file to delete

        # the loser must not have damaged the winner's lock
        assert first.path.exists()
        holder = json.loads(first.path.read_text(encoding="utf-8"))
        assert holder["actor"] == "human" and holder["pid"] == os.getpid()
    assert not first.path.exists()  # convenience context manager released it


# ------------------------------------------------------------------ staleness


def test_stale_dead_pid_on_our_host_is_stolen(tmp_path):
    lock = BuildLock(tmp_path)  # default stale_after_s: only the pid rule applies
    _write_holder(lock.path, pid=_dead_pid(), hostname=HOSTNAME)
    lock.acquire()  # dead holder on our host -> stale -> stolen
    try:
        holder = json.loads(lock.path.read_text(encoding="utf-8"))
        assert holder["pid"] == os.getpid()
    finally:
        lock.release()
    assert not lock.path.exists()


def test_stale_by_age_alive_pid_other_host(tmp_path):
    # alive pid but foreign hostname: the pid probe must NOT apply — only age.
    lock_path = tmp_path / ".manju" / "build.lock"
    old = time.time() - 120.0

    _write_holder(lock_path, pid=os.getpid(), hostname=HOSTNAME + "-elsewhere")
    os.utime(lock_path, (old, old))
    with build_lock(tmp_path, stale_after_s=0.01):  # 120s > 0.01s -> stolen
        pass

    _write_holder(lock_path, pid=os.getpid(), hostname=HOSTNAME + "-elsewhere")
    os.utime(lock_path, (old, old))
    with pytest.raises(BuildLocked):  # 120s < 3600s -> still fresh -> refused
        BuildLock(tmp_path, stale_after_s=3600.0).acquire()
    assert lock_path.exists()  # a refused acquire leaves the lock alone


@pytest.mark.parametrize("content", ["", "not json {{{", "[1, 2, 3]"])
def test_corrupt_lock_file_is_treated_as_stale(tmp_path, content):
    lock = BuildLock(tmp_path)
    _write_holder(lock.path, text=content)
    lock.acquire()  # empty / non-JSON / non-dict: torn write -> stale
    try:
        holder = json.loads(lock.path.read_text(encoding="utf-8"))
        assert holder["pid"] == os.getpid()
    finally:
        lock.release()


# -------------------------------------------------------------------- release


def test_release_is_idempotent_and_never_deletes_foreign_lock(tmp_path):
    lock = BuildLock(tmp_path)
    lock.acquire()
    lock.release()
    lock.release()  # second release: no error, nothing left to do
    assert not lock.path.exists()

    lock.acquire()
    foreign_pid = os.getpid() + 4242  # any pid that is not ours: no probe on release
    _write_holder(lock.path, pid=foreign_pid)  # someone (wrongly) took over
    lock.release()
    assert lock.path.exists()  # never delete someone else's lock
    holder = json.loads(lock.path.read_text(encoding="utf-8"))
    assert holder["pid"] == foreign_pid
    lock.path.unlink()  # clean up the hand-planted foreign lock


# ------------------------------------------------------------------ heartbeat


def test_heartbeat_touches_mtime_and_release_stops_thread(tmp_path):
    lock = BuildLock(tmp_path, stale_after_s=3600.0, heartbeat_interval_s=0.02)
    lock.acquire()
    try:
        past = time.time() - 300.0
        os.utime(lock.path, (past, past))  # backdate; a beat must re-advance it
        deadline = time.time() + 1.0  # bounded poll, normally done in ~0.05s
        while lock.path.stat().st_mtime <= past + 100.0 and time.time() < deadline:
            time.sleep(0.02)
        assert lock.path.stat().st_mtime > past + 100.0  # heartbeat advanced mtime
        hb = lock._heartbeat
        assert hb is not None and hb.is_alive()
    finally:
        lock.release()
    hb.join(timeout=1.0)
    assert not hb.is_alive()  # release stopped the daemon thread
    assert not lock.path.exists()


def test_heartbeat_auto_interval_formula(tmp_path):
    # None = auto: min(stale_after_s / 4, 30); explicit value wins as-is.
    assert BuildLock(tmp_path)._interval_s() == 30.0
    assert BuildLock(tmp_path, stale_after_s=40.0)._interval_s() == 10.0
    assert BuildLock(tmp_path, heartbeat_interval_s=0.5)._interval_s() == 0.5
