"""Offline regressions for static-audit Wave 3 review/proof consistency."""

from __future__ import annotations

import json
from copy import deepcopy

from manju.build import readiness
from manju.build.readiness import (
    approve_proof_scene,
    current_proof_scene_approval,
    proof_scene_digest,
)
from manju.core.authoring import SceneContract
from manju.core.models import TakeSidecar
from manju.qc.agent_review import (
    _current_expectation_digest,
    _current_selected_media,
    _current_spec_hash,
)
from manju.qc.assurance import compute_assurance, stable_evidence_digest
from manju.qc.production import accepted_observed_state

V2 = "manju.qc.verdict/v2"


def _selected_take(project, shot_id: str) -> None:
    source = project.root / f"_{shot_id}.mp4"
    source.write_bytes(f"offline-media:{shot_id}".encode("ascii"))
    take = project.register_take(
        shot_id, source, TakeSidecar(provider="manual_import", spec_hash="manual")
    )
    source.unlink()
    project.update_shot_raw(
        shot_id,
        lambda raw: raw.setdefault("status", {}).update(
            {"selected_take": take.name, "review": "approved", "approved": True}
        ),
    )


def _record(
    project,
    shot_id: str,
    *,
    packet_id: str,
    observed: str,
    observed_states: list[dict] | None = None,
    include_observed_states: bool = True,
    ts: str = "2026-08-06T00:00:00+00:00",
) -> dict:
    media_path, media_sha = _current_selected_media(project, shot_id)
    from manju.qc.expectations import compile_expectations

    expectations = compile_expectations(project, shot_id)["expectations"]
    row = {
        "ts": ts,
        "actor": "offline-test",
        "schema": V2,
        "binding": "bound",
        "packet_id": packet_id,
        "subject": {"kind": "shot", "id": shot_id},
        "spec_hash": _current_spec_hash(project, shot_id),
        "expectation_digest": _current_expectation_digest(project, shot_id),
        "media_sha256": media_sha,
        "media_project_path": media_path,
        "observations": [
            {"expectation_id": item["id"], "observed": observed}
            for item in expectations
        ],
        "findings": [],
        "reviewer": {"kind": "model_visual", "name": "offline"},
    }
    if include_observed_states:
        row["observed_states"] = list(observed_states or [])
    return row


def _append_record(project, row: dict) -> None:
    path = project.reports_dir / "qc_agent.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _review_project(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", quality={"must_show": ["the red umbrella appears"]})
    _selected_take(tmp_project, "S001")
    monkeypatch.setattr(
        "manju.qc.assurance._shot_qc_status",
        lambda project, shot_id, report=None: ("pass", None),
    )


def test_accepted_state_never_uses_observed_states_from_older_record(
    tmp_project, add_shot, monkeypatch
):
    _review_project(tmp_project, add_shot, monkeypatch)
    old_endpoint = {
        "dimension": "POSE_ACTION_PHASE",
        "position": "END",
        "value": "old rejected endpoint",
        "visibility": "VISIBLE",
        "evidence_refs": ["frame:end"],
        "confidence": 0.9,
    }
    _append_record(
        tmp_project,
        _record(
            tmp_project,
            "S001",
            packet_id="pkt_old_rejected",
            observed="absent",
            observed_states=[old_endpoint],
        ),
    )
    newest = _record(
        tmp_project,
        "S001",
        packet_id="pkt_new_accepted",
        observed="present",
        include_observed_states=False,
    )
    _append_record(tmp_project, newest)

    assurance = compute_assurance(tmp_project, "S001")
    state = accepted_observed_state(tmp_project, "S001")

    assert assurance["assurance_state"] == "accepted"
    assert state["status"] == "current"
    assert state["observed_opening"] == []
    assert state["observed_endpoint"] == []
    assert state["packet_id"] == assurance["evidence"]["packet_id"]
    assert state["packet_id"] == "pkt_new_accepted"
    assert state["evidence_digest"] == assurance["evidence"]["evidence_digest"]
    assert state["evidence_digest"] == stable_evidence_digest(newest)


def test_rejected_record_observed_endpoint_never_becomes_current(
    tmp_project, add_shot, monkeypatch
):
    _review_project(tmp_project, add_shot, monkeypatch)
    rejected = _record(
        tmp_project,
        "S001",
        packet_id="pkt_rejected",
        observed="absent",
        observed_states=[{
            "dimension": "POSE_ACTION_PHASE",
            "position": "END",
            "value": "rejected endpoint",
            "visibility": "VISIBLE",
            "evidence_refs": [],
            "confidence": 1.0,
        }],
    )
    _append_record(tmp_project, rejected)

    state = accepted_observed_state(tmp_project, "S001")

    assert state["assurance_state"] == "rejected"
    assert state["status"] == "not_accepted"
    assert state["packet_id"] is None
    assert state["observed_endpoint"] == []


def test_stable_evidence_digest_excludes_dynamic_timestamps():
    first = {
        "ts": "2026-08-06T00:00:00+00:00",
        "reviewed_at": "2026-08-06T00:00:01+00:00",
        "packet_id": "pkt_same",
        "observations": [{"expectation_id": "e1", "observed": "present"}],
    }
    second = deepcopy(first)
    second["ts"] = "2026-08-07T12:00:00+00:00"
    second["reviewed_at"] = "2026-08-07T12:00:01+00:00"

    assert stable_evidence_digest(first) == stable_evidence_digest(second)


