"""events.jsonl — the collaboration log (§3, §10).

Who did what, when. This is the handover surface between human and AI:
`manju status` + the tail of this log gets either party into context in 30s.
Append-only, one JSON object per line, UTF-8.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENTS_FILE = "events.jsonl"


def append_event(project_root: Path, actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,  # "human" | "ai" | "engine"
        "action": action,
        "detail": detail or {},
    }
    path = Path(project_root) / EVENTS_FILE
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


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
