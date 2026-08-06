"""Offline regressions for picture SPEC v4 and strict redo recipe replay."""

from __future__ import annotations

from manju.build.graph import _plan_redo, replayable_recipe_params
from manju.build.stale import ShotState, evaluate_shot
from manju.core.models import TakeSidecar
from manju.core.spec import SPEC_VERSION, compute_spec_hash, spec_payload


def _local_ref(project, name="identity.png", content=b"identity"):
    path = project.root / "media" / "refs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path, project.relpath(path)


def test_spec_v4_tracks_reference_binding_and_local_bytes(tmp_project, add_shot):
    path, ref = _local_ref(tmp_project)
    shot = add_shot(tmp_project, "S001", generation={"params": {"refs": [{
        "ref": ref,
        "controls": ["character_identity"],
        "subject_ref": "character:linxia",
    }]}})
    bible = tmp_project.load_bible()
    payload = spec_payload(shot, bible, version=4, project_root=tmp_project.root)
    assert payload["references"] == [{
        "kind": "image",
        "ref": ref,
        "tier": "params",
        "controls": ["character_identity"],
        "ignore": [],
        "subject_scope": "character:linxia",
        "local_sha256": payload["references"][0]["local_sha256"],
    }]
    assert payload["references"][0]["local_sha256"].startswith("sha256:")

    before = compute_spec_hash(shot, bible, version=4, project_root=tmp_project.root)
    old_v3 = compute_spec_hash(shot, bible, version=3, project_root=tmp_project.root)
    path.write_bytes(b"replacement bytes")
    assert compute_spec_hash(
        shot, bible, version=4, project_root=tmp_project.root
    ) != before
    assert compute_spec_hash(
        shot, bible, version=3, project_root=tmp_project.root
    ) == old_v3


def test_spec_v4_tracks_top_level_ref_controls_and_subject(tmp_project, add_shot):
    _path, ref = _local_ref(tmp_project)
    first = add_shot(tmp_project, "S001", refs=[{
        "ref": ref,
        "controls": ["costume"],
        "subject_ref": "character/linxia",
    }])
    second = first.model_copy(deep=True)
    second.__pydantic_extra__["refs"][0]["controls"] = ["character_identity"]
    third = first.model_copy(deep=True)
    third.__pydantic_extra__["refs"][0]["subject_ref"] = "character:other"
    bible = tmp_project.load_bible()
    hashes = {
        compute_spec_hash(value, bible, version=4, project_root=tmp_project.root)
        for value in (first, second, third)
    }
    assert len(hashes) == 3
    assert spec_payload(first, bible, version=4, project_root=tmp_project.root)[
        "references"
    ][0]["tier"] == "shot_refs"


def test_provider_delivery_policy_is_absent_from_spec_v4(tmp_project, add_shot):
    _path, ref = _local_ref(tmp_project)
    shot = add_shot(tmp_project, "S001", generation={"params": {"refs": [ref]}})
    payload = spec_payload(
        shot, tmp_project.load_bible(), version=4, project_root=tmp_project.root
    )
    assert "provider" not in payload["references"][0]
    assert "delivery" not in payload["references"][0]
    assert "budget" not in payload["references"][0]


def test_v3_take_keeps_historical_reference_comparison(
    tmp_project, add_shot, tmp_path
):
    path, ref = _local_ref(tmp_project)
    shot = add_shot(tmp_project, "S001", generation={"params": {"refs": [ref]}})
    bible = tmp_project.load_bible()
    old_hash = compute_spec_hash(shot, bible, version=3, project_root=tmp_project.root)
    media = tmp_path / "old.mp4"
    media.write_bytes(b"fake media")
    take = tmp_project.register_take(
        "S001", media,
        TakeSidecar(provider="test", spec_hash=old_hash, spec_version=3),
    )
    tmp_project.update_shot_raw(
        "S001", lambda raw: raw.setdefault("status", {}).__setitem__(
            "selected_take", take.name
        )
    )
    path.write_bytes(b"new ref bytes")
    assert evaluate_shot(
        tmp_project, tmp_project.load_shot("S001"), bible
    ).state is ShotState.FRESH


def test_new_spec_v4_take_stales_when_reference_bytes_move(
    tmp_project, add_shot, tmp_path
):
    path, ref = _local_ref(tmp_project)
    shot = add_shot(tmp_project, "S001", generation={"params": {"refs": [ref]}})
    bible = tmp_project.load_bible()
    current = compute_spec_hash(
        shot, bible, version=SPEC_VERSION, project_root=tmp_project.root
    )
    media = tmp_path / "new.mp4"
    media.write_bytes(b"fake media")
    take = tmp_project.register_take(
        "S001", media,
        TakeSidecar(provider="test", spec_hash=current, spec_version=SPEC_VERSION),
    )
    tmp_project.update_shot_raw(
        "S001", lambda raw: raw.setdefault("status", {}).__setitem__(
            "selected_take", take.name
        )
    )
    path.write_bytes(b"new ref bytes")
    assert evaluate_shot(
        tmp_project, tmp_project.load_shot("S001"), bible
    ).state is ShotState.STALE


def test_redo_replays_only_explicit_recipe_keys():
    sidecar = TakeSidecar(provider="manual_import", spec_hash="manual", params={
        "seed": 73,
        "model": "offline-model",
        "sampler": "euler",
        "provider_options": {"guidance": 4.5},
        "remote_job_id": "parent-job",
        "compiled_prompt": "old prompt",
        "ref_delivery": {"images": []},
        "bridge_admission": {"accepted": True},
        "request_digest": "sha256:old",
        "unknown_runtime_field": "must-not-replay",
    })
    assert replayable_recipe_params(sidecar) == {
        "seed": 73,
        "model": "offline-model",
        "sampler": "euler",
        "provider_options": {"guidance": 4.5},
    }


def test_plan_redo_never_reuses_parent_remote_job(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    media = tmp_path / "parent.mp4"
    media.write_bytes(b"parent")
    parent = tmp_project.register_take(
        "S001", media,
        TakeSidecar(provider="manual_import", spec_hash="manual", params={
            "seed": 11,
            "remote_job_id": "parent-job",
            "compiled_prompt": "old",
            "prompt_id": "old-prompt-id",
        }),
    )
    plan = _plan_redo(
        tmp_project,
        "S001",
        candidates=None,
        provider=None,
        seed=None,
        from_take=parent.name,
        rules=tmp_project.load_rules(),
        bible=tmp_project.load_bible(),
    )
    assert plan.params["seed"] == 11
    assert "remote_job_id" not in plan.params
    assert "compiled_prompt" not in plan.params
    assert "prompt_id" not in plan.params
