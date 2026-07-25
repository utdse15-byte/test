"""The Windows gate's own guards, checked from Linux.

`.github/workflows/windows-ci.yml` cannot RUN here, but it is a text file, and
the properties it asserts about itself are checkable anywhere. Auditing it by
hand found a hole in its anti-rot guard: the FFmpeg pin assert tested

    $v -notmatch "ffmpeg version 6\\.1"

which is a PREFIX match, so an "ffmpeg version 6.10" build would satisfy a
guard whose entire job — and whose own error message — is to reject anything
that is not 6.1.x. The pattern is anchored with `(\\.|\\s|$)`; the cases below
are the ones that were executed through a real PowerShell 7.4 to confirm the
`-notmatch` semantics, then frozen here so a future edit cannot loosen it back
to a prefix.

Also pinned: the gate keeps no continue-on-error (CLAUDE.md calls it the HARD
gate), still runs the FULL suite, still installs every extra, and still asserts
FFmpeg is present — that assert is what stops the suite going green by silently
skipping the ~63 ffmpeg-gated files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "windows-ci.yml"
TEXT = WF.read_text(encoding="utf-8")
# The header prose says "no continue-on-error anywhere" — searching raw text
# for a token that also appears in a comment ABOUT that token is how a pin
# passes while meaning nothing. Steps only.
STEPS = "\n".join(l for l in TEXT.splitlines() if not l.lstrip().startswith("#"))


def _pin_pattern() -> str:
    """The regex literal the gate feeds to -notmatch."""
    m = re.search(r'-notmatch\s+"([^"]+)"', TEXT)
    assert m, "the FFmpeg pin assert is gone from the gate"
    return m.group(1)


def _matches(version_line: str, pattern: str) -> bool:
    """.NET and Python agree on this subset (literal text, \\., \\s, $, |)."""
    return re.search(pattern, version_line) is not None


# ------------------------------------------------------------- the pin assert


@pytest.mark.parametrize("version_line, accepted", [
    ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023", True),
    ("ffmpeg version 6.1 Copyright (c) 2000-2023", True),
    ("ffmpeg version 6.1", True),
    ("ffmpeg version 6.1.2-static", True),
    # ...and everything that is NOT the pinned 6.1.x:
    ("ffmpeg version 6.10 Copyright", False),      # the prefix-match hole
    ("ffmpeg version 6.11.0", False),
    ("ffmpeg version 6.0.1 Copyright", False),
    ("ffmpeg version 7.0.2-static https://johnvansickle.com", False),
    ("ffmpeg version n7.1.5-10-g2aefd64d48-20260724", False),
    ("ffmpeg version N-125752-g2f209337fc-20260724", False),
])
def test_the_ffmpeg_pin_accepts_exactly_6_1_x(version_line: str, accepted: bool) -> None:
    assert _matches(version_line, _pin_pattern()) is accepted, version_line


def test_the_pin_is_not_a_bare_prefix() -> None:
    """Guard the guard: state the defect directly, so re-loosening is loud."""
    assert _pin_pattern() != r"ffmpeg version 6\.1", (
        "the pin is a prefix match again — 6.10 would pass as 6.1.x")


def test_the_pinned_version_and_the_assert_agree() -> None:
    """A pin bumped in the choco step but not in the assert would leave the
    guard silently rejecting the very version it was told to install."""
    m = re.search(r"choco install ffmpeg --version=(\S+)", TEXT)
    assert m, "the version-pinned choco install is gone"
    assert _matches(f"ffmpeg version {m.group(1)} Copyright", _pin_pattern()), (
        f"the assert rejects the pinned version {m.group(1)}")


# ------------------------------------------------------------ the gate's shape


def test_the_gate_has_no_continue_on_error() -> None:
    """CLAUDE.md: this is the HARD gate. One of these turns it advisory."""
    assert "continue-on-error" not in STEPS


def test_the_gate_still_runs_the_full_suite() -> None:
    """No -k/-m selection: a narrowed run would still look green.

    The check is on the args AFTER `pytest` — `python -m pytest` contains a
    literal " -m " of its own, and matching that would fail every time."""
    m = re.search(r"run: python -m pytest([^\n]*)", STEPS)
    assert m, "the full-suite step is gone"
    args = m.group(1)
    assert " -k " not in args and " -m " not in args, args
    assert "--ignore" not in args and "tests/" not in args, args


def test_the_gate_installs_every_extra() -> None:
    """The suite behaves differently with these present (7 tests stop
    skipping), so the gate's install line is part of what it verifies."""
    m = re.search(r'pip install .*-e "\.\[([^\]]+)\]"', TEXT)
    assert m, "the extras install line is gone"
    got = {e.strip() for e in m.group(1).split(",")}
    assert {"dev", "jianying", "capcut", "mcpvideo", "edgetts"} <= got, got


def test_the_ffmpeg_presence_assert_survives() -> None:
    """Without it the suite can go green by silently skipping every
    ffmpeg-gated file — green for the one reason that must never count."""
    assert "Get-Command ffmpeg -ErrorAction Stop" in TEXT
    assert "Get-Command ffprobe -ErrorAction Stop" in TEXT
