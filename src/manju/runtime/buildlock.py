"""Process-level build mutex — ``.manju/<name>.lock`` (§3, §5).

Value-hash locks (§5) guard *content*: a sealed field cannot drift without the
engine refusing to build. They say nothing about *processes*. The dual-actor
loop the design celebrates — a human running ``manju build`` in a terminal, an
AI session mutating shots over MCP, the ``manju gui`` job runner in a third
seat — means two engines can race on ``timeline.json``, ``renders/`` and the
SQLite ledger with nobody at fault. This module closes that gap: every
mutating entrypoint (CLI, GUI, MCP) funnels its work through one
:class:`BuildLock` per project, so at most one process mutates a project at a
time. The loser gets a :class:`BuildLocked` with a one-line, actionable
finding instead of a corrupted timeline.

Mechanism: atomic ``os.open(path, O_CREAT | O_EXCL | O_WRONLY)``. O_EXCL over
``fcntl`` on purpose:

- portable — ``fcntl`` does not exist on Windows, and Chinese/Windows paths
  are first-class (§14); O_EXCL-create is atomic everywhere we run.
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
- **corrupt** — an empty or non-JSON lock file can only be a torn write from
  a process that died mid-acquire; treated as stale outright.

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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["BuildLock", "BuildLocked", "build_lock"]

LOCK_DIR = ".manju"  # the disposable runtime dir (§3); mkdir'd on demand
_HEARTBEAT_CAP_S = 30.0  # never beat slower than this, however lax the staleness


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness probe via ``os.kill(pid, 0)``.

    Only ``ProcessLookupError`` proves death. ``PermissionError`` means the
    pid exists but belongs to someone else — alive. Any other error (Windows
    quirks, absurd pids from a tampered file) also counts as alive: a false
    "alive" merely delays stealing until the mtime rule kicks in, while a
    false "dead" would delete a live builder's lock.
    """
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
    (CLI, GUI, MCP) can render their own UI on top of the one-line message.
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
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass  # released between our check and the steal — fine
            try:
                self._create()
            except FileExistsError:
                raise BuildLocked(self._read_holder() or {}, self.path) from None
        self._start_heartbeat()
        return self

    def _create(self) -> None:
        """The atomic acquire: O_CREAT | O_EXCL either mints the file or raises
        FileExistsError. Holder JSON is written through the same fd so the
        empty-file window is as small as the OS allows."""
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            holder = {
                "pid": os.getpid(),
                "actor": self.actor,
                "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "hostname": self._hostname,
            }
            os.write(fd, json.dumps(holder, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)

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
        if holder is None:
            return True  # empty / non-JSON: only a torn or tampered write
        pid = holder.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            return True  # our writer always records an int pid — corrupt
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
    mutating entrypoint (CLI command, GUI job, MCP tool) wraps its work in.
    Extra keyword arguments pass straight to :class:`BuildLock`."""
    lock = BuildLock(project_root, actor=actor, **kw)
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
