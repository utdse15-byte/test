"""Phase 5: animatic, proof work, and production-readiness gates."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from manju.build import graph
from manju.build.graph import ShotState, animatic_content_key, run_build
from manju.build.readiness import (
    ReadinessError,
    approve_animatic,
    approve_proof_scene,
    current_animatic,
    media_eligibility,
    production_readiness,
    proof_scene_digest,
    proof_shot_status,
)
from manju.core.authoring import SceneContract
from manju.core.hashing import cache_key, hash_file, hash_value, short_hash
from manju.core.models import TakeSidecar, Timeline, TimelineTracks, VideoClip
from manju.core.yamlio import read_yaml, write_yaml
from manju.media.render import _audio_input_hashes, _enc_params


def _scene(project, scene_id="SC001", *, proof_scene=True):
    project.save_scene_contract(SceneContract.model_validate({
        "id": scene_id,
        "title": "Counter scene",
        "location_ref": "convenience_store",
        "time": "night",
        "purpose": "Trust changes",
        "entry_state": {"linxia": {"knowledge": ["the old story"]}},
        "irreversible_change": ["the lie is exposed"],
        "exit_state": {"linxia": {"knowledge": ["the truth"]}},
        "carry_forward": ["Linxia no longer trusts the clerk"],
        "proof_scene": proof_scene,
    }))


def _contract(*, proof_shot=False, endpoint=None):
    return {
        "purpose": "Reveal the coin",
        "viewer_must_perceive": "The date changes the relationship",
        "opening": ["the coin is hidden"],
        "endpoint": endpoint or ["the coin date is visible"],
        "performance": {"required": ["restrained shock"]},
        "physics": {"required": ["coin stays rigid"]},
        "sound": {"cue": "coin click on reveal"},
        "control": {"primary_uncertainty": "hand and coin contact"},
        "risk": {"primary": "hand deformation"},
        "acceptance": {"action_required": True, "min_end_hold_ms": 250},
        "proof_shot": proof_shot,
    }


def _narrative_shot(add_shot, project, shot_id="S001", *, scene_id="SC001",
                    proof_shot=False, endpoint=None):
    return add_shot(
        project,
        shot_id,
        scene_id=scene_id,
        duration=2.0,
        action={"main": "Linxia turns the coin", "emotion": "restrained shock"},
        contract=_contract(proof_shot=proof_shot, endpoint=endpoint),
    )


def _gate(report, gate_id):
    return next(gate for gate in report["gates"] if gate["id"] == gate_id)


def _timeline(shot_id="S001"):
    return Timeline(
        fps=24,
        width=1080,
        height=1920,
        duration_ms=2000,
        tracks=TimelineTracks(video=[VideoClip(
            shot=shot_id,
            take="__slate__",
            source=f"__slate__/{shot_id}",
            start_ms=0,
            duration_ms=2000,
        )]),
    )


def _legacy_animatic_key(project, timeline):
    """The pre-Phase-5 graph.py formula, copied here as a parity oracle."""
    from manju.build.graph import _animatic_shot_still
    from manju.media.audition import slate_path
    from manju.media.render import _toolchain_key_component

    config = project.load_config()
    data = timeline.model_dump()
    rate = config.frame_rate
    rate_key = None if rate.exact_int is not None else str(rate)
    tc_key = _toolchain_key_component(project, config)
    seg_keys = []
    for clip in data["tracks"]["video"]:
        shot = str(clip.get("shot") or "")
        dur = int(clip.get("duration_ms") or 1000)
        still = _animatic_shot_still(project, shot)
        if still is not None:
            kb_key = {
                "still": hash_file(still), "dur": dur, "w": config.width,
                "h": config.height, "fps": config.fps,
                "kind": "animatic_kenburns",
            }
            if rate_key is not None:
                kb_key["rate"] = rate_key
            if tc_key is not None:
                kb_key["toolchain"] = tc_key
            dest = project.root / ".manju" / "animatic" / "clips" / (
                f"{shot}_{short_hash(cache_key(kb_key), 12)}.mp4"
            )
            clip["source"] = project.relpath(dest)
            clip["take"] = "__animatic__"
            seg_keys.append(f"kb:{hash_file(still)}:{dur}")
        else:
            dest = slate_path(
                project, shot, duration_ms=dur, width=config.width,
                height=config.height, fps=config.fps,
            )
            clip["source"] = project.relpath(dest)
            clip["take"] = "__slate__"
            seg_keys.append(f"slate:{shot}:{dur}")
    transformed = Timeline.model_validate(data)
    payload = {
        "segments": seg_keys,
        "timeline": transformed.model_dump(exclude={"meta"}),
        "ass": None,
        "audio": _audio_input_hashes(project, transformed),
        "encoding": _enc_params("final"),
        "target": "animatic",
    }
    if tc_key is not None:
        payload["toolchain"] = tc_key
    return cache_key(payload)


def _plant_current_animatic(project):
    status = current_animatic(project)
    out = project.root / "renders" / "animatic" / "animatic_v1.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"exact-animatic-bytes")
    out.with_suffix(".key.json").write_text(json.dumps({
        "final_key": status["content_key"], "target": "animatic",
    }), encoding="utf-8")
    current = current_animatic(project)
    assert current["current"] is True
    return out, current


def _selected_video(project, shot_id, *, provider="manual_import", approved=True,
                    source_in_ms=0, source_out_ms=None):
    src = project.root / f"_{shot_id}_{provider}.mp4"
    src.write_bytes(f"video:{shot_id}:{provider}".encode())
    take = project.register_take(
        shot_id,
        src,
        TakeSidecar(
            provider=provider,
            spec_hash="manual",
            source_in_ms=source_in_ms,
            source_out_ms=source_out_ms,
        ),
    )
    src.unlink()
    project.update_shot_raw(
        shot_id,
        lambda raw: raw.setdefault("status", {}).update({
            "selected_take": take.name,
            "review": "approved" if approved else "needs_review",
            "approved": approved,
        }),
    )
    return take


def _acceptance_stubs(monkeypatch, *, accepted=True, endpoint=True):
    from manju.build import readiness

    monkeypatch.setattr(
        readiness,
        "compute_assurance",
        lambda project, sid: {
            "assurance_state": "accepted" if accepted else "unknown",
            "expectation_digest": f"expectation:{sid}",
            "reasons": [],
        },
    )
    monkeypatch.setattr(
        readiness,
        "accepted_observed_state",
        lambda project, sid: {
            "status": "current" if accepted else "not_accepted",
            "observed_endpoint": ([{"position": "END", "value": "done"}]
                                  if endpoint else []),
        },
    )


# ---------------------------------------------------------------- animatic key


def test_legacy_animatic_content_key_is_byte_identical(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    timeline = _timeline()
    assert animatic_content_key(tmp_project, timeline) == _legacy_animatic_key(
        tmp_project, timeline
    )


def test_narrative_animatic_intent_boundary(tmp_project, add_shot):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    timeline = _timeline()
    first = animatic_content_key(tmp_project, timeline)

    raw = tmp_project.load_shot_raw("S001")
    raw["contract"]["purpose"] = "different director purpose"
    raw["contract"]["viewer_must_perceive"] = "different note"
    raw["contract"]["risk"] = {"primary": "different risk"}
    raw["contract"]["proof_shot"] = True
    write_yaml(tmp_project.shot_path("S001"), raw)
    assert animatic_content_key(tmp_project, timeline) == first

    raw["contract"]["endpoint"] = ["a different visible endpoint"]
    write_yaml(tmp_project.shot_path("S001"), raw)
    assert animatic_content_key(tmp_project, timeline) != first


# --------------------------------------------------------------- base gates


def test_legacy_project_is_not_gated(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    report = production_readiness(tmp_project, paid_shot_ids=["S001"])
    assert report["state"] == report["stage"] == "LEGACY"
    assert report["allowed_paid_shot_ids"] == "all"
    assert report["disallowed_paid_shot_ids"] == []
    assert report["bulk_paid_generation_allowed"] is True
    assert report["eligibility"]["counts"]["none"] == 1
    assert report["eligibility"]["picture_lock_eligible"] is False


def test_media_eligibility_distinguishes_proxy_candidate_and_final(
        tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    add_shot(tmp_project, "S003")
    _selected_video(tmp_project, "S001", provider="caption_card")
    _selected_video(tmp_project, "S002", approved=False)
    _selected_video(tmp_project, "S003")
    _acceptance_stubs(monkeypatch)

    view = media_eligibility(tmp_project)
    by_shot = {row["shot"]: row for row in view["shots"]}
    assert by_shot["S001"]["state"] == "proxy-only"
    assert by_shot["S002"]["state"] == "candidate"
    assert by_shot["S003"]["state"] == "final-eligible"
    assert by_shot["S001"]["picture_lock_eligible"] is False
    assert by_shot["S003"]["picture_lock_eligible"] is True
    assert view["counts"] == {
        "proxy-only": 1, "candidate": 1, "final-eligible": 1, "none": 0,
    }
    assert view["picture_lock_eligible"] is False


def test_proxy_final_does_not_imply_picture_lock(
        tmp_project, add_shot, monkeypatch):
    from manju.build.status import project_status

    add_shot(tmp_project, "S001")
    _selected_video(tmp_project, "S001", provider="caption_card")
    _acceptance_stubs(monkeypatch)
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.final_dir / "final_v1.mp4").write_bytes(b"pipeline-output")
    tmp_project.timeline_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_project.timeline_path.write_text(_timeline().model_dump_json(), encoding="utf-8")

    info = project_status(tmp_project)
    assert info["latest_final"] == "renders/final/final_v1.mp4"
    assert info["production"]["eligibility"]["counts"]["proxy-only"] == 1
    assert info["production"]["eligibility"]["picture_lock_eligible"] is False
    assert info["next_step_key"] == "proxy_only"
    assert "proxy-only" in info["next_step"]
    assert "Picture Lock" in info["next_step"]


def test_proxy_notice_does_not_hide_a_more_urgent_qc_failure(
        tmp_project, add_shot, monkeypatch):
    from manju.build.status import project_status

    add_shot(tmp_project, "S001")
    take = _selected_video(tmp_project, "S001", provider="caption_card")
    _acceptance_stubs(monkeypatch)
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.final_dir / "final_v1.mp4").write_bytes(b"pipeline-output")
    tmp_project.timeline_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_project.timeline_path.write_text(_timeline().model_dump_json(), encoding="utf-8")
    tmp_project.reports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.reports_dir / "qc.json").write_text(json.dumps({
        "ok": False,
        "items": [{"level": "error", "shot": "S001", "message": "bad frame"}],
    }), encoding="utf-8")
    statuses = [SimpleNamespace(
        shot_id="S001",
        state=ShotState.FRESH,
        note=None,
        selected_take=take.name,
        take=take,
    )]
    monkeypatch.setattr("manju.build.status.next_actions", lambda *args, **kwargs: [])

    info = project_status(tmp_project, statuses=statuses)
    assert info["production"]["eligibility"]["counts"]["proxy-only"] == 1
    assert info["next_step_key"] == "fix_qc"
    assert "reports/qc.md" in info["next_step"]


def test_scene_contract_gate(tmp_project, add_shot):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    raw = read_yaml(tmp_project.scene_contract_path("SC001"))
    raw["irreversible_change"] = []
    write_yaml(tmp_project.scene_contract_path("SC001"), raw)
    report = production_readiness(tmp_project)
    assert _gate(report, "SCENE_CONTRACTS")["state"] == "fail"
    assert report["stage"] == "AUTHORING"


def test_malformed_scene_cannot_fall_back_to_legacy(tmp_project, add_shot):
    _narrative_shot(add_shot, tmp_project)
    (tmp_project.scene_contracts_dir / "SC001.yaml").write_text(
        "id: SC001\nentry_state: [\n", encoding="utf-8"
    )
    report = production_readiness(tmp_project)
    assert report["opted_in"] is True
    assert report["stage"] == "AUTHORING"
    assert _gate(report, "SCENE_CONTRACTS")["state"] == "fail"


def test_shot_contract_gate(tmp_project, add_shot):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    raw = tmp_project.load_shot_raw("S001")
    raw["contract"]["endpoint"] = []
    write_yaml(tmp_project.shot_path("S001"), raw)
    report = production_readiness(tmp_project)
    assert _gate(report, "SHOT_CONTRACTS")["state"] == "fail"
    assert "S001" in str(_gate(report, "SHOT_CONTRACTS")["details"])


def test_current_animatic_gate(tmp_project, add_shot):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    report = production_readiness(tmp_project)
    assert _gate(report, "ANIMATIC_CURRENT")["state"] == "fail"
    _plant_current_animatic(tmp_project)
    report = production_readiness(tmp_project)
    assert _gate(report, "ANIMATIC_CURRENT")["state"] == "pass"


# ------------------------------------------------------------- approvals


def test_animatic_exact_byte_approval(tmp_project, add_shot):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    out, current = _plant_current_animatic(tmp_project)
    approved = approve_animatic(tmp_project, str(out), reason="rhythm works")
    assert approved["event"]["artifact"]["sha256"] == hash_file(out)
    assert approved["event"]["artifact"]["bytes"] == out.stat().st_size
    assert approved["event"]["animatic_content_key"] == current["content_key"]
    assert _gate(production_readiness(tmp_project), "ANIMATIC_APPROVED")["state"] == "pass"

    out.write_bytes(b"tampered-after-approval")
    assert _gate(production_readiness(tmp_project), "ANIMATIC_APPROVED")["state"] == "fail"


def test_stale_animatic_approval(tmp_project, add_shot):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    out, _ = _plant_current_animatic(tmp_project)
    approve_animatic(tmp_project, str(out), reason="old rhythm")
    tmp_project.update_shot_raw(
        "S001",
        lambda raw: raw["contract"].__setitem__("endpoint", ["new endpoint"]),
    )
    report = production_readiness(tmp_project)
    assert _gate(report, "ANIMATIC_CURRENT")["state"] == "fail"
    assert _gate(report, "ANIMATIC_APPROVED")["state"] == "fail"


@pytest.mark.parametrize("kwargs", [
    {"actor_kind": "ai"},
    {"actor_kind": "human", "unattended": True},
])
def test_human_only_approval(tmp_project, add_shot, kwargs):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    out, _ = _plant_current_animatic(tmp_project)
    with pytest.raises(ReadinessError, match="human"):
        approve_animatic(tmp_project, str(out), reason="no", **kwargs)


# --------------------------------------------------------------- proof shots


def test_proof_shot_requires_current_video(tmp_project, add_shot, monkeypatch):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, proof_shot=True)
    _acceptance_stubs(monkeypatch)
    assert proof_shot_status(tmp_project, "S001")["ready"] is False

    take = _selected_video(tmp_project, "S001")
    assert proof_shot_status(tmp_project, "S001")["ready"] is True
    sidecar = read_yaml(take.sidecar_path)
    sidecar["spec_hash"] = "sha256:stale"
    sidecar["spec_version"] = 3
    write_yaml(take.sidecar_path, sidecar)
    assert proof_shot_status(tmp_project, "S001")["ready"] is False


def test_proof_shot_rejects_proxy(tmp_project, add_shot, monkeypatch):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, proof_shot=True)
    _selected_video(tmp_project, "S001", provider="caption_card")
    _acceptance_stubs(monkeypatch)
    row = proof_shot_status(tmp_project, "S001")
    assert row["ready"] is False
    assert "proxy" in str(row["reasons"]).lower()


def test_proof_shot_requires_human_approval(tmp_project, add_shot, monkeypatch):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, proof_shot=True)
    _selected_video(tmp_project, "S001", approved=False)
    _acceptance_stubs(monkeypatch)
    row = proof_shot_status(tmp_project, "S001")
    assert row["ready"] is False
    assert "review" in str(row["reasons"]).lower()


def test_proof_shot_requires_assurance_acceptance(tmp_project, add_shot, monkeypatch):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, proof_shot=True)
    _selected_video(tmp_project, "S001")
    _acceptance_stubs(monkeypatch, accepted=False)
    row = proof_shot_status(tmp_project, "S001")
    assert row["ready"] is False
    assert row["assurance_state"] == "unknown"


# -------------------------------------------------------------- proof scenes


def _proof_scene_project(project, add_shot, monkeypatch):
    _scene(project)
    _narrative_shot(add_shot, project, "S001", proof_shot=True)
    _narrative_shot(add_shot, project, "S002")
    _selected_video(project, "S001")
    _selected_video(project, "S002")
    _acceptance_stubs(monkeypatch)


def test_proof_scene_requires_all_indexed_shots(tmp_project, add_shot, monkeypatch):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, "S001", proof_shot=True)
    _narrative_shot(add_shot, tmp_project, "S002")
    _selected_video(tmp_project, "S001")
    _acceptance_stubs(monkeypatch)
    report = production_readiness(tmp_project)
    details = _gate(report, "PROOF_SCENES")["details"]
    assert _gate(report, "PROOF_SCENES")["state"] == "fail"
    assert "S002" in str(details)


def test_proof_scene_requires_current_digest_approval(tmp_project, add_shot, monkeypatch):
    _proof_scene_project(tmp_project, add_shot, monkeypatch)
    before = production_readiness(tmp_project)
    assert _gate(before, "PROOF_SCENES")["state"] == "fail"
    approved = approve_proof_scene(tmp_project, "SC001", reason="continuity works")
    digest = approved["event"]["proof_scene_digest"]
    assert digest == proof_scene_digest(tmp_project, "SC001")
    assert _gate(production_readiness(tmp_project), "PROOF_SCENES")["state"] == "pass"

    take = tmp_project.get_take("S002", tmp_project.load_shot("S002").status.selected_take)
    raw = read_yaml(take.sidecar_path)
    raw["source_in_ms"] = 100
    raw["source_out_ms"] = 800
    write_yaml(take.sidecar_path, raw)
    assert proof_scene_digest(tmp_project, "SC001") != digest
    assert _gate(production_readiness(tmp_project), "PROOF_SCENES")["state"] == "fail"


def test_proof_scene_digest_changes_with_take_trim_order_contract_and_audio(
        tmp_project, add_shot, monkeypatch):
    _proof_scene_project(tmp_project, add_shot, monkeypatch)
    base = proof_scene_digest(tmp_project, "SC001")

    take = tmp_project.get_take("S001", tmp_project.load_shot("S001").status.selected_take)
    take.media_path.write_bytes(b"different-exact-media")
    media_changed = proof_scene_digest(tmp_project, "SC001")
    assert media_changed != base
    take.media_path.write_bytes(b"video:S001:manual_import")

    sidecar = read_yaml(take.sidecar_path)
    sidecar["source_in_ms"] = 100
    sidecar["source_out_ms"] = 900
    write_yaml(take.sidecar_path, sidecar)
    trim_changed = proof_scene_digest(tmp_project, "SC001")
    assert trim_changed != base
    sidecar["source_in_ms"] = 0
    sidecar["source_out_ms"] = None
    write_yaml(take.sidecar_path, sidecar)

    index = tmp_project.load_index()
    index.order = list(reversed(index.order))
    tmp_project.save_index(index)
    order_changed = proof_scene_digest(tmp_project, "SC001")
    assert order_changed != base
    index.order = list(reversed(index.order))
    tmp_project.save_index(index)

    raw = tmp_project.load_shot_raw("S001")
    raw["contract"]["endpoint"] = ["changed scene endpoint"]
    write_yaml(tmp_project.shot_path("S001"), raw)
    contract_changed = proof_scene_digest(tmp_project, "SC001")
    assert contract_changed != base
    raw["contract"]["endpoint"] = ["the coin date is visible"]
    write_yaml(tmp_project.shot_path("S001"), raw)

    raw["dialogue"]["text"] = "A differently timed line."
    raw["contract"]["sound"]["cue"] = "a later metal click"
    write_yaml(tmp_project.shot_path("S001"), raw)
    assert proof_scene_digest(tmp_project, "SC001") != base


# ---------------------------------------------------------- build enforcement


def _readiness_stub(stage, allowed):
    def derive(project, paid_shot_ids=None):
        planned = list(dict.fromkeys(paid_shot_ids or []))
        allow = list(allowed)
        disallowed = [sid for sid in planned if sid not in allow]
        return {
            "schema": "manju.production-readiness/v1",
            "opted_in": True,
            "state": "READY" if stage == "BULK_READY" else "BLOCKED",
            "stage": stage,
            "gates": [{"id": "ANIMATIC_APPROVED", "state": "fail",
                       "blocking": True, "details": []}],
            "failed_gates": ([] if stage == "BULK_READY" else ["ANIMATIC_APPROVED"]),
            "proof_shots": ["S001"],
            "proof_scenes": ["SC001"],
            "allowed_paid_shot_ids": allow,
            "disallowed_paid_shot_ids": disallowed,
            "bulk_paid_generation_allowed": stage == "BULK_READY",
            "next_action": "complete the next proof gate",
        }
    return derive


@pytest.fixture
def paid_plan(monkeypatch):
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (5.0, "CNY"))


@pytest.fixture
def transport(monkeypatch):
    from manju.providers import registry

    calls = []

    def fake(req, chain=None, **kwargs):
        calls.append(req.shot.id)
        return []

    monkeypatch.setattr(registry, "generate_with_fallback", fake)
    return calls


def test_unattended_authoring_stage_calls_no_transport(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    from manju.build import readiness
    monkeypatch.setattr(readiness, "production_readiness", _readiness_stub("AUTHORING", []))
    result = run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
                       agent_profile="unattended")
    assert result.ok is False and transport == []
    assert result.readiness["error"]["code"] == "PRODUCTION_READINESS_REQUIRED"
    assert result.readiness["error"]["stage"] == "AUTHORING"


def test_proof_shot_stage_allows_only_proof_shots(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, "S001", proof_shot=True)
    from manju.build import readiness
    monkeypatch.setattr(
        readiness, "production_readiness",
        _readiness_stub("PROOF_SHOT_READY", ["S001"]),
    )
    run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
              agent_profile="unattended")
    assert transport == ["S001"]


def test_proof_scene_stage_allows_only_scene_shots(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, "S001")
    from manju.build import readiness
    monkeypatch.setattr(
        readiness, "production_readiness",
        _readiness_stub("PROOF_SCENE_READY", ["S001"]),
    )
    run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
              agent_profile="unattended")
    assert transport == ["S001"]


def test_mixed_allowed_disallowed_plan_is_atomic_block(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project, "S001", proof_shot=True)
    _narrative_shot(add_shot, tmp_project, "S002")
    from manju.build import readiness
    monkeypatch.setattr(
        readiness, "production_readiness",
        _readiness_stub("PROOF_SHOT_READY", ["S001"]),
    )
    result = run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
                       agent_profile="unattended")
    assert result.ok is False and transport == []
    assert result.readiness["error"]["allowed_shot_ids"] == ["S001"]
    assert result.readiness["error"]["disallowed_shot_ids"] == ["S002"]


def test_unattended_bulk_ready_can_call_transport(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    from manju.build import readiness
    monkeypatch.setattr(
        readiness, "production_readiness", _readiness_stub("BULK_READY", ["S001"])
    )
    run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
              agent_profile="unattended")
    assert transport == ["S001"]


def test_collaborative_warns_and_can_continue(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    from manju.build import readiness
    monkeypatch.setattr(readiness, "production_readiness", _readiness_stub("AUTHORING", []))
    result = run_build(tmp_project, target="qc", actor="human", assume_yes=True,
                       agent_profile="collaborative")
    assert transport == ["S001"]
    assert any("production readiness" in warning.lower() for warning in result.warnings)
    assert not any("PRODUCTION_READINESS_REQUIRED" in error for error in result.errors)


def test_dry_run_reports_without_transport(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    from manju.build import readiness
    monkeypatch.setattr(readiness, "production_readiness", _readiness_stub("AUTHORING", []))
    result = run_build(tmp_project, dry_run=True, actor="ai", agent_profile="unattended")
    assert result.ok is True and transport == []
    assert result.readiness["stage"] == "AUTHORING"


def test_free_local_not_blocked(tmp_project, add_shot, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    result = run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
                       agent_profile="unattended")
    assert not any("PRODUCTION_READINESS_REQUIRED" in e for e in result.errors)


def test_assume_yes_cannot_bypass(
        tmp_project, add_shot, monkeypatch, paid_plan, transport):
    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    from manju.build import readiness
    monkeypatch.setattr(readiness, "production_readiness", _readiness_stub("AUTHORING", []))
    result = run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
                       agent_profile="unattended")
    assert result.ok is False and transport == []
    assert result.readiness["error"]["code"] == "PRODUCTION_READINESS_REQUIRED"


# ---------------------------------------------------------------------- CLI


def test_production_status_cli_json(tmp_project, add_shot, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    _scene(tmp_project)
    _narrative_shot(add_shot, tmp_project)
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["production", "status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "manju.production-readiness/v1"
    assert payload["stage"] == "AUTHORING"


def test_production_approval_cli_has_no_yes_bypass():
    from typer.testing import CliRunner

    from manju.cli import app

    result = CliRunner().invoke(
        app, ["production", "approve-animatic", "x.mp4", "--reason", "x", "--yes"]
    )
    assert result.exit_code != 0
    assert "No such option" in result.output
