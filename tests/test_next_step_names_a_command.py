"""Two rungs of the next-step ladder described a task instead of naming one.

`next_step` is the single line a user reads to know what to do. Almost every
rung ends in a command — "manju build(补齐缺失镜头:…)", "manju select(待挑选:
…)". Two did not:

* the EMPTY project — the very first thing a new project prints — said only
  "先写 shots/", naming a directory and no command, even though `manju new`
  had just recommended `manju create` one line earlier;
* the BROKEN rung said "修复 broken 镜头:S003" and stopped, though the
  per-shot 待办 directly underneath already knew the two remedies.

`next_step_key` is the machine contract (agents and the GUI branch on it) and
is deliberately unchanged — this is the human sentence only.
"""

from __future__ import annotations

import pytest

from manju.build.status import project_status


def _next(project) -> dict:
    st = project_status(project)
    return {"key": st["next_step_key"], "text": st["next_step"]}


# ------------------------------------------------------------- empty project


def test_a_brand_new_project_names_a_command(tmp_project) -> None:
    got = _next(tmp_project)
    assert got["key"] == "create_shots"          # machine contract unchanged
    assert "manju create" in got["text"]


def test_the_empty_rung_still_offers_the_by_hand_route(tmp_project) -> None:
    """The funnel is a suggestion, not the only door — text is truth (§2)."""
    text = _next(tmp_project)["text"]
    assert "shots/" in text
    assert "引擎不编故事" in text                  # the standing discipline stays


# ------------------------------------------------------------ broken shots


@pytest.fixture
def broken(tmp_project, add_shot):
    """A shot whose selected take has no media file on disk."""
    add_shot(tmp_project, "S001", status={"selected_take": "take_01"})
    (tmp_project.root / "media" / "gen" / "S001").mkdir(parents=True, exist_ok=True)
    (tmp_project.root / "media" / "gen" / "S001" / "take_01.yaml").write_text(
        "take: take_01\n", encoding="utf-8")
    return tmp_project


def test_the_broken_rung_names_its_remedies(broken) -> None:
    got = _next(broken)
    assert got["key"] == "fix_broken", got
    assert "manju redo S001" in got["text"]
    assert "manju select S001" in got["text"]


def test_the_broken_rung_still_lists_the_shots(broken) -> None:
    """Naming a command must not cost the reader the list of what is broken."""
    assert "S001" in _next(broken)["text"]
    assert "broken" in _next(broken)["text"]


def test_with_several_broken_the_command_names_one_of_them(
        broken, add_shot) -> None:
    """All broken shots are listed, and the command targets a listed one —
    a suggestion pointing at a shot that is fine would be worse than none."""
    add_shot(broken, "S002", status={"selected_take": "take_01"})
    (broken.root / "media" / "gen" / "S002").mkdir(parents=True, exist_ok=True)
    (broken.root / "media" / "gen" / "S002" / "take_01.yaml").write_text(
        "take: take_01\n", encoding="utf-8")

    got = _next(broken)
    assert got["key"] == "fix_broken", got
    assert "S001" in got["text"] and "S002" in got["text"]
    assert "manju redo S001" in got["text"]
