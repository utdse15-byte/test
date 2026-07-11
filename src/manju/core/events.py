"""events.jsonl — the collaboration log (§3, §10).

Who did what, when. This is the handover surface between human and AI:
`manju status` + the tail of this log gets either party into context in 30s.
Append-only, one JSON object per line, UTF-8.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows: no fcntl — WP1 refuses an unlocked write (below).
    fcntl = None  # type: ignore[assignment]

EVENTS_FILE = "events.jsonl"
EVENTS_LOCK = "events.lock"

# The default flock acquisition budget (WP1). Kept a module constant (resolved at
# call time when ``events_lock`` is called with ``timeout_s=None``) so a test can
# drive the lock-timeout path fast without threading a timeout through every
# best-effort caller. The effective default is 15.0s, unchanged from DR03C.
DEFAULT_LOCK_TIMEOUT_S = 15.0


class EvidenceWriteError(RuntimeError):
    """A REQUIRED evidence append could not be made durably (WP1 fail-closed).

    ``.reason`` is a short, stable code — ``lock_timeout`` / ``no_reliable_lock``
    / ``io_error``. ``str()`` deliberately carries NO secret, NO path and NO
    signed-URL query (only the reason + a generic note), so this error is safe to
    log or fold into an event/failure anywhere the events stream travels."""

    _MESSAGES = {
        "lock_timeout": "could not acquire the events lock within the timeout",
        "no_reliable_lock": "no reliable file lock is available on this platform",
        "io_error": "the durable events write failed",
    }

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(self._MESSAGES.get(reason, "evidence write failed"))


@contextlib.contextmanager
def events_lock(project_root: Any, *, timeout_s: float | None = None,
                required: bool = False, lock_name: str = EVENTS_LOCK) -> Iterator[bool]:
    """The SINGLE cross-process/-thread mutex around an ``events.jsonl`` append —
    one flock on the sibling ``events.lock`` (the DR03C pattern, now the one
    coordinator the whole codebase shares).

    ``lock_name`` selects the sibling lock file (default ``events.lock``); a
    caller coordinating a DIFFERENT append-only log under the same discipline
    (07C's ``verifications.jsonl`` → ``verifications.lock``) passes its own so
    the two logs never contend on one another's mutex (WP2 §4.4).

    WP1 policy (``不允许锁超时后无锁写`` — never fall through to an unlocked write):

    * lock acquired → ``yield True`` (safe to write).
    * timeout: ``required`` → raise :class:`EvidenceWriteError` ``lock_timeout``;
      best-effort → ``yield False`` (the caller MUST skip the write; the record is
      dropped, never torn by an unlocked append).
    * no ``fcntl`` (Windows): ``required`` → raise
      :class:`EvidenceWriteError` ``no_reliable_lock``; best-effort → ``yield
      False`` (same skip-the-write drop). This CHANGES DR03C's no-fcntl
      no-op-and-write — a best-effort record may now be dropped, but is never
      written unlocked.
    """
    if timeout_s is None:
        timeout_s = DEFAULT_LOCK_TIMEOUT_S
    root = Path(project_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if required:
            raise EvidenceWriteError("io_error") from exc
        yield False
        return
    if fcntl is None:
        if required:
            raise EvidenceWriteError("no_reliable_lock")
        yield False  # never write unlocked — drop the best-effort record
        return
    lock_path = root / lock_name
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    if required:
                        raise EvidenceWriteError("lock_timeout")
                    yield False  # WP1: never fall through to an unlocked write
                    return
                time.sleep(0.02)
        yield True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def append_jsonl_line(project_root: Any, record: dict, *, durable: bool,
                      required: bool, file_name: str = EVENTS_FILE,
                      lock_name: str = EVENTS_LOCK) -> bool:
    """Append ONE JSON line for ``record`` to ``events.jsonl`` under the single
    :func:`events_lock` (WP1 coordinator). One ``write()`` of the full line then a
    ``flush()``; an ``fsync()`` when ``durable``.

    ``required=True`` raises :class:`EvidenceWriteError` on ANY failure (lock
    timeout / no reliable lock / open / write / flush / fsync). ``required=False``
    returns ``True`` on a durable write and ``False`` when the write was skipped
    (lock unavailable) or failed — the best-effort contract, so a caller degrades
    to a warning and never loses committed media.

    ``file_name`` / ``lock_name`` (WP2 §4.4) let a second append-only log —
    07C's ``verifications.jsonl`` (baseline approval AND draft verification) —
    ride the SAME coordinator under its own ``verifications.lock`` sibling, so
    the two writers serialize instead of tearing/truncating each other's bytes.
    A failed write is rolled back to the pre-write size WHILE the exclusive lock
    is still held (the 07C durable-append discipline): because no other process
    can have appended in between, this truncate can never clobber another
    writer's committed line."""
    root = Path(project_root)
    try:
        line = json.dumps(record, ensure_ascii=False) + "\n"
    except (TypeError, ValueError) as exc:
        if required:
            raise EvidenceWriteError("io_error") from exc
        return False
    path = root / file_name
    with events_lock(root, required=required, lock_name=lock_name) as locked:
        if not locked:
            return False  # best-effort under an unavailable lock: drop, never tear
        try:
            with open(path, "a", encoding="utf-8") as f:
                # the true EOF under our exclusive lock — the rollback anchor.
                start = os.fstat(f.fileno()).st_size
                try:
                    f.write(line)
                    f.flush()
                    if durable:
                        os.fsync(f.fileno())
                except OSError:
                    # roll the partial line back — SAFE only because we hold the
                    # exclusive lock, so no other writer's bytes sit past `start`.
                    try:
                        f.flush()
                        os.ftruncate(f.fileno(), start)
                    except OSError:
                        pass
                    raise
        except OSError as exc:
            if required:
                raise EvidenceWriteError("io_error") from exc
            return False
    return True


def append_event(project_root: Path, actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,  # "human" | "ai" | "engine"
        "action": action,
        "detail": detail or {},
    }
    # WP1: the collaboration log rides the SAME single coordinator as the attempt
    # / submission streams — best-effort (never raises out to a caller) and
    # durable (keeps the existing fsync). On a platform without a reliable lock
    # the record is dropped rather than written unlocked.
    append_jsonl_line(Path(project_root), record, durable=True, required=False)


def tail_events(project_root: Path, n: int = 20) -> list[dict[str, Any]]:
    path = Path(project_root) / EVENTS_FILE
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn write must not brick the log
    return events[-n:]


def follow_events(
    project_root: Path,
    *,
    poll_s: float = 0.5,
    from_end: bool = True,
    max_events: int | None = None,
) -> Iterator[dict]:
    """Live ``tail -f`` of events.jsonl — the co-presence channel of §10.

    When a human runs ``manju events --follow`` in a second terminal, this is
    their live window on the AI session working next door — and, symmetrically,
    an agent can follow a human's edits the same way. The collaboration log is
    the handover surface between the two (§3, §10), so watching its tail land
    line by line *is* watching the other party work. Each new record is yielded
    as a ``dict`` the instant its line is complete.

    Polling — not inotify/kqueue/FSEvents — because Manju One is stdlib-only
    (§1-④): a ``time.sleep(poll_s)`` between reads is the same deliberately
    boring pattern the CLI watch loop runs on, and it stays portable across the
    Linux/macOS/Windows + Chinese-path targets with no C dependency to build.

    Behaviour:

    - Waits (polling) for events.jsonl to exist, then seeks to the end when
      ``from_end`` (skip history — "show me what happens next") or to the start
      otherwise (replay the whole log, then keep following).
    - Reads bytes and only yields a record once its line is terminated by a
      newline; a torn/partial trailing write is kept in a buffer and finished
      on a later poll, so a half-flushed append is never parsed early. Reading
      as bytes (not text) also keeps ``tell()`` a true byte offset, which the
      truncation check below depends on when the log holds CJK text.
    - Detects truncation or rotation — the file shrank below our read position —
      and reopens from the start, so ``> events.jsonl`` or a log-rotate does not
      wedge the follower.
    - Skips invalid JSON lines silently, exactly like :func:`tail_events`: a
      torn write must not brick the log or the tail that watches it.
    - Stops after ``max_events`` yields when given — this bounds ``list()`` in
      tests and any finite CLI consumer; ``None`` follows forever.
    - Never raises on a transient :class:`OSError` (a mid-rotation ``stat``, a
      brief unreadable window): it just retries on the next poll.
    """
    path = Path(project_root) / EVENTS_FILE
    if max_events is not None and max_events <= 0:
        return

    seek_end = from_end
    yielded = 0
    f = None
    pos = 0
    buf = b""
    try:
        while True:
            if f is None:
                if not path.exists():
                    time.sleep(poll_s)
                    continue
                try:
                    f = open(path, "rb")
                except OSError:
                    f = None
                    time.sleep(poll_s)
                    continue
                if seek_end:
                    f.seek(0, os.SEEK_END)
                    seek_end = False  # a reopen (truncation/rotation) replays from 0
                pos = f.tell()
                buf = b""

            # Truncation / rotation: the file is now shorter than where we are.
            try:
                size = path.stat().st_size
            except OSError:
                time.sleep(poll_s)
                continue
            if size < pos:
                try:
                    f.close()
                except OSError:
                    pass
                f = None
                buf = b""
                continue  # reopen immediately, reading from the start

            try:
                f.seek(pos)  # also clears any buffered EOF so appends are seen
                chunk = f.read()
            except OSError:
                time.sleep(poll_s)
                continue

            if not chunk:
                time.sleep(poll_s)
                continue

            buf += chunk
            pos = f.tell()
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue  # undecodable bytes → treat like a torn line
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip torn/invalid, like tail_events
                yield record
                yielded += 1
                if max_events is not None and yielded >= max_events:
                    return
    finally:
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
