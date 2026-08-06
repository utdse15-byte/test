"""Phase 6: one vocabulary across owner and agent entry points."""

from __future__ import annotations

from pathlib import Path

from manju.cli import _MINI_PLAYBOOK
from manju.cli_workflows import WORKFLOWS
from manju.core.skills import skill_index_text
from manju.gui.create_page import render_create


ROOT = Path(__file__).resolve().parents[1]


def test_authoring_doc_is_self_contained_and_names_the_three_eligibility_states():
    text = (ROOT / "docs" / "AI_AUTHORING_SYSTEM.md").read_text(encoding="utf-8")
    for term in (
        "SceneContract", "ShotContract", "TEMP_AUDIO_FOR_ANIMATIC", "Animatic",
        "Proof Shot", "Proof Scene", "proxy-only", "candidate", "final-eligible",
        "Picture Lock", "FINAL_AUDIO_FINISHING", "manju production status",
    ):
        assert term in text
    assert "build ok" in text
    assert "不等于" in text


def test_readme_draws_creative_execution_and_picture_lock_boundaries():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for term in ("SceneContract", "ShotContract", "proxy-only", "candidate",
                 "final-eligible", "Picture Lock"):
        assert term in text
    assert "引擎不替导演决定" in text
    assert "build ok" in text


def test_agent_and_owner_surfaces_share_contract_and_eligibility_terms(tmp_project):
    workflow = str(WORKFLOWS["new-project"]) + str(WORKFLOWS["generate-takes"])
    index = skill_index_text(None)
    gui = render_create(tmp_project, "tok")
    for surface in (_MINI_PLAYBOOK, workflow, index, gui):
        for term in ("SceneContract", "ShotContract", "proxy-only", "candidate",
                     "final-eligible"):
            assert term in surface
