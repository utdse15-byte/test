"""Tests for manju.core.events.follow_events — live ``tail -f`` of the log (§10).

follow_events is the engine behind ``manju events --follow``: a second terminal
that is the human's live window on an AI session (or vice versa). These tests
drive it fast (``poll_s=0.02``) with interleaved writes / a follower thread, and
bound every run with ``max_events`` so nothing can block the suite — the daemon
follower threads are joined with a timeout and asserted dead.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from manju.core.events import EVENTS_FILE, append_event, follow_events


def _path(root: Path) -> Path:
    return root / EVENTS_FILE


def _wait_until(pred, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _follow_into(root: Path, count: int, **kw) -> tuple[list[dict], threading.Thread]:
    """Run follow_events in a daemon thread, collecting up to ``count`` events."""
    out: list[dict] = []

    def _run() -> None:
        for e in follow_events(root, poll_s=0.02, max_events=count, **kw):
            out.append(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return out, t


def test_max_events_bounds_and_terminates_list(tmp_path: Path):
    # from_end=False replays from the start; max_events caps the yields so a
    # plain list() returns instead of following forever.
    for i in range(4):
        append_event(tmp_path, "ai", f"act{i}")
    result = list(follow_events(tmp_path, poll_s=0.02, from_end=False, max_events=2))
    assert [e["action"] for e in result] == ["act0", "act1"]
    assert all(isinstance(e, dict) for e in result)


def test_invalid_json_line_is_skipped(tmp_path: Path):
    append_event(tmp_path, "ai", "good1")
    with open(_path(tmp_path), "a", encoding="utf-8") as f:
        f.write("{not valid json at all\n")  # a garbage line between two good ones
    append_event(tmp_path, "ai", "good2")
    result = list(follow_events(tmp_path, poll_s=0.02, from_end=False, max_events=2))
    assert [e["action"] for e in result] == ["good1", "good2"]


def test_from_end_skips_history_yields_only_new(tmp_path: Path):
    append_event(tmp_path, "human", "history1")
    append_event(tmp_path, "human", "history2")

    out, t = _follow_into(tmp_path, count=2, from_end=True)
    # let the follower open the file and seek to EOF before anything new lands
    time.sleep(0.2)
    append_event(tmp_path, "ai", "live1", {"shot": "S001"})
    append_event(tmp_path, "ai", "live2")

    t.join(timeout=3.0)
    assert not t.is_alive()
    assert [e["action"] for e in out] == ["live1", "live2"]  # history skipped
    assert all(isinstance(e, dict) for e in out)
    assert out[0]["actor"] == "ai"
    assert out[0]["detail"] == {"shot": "S001"}


def test_torn_trailing_line_yields_once_when_completed(tmp_path: Path):
    _path(tmp_path).touch()
    out, t = _follow_into(tmp_path, count=1, from_end=False)

    # write half a record WITHOUT a trailing newline
    with open(_path(tmp_path), "a", encoding="utf-8") as f:
        f.write('{"ts": "2026-07-06T00:00:00+00:00", "actor": "ai", "action": "torn"')
    time.sleep(0.12)  # several poll cycles
    assert out == []  # a partial line must be buffered, never yielded early

    # now complete the line
    with open(_path(tmp_path), "a", encoding="utf-8") as f:
        f.write(', "detail": {}}\n')

    t.join(timeout=3.0)
    assert not t.is_alive()
    assert len(out) == 1  # exactly once, with the full record
    assert out[0]["action"] == "torn"
    assert out[0]["actor"] == "ai"


def test_truncation_recovers_and_yields_subsequent_events(tmp_path: Path):
    append_event(tmp_path, "human", "old1")
    append_event(tmp_path, "human", "old2")

    out, t = _follow_into(tmp_path, count=3, from_end=False)
    assert _wait_until(lambda: len(out) >= 2)  # the history has been drained

    # rotate/truncate in place: open("w") truncates to 0, so the file shrinks
    # below the follower's read position and it must reopen from the start.
    with open(_path(tmp_path), "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "t", "actor": "ai", "action": "fresh", "detail": {}}) + "\n")

    t.join(timeout=3.0)
    assert not t.is_alive()
    assert [e["action"] for e in out] == ["old1", "old2", "fresh"]
