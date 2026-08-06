"""Phase 7A: no-paid code/path rehearsal, never real production proof."""

from __future__ import annotations

import json

from manju.build.readiness import (
    approve_animatic,
    approve_proof_scene,
    current_animatic,
    media_eligibility,
    production_readiness,
)
from manju.build.attempts import read_attempts
from manju.core.authoring import SceneContract
from manju.core.container import Project
from manju.core.models import ProbeInfo, TakeSidecar
from manju.qc.agent_review import qc_brief, read_v2_records, record_verdicts
from manju.qc.assurance import compute_assurance
from manju.qc.checks import QCReport
from manju.qc.production import (
    accepted_observed_state,
    candidate_families,
    repair_route,
)


def _scene(project: Project) -> None:
    project.save_scene_contract(SceneContract.model_validate({
        "id": "SC001",
        "title": "Coin reveal",
        "location_ref": "convenience_store",
        "time": "night",
        "purpose": "The relationship changes when the coin is identified",
        "entry_state": {"linxia": {"knowledge": ["the date is hidden"]}},
        "irreversible_change": ["Linxia sees the date on the coin"],
        "exit_state": {"linxia": {"knowledge": ["the date proves the lie"]}},
        "carry_forward": ["Linxia no longer trusts the clerk"],
        "proof_scene": True,
    }))


def _contract(endpoint: str) -> dict:
    return {
        "purpose": "Reveal the evidence that changes the relationship",
        "viewer_must_perceive": "The hand action and the coin state are legible",
        "opening": ["the coin starts hidden in the open hand"],
        "endpoint": [endpoint],
        "performance": {
            "required": ["restrained realization"],
            "avoid": ["celebratory gesture"],
        },
        "physics": {
            "required": ["the rigid coin stays in the hand"],
            "avoid": ["the coin bends"],
        },
        "sound": {"cue": "one coin click"},
        "control": {
            "production_method": "manual",
            "motion_source": "manual",
            "primary_uncertainty": "whether the endpoint reads before the cut",
        },
        "risk": {
            "primary": "the hand may occlude the date",
            "fallback_staging": "hold the closed hand as the new endpoint",
        },
        "acceptance": {"action_required": True, "min_end_hold_ms": 200},
        "proof_shot": True,
    }


def _register_fixture_take(project: Project, shot_id: str, label: str, *,
                           provider: str = "manual_import"):
    source = project.root / f"_{shot_id}_{label}.mp4"
    source.write_bytes(f"synthetic-fixture:{shot_id}:{label}".encode("ascii"))
    take = project.register_take(
        shot_id,
        source,
        TakeSidecar(
            provider=provider,
            spec_hash="manual",
            probe=ProbeInfo(duration_ms=1000),
        ),
    )
    source.unlink()
    project.update_shot_raw(
        shot_id,
        lambda raw: raw.setdefault("status", {}).update({
            "selected_take": take.name,
            "review": "approved",
            "approved": True,
        }),
    )
    return take


def _plant_current_animatic(project: Project, version: int):
    expected = current_animatic(project)
    assert expected["content_key"] and not expected["current"]
    out = project.root / "renders" / "animatic" / f"animatic_v{version}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(f"synthetic-animatic-v{version}".encode("ascii"))
    out.with_suffix(".key.json").write_text(json.dumps({
        "final_key": expected["content_key"],
        "target": "animatic",
    }), encoding="utf-8")
    current = current_animatic(project)
    assert current["current"] is True
    return out


def _review_row(project: Project, shot_id: str) -> dict:
    brief = qc_brief(project, [shot_id])
    return next(row for row in brief["shots"] if row["shot"] == shot_id)


def _observations(row: dict, *, fail_endpoint: bool = False) -> list[dict]:
    observations = []
    endpoint_failed = False
    for expectation in row["expectations"]:
        observed = "present" if expectation["polarity"] == "present" else "absent"
        if fail_endpoint and "/contract/endpoint/" in expectation["source_path"]:
            observed = "absent"
            endpoint_failed = True
        observations.append({
            "expectation_id": expectation["id"],
            "observed": observed,
            "evidence_refs": ["fixture:end"],
        })
    if fail_endpoint:
        assert endpoint_failed, "the rehearsal must fail an authored endpoint expectation"
    return observations


def _verdict(row: dict, *, observations: list[dict], decision: dict,
             observed_endpoint: str) -> dict:
    return {
        "schema": "manju.qc.verdict/v2",
        "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": observations,
        "findings": [],
        "reviewer": {"kind": "human", "name": "phase7a-fixture-reviewer"},
        "decision": decision,
        "observed_states": [{
            "dimension": "POSE_ACTION_PHASE",
            "position": "END",
            "value": observed_endpoint,
            "visibility": "VISIBLE",
            "evidence_refs": ["fixture:end"],
            "confidence": 1.0,
        }],
    }


def _gate(report: dict, gate_id: str) -> dict:
    return next(gate for gate in report["gates"] if gate["id"] == gate_id)


