"""The export centre's status column has to be a column.

`manju exports` padded its label with ``f"{label:<18}"`` — code points, not
terminal columns. A CJK label is one code point per TWO columns, so the status
words landed anywhere between column 27 and 37, and the longest label
(``M_AND_E_BUS_EXCLUSION_MASTER``, 28 columns) blew past 18 entirely and
printed as ``M_AND_E_BUS_EXCLUSION_MASTER上新`` — glued, no gap.

The CJK-aware padder already exists and four other CLI tables already use it
(`presets.display_width` / `presets.pad`); this table simply had not joined it.
Widths are measured from the rows actually present, so a new deliverable kind
cannot silently re-break the gap the way a hardcoded 18 did.
"""

from __future__ import annotations

import re
import unicodedata

from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()

STATES = ("上新", "缺失", "待更新", "已核验")


def _w(text: str) -> int:
    """Terminal columns, independently of the implementation under test."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in text)


def _rows(out: str) -> list[str]:
    """The deliverable lines: two-space indent, not a └/→/path continuation."""
    return [ln for ln in out.splitlines()
            if ln.startswith("  ") and not ln.startswith("    ")
            and any(s in ln for s in STATES)]


def _label(row: str) -> str:
    """Everything before the status word — labels contain spaces ("成片 Final"),
    so splitting on whitespace measures the wrong thing."""
    m = re.search("|".join(STATES), row)
    assert m, row
    return row[:m.start()].strip()


def _exports(project, monkeypatch) -> str:
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["exports"])
    assert res.exit_code == 0, res.output
    return res.output


def test_every_status_word_starts_at_the_same_column(
        tmp_project, add_shot, monkeypatch) -> None:
    add_shot(tmp_project, "S001")
    rows = _rows(_exports(tmp_project, monkeypatch))
    assert len(rows) >= 5, f"expected a real table, got {rows}"
    starts = set()
    for ln in rows:
        m = re.search("|".join(STATES), ln)
        assert m, ln
        starts.add(_w(ln[:m.start()]))
    assert len(starts) == 1, f"status column ragged across {sorted(starts)}"


def test_the_longest_label_still_has_a_gap_before_its_status(
        tmp_project, add_shot, monkeypatch) -> None:
    """The regression that was actually visible: label glued to status."""
    rows = _rows(_exports(tmp_project, monkeypatch))
    longest = max(rows, key=lambda ln: _w(_label(ln)))
    m = re.search("|".join(STATES), longest)
    assert longest[m.start() - 1] == " ", f"no gap before the status: {longest!r}"


def test_a_long_ascii_label_is_present_to_make_the_check_real(
        tmp_project, add_shot, monkeypatch) -> None:
    """Guard the guard: the alignment checks above are only meaningful while
    the table mixes wide CJK labels with a long ASCII one."""
    rows = _rows(_exports(tmp_project, monkeypatch))
    labels = [_label(ln) for ln in rows]
    widths = {_w(s) for s in labels}
    # A pure code-point pad only misaligns when the labels DISAGREE about how
    # many columns a code point buys — i.e. some are CJK and some are not.
    assert max(widths) - min(widths) >= 4, f"labels too uniform: {labels}"
    assert any(any(unicodedata.east_asian_width(c) in ("W", "F") for c in s)
               for s in labels), "no CJK label — the naive pad would pass here"
    # The labels must also disagree about how many CJK chars they carry: equal
    # CJK counts would drift by the same amount and stay accidentally aligned.
    cjk = {sum(unicodedata.east_asian_width(c) in ("W", "F") for c in s)
           for s in labels}
    assert len(cjk) > 1, f"every label has the same CJK count: {labels}"


def test_rows_carry_no_trailing_whitespace(
        tmp_project, add_shot, monkeypatch) -> None:
    """Padding the last column left ragged trailing spaces on every row."""
    for ln in _rows(_exports(tmp_project, monkeypatch)):
        assert ln == ln.rstrip(), f"trailing whitespace: {ln!r}"


def test_the_version_suffix_survives(tmp_project, add_shot, monkeypatch) -> None:
    """rstrip() must not eat a real trailing field."""
    from manju.build.exportstatus import deliverables_data

    out = _exports(tmp_project, monkeypatch)
    versioned = [r for r in deliverables_data(tmp_project)["deliverables"]
                 if r.get("version")]
    for row in versioned:
        assert str(row["version"]) in out
