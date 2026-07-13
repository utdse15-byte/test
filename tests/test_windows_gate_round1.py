"""Windows gate fix round 1 — pins for the first real windows-ci.yml verdict.

Run #1 of the hard gate (76F/4016P/35E) exposed four product-bug classes; each
fix here is pinned by a test that runs on ANY platform (stub-driven where the
OS primitive is Windows-only) plus, for the filter-escaping fix, a REAL ffmpeg
behavior test that reproduces the exact two-level parsing failure on Linux via
a colon-carrying path (colons are legal in POSIX filenames — the identical
parse ambiguity, no Windows host needed).

Classes: (1) ``ass=``/``textfile=`` filter paths broke on drive-letter colons
(every subtitled/carded render on Windows failed, the ~60-test cascade);
(2) fcntl-only advisory locks (recents/library/failures) silently no-opped —
lost updates under real concurrency; (3) the agent_review verdict append had
NO lock at all and tore; (4) POSIX shlex ate the backslashes out of local_cmd
command templates (exit 127 for every real Windows path).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.media.render import _escape_filter_path


# --------------------------------------------------------------------------
# (1) two-level filtergraph escaping
# --------------------------------------------------------------------------


def test_escape_filter_path_posix_plain_is_byte_identical():
    """Every colon-free POSIX path (all Linux tmp paths) must come back
    UNCHANGED — command lines, logs and any downstream diffing never shift."""
    for p in ("/tmp/x/f.ass", "/tmp/雨夜 便利店/captions.ass", "relative/f.txt"):
        assert _escape_filter_path(p) == p


def test_escape_filter_path_windows_drive_two_level():
    """Drive-letter paths: forward slashes, option-level ``\\:``, graph-level
    quotes — the exact form the ass/drawtext option parsers need."""
    assert (_escape_filter_path("C:\\Users\\晓 明\\f.ass")
            == "'C\\:/Users/晓 明/f.ass'")
    assert (_escape_filter_path("D:\\a\\test\\captions.ass")
            == "'D\\:/a/test/captions.ass'")


def test_escape_filter_path_colon_and_quote():
    assert _escape_filter_path("/tmp/a:b/f.ass") == "'/tmp/a\\:b/f.ass'"
    assert _escape_filter_path("/tmp/o'brien/f.ass") == "'/tmp/o'\\''brien/f.ass'"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_ass_filter_accepts_colon_path_end_to_end(tmp_path):
    """The REAL parser, the real bug shape: an ass= path containing a colon
    must burn (exit 0). At the pre-fix escaping this failed with the exact
    'Unable to parse original_size' the Windows gate reported — the filter's
    own option parser split the unquoted value at the colon."""
    weird = tmp_path / "a:b"
    weird.mkdir()
    ass = weird / "c.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 192\nPlayResY: 108\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,16,&H00FFFFFF,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,hi\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-t", "0.2", "-i", "color=c=black:s=192x108:r=24",
        "-vf", f"ass={_escape_filter_path(ass)}",
        "-frames:v", "2", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and out.stat().st_size > 0


# --------------------------------------------------------------------------
# (2) the three fcntl-only advisory locks gain the msvcrt branch
# --------------------------------------------------------------------------


class _StubMsvcrt:
    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self):
        self.calls: list[int] = []

    def locking(self, fd, mode, nbytes):
        self.calls.append(mode)


def test_recents_lock_uses_msvcrt_when_fcntl_missing(tmp_path, monkeypatch):
    import manju.core.recents as recents

    stub = _StubMsvcrt()
    monkeypatch.setattr(recents, "fcntl", None)
    monkeypatch.setattr(recents, "msvcrt", stub, raising=False)
    monkeypatch.setenv("MANJU_RECENTS", str(tmp_path / "recents.json"))
    with recents._lock(recents.recents_path()):
        pass
    assert stub.LK_NBLCK in stub.calls and stub.LK_UNLCK in stub.calls


def test_library_lock_uses_msvcrt_when_fcntl_missing(tmp_path, monkeypatch):
    import manju.core.library as library

    stub = _StubMsvcrt()
    monkeypatch.setattr(library, "fcntl", None)
    monkeypatch.setattr(library, "msvcrt", stub, raising=False)
    with library._index_lock(tmp_path):
        pass
    assert stub.LK_NBLCK in stub.calls and stub.LK_UNLCK in stub.calls


def test_failures_lock_uses_msvcrt_when_fcntl_missing(tmp_path, monkeypatch):
    import manju.core.failures as failures

    stub = _StubMsvcrt()
    monkeypatch.setattr(failures, "fcntl", None)
    monkeypatch.setattr(failures, "msvcrt", stub, raising=False)
    with failures._ledger_lock(tmp_path):
        pass
    assert stub.LK_NBLCK in stub.calls and stub.LK_UNLCK in stub.calls


def test_locks_degrade_unlocked_only_when_no_primitive_exists(tmp_path, monkeypatch):
    """Neither fcntl nor msvcrt: the historical degrade (proceed unlocked)
    stays byte-identical — these are best-effort convenience locks, never
    worth failing a real command over."""
    import manju.core.failures as failures
    import manju.core.library as library
    import manju.core.recents as recents

    for mod, lock in (
        (recents, lambda: recents._lock(tmp_path / "r.json")),
        (library, lambda: library._index_lock(tmp_path)),
        (failures, lambda: failures._ledger_lock(tmp_path)),
    ):
        monkeypatch.setattr(mod, "fcntl", None)
        monkeypatch.setattr(mod, "msvcrt", None, raising=False)
        with lock():
            pass  # must not raise, must not hang


# --------------------------------------------------------------------------
# (3) the agent_review verdict append is serialized by THE coordinator
# --------------------------------------------------------------------------


def test_record_verdicts_append_is_coordinated_grep_pin():
    """Source pin (house style): the verdict append must sit under THE events
    coordinator with its own lock name — an unlocked ``open(path, "a")`` here
    is exactly what tore 12 concurrent verdicts down to 6 on the gate's first
    run. The lock lives under .manju/ (disposable, never packed)."""
    import manju.qc.agent_review as agent_review

    src = Path(agent_review.__file__).read_text(encoding="utf-8")
    assert 'events_lock(project.root, lock_name=".manju/agent_review.lock")' in src
    append_at = src.index('open(path, "a", encoding="utf-8")')
    lock_at = src.index('lock_name=".manju/agent_review.lock"')
    assert lock_at < append_at, "the coordinator must wrap the append, not follow it"


# --------------------------------------------------------------------------
# (4) local_cmd command templates keep their backslashes on Windows
# --------------------------------------------------------------------------


def test_split_command_windows_keeps_backslashes(monkeypatch):
    import manju.providers.local_cmd as local_cmd

    monkeypatch.setattr(local_cmd, "_IS_WINDOWS", True)
    words = local_cmd._split_command(
        'sh C:\\Users\\晓明\\gen.sh --prompt "two words" --out {out}')
    assert words[1] == "C:\\Users\\晓明\\gen.sh"
    assert "two words" in words, words
    assert words[-1] == "{out}"


def test_split_command_posix_unchanged():
    import manju.providers.local_cmd as local_cmd

    if local_cmd._IS_WINDOWS:
        pytest.skip("POSIX byte-identity pin")
    assert (local_cmd._split_command('sh /tmp/gen.sh --prompt "two words" --out {out}')
            == ["sh", "/tmp/gen.sh", "--prompt", "two words", "--out", "{out}"])