def _proof_project(project, add_shot):
    project.save_scene_contract(SceneContract.model_validate({
        "id": "SC001",
        "title": "Offline proof scene",
        "location_ref": "convenience_store",
        "time": "night",
        "purpose": "bind accepted evidence",
        "entry_state": {},
        "irreversible_change": ["the reveal happens"],
        "exit_state": {},
        "carry_forward": [],
        "proof_scene": True,
    }))
    add_shot(
        project,
        "S001",
        scene_id="SC001",
        duration=2.0,
        contract={
            "purpose": "show the reveal",
            "viewer_must_perceive": "the reveal is legible",
            "opening": ["the object is hidden"],
            "endpoint": ["the object is visible"],
            "performance": {"required": []},
            "physics": {"required": []},
            "sound": {},
            "control": {"primary_uncertainty": "visibility"},
            "risk": {"primary": "occlusion"},
            "acceptance": {"action_required": True, "min_end_hold_ms": 250},
            "proof_shot": True,
        },
    )
    _selected_take(project, "S001")


def _accepted_projection(*, opening="opening A", endpoint="endpoint A") -> dict:
    assurance_projection = {
        "schema": "manju.qc.assurance/stable-v1",
        "subject": {"kind": "shot", "id": "S001"},
        "assurance_state": "accepted",
        "evidence": {
            "packet_id": "pkt_accepted",
            "evidence_digest": "sha256:evidence-a",
        },
    }
    return {
        "status": "current",
        "assurance_state": "accepted",
        "packet_id": "pkt_accepted",
        "evidence_digest": "sha256:evidence-a",
        "observed_opening": [{"position": "START", "value": opening}],
        "observed_endpoint": [{"position": "END", "value": endpoint}],
        "assurance_projection": assurance_projection,
        "assurance_digest": "sha256:assurance-a",
    }


def test_new_accepted_opening_or_endpoint_invalidates_proof_scene_approval(
    tmp_project, add_shot, monkeypatch
):
    _proof_project(tmp_project, add_shot)
    projection = _accepted_projection()
    monkeypatch.setattr(readiness, "accepted_observed_state", lambda project, sid: projection)
    monkeypatch.setattr(
        readiness,
        "proof_shot_status",
        lambda project, sid: {"shot": sid, "ready": True, "reasons": []},
    )
    base = proof_scene_digest(tmp_project, "SC001")
    approval = approve_proof_scene(tmp_project, "SC001", reason="offline review")
    assert approval["event"]["proof_scene_digest"] == base
    assert current_proof_scene_approval(tmp_project, "SC001")["approved"] is True

    projection["observed_endpoint"] = [{"position": "END", "value": "endpoint B"}]
    assert proof_scene_digest(tmp_project, "SC001") != base
    assert current_proof_scene_approval(tmp_project, "SC001")["approved"] is False

    projection.update(_accepted_projection(opening="opening B"))
    assert proof_scene_digest(tmp_project, "SC001") != base
    assert current_proof_scene_approval(tmp_project, "SC001")["approved"] is False


def test_equivalent_rereview_does_not_invalidate_proof_scene_digest(
    tmp_project, add_shot, monkeypatch
):
    _proof_project(tmp_project, add_shot)
    projection = _accepted_projection()
    projection["observed_at"] = "2026-08-06T00:00:00+00:00"
    monkeypatch.setattr(readiness, "accepted_observed_state", lambda project, sid: projection)
    base = proof_scene_digest(tmp_project, "SC001")

    projection["observed_at"] = "2026-08-07T00:00:00+00:00"
    projection["observed_opening"] = list(reversed(projection["observed_opening"]))
    projection["observed_endpoint"] = list(reversed(projection["observed_endpoint"]))
    assert proof_scene_digest(tmp_project, "SC001") == base


def test_stale_or_rejected_evidence_never_enters_proof_scene_digest(
    tmp_project, add_shot, monkeypatch
):
    _proof_project(tmp_project, add_shot)
    projection = _accepted_projection()
    projection.update({
        "status": "not_accepted",
        "assurance_state": "stale",
        "packet_id": "pkt_stale_a",
        "evidence_digest": "sha256:stale-a",
        "observed_endpoint": [{"position": "END", "value": "stale A"}],
    })
    monkeypatch.setattr(readiness, "accepted_observed_state", lambda project, sid: projection)
    base = proof_scene_digest(tmp_project, "SC001")

    projection.update({
        "assurance_state": "rejected",
        "packet_id": "pkt_rejected_b",
        "evidence_digest": "sha256:rejected-b",
        "observed_endpoint": [{"position": "END", "value": "rejected B"}],
    })
    assert proof_scene_digest(tmp_project, "SC001") == base


def test_proof_scene_digest_binds_packet_and_stable_assurance(
    tmp_project, add_shot, monkeypatch
):
    _proof_project(tmp_project, add_shot)
    projection = _accepted_projection()
    monkeypatch.setattr(readiness, "accepted_observed_state", lambda project, sid: projection)
    base = proof_scene_digest(tmp_project, "SC001")

    projection["packet_id"] = "pkt_other"
    assert proof_scene_digest(tmp_project, "SC001") != base

    projection.update(_accepted_projection())
    projection["assurance_digest"] = "sha256:assurance-b"
    assert proof_scene_digest(tmp_project, "SC001") != base
