"""Offline regressions for scoped reference ownership, audit Wave 1."""

from __future__ import annotations

import pytest

from manju.build.readiness import _shot_contract_gate
from manju.core.intent import prompt_contract_sections
from manju.core.yamlio import write_yaml
from manju.providers.base import GenerationRequest
from manju.providers.generic_cloud import GenericCloudProvider
from manju.providers.manifest import ProviderManifest
from manju.providers.refs import (
    RefItem,
    RefSet,
    ReferenceControlConflict,
    reference_control_conflicts,
    resolve_refs,
    validate_control_ownership,
)


def _item(path, *, role="character_identity", subject=None, ignore=()):
    return RefItem(
        ref=path.name,
        tier="shot_refs",
        kind="image",
        path=path,
        is_url=False,
        exists=True,
        controls=(role,) if role else (),
        ignore=tuple(ignore),
        subject_ref=subject,
        declared_transfer=bool(role or subject or ignore),
    )


def _project_scoped_opening(tmp_project, add_shot, opening, *, role, subject):
    shot = add_shot(
        tmp_project,
        "S001",
        contract={"opening": opening},
    )
    refs = RefSet.from_items([
        _item(
            tmp_project.root / "offline-reference.bin",
            role=role,
            subject=subject,
        )
    ])
    return prompt_contract_sections(
        shot,
        tmp_project.load_bible(),
        refset=refs,
    )["opening"]


