"""Parity + boundedness tests for the backward-tail rewrite of
``manju.core.events.tail_events`` (audit Tier-1 #1).

WHY these tests exist
---------------------
``tail_events`` used to parse the ENTIRE unbounded events.jsonl to return only
its last ``n`` records (195ms at 9.8MB). The rewrite seeks from EOF and reads
fixed-size blocks LEFTWARD. These tests prove the rewrite (a) returns byte-for-
byte what the old full-parse reader returned, and (b) actually reads a bounded
number of bytes for small ``n`` on a big file.

PIN ORDER (born-green vs red-first)
-----------------------------------
Every *parity* test compares ``tail_events`` to ``_reference_tail`` — a self-
contained re-implementation of the HISTORICAL algorithm (full forward parse,
text-mode strict decode, skip ``json.JSONDecodeError``, slice ``[-n:]``). Run
against HEAD (the old ``tail_events``) these parity tests are BORN GREEN: they
pin the old behaviour. The rewrite must keep them green — that IS the parity
proof.

Two tests are deliberately RED against HEAD and green only after the swap:
  * ``test_bounded_read_touches_at_most_one_block`` — HEAD reads the whole file;
  * ``test_undecodable_bytes_skipped_not_raised`` — HEAD *raises*
    ``UnicodeDecodeError`` on an undecodable byte (verified); the rewrite skips
    the line exactly as the streaming sibling ``follow_events`` already does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.core import events
from manju.core.events import EVENTS_FILE, tail_events


# ---------------------------------------------------------------------------
# Oracle + builders
# ---------------------------------------------------------------------------

def _reference_tail(path: Path, n: int) -> list[dict]:
    """The EXACT historical algorithm — the parity oracle. A straight full
    forward parse in text mode with the default strict codec, skipping empty and
    JSON-invalid lines, then ``events[-n:]``. Only ever run here on valid-UTF-8
    logs, where it equals what HEAD's ``tail_events`` returned."""
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-n:]


def _log_path(root: Path) -> Path:
    return root / EVENTS_FILE


def _build_log(path: Path, n_records: int, *, cjk: bool = False) -> list[dict]:
    """Write ``n_records`` well-formed event lines (``json.dumps`` + ``\\n``),
    returning the records so a test can build its own expectation. With
    ``cjk=True`` each line carries a multibyte payload."""
    recs: list[dict] = []
    lines: list[str] = []
    for i in range(n_records):
        rec: dict = {
            "ts": f"2026-07-13T12:00:{i % 60:02d}",
            "actor": "ai",
            "action": f"act{i}",
            "seq": i,
        }
        if cjk:
            # CJK payload of varying length so char boundaries land all over the
            # place relative to any fixed block size.
            rec["detail"] = "雨夜便利店的第%d次心跳,霓虹灯在水洼里融化。" % i + "好" * (i % 7)
        recs.append(rec)
        lines.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return recs


# ---------------------------------------------------------------------------
# 1. Parity on a synthetic 50k-line log for the spectrum of n
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [0, 1, 5, 100, 100_000])
def test_parity_synthetic_50k(tmp_path: Path, n: int):
    path = _log_path(tmp_path)
    _build_log(path, 50_000)
    assert tail_events(tmp_path, n) == _reference_tail(path, n)


def test_parity_50k_matches_expected_slice(tmp_path: Path):
    # A second, oracle-independent pin: the returned records are exactly the tail
    # slice of the records we wrote, oldest-first.
    path = _log_path(tmp_path)
    recs = _build_log(path, 50_000)
    assert tail_events(tmp_path, 5) == recs[-5:]
    assert tail_events(tmp_path, 1) == recs[-1:]
    assert tail_events(tmp_path, 100_000) == recs           # fewer than n → all
    assert tail_events(tmp_path, 0) == recs                 # -0 == 0 → ALL


# ---------------------------------------------------------------------------
# 2. Torn / empty / edge structural cases  (all parity vs the oracle)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 5, 100])
def test_parity_torn_last_line(tmp_path: Path, n: int):
    path = _log_path(tmp_path)
    recs = _build_log(path, 20)
    # a write torn at a CHARACTER boundary: valid UTF-8, invalid JSON, no newline.
    with open(path, "ab") as f:
        f.write(b'{"ts": "2026-07-13T12:00:59", "actor": "ai", "acti')
    got = tail_events(tmp_path, n)
    assert got == _reference_tail(path, n)
    # the torn fragment never appears as a record.
    assert all("acti" not in r.get("action", "") for r in got)
    assert got == recs[-n:]


