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
except ImportError:  # Windows: no fcntl — the msvcrt branch below takes over (W1).
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # W1 (§3.4): Windows byte-range lock on the SAME sibling lock file
except ImportError:  # POSIX: fcntl above is the coordinator
    msvcrt = None  # type: ignore[assignment]

EVENTS_FILE = "events.jsonl"
EVENTS_LOCK = "events.lock"

# Gate round 2: per-lock-name in-process thread locks for the Windows branch
# (see events_lock). POSIX never needs them — flock excludes same-process fds.
import threading as _threading

_THREAD_LOCKS: dict[str, "_threading.Lock"] = {}
_THREAD_LOCKS_GUARD = _threading.Lock()


def _name_thread_lock(lock_name: str) -> "_threading.Lock":
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(lock_name)
        if lock is None:
            lock = _THREAD_LOCKS[lock_name] = _threading.Lock()
        return lock

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
    * no ``fcntl`` (Windows): lock byte 0 of the same sibling lock file via
      ``msvcrt.locking`` — same non-blocking poll loop, same deadline, same
      never-write-unlocked policy (W1 §3.4; deliberately reverses DECISIONS
      #20's refuse-on-Windows now that Windows is the primary platform).
    * NEITHER primitive (exotic platform): ``required`` → raise
      :class:`EvidenceWriteError` ``no_reliable_lock``; best-effort → ``yield
      False`` (same skip-the-write drop) — the pre-W1 contract, unchanged.
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
    if fcntl is None and msvcrt is None:
        if required:
            raise EvidenceWriteError("no_reliable_lock")
        yield False  # never write unlocked — drop the best-effort record
        return
    lock_path = root / lock_name
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        # WP1 contract: a best-effort append NEVER raises out — a caller that has
        # already committed media/project state must not be told it failed just
        # because the lock file could not be opened (a read-only .manju, an AV
        # handle, a full disk). required → structured EvidenceWriteError.
        if required:
            raise EvidenceWriteError("io_error") from exc
        yield False
        return
    if fcntl is not None:
        # POSIX: byte-identical to the pre-W1 path (flock; only a WOULDBLOCK
        # retries — any other OSError propagates exactly as before).
        retry_exc: tuple[type[BaseException], ...] = (BlockingIOError,)

        def _try_lock() -> None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def _unlock() -> None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        # Windows: non-blocking byte-0 lock. msvcrt raises plain OSError
        # (EACCES/EDEADLK) when the region is held, so OSError retries here.
        # Gate round 2: the byte lock alone did NOT exclude THREADS of the
        # same process (12 concurrent record_verdicts threads → 6 surviving
        # lines on the real host; POSIX flock excludes same-process fds, the
        # CRT lock demonstrably did not) — so the Windows branch pairs the
        # byte lock (cross-PROCESS) with a per-lock-name threading.Lock
        # (cross-THREAD). The thread lock rides the same non-blocking poll
        # loop, so timeout semantics are unchanged.
        retry_exc = (OSError,)
        _thread_lock = _name_thread_lock(lock_name)

        def _try_lock() -> None:
            if not _thread_lock.acquire(blocking=False):
                raise OSError(13, "thread lock held (same-process contention)")
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                _thread_lock.release()
                raise

        def _unlock() -> None:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                _thread_lock.release()

    locked = False
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                _try_lock()
                locked = True
                break
            except retry_exc:
                if time.monotonic() >= deadline:
                    if required:
                        raise EvidenceWriteError("lock_timeout")
                    yield False  # WP1: never fall through to an unlocked write
                    return
                time.sleep(0.02)
            except OSError as exc:
                # An UNEXPECTED lock error (not the WOULDBLOCK/held retry above)
                # must obey the same never-raise contract as the open() gate —
                # else a best-effort append could still throw out of the lock
                # acquisition. (On Windows retry_exc already covers OSError.)
                if required:
                    raise EvidenceWriteError("io_error") from exc
                yield False
                return
        yield True
    finally:
        if locked:
            try:
                _unlock()
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass


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


def humanize_age(ts: str, now: datetime | None = None) -> str:
    """A record's age as the ONE Chinese phrase status/doctor print (continuity
    wave): 刚刚 / N 分钟前 / N 小时前 / N 天前. The anchor for an owner coming
    back after days — a raw ISO timestamp answers "when" only after mental
    math; this answers it at a glance. Unparseable/未来 timestamps degrade to
    "" so a hand-edited log line can never crash a status render."""
    try:
        then = datetime.fromisoformat(ts)
        if then.tzinfo is None:  # naive → assume UTC (append_event writes UTC)
            then = then.replace(tzinfo=timezone.utc)
        ref = now if now is not None else datetime.now(timezone.utc)
        secs = (ref - then).total_seconds()
    except (TypeError, ValueError):
        return ""
    if secs < 0:
        return ""
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    return f"{int(secs // 86400)} 天前"


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


# Backward-tail read (audit Tier-1 #1). ``tail_events`` used to parse the ENTIRE
# unbounded events.jsonl only to return its last ``n`` records — 195ms at 9.8MB,
# vs 0.095ms for a backward read (2042x). It now seeks from EOF and reads
# fixed-size blocks LEFTWARD, so the common small-``n`` callers (status n=5,
# cockpit n=8, board n=10, gui-state n=15) touch a block or two regardless of how
# large the log has grown. The return value is byte-for-byte what the old
# full-parse reader produced — see :func:`_collect_backward` for the contract.
_TAIL_BLOCK_SIZE = 65536  # 64 KiB — the leftward read granularity.


def _decode_and_parse(raw: bytes) -> dict[str, Any] | None:
    r"""Turn one raw line — the bytes BETWEEN two ``\n`` bytes (newline excluded) —
    into a record, or ``None`` when the line contributes nothing, matching the old
    reader's skip rules line-for-line:

    * empty / all-whitespace line → ``None`` (old: ``if not line: continue``);
    * invalid JSON → ``None`` (old: ``except json.JSONDecodeError: continue`` —
      "a torn write must not brick the log");
    * undecodable UTF-8 → ``None``. This is the ONE deliberate behavioural change,
      documented here. The old reader opened the log in TEXT mode with the default
      strict codec, so a genuinely undecodable byte ANYWHERE in the file made it
      *raise* ``UnicodeDecodeError`` (it read the whole file, so any bad byte
      aborted the tail). A well-formed events.jsonl can never hold one — every
      line is ``json.dumps(..., ensure_ascii=False)`` output, always valid UTF-8 —
      so the old reader effectively never reached this path; the only real source
      is a write torn mid-multibyte-char at EOF, and on THAT the old reader bricked
      the entire tail. We instead skip the offending line, exactly as the
      streaming sibling :func:`follow_events` already does ("undecodable bytes →
      treat like a torn line"), so a torn write can never brick the tail. Because
      we only ever hand this function COMPLETE lines and a ``\n`` byte can never
      fall inside a multibyte sequence, a boundary-straddling character is always
      whole before it is decoded — the skip fires only on truly torn bytes.
    """
    try:
        line = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _collect_backward(path: Path, limit: int | None) -> list[dict[str, Any]]:
    r"""Return the last ``limit`` parseable records from ``path`` in file (forward)
    order — or ALL of them when ``limit`` is ``None`` — by seeking from EOF and
    reading ``_TAIL_BLOCK_SIZE`` blocks LEFTWARD. Byte-exact parity with the old
    full-file reader for every valid-UTF-8 log:

    * We split on the newline BYTES ``\n`` (0x0A) and ``\r`` (0x0D) — the old
      text-mode reader's universal newlines split on ``\n``, ``\r\n`` and a lone
      ``\r``, so we must too. Both bytes are < 0x80 and so can NEVER be part of a
      multibyte UTF-8 sequence (leader/continuation bytes are all >= 0x80); a
      record — even CJK-heavy — that straddles a block boundary is therefore
      reassembled into ONE complete line before it is decoded, and each line is
      decoded on its own, so no boundary can corrupt a character.
    * After prepending a block, the segment BEFORE the first ``\n`` is the only
      one whose left delimiter has not been read yet; it becomes the ``carry``
      prepended to the next (further-left) block. Every other segment is a
      complete line. When the block starts at byte 0 the leading segment is ALSO
      complete — it is the file's first line, so a first line with no preceding
      newline still parses.
    * Lines are visited newest-first, so we stop the instant we have ``limit`` of
      them: the work is bounded by ``min(records-requested, whole-file)``, never
      more than one full pass — the ``carry`` is at most one line long, so a huge
      ``n`` degrades to a single forward-equivalent read with no quadratic
      re-slicing.
    """
    out: list[dict[str, Any]] = []
    carry = b""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0:
            read_size = min(_TAIL_BLOCK_SIZE, pos)
            pos -= read_size
            f.seek(pos)
            data = f.read(read_size) + carry
            # Split on BOTH newline bytes so a lone ``\r`` (old-Mac / hand-edited /
            # externally-written log) is a line break too — the old TEXT-mode
            # reader used UNIVERSAL NEWLINES (``\n``, ``\r\n`` and a bare ``\r``).
            # Normalising ``\r`` → ``\n`` first collapses ``\r\n`` to one empty
            # segment (skipped as blank), matching universal newlines byte-for-
            # byte, including a trailing ``\r`` and a ``\r\n`` straddling a block
            # boundary. We do NOT use ``splitlines()`` — it ALSO breaks on
            # U+2028/U+2029/U+0085, which are legal inside our ``ensure_ascii=False``
            # JSON string values and must never tear a record.
            segments = data.replace(b"\r", b"\n").split(b"\n")
            if pos == 0:
                # reached BOF: the leading segment is the file's first line.
                complete = segments
                carry = b""
            else:
                carry = segments[0]
                complete = segments[1:]
            for raw in reversed(complete):
                rec = _decode_and_parse(raw)
                if rec is not None:
                    out.append(rec)
                    if limit is not None and len(out) >= limit:
                        out.reverse()
                        return out
    out.reverse()
    return out


def tail_events(project_root: Path, n: int = 20) -> list[dict[str, Any]]:
    r"""The last ``n`` records of events.jsonl, oldest-first — the handover tail.

    Reads BACKWARD from EOF (see :func:`_collect_backward`) instead of parsing the
    whole unbounded log, so a five-line ``manju status`` tail no longer pays for a
    ten-megabyte file. The return value is byte-for-byte what the old full-parse
    reader returned: same records, same order, torn/empty lines skipped silently,
    a missing file → ``[]`` (the one documented difference is undecodable bytes —
    see :func:`_decode_and_parse`).

    ``n`` keeps its exact historical slice semantics — the result is
    ``all_records[-n:]`` — including the quirks: ``n == 0`` returns EVERY record
    (``-0`` is ``0``) and a negative ``n`` drops the first ``|n|``. Those two need
    the whole list, so they fall back to a full read; every ``n > 0`` caller gets
    the bounded backward scan.
    """
    path = Path(project_root) / EVENTS_FILE
    if not path.exists():
        return []
    if n <= 0:
        # events[-n:] for n <= 0 is a whole-list slice (n == 0 → ALL via -0 == 0;
        # n < 0 → all-but-first-|n|): read everything, then reproduce it exactly.
        return _collect_backward(path, None)[-n:]
    return _collect_backward(path, n)


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