def test_two_scoped_character_identity_owners_are_valid(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    refs = RefSet.from_items([
        _item(a, subject="character:A"),
        _item(b, subject="character/B"),
    ])
    validate_control_ownership(refs)


def test_same_scope_and_global_scope_conflict(tmp_path):
    paths = [tmp_path / name for name in ("a.png", "b.png")]
    for path in paths:
        path.write_bytes(path.name.encode("ascii"))
    same = RefSet.from_items([
        _item(paths[0], subject="character:A"),
        _item(paths[1], subject="asset/A"),
    ])
    with pytest.raises(ReferenceControlConflict):
        validate_control_ownership(same)

    global_and_scoped = RefSet.from_items([
        _item(paths[0]),
        _item(paths[1], subject="character:B"),
    ])
    with pytest.raises(ReferenceControlConflict):
        validate_control_ownership(global_and_scoped)


def test_ignore_never_claims_owner(tmp_path):
    paths = [tmp_path / name for name in ("a.png", "b.png")]
    for path in paths:
        path.write_bytes(path.name.encode("ascii"))
    refs = RefSet.from_items([
        _item(paths[0], role=None, ignore=("character_identity",)),
        _item(paths[1], subject="character:A"),
    ])
    assert reference_control_conflicts(refs) == []


def test_same_blob_keeps_logical_bindings_and_uploads_once(
    tmp_project, add_shot, monkeypatch
):
    path = tmp_project.root / "media" / "refs" / "group.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"group-image")
    ref = tmp_project.relpath(path)
    bindings = [
        {"ref": ref, "controls": ["character_identity"], "subject_ref": "character:A"},
        {"ref": ref, "controls": ["character_identity"], "subject_ref": "character:B"},
    ]
    shot = add_shot(tmp_project, "S001", generation={"params": {"refs": bindings}})
    req = GenerationRequest(
        project=tmp_project,
        shot=shot,
        bible=tmp_project.load_bible(),
        spec_hash="sha256:wave1",
        duration_ms=1000,
        params={"refs": bindings},
    )
    monkeypatch.setenv("WAVE1_KEY", "offline")
    manifest = ProviderManifest.model_validate({
        "id": "wave1_cloud",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": "WAVE1_KEY"},
        "submit": {
            "url": "https://example.invalid/generate",
            "body_template": {"prompt": "{prompt}"},
            "job_id_path": "$.id",
        },
        "poll": {
            "url": "https://example.invalid/jobs/{job_id}",
            "status_path": "$.status",
            "status_map": {"done": "succeeded"},
        },
        "refs": {"image_mode": "multipart", "multipart_field": "image", "max_images": 1},
        "limits": {"max_ref_images": 1},
    })
    provider = GenericCloudProvider(manifest)
    provider._ensure_provider_request(req)
    assert len(req.refset().items) == 2
    files = provider._deliver_refs({}, req, record=False)
    assert len(files) == 1


def test_invalid_duplicate_binding_is_not_dropped(tmp_project, add_shot):
    path = tmp_project.root / "media" / "refs" / "same.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"same")
    ref = tmp_project.relpath(path)
    bindings = [
        {"ref": ref, "controls": ["style"]},
        {"ref": ref, "controls": ["not_a_transfer_role"]},
    ]
    shot = add_shot(tmp_project, "S001", generation={"params": {"refs": bindings}})
    refs = resolve_refs(tmp_project, shot, tmp_project.load_bible())
    assert len(refs.items) == 2
    with pytest.raises(ReferenceControlConflict, match="not_a_transfer_role"):
        validate_control_ownership(refs)


def test_prop_bible_ref_and_prompt_ownership(tmp_project, add_shot):
    path = tmp_project.root / "media" / "refs" / "coin.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"coin")
    write_yaml(tmp_project.root / "bible" / "props.yaml", {
        "coin": {
            "name": "Future Coin",
            "color": "bright gold",
            "material": "brushed metal",
            "ref_image": tmp_project.relpath(path),
        }
    })
    binding = {
        "ref": tmp_project.relpath(path),
        "controls": ["prop"],
        "subject_ref": "prop:coin",
    }
    shot = add_shot(
        tmp_project,
        "S001",
        props=["coin"],
        generation={"params": {"refs": [binding]}},
        action={"main": "the coin rolls to the door"},
    )
    bible = tmp_project.load_bible()
    refs = resolve_refs(tmp_project, shot, bible)
    assert any(item.tier == "bible" and item.ref == tmp_project.relpath(path)
               for item in refs.items)
    fields = prompt_contract_sections(shot, bible, refset=refs)
    assert "Future Coin" in fields["props"]
    assert "bright gold" not in fields["props"]
    assert "brushed metal" not in fields["props"]
    assert "coin rolls" in fields["action"]


def test_scoped_motion_preserves_camera_and_legacy_opening(tmp_project, add_shot):
    path = tmp_project.root / "media" / "refs" / "motion.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"motion")
    binding = {
        "ref": tmp_project.relpath(path),
        "controls": ["motion"],
        "subject_ref": "character:A",
    }
    shot = add_shot(
        tmp_project,
        "S001",
        camera={"shot_size": "medium", "movement": "pan_left", "angle": "eye_level"},
        contract={"opening": ["A kneels", "B stands by the door", "coin on table"]},
        generation={"params": {"refs": [binding]}},
    )
    fields = prompt_contract_sections(
        shot, tmp_project.load_bible(),
        refset=resolve_refs(tmp_project, shot, tmp_project.load_bible()),
    )
    assert "pan left" in fields["camera"]
    assert "A kneels" in fields["opening"]
    assert "B stands" in fields["opening"]
    assert "coin on table" in fields["opening"]


def test_scoped_character_pose_suppresses_only_matching_character_fact(
    tmp_project, add_shot
):
    opening = _project_scoped_opening(
        tmp_project,
        add_shot,
        [
            {"subject_ref": "character:A", "statement": "A kneels"},
            {"subject_ref": "character:B", "statement": "B stands"},
        ],
        role="pose",
        subject="character/A",
    )

    assert "A kneels" not in opening
    assert "B stands" in opening


def test_scoped_motion_preserves_other_character_fact(tmp_project, add_shot):
    opening = _project_scoped_opening(
        tmp_project,
        add_shot,
        [
            {"subject_ref": "character:A", "statement": "A runs"},
            {"subject_ref": "character:B", "statement": "B waits"},
        ],
        role="motion",
        subject="asset:A",
    )

    assert "A runs" not in opening
    assert "B waits" in opening


def test_scoped_motion_preserves_prop_fact(tmp_project, add_shot):
    opening = _project_scoped_opening(
        tmp_project,
        add_shot,
        [{"subject_ref": "prop:coin", "statement": "coin rests on table"}],
        role="motion",
        subject="character:A",
    )

    assert opening == "coin rests on table"


def test_scoped_motion_preserves_environment_fact(tmp_project, add_shot):
    opening = _project_scoped_opening(
        tmp_project,
        add_shot,
        [{"subject_ref": "scene:store", "statement": "rain strikes the window"}],
        role="motion",
        subject="character:A",
    )

    assert opening == "rain strikes the window"


def test_character_and_prop_same_id_do_not_cross_match(tmp_project, add_shot):
    opening = _project_scoped_opening(
        tmp_project,
        add_shot,
        [
            {"subject_ref": "character:A", "statement": "A waves"},
            {"subject_ref": "prop:A", "statement": "A-shaped prop stays still"},
        ],
        role="motion",
        subject="character:A",
    )

    assert "A waves" not in opening
    assert "A-shaped prop stays still" in opening


def test_readiness_uses_generation_ownership_validator(tmp_project, add_shot):
    paths = []
    for name in ("a.png", "b.png"):
        path = tmp_project.root / "media" / "refs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode("ascii"))
        paths.append(tmp_project.relpath(path))
    bindings = [
        {"ref": paths[0], "controls": ["character_identity"], "subject_ref": "character:A"},
        {"ref": paths[1], "controls": ["character_identity"], "subject_ref": "asset/A"},
    ]
    add_shot(
        tmp_project,
        "S001",
        action={"main": "move"},
        contract={"purpose": "show movement", "endpoint": ["done"]},
        generation={"params": {"refs": bindings}},
    )
    gate = _shot_contract_gate(tmp_project)
    conflict = next(
        row for row in gate["details"]
        if row["code"] == "SHOT_REFERENCE_CONTROL_CONFLICT"
    )
    assert conflict["conflicts"]
