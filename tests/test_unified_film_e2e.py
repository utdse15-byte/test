from __future__ import annotations

from manju.build.authoring_patch import TRUTH_PATCH_SCHEMA
from manju.build.control_view import derive_control_view
from manju.build.director import confirm, execute, propose
from manju.core.hashing import hash_file
from manju.core.intent import picture_contract_payload
from manju.qc.animatic_experience import (
    build_animatic_experience_review,
    record_animatic_experience_review,
)
from manju.qc.asset_qualification import build_asset_qualification, record_asset_qualification
from manju.story.coverage import derive_coverage
from manju.story.lint import lint_screen, lint_story
from tests.fixtures.make_unified_film_project import make_unified_film_project


def test_zero_network_unified_film_rehearsal(tmp_path):
    project = make_unified_film_project(tmp_path / "unified")
    assert project.creative_status()["state"] == "current"
    assert lint_story(project)["ok"]
    assert lint_screen(project)["ok"]
    coverage = derive_coverage(project, persist=True)
    assert not coverage["uncovered_required_experience_beats"]

    qualification = build_asset_qualification(
        asset_ref="prop:coin", asset_kind="prop", media_sha256="sha256:" + "1" * 64,
        capabilities={"identity": "qualified", "pickup": "qualified"}, state="qualified",
    )
    record_asset_qualification(project, qualification)

    media = project.root / "renders" / "animatic_v01.mp4"
    media.write_bytes(b"TEMP_ANIMATIC")
    review = build_animatic_experience_review(
        path="renders/animatic_v01.mp4", media_sha256=hash_file(media),
        timeline_digest="timeline-fixture", screen_intent_digest_value="screen-fixture",
        script_sha256=hash_file(project.root / "story" / "script.md"),
        temp_audio_digest="TEMP_AUDIO-fixture",
        passes={name: {"result": "pass", "findings": []} for name in
                ("causal", "silent_visual", "audio_only", "thumbnail", "duration", "transitions")},
        verdict="approved", actor_kind="human",
    )
    record_animatic_experience_review(project, review)
    assert derive_control_view(project, "S002")["schema"] == "manju.control-view/v1"

    before = picture_contract_payload(project.load_shot("S002"))
    action = {"type": "truth_patch_set", "patches": [{
        "path": "story/script.md", "content": project.root.joinpath("story/script.md").read_text(encoding="utf-8") + "\n# rewrite\n"
    }]}
    proposal = propose(project, [action], actor="ai")
    confirm(project, proposal.id, actor="human")
    assert execute(project, proposal.id, actor="human").ok
    assert TRUTH_PATCH_SCHEMA
    assert picture_contract_payload(project.load_shot("S002")) == before

    legacy = make_unified_film_project(tmp_path / "legacy")
    (legacy.root / "story" / "creative.yaml").unlink()
    assert legacy.creative_status()["state"] == "legacy"
