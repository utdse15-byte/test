"""Process-level build mutex — ``.manju/<name>.lock`` (§3, §5).

Value-hash locks (§5) guard *content*: a sealed field cannot drift without the
engine refusing to build. They say nothing about *processes*. The dual-actor
loop the design celebrates — a human running ``manju build`` in a terminal, an
AI session mutating shots over CLI/GUI, the ``manju gui`` job runner in a third
seat — means two engines can race on ``timeline.json``, ``renders/`` and the
SQLite ledger with nobody at fault. This module closes that gap: every
mutating entrypoint (CLI, GUI, CLI/GUI) funnels its work through one
:class:`BuildLock` per project, so at most one process mutates a project at a
time. The loser gets a :class:`BuildLocked` with a one-line, actionable
finding instead of a corrupted timeline.

Mechanism: write-then-hardlink — the holder JSON is fully written (and
fsynced) to a per-pid temp sibling, then ``os.link``ed to the lock path.
Link creation is atomic AND the lock file's first visible state already
carries the complete holder record, so no sibling can ever observe an empty
lock mid-acquire (FINAL_ACCEPTANCE F4/D: GitHub CI reproduced a real dual
ownership through exactly that window — O_EXCL-create THEN write left an
empty file that the old "empty = stale outright" rule let a racing sibling
steal). Filesystems without hard links fall back to the historical
``O_CREAT | O_EXCL`` create-then-write; the staleness grace below keeps the
fallback safe too. O_EXCL/link over ``fcntl`` on purpose:

- portable — ``fcntl`` does not exist on Windows, and Chinese/Windows paths
  are first-class (§14); both creates are atomic everywhere we run.
- the lock file doubles as human-readable holder info: JSON with ``pid``,
  ``actor``, ``started`` and ``hostname``, so ``cat .manju/build.lock`` (or
  the BuildLocked message itself) tells a stuck user exactly who holds the
  door and whether it is safe to delete.

Staleness — a crashed build must never brick the project. A lock is stale
when any of:

- **dead pid, same host** — the recorded pid no longer exists on *our*
  hostname. Probe is ``os.kill(pid, 0)``: only ``ProcessLookupError`` proves
  death; ``PermissionError`` (alive, not ours) and every other error count
  as alive, erring on the side of not stealing.
- **too old** — the file's mtime is older than ``stale_after_s``. This is the
  backstop for other hosts (NFS shares), pid reuse and imprecise platforms.
  While held, a daemon heartbeat thread touches the mtime every
  ``min(stale_after_s / 4, 30)`` seconds, so a long render never *looks*
  stale.
- **corrupt AND old** — an empty or non-JSON lock file is a torn write from
  a dead process ONLY once it has aged past a short grace
  (``_CORRUPT_GRACE_S``); a FRESH unparsable lock is presumed to be a
  legacy/fallback writer mid-acquire and is NOT stealable (the CI-caught
  dual-ownership fix; with link-create the window no longer exists at all,
  the grace guards the O_EXCL fallback and tampered files).

A stale lock is removed and the atomic create retried exactly once; losing
that race raises :class:`BuildLocked` like any fresh lock. Consistent with §3
the lock lives in the disposable runtime dir: deleting it by hand is always
safe *when no build is running* — which is precisely what the error message
tells you to verify (若确认无进程在跑,删除后重试).
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["BuildLock", "BuildLocked", "build_lock"]

LOCK_DIR = ".manju"  # the disposable runtime dir (§3); mkdir'd on demand
_HEARTBEAT_CAP_S = 30.0  # never beat slower than this, however lax the staleness
# How long an EMPTY/unparsable lock must sit before it counts as a corpse. A
# fresh one is a mid-acquire writer (O_EXCL fallback path) — stealing it is the
# dual-ownership race GitHub CI reproduced. 10s dwarfs any open->write gap by
# orders of magnitude while a genuinely torn lock still clears fast.
_CORRUPT_GRACE_S = 10.0


# W1 (§3.4): os.name is process-constant; a module flag keeps the dispatch
# below patchable in tests without touching the global ``os`` module.
_IS_WINDOWS = os.name == "nt"

# OpenProcess access right / sentinel values for the Windows liveness probe.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259  # GetExitCodeProcess: process has not exited
_ERROR_INVALID_PARAMETER = 87  # OpenProcess: no such pid


def _win_kernel32():
    """kernel32 with ``use_last_error`` so OpenProcess failures are readable
    via ``ctypes.get_last_error``. A function (not a module constant) so tests
    can stub the DLL surface and non-Windows platforms never load it."""
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness probe that CANNOT kill what it probes.

    ``os.kill(pid, 0)`` is NOT a probe on Windows: CPython routes any signal
    other than CTRL_C_EVENT/CTRL_BREAK_EVENT through ``TerminateProcess`` —
    the old code path would have KILLED a live same-user lock holder while
    "checking" it. Instead: OpenProcess with the weakest query right, then
    GetExitCodeProcess — STILL_ACTIVE means alive; a real exit code means the
    pid is a corpse (possibly held open by a handle). OpenProcess refusing
    with access-denied proves existence (someone else's process → alive);
    invalid-parameter proves absence (→ dead). Every undecidable outcome
    counts as ALIVE, same philosophy as the POSIX probe: a false "alive" only
    delays stealing until the mtime backstop, a false "dead" kills a builder.
    """
    try:
        import ctypes

        k32 = _win_kernel32()
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return (not ok) or code.value == _STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return True  # defensive: the probe must never crash an acquire


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness probe. POSIX: ``os.kill(pid, 0)`` (a real probe
    there). Windows: :func:`_pid_alive_windows` — see its docstring for why
    ``os.kill`` must never run on that branch.

    Only ``ProcessLookupError`` proves death. ``PermissionError`` means the
    pid exists but belongs to someone else — alive. Any other error (absurd
    pids from a tampered file) also counts as alive: a false "alive" merely
    delays stealing until the mtime rule kicks in, while a false "dead" would
    delete a live builder's lock.
    """
    if _IS_WINDOWS:
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    except Exception:
        return True  # defensive: the probe must never crash an acquire
    return True


class BuildLocked(RuntimeError):
    """Another process is (or appears to be) building this project.

    Carries the offending lock's ``holder`` dict and ``lock_path`` so callers
    (CLI, GUI, CLI/GUI) can render their own UI on top of the one-line message.
    """

    def __init__(self, holder: dict[str, Any], lock_path: Path | str):
        self.holder: dict[str, Any] = dict(holder or {})
        self.lock_path = Path(lock_path)
        pid = self.holder.get("pid", "?")
        actor = self.holder.get("actor", "?")
        started = self.holder.get("started", "?")
        super().__init__(
            f"build already running: pid {pid} (actor={actor}, started {started}) "
            f"— 若确认无进程在跑,删除 {self.lock_path} 重试"
        )


class BuildLock:
    """One-per-project process mutex. Deliberately NOT reentrant: a second
    ``acquire()`` — same process or another — raises :class:`BuildLocked`.

    Usage::

        with BuildLock(project.root, actor="human"):
            ...  # exclusive: compile / render / ledger writes

    ``heartbeat_interval_s=None`` means auto (``min(stale_after_s / 4, 30)``);
    tests pin it low to observe beats without waiting.
    """

    def __init__(
        self,
        project_root: Path | str,
        *,
        name: str = "build",
        actor: str = "engine",
        stale_after_s: float = 3600.0,
        heartbeat_interval_s: float | None = None,
    ):
        self.project_root = Path(project_root)
        self.name = name
        self.actor = actor  # "human" | "ai" | "engine" — mirrors events.jsonl
        self.stale_after_s = float(stale_after_s)
        self.heartbeat_interval_s = heartbeat_interval_s
        self.path = self.project_root / LOCK_DIR / f"{name}.lock"
        self._hostname = socket.gethostname()
        # A unique per-acquisition token. Identity by (pid, hostname) ALONE has
        # an ABA hole: after this instance's lock is stolen (stale) and a
        # SUCCESSOR re-minted by the SAME pid+hostname, a bare pid/host match
        # let release() delete — and the heartbeat refresh — the successor's
        # lock, so two builders could believe they held it. The token pins the
        # holder to THIS acquisition; release/heartbeat verify it. It also names
        # the staging temp file so two threads of one process never share one.
        self._token = uuid.uuid4().hex
        self._stop: threading.Event | None = None
        self._heartbeat: threading.Thread | None = None

    # -------------------------------------------------------------- acquire

    def acquire(self) -> "BuildLock":
        """Take the lock or raise :class:`BuildLocked`.

        A stale lock (module docstring) is stolen: removed, then the atomic
        create retried exactly once. Losing that retry race must not crash —
        whoever re-locked in the window wins and we report *their* holder.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)  # .manju/ may not exist yet
        try:
            self._create()
        except FileExistsError:
            holder = self._read_holder()
            if not self._is_stale(holder):
                raise BuildLocked(holder or {}, self.path)
            if not self._steal_stale():
                # another contender is mid-steal (or the lock re-minted fresh
                # while we queued for the steal mutex) — their win, our clean
                # immediate BuildLocked, same no-wait contract as ever.
                raise BuildLocked(self._read_holder() or holder or {}, self.path)
            try:
                self._create()
            except FileExistsError:
                raise BuildLocked(self._read_holder() or {}, self.path) from None
        self._start_heartbeat()
        return self

    def _steal_stale(self) -> bool:
        """Unlink a stale lock under a short-lived STEAL MUTEX (`.steal`,
        O_EXCL) — returns True when the stale lock was removed by US.

        The old blind ``unlink()`` raced two stealers: A unlinks + re-creates,
        then B's unlink deletes A's FRESH lock and B creates too — dual
        ownership of the one-per-project mutex. Under the mutex, staleness is
        RE-CHECKED before the unlink, so a lock re-minted in the window is
        never deleted."""
        steal = self.path.with_name(self.path.name + ".steal")
        try:
            fd = os.open(str(steal), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:  # a stealer that died mid-steal must not wedge acquires forever
                if time.time() - steal.stat().st_mtime > _CORRUPT_GRACE_S:
                    steal.unlink()
            except OSError:
                pass
            return False
        except OSError:
            return False
        try:
            # RE-CHECK under the mutex — _is_stale also protects a fresh
            # corrupt (mid-acquire O_EXCL fallback) lock via the corrupt-grace.
            if not self._is_stale(self._read_holder()):
                return False  # re-minted fresh while we took the mutex
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass  # released between check and steal — fine, name is free
            except OSError:
                return False
            return True
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                steal.unlink()
            except OSError:
                pass

    def _create(self) -> None:
        """The atomic acquire: holder JSON is FULLY written (+fsynced) to a
        per-pid temp sibling, then hard-linked to the lock path. ``os.link``
        either mints the lock (already complete — no empty-file window, the
        FINAL_ACCEPTANCE F4/D fix) or raises FileExistsError. Filesystems
        without hard links (rare: some FAT/network mounts) fall back to the
        historical O_EXCL create-then-write; ``_is_stale``'s corrupt-grace
        keeps that fallback unstealable mid-acquire."""
        holder = {
            "pid": os.getpid(),
            "actor": self.actor,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hostname": self._hostname,
            "token": self._token,  # pins this exact acquisition (ABA guard)
        }
        payload = json.dumps(holder, ensure_ascii=False).encode("utf-8")
        # Per-ACQUISITION staging name (the token, not the pid): two threads of
        # one process were minting the SAME `.<pid>.tmp`, opening it O_TRUNC, so
        # A could link B's payload and either could unlink the file the other
        # still needed. A unique name + O_EXCL gives each acquisition its own
        # staging inode.
        tmp = self.path.with_name(self.path.name + f".{self._token}.tmp")
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(tmp, self.path)  # atomic; target appears fully written
                return
            except FileExistsError:
                raise
            except OSError:
                # hard links unsupported here — historical O_EXCL fallback (the
                # corrupt-grace in _is_stale closes its mid-acquire window).
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                try:
                    os.write(fd, payload)
                finally:
                    os.close(fd)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------ staleness

    def _read_holder(self) -> dict[str, Any] | None:
        """The lock file's JSON payload, or None when missing / empty /
        corrupt — a torn write from a dead process must read as stale, never
        crash the next builder."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _is_stale(self, holder: dict[str, Any] | None) -> bool:
        pid = holder.get("pid") if holder is not None else None
        corrupt = holder is None or not isinstance(pid, int) or isinstance(pid, bool)
        if corrupt:
            # FINAL_ACCEPTANCE F4/D: a FRESH unparsable lock is a mid-acquire
            # writer (O_EXCL fallback), NOT a corpse — stealing it was the
            # dual-ownership race GitHub CI reproduced. Only corrupt AND old
            # is stealable.
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                return True  # vanished mid-check; the retried create decides
            return age > _CORRUPT_GRACE_S
        if holder.get("hostname") == self._hostname and not _pid_alive(pid):
            return True  # provably dead on this very host
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True  # vanished mid-check; the retried create decides
        return age > self.stale_after_s  # cross-host / pid-reuse backstop

    # ------------------------------------------------------------ heartbeat

    def _interval_s(self) -> float:
        if self.heartbeat_interval_s is not None:
            return max(float(self.heartbeat_interval_s), 0.001)
        return max(min(self.stale_after_s / 4.0, _HEARTBEAT_CAP_S), 0.001)

    def _start_heartbeat(self) -> None:
        self._stop = threading.Event()
        self._heartbeat = threading.Thread(
            target=self._beat,
            args=(self._stop,),
            name=f"manju-{self.name}-lock-heartbeat",
            daemon=True,  # must never keep the process alive
        )
        self._heartbeat.start()

    def _beat(self, stop: threading.Event) -> None:
        """Touch the mtime so a long build never trips the age rule. Every
        failure is swallowed: the file may vanish under a hand-clean or a
        (buggy) steal, and the heartbeat must die silently, never raise."""
        try:
            while not stop.wait(self._interval_s()):
                # Only refresh the mtime while the lock is STILL OURS — a lock
                # our token no longer owns (stolen-as-stale then re-minted by a
                # successor, possibly same pid+host) must not be kept alive by
                # our heartbeat. A read hiccup errs toward touching (a transient
                # unreadable lock should not orphan a live build).
                holder = self._read_holder()
                if holder is not None and holder.get("token") not in (None, self._token):
                    return  # someone else owns it now — stop beating, silently
                try:
                    os.utime(self.path)
                except OSError:
                    pass
        except Exception:
            pass

    # -------------------------------------------------------------- release

    def release(self) -> None:
        """Idempotent. Stops the heartbeat, then removes the lock file only if
        it still records OUR pid on OUR host (re-read + compare) — never
        delete someone else's lock, even one that stole ours."""
        if self._stop is not None:
            self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=2.0)
        holder = self._read_holder()
        if (
            holder is not None
            and holder.get("token") == self._token
            and holder.get("pid") == os.getpid()
            and holder.get("hostname") == self._hostname
        ):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass  # already gone: double release or hand-cleaned

    # ---------------------------------------------------------------- with

    def __enter__(self) -> "BuildLock":
        return self.acquire()

    def __exit__(self, *exc: object) -> bool:
        self.release()  # on success and on exception alike
        return False


@contextmanager
def build_lock(project_root: Path | str, actor: str = "engine", **kw: Any) -> Iterator[BuildLock]:
    """``with build_lock(project.root, actor="ai"): ...`` — the one-liner every
    mutating entrypoint (CLI command or GUI job) wraps its work in.
    Extra keyword arguments pass straight to :class:`BuildLock`."""
    lock = BuildLock(project_root, actor=actor, **kw)
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
