"""The New-shot box suggested an id that already existed.

Found by following my own advice. The storyboard stage of the funnel now tells
newcomers "manju gui → 分镜页「新建镜头」点着建", so that route had to be walked
before shipping the recommendation.

The button works — it opens an inline id field with 创建/取消. But the field's
placeholder was the literal string "S007" on every project. On the 12-shot demo
project, clicking 新建镜头 and typing the id it suggests answers:

    S007 已存在,进入编辑 (already exists — editing)

You asked to create a shot and got the editor for one you already had. The
engine is right to refuse to overwrite; the SUGGESTION was wrong. On an empty
project it was equally wrong in the other direction — S007 when the obvious
first id is S001.

Two separate bugs, and the second only showed up after fixing the first:
computing the placeholder where the input is built runs before the first state
arrives, so `lastShots` is empty and a twelve-shot project still suggested
S001. It has to be recomputed when the form OPENS.

Verified live: 12-shot project → S013, empty project → S001.
"""

from __future__ import annotations

import re

from manju.gui.page import render_js

JS = render_js()


def _fn(name: str) -> str:
    """The body of a top-level `function name(...) {...}` in the page JS."""
    i = JS.index(f"function {name}(")
    depth, start = 0, JS.index("{", i)
    for j in range(start, len(JS)):
        depth += (JS[j] == "{") - (JS[j] == "}")
        if depth == 0:
            return JS[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_the_hardcoded_id_is_gone() -> None:
    """The exact assignment that named an existing shot on any real project.

    Scoped to the ASSIGNMENT, not to the string anywhere: the comment above the
    fix quotes "S007" while explaining it, and a blanket search flagged that —
    the same comment-trips-a-raw-source-check mistake this session made twice
    before. What must not come back is the hardcoded assignment."""
    assert 'placeholder = "S007"' not in JS
    assert "placeholder = 'S007'" not in JS


def test_the_suggestion_is_computed() -> None:
    assert "function nextFreeShotId(" in JS
    assert JS.count("input.placeholder = nextFreeShotId()") >= 1


def test_it_is_recomputed_when_the_form_opens() -> None:
    """The second bug: computing it once at construction runs before the first
    state arrives, so `lastShots` is empty and every project suggests S001."""
    i = JS.index('newBtn.addEventListener("click"')
    handler = JS[i:i + 700]
    assert "nextFreeShotId()" in handler, (
        "the placeholder is not refreshed on open — it will be stale")


def test_the_suggestion_derives_from_the_loaded_shots() -> None:
    body = _fn("nextFreeShotId")
    assert "lastShots" in body, "not derived from the project's shots at all"


def test_it_skips_ids_that_are_taken() -> None:
    """Suggesting a free id is the whole point; a max+1 that collides with a
    hand-numbered shot would reintroduce the bug in a rarer shape."""
    body = _fn("nextFreeShotId")
    assert "taken" in body and "has(" in body, body[:300]


def test_only_s_numbered_ids_move_the_counter() -> None:
    """A hand-named shot ("intro") must not push the suggestion somewhere odd."""
    body = _fn("nextFreeShotId")
    assert re.search(r"\^S\\\\d\{3,\}\$|\^S\(\\\\d\{3,\}\)\$", body) or "S(\\d{3,})" in body, (
        f"no S### shape filter: {body[:300]}")


def test_it_pads_to_the_house_convention() -> None:
    """S001, not S1 — every doc, fixture and shot file uses three digits."""
    body = _fn("nextFreeShotId")
    assert "padStart(3" in body, body[:300]


def test_the_empty_project_case_starts_at_one() -> None:
    body = _fn("nextFreeShotId")
    assert "length ?" in body or "nums.length" in body, body[:300]
    assert ": 1" in body or "|| 1" in body, (
        f"no empty-project base case — would suggest S000 or NaN: {body[:300]}")
