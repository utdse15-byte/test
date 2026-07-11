"""AI_IDE_08_10_12C WP6 — the three task Skills + their eval fixtures.

Skills are data (SKILL.md), but they must stay TRUE: these evals pin (a)
discovery through the existing core.skills scanner with complete front-matter,
(b) the required sections (inputs/outputs/failure conditions/discipline), and
(c) the embedded data packs against the CODE vocabularies they mirror — the
one-variable repair map, the five dispositions, the visibility enums and the
CONTINUATION_* check codes — so a vocabulary change cannot silently strand the
skill text.
"""

from __future__ import annotations

import pytest

from manju.core.skills import bundled_skills_dir, list_skills
from manju.qc.agent_review import (
    DISPOSITIONS,
    OBS_STATE_VISIBILITY,
    REPAIR_VARIABLES,
)

SKILLS = (
    "direct-shot-source-patch",
    "review-take-and-route-repair",
    "continue-from-accepted-take",
)


def _body(skill_id: str) -> str:
    return (bundled_skills_dir() / skill_id / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("skill_id", SKILLS)
def test_skill_discovered_with_complete_frontmatter(skill_id):
    skills = {s.id: s for s in list_skills()}
    assert skill_id in skills, f"{skill_id} not discovered by core.skills"
    info = skills[skill_id]
    assert info.description
    assert info.when_to_use
    assert "task" in info.tags


@pytest.mark.parametrize("skill_id", SKILLS)
def test_skill_has_io_and_failure_sections(skill_id):
    body = _body(skill_id)
    assert "## 输入" in body
    assert "## 输出" in body
    assert "## 失败条件" in body
    assert "## 纪律" in body


def test_review_skill_data_pack_matches_code_vocabulary():
    """The one-variable map and disposition table in the skill must list
    EXACTLY the enforced vocabularies (drift-catcher)."""
    body = _body("review-take-and-route-repair")
    for var in REPAIR_VARIABLES:
        assert var in body, f"repair variable {var} missing from skill data pack"
    for dispo in DISPOSITIONS:
        assert dispo in body, f"disposition {dispo} missing from skill"
    for vis in OBS_STATE_VISIBILITY:
        assert vis in body, f"visibility {vis} missing from skill"
    assert "diagnostic_isolation" in body


def test_continue_skill_names_the_gate_codes():
    body = _body("continue-from-accepted-take")
    for code in ("CONTINUATION_SOURCE_NOT_ACCEPTED",
                 "CONTINUATION_SOURCE_HASH_MISMATCH",
                 "CONTINUATION_ENDPOINT_UNOBSERVED"):
        assert code in body


def test_direct_skill_maps_only_to_existing_fields():
    """The mapping table may only name fields the ShotSpec model actually
    owns — no invented bypass fields."""
    body = _body("direct-shot-source-patch")
    for field in ("action.main", "quality.must_show", "continuity.locks",
                  "generation.params", "camera."):
        assert field in body
    assert "reports/directing" in body  # the forbidden path is named as forbidden


@pytest.mark.parametrize("skill_id", SKILLS)
def test_skills_never_instruct_direct_provider_calls(skill_id):
    """§11: Skills output patches/evidence, never provider invocations. The
    spend-bearing commands may appear only as HUMAN follow-ups behind existing
    gates — never `--yes` auto-confirm in skill instructions."""
    body = _body(skill_id)
    assert "--yes" not in body, "a skill must never pre-authorize spend"


def test_reroll_route_keeps_source_and_spend_gate():
    """§13.6/49: REROLL maps to the EXISTING explicit redo (same source, §8.3
    spend gate — engine-enforced, red-tested in tests/test_ask_before.py)."""
    from manju.qc.production import repair_route

    route = repair_route("REROLL")
    assert route["path"] == "explicit_redo"
    assert "redo" in route["command"]
    assert "spend gate" in route["command"] or "§8.3" in route["command"]


def test_rewrite_source_route_is_proposal_first():
    """§13.6/50: REWRITE_SOURCE routes to the director proposal path — patch
    BEFORE any generation; the route itself never submits anything."""
    from manju.qc.production import repair_route

    route = repair_route("REWRITE_SOURCE")
    assert route["path"] == "director_proposal"
    assert route["do_not_execute_automatically"] is True
