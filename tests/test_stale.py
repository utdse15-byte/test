"""Tests for manju.build.stale and manju.core.spec — the §4.3 staleness matrix
and the "spec_hash only tracks the picture" contract.

The system fills gaps automatically but never overturns a decision either party
has already made.
"""

from __future__ import annotations

from manju.build.stale import ShotState, evaluate_shot
from manju.core.spec import compute_spec_hash


# --------------------------------------------------------------- state matrix


def test_missing_when_no_takes(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")
    status = evaluate_shot(tmp_project, shot)
    assert status.state is ShotState.MISSING
    assert status.take is None
    assert not status.usable


def test_fresh_when_selected_take_hash_matches(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    bible = tmp_project.load_bible()
    spec_hash = compute_spec_hash(shot, bible)
    take = make_take(tmp_project, "S001", spec_hash)
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)

    status = evaluate_shot(tmp_project, tmp_project.load_shot("S001"), bible)
    assert status.state is ShotState.FRESH
    assert status.usable
    assert status.take is not None and status.take.name == take.name


def test_stale_when_selected_take_hash_differs(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:0000000000stale")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)

    status = evaluate_shot(tmp_project, tmp_project.load_shot("S001"))
    assert status.state is ShotState.STALE
    # stale is still usable — the human's selection stands until they regen.
    assert status.usable


def test_manual_take_never_invalidated_even_after_edit(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)
    bible = tmp_project.load_bible()

    assert evaluate_shot(tmp_project, tmp_project.load_shot("S001"), bible).state is ShotState.MANUAL

    # editing a picture field would make a generated take stale — but not a manual one.
    edited = tmp_project.load_shot("S001")
    edited.action.main = "完全不同的动作:林夏摔碎了硬币"
    assert evaluate_shot(tmp_project, edited, bible).state is ShotState.MANUAL


def test_needs_selection_when_takes_exist_but_none_selected(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:abc")
    make_take(tmp_project, "S001", "sha256:def")
    status = evaluate_shot(tmp_project, shot)
    assert status.state is ShotState.NEEDS_SELECTION
    assert not status.usable
    assert "none selected" in status.note


def test_broken_when_selected_take_does_not_exist(tmp_project, add_shot, make_take):
    # one real take exists, but the shot points at a name that isn't there.
    add_shot(tmp_project, "S001", status={"selected_take": "take_99"})
    make_take(tmp_project, "S001", "sha256:abc")
    status = evaluate_shot(tmp_project, tmp_project.load_shot("S001"))
    assert status.state is ShotState.BROKEN
    assert not status.usable


# ------------------------------------------------ spec_hash: only the picture


def test_editing_status_does_not_change_spec_hash(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")
    bible = tmp_project.load_bible()
    before = compute_spec_hash(shot, bible)
    shot.status.selected_take = "take_07"
    shot.status.approved = True
    assert compute_spec_hash(shot, bible) == before


def test_editing_referenced_character_bible_changes_spec_hash(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")  # characters: [linxia]
    bible = tmp_project.load_bible()
    before = compute_spec_hash(shot, bible)

    bible["linxia"]["appearance"] = "改成:一头长发,红色雨衣"
    assert compute_spec_hash(shot, bible) != before


def test_editing_unrelated_bible_entry_does_not_change_spec_hash(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")  # references convenience_store + linxia only
    bible = tmp_project.load_bible()
    before = compute_spec_hash(shot, bible)

    bible["future_coin"] = {"name": "未来硬币", "description": "刻着 2036"}
    bible["old_zhou"] = {"name": "周叔", "appearance": "秃顶,围裙"}
    assert compute_spec_hash(shot, bible) == before
