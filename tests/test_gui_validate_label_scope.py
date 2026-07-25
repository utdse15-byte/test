"""The live-validation tick claimed more than it checked.

Walking a task through the GUI instead of the CLI: create a shot, type a scene
and character that are NOT in the bible. The strip under the editor said

    ✓ 校验通过 (valid)

Save was enabled, and clicking it produced

    check failed — shots/S001.yaml 已回滚 (reverted)
    ✗ scene '不存在的场景' not found in bible
    ✗ character '不存在的人' not found in bible

The engine is right and its errors are excellent — it writes, runs the real
check, reverts, and names both dangling refs in the dialog. (My first read of
this was that Save failed SILENTLY; that was wrong — my scrape searched for the
wrong words. Verified before reporting, which is why it is not in the fix.)

What was wrong is only the label. `/api/validate` is documented in this very
file as advisory: it parses and model-validates the buffer with NO cross-
reference and NO lock verification, and the comment says outright that "a ✓
here is NOT a promise that Save will succeed". The user was not told that — an
unqualified 校验通过 promises exactly what the endpoint cannot check.

So the tick now names the check it actually ran, and points at the one that
will decide.
"""

from __future__ import annotations

import re

from manju.gui.page import render_js


JS = render_js()


def _tick_label() -> str:
    """The string rendered by renderValid()."""
    i = JS.index("const renderValid")
    block = JS[i:i + 600]
    m = re.search(r'"ed-valid-ok",\s*\n?\s*"([^"]+)"', block)
    assert m, block[:300]
    return m.group(1)


def test_the_tick_does_not_claim_an_unqualified_pass() -> None:
    label = _tick_label()
    assert label != "✓ 校验通过 (valid)", "the unqualified claim is back"


def test_the_tick_names_what_it_checked() -> None:
    """"格式" / format — the thing /api/validate actually does."""
    label = _tick_label()
    assert "格式" in label or "format" in label.lower(), label


def test_the_tick_names_what_it_did_not_check() -> None:
    """Bible refs and locks are exactly what bit on Save."""
    label = _tick_label()
    assert "引用" in label, label
    assert "锁" in label, label


def test_the_tick_points_at_the_authority() -> None:
    """Save runs the real check; the strip should say so rather than leave the
    owner to discover it by being refused."""
    assert "check" in _tick_label(), _tick_label()


def test_it_is_still_a_pass_not_a_warning() -> None:
    """Well-formed YAML IS good news — over-hedging would make the tick useless
    for the case it exists to serve (catching a typo while you type)."""
    assert _tick_label().lstrip().startswith("✓"), _tick_label()


def test_the_invalid_branch_is_untouched() -> None:
    """renderInvalid already printed server-provided messages verbatim."""
    assert "renderInvalid" in JS
    i = JS.index("const renderInvalid")
    assert "校验未通过" in JS[i:i + 500]


def test_the_advisory_contract_is_still_documented_in_source() -> None:
    """Guard the guard: this fix exists because the code KNEW and the UI did
    not say. If that comment goes, the next author loses the reason."""
    from manju.gui import page

    src = page.__file__
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    assert "ADVISORY ONLY" in body
    assert "NOT a promise that Save will succeed" in body
