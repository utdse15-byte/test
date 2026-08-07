from __future__ import annotations

import warnings

import pytest

from manju.build.authoring_patch import TRUTH_PATCH_SCHEMA
from manju.build.control_view import derive_control_view
from manju.build.director import DirectorError, confirm, execute, propose
from manju.build.readiness import production_readiness
from manju.core.authoring import SceneContractV2, ShotContract, ScreenIntent
from manju.core.creative import CreativeCharter, TruthRef
from manju.core.hashing import hash_file, hash_text
from manju.core.intent import picture_contract_payload, prompt_contract_sections, screen_intent_digest
from manju.core.models import ShotSpec
from manju.core.source_spans import SourceSpanError, load_source_spans, parse_source_spans
from manju.qc.animatic_experience import build_animatic_experience_review
from manju.story.coverage import derive_coverage
from manju.story.lint import lint_screen
from manju.story.trajectory import open_threads


def _project(tmp_path):
    from manju.core.container import Project
    return Project.create(tmp_path / "film")


def test_charter_is_opt_in_and_hash_bound(tmp_path):
    project = _project(tmp_path)
    assert project.creative_status()["state"] == "legacy"
    (project.root / "story" / "brief.md").write_text("brief", encoding="utf-8")
    (project.root / "story" / "ending.md").write_text("ending", encoding="utf-8")
    charter = CreativeCharter(
        brief_ref=TruthRef(path="story/brief.md", sha256=hash_file(project.root / "story/brief.md")),
        ending_ref=TruthRef(path="story/ending.md", sha256=hash_file(project.root / "story/ending.md")),
        audiovisual={"ambiguity_to_preserve": ["unknown"]},
    )
    from manju.core.yamlio import write_yaml
    write_yaml(project.creative_path, charter.model_dump(exclude_none=True))
    assert project.creative_status()["state"] == "current"
    (project.root / "story" / "brief.md").write_text("changed", encoding="utf-8")
    assert project.creative_status()["state"] == "drifted"


def test_source_spans_are_stable_and_strict():
    rows = parse_source_spans(
        "<!-- manju:scene SC1 -->\n\n<!-- manju:span SC1-B001 -->\nA line\n"
    )
    assert rows[0].span_id == "SC1-B001"
    assert rows[0].text_sha256 == hash_text("A line")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            parse_source_spans("<!-- manju:span SC1-B001 -->\nA")
        except SourceSpanError:
            pass
        else:
            raise AssertionError("span before scene must fail")


def test_source_spans_reject_malformed_markers_and_unknown_scenes(tmp_path):
    with pytest.raises(SourceSpanError, match="malformed"):
        parse_source_spans(
            "<!-- manju:scene SC1 -->\n"
            "<!-- manju:span SC1-B001 -- >\n"
            "A line\n"
        )

    project = _project(tmp_path)
    (project.root / "story" / "script.md").write_text(
        "<!-- manju:scene UNKNOWN -->\n"
        "<!-- manju:span UNKNOWN-B001 -->\n"
        "A line\n",
        encoding="utf-8",
    )
    with pytest.raises(SourceSpanError, match="unknown scene contract"):
        load_source_spans(project)


def test_screen_intent_stays_out_of_picture_and_prompt(tmp_path):
    project = _project(tmp_path)
    shot = ShotSpec(
        id="S001",
        action={"main": "look up"},
        source_span_refs=["script:SC1-B001"],
        contract=ShotContract(
            endpoint=["hold"],
            screen=ScreenIntent(role="aftermath", justification="let the choice land"),
        ),
    )
    project.save_shot(shot)
    picture = picture_contract_payload(shot)
    assert "screen" not in picture
    assert "aftermath" not in " ".join(prompt_contract_sections(shot, {}) .values())
    assert screen_intent_digest(project)
    assert derive_control_view(project, "S001")["screen_context"]["role"] == "aftermath"


def test_v2_scene_and_coverage(tmp_path):
    project = _project(tmp_path)
    scene = SceneContractV2(
        id="SC1", purpose="change", entry_state={},
        direction={"experience": {"beats": [{"id": "XP1", "role": "aftermath", "required": True}] }},
    )
    project.save_scene_contract(scene)
    shot = ShotSpec(id="S001", scene_id="SC1", contract=ShotContract(
        screen=ScreenIntent(role="aftermath", experience_beat_refs=["XP1"], justification="land")
    ))
    project.save_shot(shot)
    project.save_index(project.load_index().model_copy(update={"order": ["S001"]}))
    report = derive_coverage(project, persist=False)
    assert report["screen_experience"]["XP1"]["status"] == "covered"
    assert lint_screen(project)["ok"]


def test_coverage_exception_is_explicitly_excepted(tmp_path):
    project = _project(tmp_path)
    (project.root / "story" / "script.md").write_text(
        "<!-- manju:scene SC1 -->\n"
        "<!-- manju:span SC1-B001 -->\n"
        "A line intentionally omitted from the picture.\n",
        encoding="utf-8",
    )
    project.save_scene_contract(SceneContractV2(
        id="SC1",
        purpose="exception test",
        coverage_exceptions=[{
            "source_span_ref": "script:SC1-B001",
            "disposition": "excepted",
            "reason": "deliberately withheld",
        }],
    ))
    report = derive_coverage(project, persist=False)
    assert report["source_coverage"]["SC1-B001"]["status"] == "excepted"
    assert report["uncovered_source_spans"] == []


