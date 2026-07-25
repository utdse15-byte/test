"""Two "Windows-only" invariants that turn out to be observable from Linux.

`test_windows_invariants_guard.py` declared case-folded PurePath ordering and
Windows command quoting unobservable here, and pinned neither. That was wrong,
and it mattered: those two are exactly the invariants a Linux dev loop is most
likely to break, and the hard gate only reports them after the fact.

Both have PURE-PYTHON Windows implementations that run anywhere:

* `PureWindowsPath` normalises case for comparison on ANY platform, so the
  real Windows sort order can be produced (and shown to differ) on Linux.
* `subprocess.list2cmdline` implements the CreateProcess quoting rules that
  `providers/local_cmd._split_command` has to survive — the actual Windows
  contract, not a hand-written approximation.

What is still NOT observable here, and is deliberately still unpinned: real
msvcrt byte-lock behaviour, the actual CreateProcess syscall, and NTFS's own
collation (which is not identical to `PureWindowsPath`'s casefold, only close
enough to expose ORDER-DEPENDENCE — which is the property we defend).
"""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath, PureWindowsPath

import pytest

# Mixed case AND a digit-width case, the two ways this ordering bites:
# "Zebra vs apple" flips on case folding, "s002 vs S010" flips on both.
NAMES = ["Zebra.mp4", "apple.mp4", "Banana.mp4", "cherry.mp4",
         "S010.yaml", "s002.yaml", "第二幕.md", "Act1.md"]


# --------------------------------------------------- case-folded path ordering


def test_the_two_platforms_really_do_disagree() -> None:
    """Guard the guard. If this ever stops differing, the tests below prove
    nothing and must be re-derived rather than left passing vacuously."""
    posix = [p.as_posix() for p in sorted(PurePosixPath(n) for n in NAMES)]
    win = [p.as_posix() for p in sorted(PureWindowsPath(n) for n in NAMES)]
    assert posix != win, (
        "PureWindowsPath no longer case-folds for comparison; this file's "
        "premise is gone")


def test_sorting_bare_path_objects_is_platform_dependent() -> None:
    """The defect class: `sorted(paths)` with no key gives a DIFFERENT order
    on Windows, so anything downstream that depends on order — a scan, a
    manifest, a hash over a listing — silently diverges by platform."""
    posix = [p.as_posix() for p in sorted(PurePosixPath(n) for n in NAMES)]
    win = [p.as_posix() for p in sorted(PureWindowsPath(n) for n in NAMES)]
    # Name the concrete flip so the failure message teaches, not just fails.
    assert posix.index("Zebra.mp4") < posix.index("apple.mp4")
    assert win.index("apple.mp4") < win.index("Zebra.mp4")


def test_the_documented_key_is_platform_stable() -> None:
    """CLAUDE.md: `sorted(..., key=as_posix)` for cross-platform determinism.
    This is that claim, executed on both path flavours."""
    by_key_posix = sorted(NAMES, key=lambda n: PurePosixPath(n).as_posix())
    by_key_win = sorted(NAMES, key=lambda n: PureWindowsPath(n).as_posix())
    assert by_key_posix == by_key_win


def test_the_key_orders_by_bytes_not_by_locale() -> None:
    """CJK names must order the same way on both platforms too."""
    for flavour in (PurePosixPath, PureWindowsPath):
        got = sorted(NAMES, key=lambda n: flavour(n).as_posix())
        assert got.index("Act1.md") < got.index("第二幕.md"), flavour


def test_the_locale_language_scan_orders_strings_not_paths(tmp_path) -> None:
    """The scan whose ordering this rule was written for, checked by BEHAVIOUR.

    `list_locales` sorts `p.name` — plain strings, whose order is the same
    everywhere. Sorting the Path objects instead would put "ZH" before "en" on
    Linux and after it on Windows, and the locale list feeds user-visible
    output and derived reports.

    Asserted as an outcome, not a grep, so it survives refactoring: the
    returned order must equal a pure byte sort of the same names."""
    from manju.core.container import Project
    from manju.core.locale import list_locales, locales_dir

    project = Project.create(tmp_path / "p", git_init=False)
    d = locales_dir(project)
    for lang in ("ZH", "en", "Ja", "de"):
        (d / lang).mkdir(parents=True, exist_ok=True)
        (d / lang / "lines.yaml").write_text("{}\n", encoding="utf-8")

    got = list_locales(project)
    assert got == sorted(got), f"not in byte order: {got}"
    # The concrete flip: a case-folding sort would put "en" before "Ja"/"ZH".
    assert got == ["Ja", "ZH", "de", "en"], got


# ------------------------------------------------- Windows command quoting


WINDOWS_ARGV = [
    [r"C:\Program Files\ffmpeg\bin\ffmpeg.exe", "-i", r"D:\a b.mp4"],
    [r"C:\tools\gen.exe", "--out", r"C:\Users\me\out.mp4"],
    ["sh", r"C:\Users\me\gen.sh", "--out", "x.mp4"],
    [r"\\server\share\tool.exe", "--flag", "value"],
    ["tool", r"D:\素材 集\雨夜 便利店.mp4"],
    ["tool", "key=a b"],
    ["tool", "plain", "another"],
]


@pytest.mark.parametrize("argv", WINDOWS_ARGV, ids=lambda a: a[0][:24])
def test_split_command_round_trips_real_windows_quoting(argv, monkeypatch) -> None:
    """`list2cmdline` produces the command line CreateProcess would be handed.
    `_split_command` must recover the original argv from it — otherwise a
    provider template with a real Windows path loses its backslashes and the
    child exits 127 (the gate-round-1 defect)."""
    from manju.providers import local_cmd

    monkeypatch.setattr(local_cmd, "_IS_WINDOWS", True)
    line = subprocess.list2cmdline(argv)
    assert local_cmd._split_command(line) == argv, line


def test_the_posix_path_is_untouched_by_this(monkeypatch) -> None:
    """On POSIX the owner must still be plain shlex — the Windows branch is an
    addition, never a change to the platform the dev loop runs on."""
    import shlex

    from manju.providers import local_cmd

    monkeypatch.setattr(local_cmd, "_IS_WINDOWS", False)
    for line in ('tool --out "a b.mp4"', "tool plain", "sh ./gen.sh --out x"):
        assert local_cmd._split_command(line) == shlex.split(line), line


def test_a_backslash_path_survives_the_windows_branch(monkeypatch) -> None:
    """The original bug, stated directly: every backslash was eaten."""
    from manju.providers import local_cmd

    monkeypatch.setattr(local_cmd, "_IS_WINDOWS", True)
    got = local_cmd._split_command(r"sh C:\Users\me\gen.sh --out {out}")
    assert got == ["sh", r"C:\Users\me\gen.sh", "--out", "{out}"], got


def test_a_unc_path_keeps_both_leading_slashes(monkeypatch) -> None:
    from manju.providers import local_cmd

    monkeypatch.setattr(local_cmd, "_IS_WINDOWS", True)
    got = local_cmd._split_command(r"\\server\share\tool.exe --flag")
    assert got[0] == r"\\server\share\tool.exe", got
