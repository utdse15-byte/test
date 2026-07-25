"""A cockpit activity row must stay ONE row (found by looking at the GUI).

`.ck-ev` is a flex line inside a narrow cockpit column. Flex children shrink by
default and a flex item's `min-width` resolves to `auto`, so a long action name
("mentions_apply") squeezed `.edetail` towards zero width — and `.edetail`
carries `word-break: break-all`, which then wrapped the text ONE CHARACTER PER
LINE. A single event rendered as a dozen-line vertical ladder:

    mentions_apply   s
                     h
                     o
                     t
                     …

Three things together fix it and all three are pinned here, because any one of
them alone still breaks: pin the fixed cells so they do not shrink, give the
elastic cell `min-width: 0` so it may actually shrink, and stop `break-all`
from applying inside this row. The full text stays reachable via `title`, and
the block spans two grid columns so ordinary details fit without eliding.
"""

from __future__ import annotations

import re

import pytest

from manju.gui.page import render_css, render_js

# Comments must go BEFORE the selector split: a prose comment containing a
# comma otherwise glues itself onto the first selector and nothing matches.
CSS = re.sub(r"/\*.*?\*/", "", render_css(), flags=re.S)
JS = render_js()


def _rule(selector: str) -> str:
    """The declaration block of the first CSS rule whose selector list contains
    ``selector`` as a whole comma-separated entry."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        sels = [s.strip() for s in m.group(1).split(",")]
        if selector in sels:
            return m.group(2)
    raise AssertionError(f"no CSS rule for {selector!r}")


# ------------------------------------------------------------------- the CSS


def test_the_detail_cell_may_actually_shrink() -> None:
    """Without min-width:0 a flex item never goes below its content width."""
    body = _rule(".ck-ev .edetail")
    assert re.search(r"min-width:\s*0", body), body


def test_the_detail_cell_does_not_break_mid_word() -> None:
    """.edetail's shared rule sets word-break:break-all — the per-character
    wrap only happens because of it, so this row must override it."""
    body = _rule(".ck-ev .edetail")
    assert re.search(r"word-break:\s*normal", body), body
    assert re.search(r"white-space:\s*nowrap", body), body


def test_the_overflow_becomes_an_ellipsis_not_a_clip() -> None:
    body = _rule(".ck-ev .edetail")
    assert "text-overflow: ellipsis" in body, body
    assert re.search(r"overflow:\s*hidden", body), body


@pytest.mark.parametrize("cell", [".ck-ev .badge", ".ck-ev .eaction", ".ck-ev .etime"])
def test_the_fixed_cells_do_not_shrink(cell: str) -> None:
    """If these shrink too, the row still collapses — just less obviously."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        sels = [s.strip() for s in m.group(1).split(",")]
        if cell in sels and re.search(r"flex:\s*0 0 auto", m.group(2)):
            return
    raise AssertionError(f"{cell} is still allowed to shrink")


def test_the_shared_edetail_rule_still_wraps_elsewhere() -> None:
    """The fix is scoped: other .edetail users keep break-all on purpose."""
    assert "word-break: break-all" in _rule(".edetail")


# ------------------------------------------------------------------- the JS


def test_the_row_carries_the_full_text_as_a_tooltip() -> None:
    """The cell ellipsises, so the elided text has to stay reachable."""
    block = JS[JS.index("最近动态 (activity)"):][:1200]
    assert "d.title = e.summary" in block, block[:400]


def test_the_activity_block_spans_two_columns() -> None:
    """One column is not wide enough for "action  shot=S007  07:13:25"."""
    start = JS.index("最近动态 (activity)")
    block = JS[start:start + 1400]
    end = block.index("其它建议") if "其它建议" in block else len(block)
    assert '"span2"' in block[:end], block[:end][-300:]


def test_span2_exists_as_a_real_rule() -> None:
    """Guard the guard: passing "span2" to ckBlock does nothing without it."""
    assert "grid-column: span 2" in _rule(".ck-block.span2")