def test_uncovered_proof_scene_source_blocks_readiness(tmp_path):
    from tests.fixtures.make_unified_film_project import make_unified_film_project

    project = make_unified_film_project(tmp_path / "proof")
    script = project.root / "story" / "script.md"
    script.write_text(
        script.read_text(encoding="utf-8")
        + "\n<!-- manju:span SC001-B003 -->\nUncovered proof-scene source.\n",
        encoding="utf-8",
    )
    readiness = production_readiness(project)
    gate = next(row for row in readiness["gates"] if row["id"] == "CREATIVE_AUTHORING")
    assert gate["state"] == "fail"
    assert any(detail["code"] == "SOURCE_COVERAGE" for detail in gate["details"])


def test_unknown_experience_beat_is_a_structured_lint_error(tmp_path):
    project = _project(tmp_path)
    (project.root / "story" / "script.md").write_text(
        "<!-- manju:scene SC1 -->\n"
        "<!-- manju:span SC1-B001 -->\n"
        "A line.\n",
        encoding="utf-8",
    )
    project.save_scene_contract(SceneContractV2(id="SC1", purpose="unknown beat test"))
    project.save_shot(ShotSpec(
        id="S001", scene_id="SC1", source_span_refs=["script:SC1-B001"],
        contract=ShotContract(screen=ScreenIntent(
            role="event", experience_beat_refs=["UNKNOWN"], justification="test",
        )),
    ))
    project.save_index(project.load_index().model_copy(update={"order": ["S001"]}))
    report = lint_screen(project)
    assert not report["ok"]
    assert any(row["code"] == "EXPERIENCE_REF_UNKNOWN" for row in report["errors"])


def test_invalid_creative_charter_returns_blocked_readiness(tmp_path):
    project = _project(tmp_path)
    project.save_scene_contract(SceneContractV2(id="SC1", purpose="invalid charter test"))
    project.creative_path.write_text("format: invalid\n", encoding="utf-8")
    readiness = production_readiness(project)
    gate = next(row for row in readiness["gates"] if row["id"] == "CREATIVE_AUTHORING")
    assert gate["state"] == "fail"
    assert any(detail["code"] == "CREATIVE_CHARTER_INVALID" for detail in gate["details"])


def test_truth_patch_is_human_only_and_atomic(tmp_path):
    project = _project(tmp_path)
    action = {"type": "truth_patch_set", "patches": [{"path": "story/script.md", "content": "new"}]}
    proposal = propose(project, [action], actor="ai")
    try:
        confirm(project, proposal.id, actor="ai")
    except DirectorError:
        pass
    else:
        raise AssertionError("AI must not confirm a truth patch")
    confirm(project, proposal.id, actor="human")
    outcome = execute(project, proposal.id, actor="human")
    assert outcome.ok
    assert (project.root / "story" / "script.md").read_text(encoding="utf-8") == "new"
    assert TRUTH_PATCH_SCHEMA


def test_animatic_ai_review_is_provisional():
    review = build_animatic_experience_review(
        path="renders/animatic.mp4", media_sha256="sha256:x", timeline_digest="t",
        screen_intent_digest_value="s", script_sha256="c", temp_audio_digest="a",
        passes={name: {"result": "pass", "findings": []} for name in
                ("causal", "silent_visual", "audio_only", "thumbnail", "duration", "transitions")},
        verdict="approved", actor_kind="fresh_agent",
    )
    assert review["verdict"] == "provisional"


def test_evidence_write_boundaries_are_revalidated(tmp_path):
    from manju.qc.animatic_experience import record_animatic_experience_review
    from manju.qc.artifact_review import build_artifact_review, record_artifact_review

    project = _project(tmp_path)
    target = project.root / "story" / "script.md"
    target.write_text("script", encoding="utf-8")
    review = build_artifact_review(
        target="story/script.md", lenses=["story_engine"], findings=[{}],
        verdict="approved", actor_kind="human",
    )
    record_artifact_review(project, review)
    self_check = dict(review, actor={"kind": "self_check"})
    with pytest.raises(ValueError, match="self_check cannot approve"):
        record_artifact_review(project, self_check)

    media = project.root / "renders" / "animatic.mp4"
    media.write_bytes(b"animatic")
    passes = {
        name: {"result": "pass", "findings": []}
        for name in ("causal", "silent_visual", "audio_only", "thumbnail", "duration", "transitions")
    }
    animatic = build_animatic_experience_review(
        path="renders/animatic.mp4", media_sha256=hash_file(media),
        timeline_digest="timeline", screen_intent_digest_value="screen",
        script_sha256="script", temp_audio_digest="audio", passes=passes,
        verdict="approved", actor_kind="human",
    )
    del animatic["passes"]["thumbnail"]
    with pytest.raises(ValueError, match="missing passes"):
        record_animatic_experience_review(project, animatic)


def test_open_threads_are_derived_without_future_scene_leaks(tmp_path):
    project = _project(tmp_path)
    for scene_id, operation in (("SC1", "create"), ("SC2", "advance"), ("SC3", "close")):
        project.save_scene_contract(SceneContractV2(
            id=scene_id,
            purpose="thread test",
            change={"thread_changes": [{
                "thread_id": "T1", "operation": operation,
                "statement": operation,
            }]},
        ))

    through = open_threads(project, through_scene="SC2")
    assert through["threads"] == [{
        "thread_id": "T1",
        "state": "open",
        "events": [
            {"scene": "SC1", "operation": "create", "statement": "create", "source_span_ref": None},
            {"scene": "SC2", "operation": "advance", "statement": "advance", "source_span_ref": None},
        ],
    }]
    assert open_threads(project)["threads"][0]["state"] == "closed"