def test_no_paid_narrative_loop_reaches_bulk_ready_without_claiming_production(
        tmp_project, add_shot, monkeypatch):
    # Synthetic bytes intentionally do not pretend to be viewable media. The
    # rehearsal exercises review/readiness plumbing with explicit fixture QC.
    monkeypatch.setattr(
        "manju.qc.agent_review._shot_frames",
        lambda project, take: {"first": None, "mid": None, "last": None},
    )
    monkeypatch.setattr(
        "manju.qc.checks.run_qc",
        lambda *args, **kwargs: QCReport(),
    )

    _scene(tmp_project)
    add_shot(
        tmp_project,
        "S001",
        scene_id="SC001",
        duration=1.0,
        action={"main": "Linxia opens her hand to reveal the coin"},
        contract=_contract("the engraved date is visible before the cut"),
    )
    assert tmp_project.narrative_opted_in is True
    assert tmp_project.load_shot("S001").contract is not None

    animatic_v1 = _plant_current_animatic(tmp_project, 1)
    approve_animatic(tmp_project, str(animatic_v1), reason="fixture rhythm accepted")
    before_proof = production_readiness(tmp_project)
    assert _gate(before_proof, "ANIMATIC_APPROVED")["state"] == "pass"
    assert before_proof["stage"] == "PROOF_SHOT_READY"

    first_take = _register_fixture_take(tmp_project, "S001", "occluded")
    first_row = _review_row(tmp_project, "S001")
    experiment = {
        "hypothesis": "holding the hand open makes the engraved date readable",
        "tested_variable": "endpoint",
        "held_constant": ["camera", "lighting", "duration"],
        "expected_result": "the engraved date is visible before the cut",
        "observed_result": "the fingers close and hide the engraved date",
        "usable_ranges_ms": [{"start_ms": 0, "end_ms": 700}],
        "next_test": "rewrite the endpoint to the closed hand and hold it",
    }
    record_verdicts(tmp_project, _verdict(
        first_row,
        observations=_observations(first_row, fail_endpoint=True),
        decision={
            "disposition": "REWRITE_SOURCE",
            "primary_repair_variable": "endpoint",
            "diagnostic_isolation": True,
            "reason": "the authored endpoint did not occur in the fixture candidate",
            "experiment": experiment,
        },
        observed_endpoint="the fingers close around the coin and hide the date",
    ))
    assert compute_assurance(tmp_project, "S001")["assurance_state"] == "rejected"
    [first_record], malformed = read_v2_records(tmp_project)
    assert malformed == 0
    assert first_record["decision"]["experiment"] == experiment
    assert first_record["observed_states"][0]["value"].startswith("the fingers close")
    assert repair_route("REWRITE_SOURCE")["path"] == "director_proposal"

    # Execute the explicit source rewrite. The old media-bound experiment stays
    # history and the exact-byte Animatic approval must no longer be current.
    tmp_project.update_shot_raw(
        "S001",
        lambda raw: (
            raw["action"].__setitem__("main", "Linxia closes her hand around the coin"),
            raw["contract"].__setitem__(
                "endpoint", ["the closed hand holds long enough to read the decision"]
            ),
        ),
    )
    after_rewrite = production_readiness(tmp_project)
    assert after_rewrite["stage"] == "AUTHORING"
    assert _gate(after_rewrite, "ANIMATIC_CURRENT")["state"] == "fail"
    first_member = next(
        member
        for family in candidate_families(tmp_project, "S001")["families"]
        for member in family["takes"]
        if member["take"] == first_take.name
    )
    assert first_member["experiment"] is None
    assert first_member["historical_experiment"] == experiment
    assert first_member["experiment_binding_status"] == "stale"

    animatic_v2 = _plant_current_animatic(tmp_project, 2)
    approve_animatic(tmp_project, str(animatic_v2), reason="rewritten fixture rhythm accepted")
    second_take = _register_fixture_take(tmp_project, "S001", "closed-hand")
    second_row = _review_row(tmp_project, "S001")
    record_verdicts(tmp_project, _verdict(
        second_row,
        observations=_observations(second_row),
        decision={"disposition": "KEEP"},
        observed_endpoint="the closed hand holds through the cut",
    ))

    assurance = compute_assurance(tmp_project, "S001")
    observed = accepted_observed_state(tmp_project, "S001")
    assert assurance["assurance_state"] == "accepted"
    assert observed["status"] == "current"
    assert observed["media_sha256"] == second_row["media"]["sha256"]
    assert observed["observed_endpoint"][0]["value"] == (
        "the closed hand holds through the cut"
    )
    assert media_eligibility(tmp_project)["shots"][0]["state"] == "final-eligible"

    before_scene_approval = production_readiness(tmp_project)
    assert before_scene_approval["stage"] == "PROOF_SCENE_READY"
    assert _gate(before_scene_approval, "PROOF_SHOTS")["state"] == "pass"
    assert _gate(before_scene_approval, "PROOF_SCENES")["state"] == "fail"
    approve_proof_scene(tmp_project, "SC001", reason="fixture digest path accepted")
    complete = production_readiness(tmp_project)
    assert complete["stage"] == "BULK_READY"
    assert complete["bulk_paid_generation_allowed"] is True
    assert complete["eligibility"]["picture_lock_eligible"] is True
    attempts, malformed_attempts = read_attempts(tmp_project)
    assert attempts == []
    assert malformed_attempts == 0


def test_legacy_proxy_path_remains_ungated_but_never_picture_lock(
        tmp_path, add_shot):
    legacy = Project.create(tmp_path / "phase7a-legacy", git_init=False)
    add_shot(legacy, "S001", duration=1.0, characters=[], dialogue={})
    _register_fixture_take(legacy, "S001", "proxy", provider="caption_card")

    report = production_readiness(legacy, paid_shot_ids=["S001"])
    assert legacy.narrative_opted_in is False
    assert report["stage"] == "LEGACY"
    assert report["allowed_paid_shot_ids"] == "all"
    assert report["bulk_paid_generation_allowed"] is True
    assert report["eligibility"]["counts"]["proxy-only"] == 1
    assert report["eligibility"]["picture_lock_eligible"] is False