@pytest.mark.parametrize("n", [1, 2, 5, 100])
def test_parity_torn_middle_line(tmp_path: Path, n: int):
    path = _log_path(tmp_path)
    good_before = _build_log(path, 10)
    # splice a garbage line into the middle, then append more good lines.
    blob = path.read_bytes()
    blob += b'{ this is not json at all \n'
    tail_recs = []
    for i in range(10, 25):
        rec = {"ts": f"2026-07-13T12:01:{i % 60:02d}", "actor": "ai",
               "action": f"act{i}", "seq": i}
        tail_recs.append(rec)
        blob += (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(blob)
    all_good = good_before + tail_recs
    assert tail_events(tmp_path, n) == _reference_tail(path, n)
    assert tail_events(tmp_path, n) == all_good[-n:]


@pytest.mark.parametrize("n", [1, 3, 100])
def test_parity_empty_lines(tmp_path: Path, n: int):
    path = _log_path(tmp_path)
    # blank and whitespace-only lines interleaved with good records.
    parts = [
        b"",
        json.dumps({"action": "a0", "seq": 0}).encode(),
        b"",
        b"   ",
        json.dumps({"action": "a1", "seq": 1}).encode(),
        b"\t",
        json.dumps({"action": "a2", "seq": 2}).encode(),
        b"",
    ]
    path.write_bytes(b"\n".join(parts) + b"\n")
    assert tail_events(tmp_path, n) == _reference_tail(path, n)


def test_missing_file_returns_empty(tmp_path: Path):
    # no events.jsonl at all.
    assert tail_events(tmp_path, 5) == []
    assert tail_events(tmp_path, 0) == []
    assert tail_events(tmp_path, 100_000) == []


def test_empty_file_returns_empty(tmp_path: Path):
    path = _log_path(tmp_path)
    path.write_bytes(b"")
    assert tail_events(tmp_path, 5) == []
    assert tail_events(tmp_path, 0) == []


@pytest.mark.parametrize("trailing_newline", [True, False])
def test_single_line_file(tmp_path: Path, trailing_newline: bool):
    path = _log_path(tmp_path)
    rec = {"ts": "2026-07-13T12:00:00", "actor": "ai", "action": "only", "seq": 0}
    blob = json.dumps(rec, ensure_ascii=False).encode("utf-8")
    if trailing_newline:
        blob += b"\n"
    path.write_bytes(blob)
    for n in (1, 5, 100_000, 0):
        assert tail_events(tmp_path, n) == _reference_tail(path, n)
    assert tail_events(tmp_path, 1) == [rec]


def test_n_larger_than_file(tmp_path: Path):
    path = _log_path(tmp_path)
    recs = _build_log(path, 3)
    got = tail_events(tmp_path, 1000)
    assert got == recs
    assert len(got) == 3
    assert got == _reference_tail(path, 1000)


def test_partial_first_line_parses(tmp_path: Path, monkeypatch):
    # First line is a complete record but the file has NO leading newline. With a
    # tiny block the reader reaches BYTE 0 mid-way through that first line; it must
    # still be parsed (old text-mode read it as line 1 too).
    monkeypatch.setattr(events, "_TAIL_BLOCK_SIZE", 4, raising=False)
    path = _log_path(tmp_path)
    recs = _build_log(path, 6)
    assert tail_events(tmp_path, 100) == recs
    assert tail_events(tmp_path, 100)[0] == recs[0]      # the first line survived


def test_first_line_torn_is_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(events, "_TAIL_BLOCK_SIZE", 8, raising=False)
    path = _log_path(tmp_path)
    tail_recs = _build_log(path, 5)
    # prepend a garbage first line.
    blob = b'{ broken first line no newline separator has a real one after }\n' + path.read_bytes()
    path.write_bytes(blob)
    got = tail_events(tmp_path, 100)
    assert got == tail_recs
    assert got == _reference_tail(path, 100)


# ---------------------------------------------------------------------------
# 3. Multibyte UTF-8 straddling block boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("block", [1, 2, 3, 4, 7, 8, 16, 64])
@pytest.mark.parametrize("n", [1, 3, 50, 10_000])
def test_parity_cjk_tiny_blocks(tmp_path: Path, monkeypatch, block: int, n: int):
    # Force multibyte chars (3-byte CJK) to split across blocks CONSTANTLY by
    # shrinking the block far below a char/line. Parity must still hold — proof
    # that a boundary-straddling char is reassembled into one complete line before
    # it is ever decoded.
    monkeypatch.setattr(events, "_TAIL_BLOCK_SIZE", block, raising=False)
    path = _log_path(tmp_path)
    _build_log(path, 200, cjk=True)
    assert tail_events(tmp_path, n) == _reference_tail(path, n)


@pytest.mark.parametrize("n", [1, 5, 1500, 100_000])
def test_parity_cjk_real_block_multi_block_file(tmp_path: Path, n: int):
    # A genuinely >2-block (>130KB) CJK log at the REAL 64KiB block size, so the
    # true boundary lands inside multibyte content.
    path = _log_path(tmp_path)
    _build_log(path, 3_000, cjk=True)
    assert path.stat().st_size > 2 * getattr(events, "_TAIL_BLOCK_SIZE", 65536)
    got = tail_events(tmp_path, n)
    assert got == _reference_tail(path, n)
    # spot-check the payload decoded intact (no U+FFFD, exact CJK).
    assert all("雨夜便利店" in str(r.get("detail", "")) for r in got)


def test_cjk_char_split_exactly_on_block_boundary(tmp_path: Path, monkeypatch):
    # Construct the file so the read boundary (size - BLOCK) falls INSIDE a 3-byte
    # char of a specific record, then assert that record round-trips exactly.
    block = 32
    monkeypatch.setattr(events, "_TAIL_BLOCK_SIZE", block, raising=False)
    path = _log_path(tmp_path)
    # target line first, then filler to control total size; we assert parity which
    # implies the straddling target decoded correctly.
    recs = []
    lines = []
    for i in range(40):
        rec = {"a": i, "t": "好" * (i % 5 + 1)}
        recs.append(rec)
        lines.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for n in (1, 4, 40, 100):
        assert tail_events(tmp_path, n) == _reference_tail(path, n)


# ---------------------------------------------------------------------------
# 3b. Universal-newline parity + splitlines-immunity
#     The old reader opened TEXT mode, whose universal-newline translation splits
#     on \n, \r\n AND a lone \r. A byte reader that splits on \n only would drop
#     valid events separated by a lone \r (hand-edited / externally-written logs
#     can carry one; json.dumps never emits a bare control char but the module's
#     stance is maximal recovery). These pin exact parity. They must NOT be
#     "fixed" with str.splitlines(), which ALSO breaks on U+2028/U+2029/U+0085 —
#     legal characters inside our ensure_ascii=False JSON string values.
# ---------------------------------------------------------------------------

def _abc_bytes(sep: bytes) -> tuple[bytes, list[dict]]:
    a = {"k": "A", "seq": 0}
    b = {"k": "B", "seq": 1}
    c = {"k": "C", "seq": 2}
    blob = (json.dumps(a).encode() + sep + json.dumps(b).encode()
            + b"\n" + json.dumps(c).encode() + b"\n")
    return blob, [a, b, c]


def test_parity_lone_cr_separator(tmp_path: Path):
    # A\rB\nC\n  — the exact divergence the orchestrator's harness flagged.
    path = _log_path(tmp_path)
    blob, (a, b, c) = _abc_bytes(b"\r")
    path.write_bytes(blob)
    assert tail_events(tmp_path, 3) == [a, b, c]
    assert tail_events(tmp_path, 100) == [a, b, c]
    assert tail_events(tmp_path, 2) == [b, c]     # A dropped; B,C are newest
    assert tail_events(tmp_path, 1) == [c]
    for n in (1, 2, 3, 100):
        assert tail_events(tmp_path, n) == _reference_tail(path, n)


@pytest.mark.parametrize("block", [1, 2, 3, 4, 8, 64])
def test_parity_lone_cr_across_blocks(tmp_path: Path, monkeypatch, block: int):
    monkeypatch.setattr(events, "_TAIL_BLOCK_SIZE", block, raising=False)
    path = _log_path(tmp_path)
    blob, recs = _abc_bytes(b"\r")
    path.write_bytes(blob)
    assert tail_events(tmp_path, 100) == recs
    assert tail_events(tmp_path, 100) == _reference_tail(path, 100)


def test_parity_crlf_separator(tmp_path: Path):
    path = _log_path(tmp_path)
    blob, recs = _abc_bytes(b"\r\n")
    path.write_bytes(blob)
    for n in (1, 2, 3, 100):
        assert tail_events(tmp_path, n) == _reference_tail(path, n)
    assert tail_events(tmp_path, 100) == recs


@pytest.mark.parametrize("block", [1, 2, 3, 4, 8, 64])
def test_parity_crlf_straddling_block_boundary(tmp_path: Path, monkeypatch, block: int):
    # \r and \n of a CRLF pair fall in different blocks at tiny block sizes.
    monkeypatch.setattr(events, "_TAIL_BLOCK_SIZE", block, raising=False)
    path = _log_path(tmp_path)
    lines = [json.dumps({"k": f"L{i}", "seq": i}) for i in range(12)]
    path.write_bytes(b"\r\n".join(l.encode() for l in lines) + b"\r\n")
    for n in (1, 3, 100):
        assert tail_events(tmp_path, n) == _reference_tail(path, n)


def test_parity_cr_as_last_byte(tmp_path: Path):
    # A file whose final byte is a lone \r (universal newlines: line terminator,
    # no phantom trailing record).
    path = _log_path(tmp_path)
    blob = json.dumps({"k": "X", "seq": 0}).encode() + b"\r"
    path.write_bytes(blob)
    assert tail_events(tmp_path, 5) == _reference_tail(path, 5)
    assert tail_events(tmp_path, 5) == [{"k": "X", "seq": 0}]


@pytest.mark.parametrize("uni", ["\u2028", "\u2029", "\u0085"])
def test_unicode_line_separators_do_not_tear_records(tmp_path: Path, uni: str):
    # U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR, U+0085 NEL are LEGAL
    # inside a JSON string emitted with ensure_ascii=False. They must remain part
    # of the one record, never split it (str.splitlines() would — we split bytes).
    path = _log_path(tmp_path)
    rec = {"k": f"first{uni}second", "seq": 0}
    other = {"k": "plain", "seq": 1}
    path.write_bytes(json.dumps(rec, ensure_ascii=False).encode("utf-8") + b"\n"
                     + json.dumps(other, ensure_ascii=False).encode("utf-8") + b"\n")
    got = tail_events(tmp_path, 5)
    assert got == [rec, other]
    assert got[0]["k"] == f"first{uni}second"     # separator preserved verbatim
    assert got == _reference_tail(path, 5)


# ---------------------------------------------------------------------------
# 4. Bounded-read guard  (RED vs HEAD, green vs the rewrite)
# ---------------------------------------------------------------------------

class _ByteCountingFile:
    """Wrap a file object (text OR binary) and tally the bytes it hands out — via
    read()/readline() and via iteration — so a test can observe how much of the
    file a reader actually touched, independent of open mode. Old ``tail_events``
    iterates a text file (``for line in f``); the rewrite calls ``.read()`` on a
    binary file. Both are counted, so the same assertion is RED for the old (whole
    file) and green for the new (a block)."""

    def __init__(self, f, counter: list, binary: bool):
        self._f = f
        self._counter = counter
        self._binary = binary

    def _tally(self, data):
        if data:
            self._counter[0] += len(data) if self._binary else len(data.encode("utf-8"))
        return data

    def read(self, *a, **k):
        return self._tally(self._f.read(*a, **k))

    def readline(self, *a, **k):
        return self._tally(self._f.readline(*a, **k))

    def __iter__(self):
        for line in self._f:
            self._tally(line)
            yield line

    def seek(self, *a, **k):
        return self._f.seek(*a, **k)

    def tell(self):
        return self._f.tell()

    def __enter__(self):
        self._f.__enter__()
        return self

    def __exit__(self, *a):
        return self._f.__exit__(*a)

    def close(self):
        return self._f.close()


def test_bounded_read_touches_at_most_one_block(tmp_path: Path, monkeypatch):
    path = _log_path(tmp_path)
    # >5MB log of ordinary ~80-byte lines.
    recs = _build_log(path, 70_000)
    file_size = path.stat().st_size
    assert file_size >= 5 * 1024 * 1024

    counter = [0]
    real_open = open

    def counting_open(file, mode="r", *a, **k):
        f = real_open(file, mode, *a, **k)
        return _ByteCountingFile(f, counter, "b" in mode)

    monkeypatch.setattr(events, "open", counting_open, raising=False)
    got = tail_events(tmp_path, 5)
    bytes_touched = counter[0]
    monkeypatch.undo()

    # correctness still holds under the spy.
    assert got == recs[-5:]
    # THE GUARD: a 5-record tail must not touch anywhere near the whole file.
    # (HEAD reads every byte → this fails; the rewrite reads one 64KiB block.)
    assert bytes_touched < file_size / 10
    # tighter, implementation-observable bound: a single block suffices here.
    assert bytes_touched <= getattr(events, "_TAIL_BLOCK_SIZE", 65536)


def test_bounded_read_scales_with_n_not_filesize(tmp_path: Path, monkeypatch):
    # Same big file, a mid-size n=1000: still bounded well under the file, and
    # strictly more than the n=5 read — the work tracks n, not file size.
    path = _log_path(tmp_path)
    _build_log(path, 55_000)
    file_size = path.stat().st_size

    def measure(n: int) -> int:
        counter = [0]
        real_open = open

        def counting_open(file, mode="r", *a, **k):
            return _ByteCountingFile(real_open(file, mode, *a, **k), counter, "b" in mode)

        monkeypatch.setattr(events, "open", counting_open, raising=False)
        tail_events(tmp_path, n)
        monkeypatch.undo()
        return counter[0]

    read_1000 = measure(1000)
    assert read_1000 < file_size / 3


# ---------------------------------------------------------------------------
# 5. Undecodable bytes  (RED vs HEAD — it RAISED; green vs the rewrite — it skips)
# ---------------------------------------------------------------------------

def test_undecodable_bytes_skipped_not_raised(tmp_path: Path):
    # DOCUMENTED behavioural choice. HEAD opened the log in text mode with the
    # strict codec, so a raw invalid-UTF-8 byte anywhere made it RAISE
    # UnicodeDecodeError (verified empirically). A well-formed events.jsonl can
    # never contain one (every line is json.dumps output → valid UTF-8); the only
    # real source is a write torn mid-multibyte-char at EOF, on which HEAD bricked
    # the whole tail. The rewrite skips the offending line like follow_events, so a
    # torn write can never brick the tail.
    path = _log_path(tmp_path)
    good = [
        {"action": "before", "seq": 0},
        {"action": "after", "seq": 1},
    ]
    blob = json.dumps(good[0]).encode() + b"\n"
    blob += b'{"action": "\xff\xfe corrupt torn mid-char", "seq": 99}\n'  # invalid UTF-8
    blob += json.dumps(good[1]).encode() + b"\n"
    path.write_bytes(blob)

    # Must NOT raise, and must return the surrounding valid records.
    got = tail_events(tmp_path, 10)
    assert got == good
    assert all(r.get("seq") != 99 for r in got)


def test_undecodable_torn_final_line_skipped(tmp_path: Path):
    # The realistic case (RED vs HEAD, green vs the rewrite): a final append torn
    # in the MIDDLE of a 3-byte CJK char. The tail ends with an incomplete UTF-8
    # sequence and no newline. HEAD raised; the rewrite skips it.
    path = _log_path(tmp_path)
    recs = _build_log(path, 8)
    prefix = '{"action": "torn", "t": "'.encode("utf-8")
    partial_char = "好".encode("utf-8")[:2]   # 2 of 3 bytes → invalid at EOF
    with open(path, "ab") as f:
        f.write(prefix + partial_char)         # no closing bytes, no newline
    got = tail_events(tmp_path, 20)
    assert got == recs                     # the torn fragment contributes nothing
    assert all(r.get("action") != "torn" for r in got)
