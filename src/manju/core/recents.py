"""``~/.manju/recents.json`` — recently-opened Manju projects (round X agent XE).

Cross-project memory for the workspace: user pain #6 (workspace/multi-project
rough edges) + the "recents" half of pain #8 (``manju gui`` used to hard-fail
outside a project, and nothing anywhere remembered what you'd worked on
before). This module is the one store both the CLI, the GUI server and the
MCP server touch at their high-traffic entry points; the workspace picker
(:mod:`manju.gui.workspace`) reads it to build the recents list + the
in-chrome project switcher.

Mirrors :mod:`manju.gui.userstate`'s stance exactly: this is a PREFERENCE of
the person at the keyboard, not project truth (§3) — it lives outside every
project at ``~/.manju/`` (override with ``MANJU_RECENTS``, the test-suite
hermetic knob, same pattern as ``MANJU_GUI_STATE``/``MANJU_LIBRARY``), is
never git-tracked, and is best-effort: a corrupt or unwritable store degrades
to "no recents", never a failure of the real command that tried to touch it.

Schema (``version: 1``)::

    {
      "version": 1,
      "entries": [
        {"path": "<resolved project root>", "name": "<display name>",
         "last_opened": "<iso8601 ts>", "pinned": false}
      ]
    }

Entries are ordered most-recently-opened first, deduped by resolved path,
capped at :data:`MAX_ENTRIES` (oldest UNPINNED entries evicted first — a
pinned entry is exempt from the cap; nothing in this round sets ``pinned``
yet, but the field round-trips for a future manual/UI pin). A read drops —
and persists the removal of — any entry whose path no longer holds a manju
project (deleted, renamed, moved); the dropped paths ride back on
``RecentsResult.dropped`` so a caller can report them once — the entry is
already gone from the store by the time the call returns, so a second read
never re-reports it.

Cross-process safety mirrors :mod:`manju.core.library`'s index lock:
``fcntl.flock`` on a sibling ``.lock`` file, advisory and POSIX-only —
degrades to unlocked (never a hang, never a crash) on a platform without
``fcntl`` or once the timeout elapses, because recents is convenience data,
never load-bearing truth.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .container import PROJECT_FILE, Project
from .yamlio import read_json, write_json

try:
    import fcntl
except ImportError:  # Windows: no fcntl — the lock degrades to a no-op below
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # Windows byte-range lock — the fcntl twin (gate round 1)
except ImportError:  # POSIX: fcntl above is the coordinator
    msvcrt = None  # type: ignore[assignment]

__all__ = ["recents_path", "load_recents", "touch_recent", "RecentsResult", "MAX_ENTRIES"]

_VERSION = 1
MAX_ENTRIES = 20
_LOCK_TIMEOUT_S = 10.0


def recents_path() -> Path:
    """``~/.manju/recents.json`` unless ``MANJU_RECENTS`` names another file
    (tests point it at a tmp path so they never touch a real home)."""
    override = os.environ.get("MANJU_RECENTS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".manju" / "recents.json"


@contextlib.contextmanager
def _lock(path: Path, *, timeout_s: float = _LOCK_TIMEOUT_S) -> Iterator[None]:
    """Cross-process mutex around the recents read-modify-write, the same
    discipline as :func:`manju.core.library._index_lock`. A timed-out lock
    degrades to proceeding unlocked rather than hanging or raising — recents
    is best-effort convenience data, never worth blocking a real command
    over (§3: a missing OS primitive, or a stuck lock, degrades)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None and msvcrt is None:
        yield  # no lock primitive at all (exotic platform): degrade unlocked
        return
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    # Windows gate round 1: the fcntl-only lock no-opped on Windows and the
    # gate's first run proved it (12 concurrent touches -> 3 survivors). The
    # msvcrt byte-0 branch mirrors core/events.events_lock; POSIX unchanged.
    if fcntl is not None:
        retry_exc: tuple[type[BaseException], ...] = (BlockingIOError,)

        def _try() -> None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def _unlock() -> None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        retry_exc = (OSError,)

        def _try() -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

        def _unlock() -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    locked = False
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                _try()
                locked = True
                break
            except retry_exc:
                if time.monotonic() >= deadline:
                    break  # degrade: proceed unlocked rather than hang forever
                time.sleep(0.02)
        yield
    finally:
        if locked:
            try:
                _unlock()
            except OSError:
                pass
        os.close(fd)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_raw(path: Path) -> list[dict[str, Any]]:
    """The stored entries, tolerant of a missing/corrupt/malformed file (a
    torn write, hand-edited garbage) — degrades to empty, never raises."""
    if not path.exists():
        return []
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return []
    out: list[dict[str, Any]] = []
    for e in data["entries"]:
        if isinstance(e, dict) and isinstance(e.get("path"), str) and e["path"]:
            out.append(e)
    return out


def _write_raw(path: Path, entries: list[dict[str, Any]]) -> None:
    write_json(path, {"version": _VERSION, "entries": entries})


@dataclass
class RecentsResult:
    entries: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def load_recents(*, drop_missing: bool = True) -> RecentsResult:
    """The recents list, most-recently-opened first.

    ``drop_missing`` (default True) filters out — and persists the removal
    of — entries whose path no longer holds a manju project. The dropped
    paths ride back on ``.dropped`` so a caller (the workspace picker) can
    report them ONCE; the store already reflects the removal, so the same
    stale entry is never reported twice."""
    path = recents_path()
    with _lock(path):
        entries = _read_raw(path)
        if not drop_missing:
            return RecentsResult(entries=list(entries), dropped=[])
        kept: list[dict[str, Any]] = []
        dropped: list[str] = []
        for e in entries:
            try:
                exists = (Path(e["path"]) / PROJECT_FILE).exists()
            except OSError:
                exists = False
            if exists:
                kept.append(e)
            else:
                dropped.append(e["path"])
        if dropped:
            _write_raw(path, kept)
        return RecentsResult(entries=kept, dropped=dropped)


def touch_recent(project: Project) -> None:
    """Record that ``project`` was just opened — called ONCE per CLI
    invocation (``cli._project()``), at GUI server startup / rebind, and at
    MCP server startup. Upserts by resolved root path (moves it to the front,
    refreshes its name + timestamp), then caps at :data:`MAX_ENTRIES`,
    evicting the oldest UNPINNED entries first.

    Best-effort and silent on any failure (unwritable home, lock contention,
    a project whose config fails to parse) — recents is a convenience shelf,
    never load-bearing, and must never turn a successful project resolution
    into a failed command (mirrors ``gui.userstate.save_gui_state``)."""
    try:
        root = Path(project.root).resolve()
        name = root.stem
        try:
            cfg_name = project.load_config().name
            if cfg_name:
                name = cfg_name
        except Exception:
            pass
        path = recents_path()
        with _lock(path):
            entries = _read_raw(path)
            entries = [e for e in entries if e.get("path") != str(root)]
            entries.insert(0, {"path": str(root), "name": name, "last_opened": _now()})
            # cap: evict the oldest (tail-most) UNPINNED entries first; a
            # pathological all-pinned list is allowed to exceed the cap
            # rather than ever evict something the user explicitly pinned.
            i = len(entries) - 1
            while len(entries) > MAX_ENTRIES and i >= 0:
                if not entries[i].get("pinned"):
                    entries.pop(i)
                i -= 1
            _write_raw(path, entries)
    except Exception:
        pass
