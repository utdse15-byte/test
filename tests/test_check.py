"""Tests for manju.core.check — the agent's safety net (§4, §5, §8.2).

Schema + referential integrity + hard lock verification + secret scan. Each
error case is asserted by a stable substring of the reported message.
"""

from __future__ import annotations

from manju.core.check import run_check
from manju.core.locks import seal_lock
from manju.core.yamlio import read_yaml, write_yaml


def _errors_text(project) -> str:
    return "\n".join(run_check(project).errors)


def test_healthy_project_passes(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)

    report = run_check(tmp_project)
    assert report.ok, report.errors
    assert report.errors == []


def test_scene_not_in_bible(tmp_project, add_shot):
    add_shot(tmp_project, "S001", scene="dark_alley")
    assert "scene 'dark_alley' not found in bible" in _errors_text(tmp_project)


def test_character_not_in_bible(tmp_project, add_shot):
    add_shot(tmp_project, "S001", characters=["ghost"])
    assert "character 'ghost' not found in bible" in _errors_text(tmp_project)


def test_selected_take_nonexistent(tmp_project, add_shot):
    add_shot(tmp_project, "S001", status={"selected_take": "take_99"})
    assert "selected_take 'take_99' does not exist" in _errors_text(tmp_project)


def test_lock_violation_after_tampering_yaml_on_disk(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    path = tmp_project.shot_path("S001")

    # seal a lock on dialogue.text with the real current-value hash...
    raw = read_yaml(path)
    raw["locked"] = {"dialogue.text": seal_lock(raw, "dialogue.text")}
    write_yaml(path, raw)

    # ...then tamper the value directly on disk (as a rogue edit would).
    raw = read_yaml(path)
    raw["dialogue"]["text"] = "被偷偷改掉的台词"
    write_yaml(path, raw)

    text = _errors_text(tmp_project)
    assert "locked field 'dialogue.text' changed" in text


def test_unsealed_lock_written_as_bare_list(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    path = tmp_project.shot_path("S001")
    raw = read_yaml(path)
    raw["locked"] = ["dialogue.text"]  # hand-written list, never sealed
    write_yaml(path, raw)

    text = _errors_text(tmp_project)
    assert "unsealed" in text
    assert "dialogue.text" in text


def test_index_references_missing_shot(tmp_project):
    index = tmp_project.load_index()
    index.order = ["S404"]
    tmp_project.save_index(index)
    assert "references missing shot 'S404'" in _errors_text(tmp_project)


def test_secret_pattern_in_story_markdown(tmp_project):
    brief = tmp_project.root / "story" / "brief.md"
    brief.write_text("# 创意\n\n临时把密钥贴这里:sk-" + "a" * 30 + "\n", encoding="utf-8")
    text = _errors_text(tmp_project)
    assert "API key" in text
