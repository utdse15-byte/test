"""Phase 1 narrative authoring contracts and legacy compatibility."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from manju.core.appearances import appearances
from manju.core.authoring import SceneContract
from manju.core.check import run_check
from manju.core.models import ShotSpec
from manju.core.yamlio import read_yaml, write_yaml


def _save_scene(project, scene_id: str = "SC001", **overrides) -> SceneContract:
    data = {
        "id": scene_id,
        "title": "First suspicion",
        "location_ref": "convenience_store",
        "time": "03:00",
        "purpose": "Linxia realizes information is being withheld",
        "entry_state": {"linxia": {"props": {}}},
        "irreversible_change": ["Trust breaks"],
        "exit_state": {"linxia": {"props": {}}},
        "proof_scene": True,
    }
    data.update(overrides)
    contract = SceneContract.model_validate(data)
    project.save_scene_contract(contract)
    return contract


def _text(items: list[str]) -> str:
    return "\n".join(items)


def test_legacy_shot_does_not_emit_new_fields(tmp_project):
    shot = ShotSpec.model_validate({"id": "S001", "scene": "convenience_store"})
    tmp_project.save_shot(shot)

    raw = read_yaml(tmp_project.shot_path("S001"))
    assert "scene_id" not in raw
    assert "props" not in raw
    assert "contract" not in raw
    assert "source_media_sha256" not in raw["continuity"]


def test_scene_contract_roundtrip(tmp_project):
    expected = _save_scene(
        tmp_project,
        entry_state={"linxia": {"knowledge": ["the coin is from the future"]}},
        carry_forward=["Linxia does not confront Zhou"],
    )

    assert tmp_project.scene_contract_ids() == ["SC001"]
    assert tmp_project.load_scene_contract("SC001") == expected
    assert read_yaml(tmp_project.scene_contract_path("SC001"))["format"] == (
        "manju.scene-contract/v1"
    )


def test_scene_contract_filename_matches_id(tmp_project):
    write_yaml(
        tmp_project.scene_contracts_dir / "SC001.yaml",
        {"format": "manju.scene-contract/v1", "id": "SC002"},
    )

    errors = _text(run_check(tmp_project).errors)
    assert "story/scenes/SC001.yaml" in errors
    assert "does not match filename" in errors


def test_scene_membership_is_derived_from_indexed_shots(tmp_project, add_shot):
    _save_scene(tmp_project)
    _save_scene(tmp_project, "SC002")
    add_shot(tmp_project, "S001", scene_id="SC001")
    add_shot(tmp_project, "S002", scene_id="SC002")
    # On disk but deliberately not indexed: it cannot become scene membership.
    tmp_project.save_shot(
        ShotSpec.model_validate(
            {"id": "S003", "scene": "convenience_store", "scene_id": "SC001"}
        )
    )

    assert tmp_project.scene_shot_ids("SC001") == ["S001"]
    assert tmp_project.scene_shot_ids("SC002") == ["S002"]


def test_scene_contract_does_not_own_shot_list():
    with pytest.raises(ValidationError, match="shots"):
        SceneContract.model_validate({"id": "SC001", "shots": ["S001"]})


def test_missing_scene_character_ref_is_contract_error(tmp_project):
    _save_scene(tmp_project, entry_state={"unknown_actor": {}}, exit_state={"linxia": {}})

    errors = _text(run_check(tmp_project).errors)
    assert "contract error" in errors
    assert "unknown_actor" in errors and "bible/characters.yaml" in errors


def test_missing_scene_prop_ref_is_contract_error(tmp_project):
    _save_scene(
        tmp_project,
        entry_state={"linxia": {"props": {"future_coin": "left_hand"}}},
    )

    errors = _text(run_check(tmp_project).errors)
    assert "contract error" in errors
    assert "future_coin" in errors and "bible/props.yaml" in errors


def test_missing_scene_location_ref_is_contract_error(tmp_project):
    _save_scene(tmp_project, location_ref="missing_store")

    errors = _text(run_check(tmp_project).errors)
    assert "contract error" in errors
    assert "missing_store" in errors and "bible/scenes.yaml" in errors


def test_legacy_prop_lock_still_works(tmp_project, add_shot):
    write_yaml(
        tmp_project.root / "bible" / "props.yaml",
        {"future_coin": {"name": "Future coin"}},
    )
    add_shot(tmp_project, "S001", continuity={"locks": ["prop:future_coin"]})

    report = run_check(tmp_project)
    assert not any("future_coin" in error for error in report.errors)
    assert appearances(tmp_project)["props"]["future_coin"]["shots"] == ["S001"]


def test_new_props_are_deduplicated_with_legacy_locks(tmp_project, add_shot):
    write_yaml(
        tmp_project.root / "bible" / "props.yaml",
        {"future_coin": {"name": "Future coin"}, "receipt": {"name": "Receipt"}},
    )
    add_shot(
        tmp_project,
        "S001",
        props=["future_coin", "receipt", "future_coin"],
        continuity={"locks": ["prop:receipt", "prop:future_coin"]},
    )

    report = run_check(tmp_project)
    assert not report.errors
    assert any("deprecation" in warning for warning in report.warnings)
    assert list(appearances(tmp_project)["props"]) == ["future_coin", "receipt"]
    assert appearances(tmp_project)["props"]["future_coin"]["shots"] == ["S001"]


def test_new_prop_missing_from_prop_bible_is_contract_error(tmp_project, add_shot):
    add_shot(tmp_project, "S001", props=["future_coin"])

    errors = _text(run_check(tmp_project).errors)
    assert "contract error" in errors
    assert "future_coin" in errors and "bible/props.yaml" in errors


def test_empty_scene_directory_does_not_opt_in(tmp_project):
    assert tmp_project.scene_contracts_dir.is_dir()
    assert tmp_project.narrative_opted_in is False


def test_malformed_scene_file_still_opts_in_and_is_parser_error(tmp_project):
    (tmp_project.scene_contracts_dir / "SC001.yaml").write_text(
        "id: SC001\nentry_state: [\n", encoding="utf-8"
    )

    assert tmp_project.narrative_opted_in is True
    errors = _text(run_check(tmp_project).errors)
    assert "story/scenes/SC001.yaml" in errors and "parser error" in errors


def test_narrative_contract_errors_and_readiness_warnings_are_distinct(
    tmp_project, add_shot
):
    _save_scene(tmp_project, purpose="", irreversible_change=[])
    add_shot(
        tmp_project,
        "S001",
        scene_id="SC001",
        action={"main": ""},
        contract={
            "purpose": "",
            "endpoint": [],
            "acceptance": {"action_required": True, "min_end_hold_ms": -1},
        },
    )

    report = run_check(tmp_project)
    errors = _text(report.errors)
    warnings = _text(report.warnings)
    assert "contract error" in errors
    assert "action_required" in errors and "min_end_hold_ms" in errors
    assert "readiness" in warnings
    assert "purpose" in warnings and "irreversible_change" in warnings


def test_narrative_indexed_shot_without_scene_id_is_readiness_warning(
    tmp_project, add_shot
):
    _save_scene(tmp_project)
    add_shot(tmp_project, "S001")

    warnings = _text(run_check(tmp_project).warnings)
    assert "readiness" in warnings and "scene_id" in warnings and "S001" in warnings


def test_scene_location_mismatch_and_empty_proof_scene_are_warnings(
    tmp_project, add_shot
):
    write_yaml(
        tmp_project.root / "bible" / "scenes.yaml",
        {
            "convenience_store": {"name": "Store"},
            "alley": {"name": "Alley"},
        },
    )
    _save_scene(tmp_project, "SC001", location_ref="alley", proof_scene=False)
    _save_scene(tmp_project, "SC002", location_ref="alley", proof_scene=True)
    add_shot(tmp_project, "S001", scene_id="SC001", scene="convenience_store")

    warnings = _text(run_check(tmp_project).warnings)
    assert "SC001" in warnings and "location_ref" in warnings
    assert "SC002" in warnings and "indexed shot" in warnings


def test_scene_contract_rejects_unsafe_id_and_path(tmp_project):
    with pytest.raises(ValidationError):
        SceneContract.model_validate({"id": "../SC001"})
    with pytest.raises(Exception):
        tmp_project.scene_contract_path("../SC001")


def test_character_behavior_bible_fields_roundtrip_incrementally(tmp_project):
    characters_path = tmp_project.root / "bible" / "characters.yaml"
    characters = read_yaml(characters_path)
    characters["linxia"]["behavior"] = {
        "default_posture": ["shoulders inward"],
        "under_pressure": ["speech becomes shorter"],
    }
    write_yaml(characters_path, characters)

    assert tmp_project.load_bible()["linxia"]["behavior"]["under_pressure"] == [
        "speech becomes shorter"
    ]


def test_scene_contracts_enter_source_revision_without_legacy_drift(tmp_project):
    from manju.build.shotpackage import project_revision
    from manju.core.hashing import hash_value

    index_path = tmp_project.shots_dir / "index.yaml"
    legacy_payload = {
        "shot_ids": tmp_project.shot_ids(),
        "index": index_path.read_text(encoding="utf-8"),
    }
    assert project_revision(tmp_project) == hash_value(legacy_payload)

    _save_scene(tmp_project, purpose="First purpose")
    first = project_revision(tmp_project)
    _save_scene(tmp_project, purpose="Changed purpose")
    assert project_revision(tmp_project) != first
