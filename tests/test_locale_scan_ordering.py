"""Every locale scan must order by the POSIX string, not by Path.

`build/ingest.py` records the rule and the incident behind it:

    sorted(Path) is platform-dependent (Windows folds case in PurePath
    ordering — gate run #4 moved plan row indices). Sort by the POSIX string:
    byte-identical order on every platform.

Six locale-directory scans had not adopted it, and their order is user-visible:
it decides which locale the funnel's evidence sentence, `manju status`'s next
step, the director's proposal and the export centre name FIRST. The same
project could therefore advise differently on Windows than elsewhere.

The pin is a source-text one on purpose. Case-folded ordering cannot be
reproduced on Linux (PurePath comparison here is already byte-ordered), so a
behavioural test would pass on this platform whether or not the bug is present
— it would prove nothing. What CAN be checked here is that no locale scan sorts
raw Path objects, which is the property the rule actually asks for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "manju"

# `sorted(<something>.iterdir()` / `.rglob(` / `.glob(` with no key= before the
# closing paren of the sorted() call.
BARE_PATH_SORT = re.compile(
    r"sorted\(\s*[\w.\[\]]*\.(?:iterdir|rglob|glob)\([^)]*\)\s*\)")

LOCALE_SCANNERS = [
    "build/funnel.py", "build/status.py", "build/director.py",
    "build/exportstatus.py", "runtime/state.py",
]


@pytest.mark.parametrize("rel", LOCALE_SCANNERS)
def test_no_locale_scanner_sorts_raw_paths(rel: str) -> None:
    text = (SRC / rel).read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), 1):
        if "locales" not in line and "locale" not in line.lower():
            continue
        assert not BARE_PATH_SORT.search(line), (
            f"{rel}:{line_no} sorts Path objects — use key=lambda p: p.as_posix()"
            f"\n    {line.strip()}")


@pytest.mark.parametrize("rel", LOCALE_SCANNERS)
def test_each_locale_scanner_actually_sorts_by_posix(rel: str) -> None:
    """The counterpart: they must still sort (an unordered scan is worse)."""
    text = (SRC / rel).read_text(encoding="utf-8")
    if "locales_root.iterdir" not in text and "root.iterdir" not in text:
        pytest.skip(f"{rel} has no directory scan to check")
    assert "as_posix()" in text, f"{rel} scans directories but never sorts by POSIX"


def test_the_rule_is_still_documented_where_it_was_learned() -> None:
    """If ingest.py's note ever goes, this whole file loses its citation."""
    text = (SRC / "build" / "ingest.py").read_text(encoding="utf-8")
    assert "sorted(Path) is platform-dependent" in text
    assert "as_posix" in text
