"""Phase 2 unified intent projection and versioned compatibility pins."""

from __future__ import annotations

from copy import deepcopy

from manju.build.stale import ShotState, evaluate_shot
from manju.core.intent import (
    director_contract_view,
    expectation_assertions,
    picture_contract_payload,
    prompt_contract_sections,
)
from manju.core.models import ShotSpec
from manju.core.spec import SPEC_VERSION, compute_spec_hash, spec_payload
from manju.core.yamlio import write_yaml
from manju.providers.prompt import (
    compile_director_prompt,
    compile_image_prompt,
    compile_negative_prompt,
    compile_prompt,
)
from manju.qc.expectations import compile_expectations


PIN_BIBLE = {
    "store": {"name": "Store", "desc": "fluorescent aisle"},
    "lin": {"name": "Lin", "appearance": "short hair"},
}


def _legacy_shot(**overrides) -> ShotSpec:
    data = {
        "id": "S001",
        "scene": "store",
        "characters": ["lin"],
        "camera": {
            "shot_size": "close_up",
            "movement": "slow_push_in",
            "angle": "eye_level",
        },
        "action": {"main": "Lin lifts the coin", "emotion": "controlled shock"},
        "quality": {
            "must_show": ["2036 is readable"],
            "avoid": ["extra fingers"],
        },
        "dialogue": {"speaker": "lin", "text": "Impossible."},
    }
    data.update(overrides)
    return ShotSpec.model_validate(data)


def _contract_shot(**contract_overrides) -> ShotSpec:
    contract = {
        "purpose": "Break trust",
        "viewer_must_perceive": "Lin recognizes the date",
        "opening": ["coin rests in Lin's open left palm"],
        "endpoint": ["Lin closes her fingers around the intact coin"],
        "performance": {
            "required": ["restrained breath"],
            "avoid": ["broad smile"],
        },
        "physics": {
            "required": ["coin remains rigid"],
            "avoid": ["coin merges with fingers"],
        },
        "sound": {"cue": "coin click triggers the hand close"},
        "risk": {"primary": "hand deformation", "fallback_staging": "cut on occlusion"},
        "acceptance": {"action_required": True, "min_end_hold_ms": 500},
        "proof_shot": True,
    }
    contract.update(contract_overrides)
    return _legacy_shot(
        props=["future_coin"],
        scene_id="SC001",
        contract=contract,
    )


def _install_pin_bible(project) -> None:
    write_yaml(project.root / "bible" / "scenes.yaml", {"store": PIN_BIBLE["store"]})
    write_yaml(project.root / "bible" / "characters.yaml", {"lin": PIN_BIBLE["lin"]})
    write_yaml(
        project.root / "bible" / "props.yaml",
        {"future_coin": {"name": "Future coin", "appearance": "brass, stamped 2036"}},
    )


def test_spec_version_is_three():
    assert SPEC_VERSION == 3


def test_one_authored_action_drives_prompt_and_expectation(tmp_project):
    shot = _contract_shot()
    tmp_project.save_shot(shot)
    text = shot.action.main

    assert f"Action: {text}" in compile_prompt(shot, PIN_BIBLE)
    actions = [a for a in expectation_assertions(shot) if a.kind == "action"]
    assert len(actions) == 1 and actions[0].statement == text
    compiled = compile_expectations(tmp_project, "S001")
    assert any(e["statement"] == text for e in compiled["expectations"])


def test_opening_is_start_expectation(tmp_project):
    shot = _contract_shot()
    tmp_project.save_shot(shot)
    [opening] = [e for e in compile_expectations(tmp_project, "S001")["expectations"]
                 if e["statement"] == shot.contract.opening[0]]
    assert opening["position"] == "start"
    assert opening["source_path"].endswith("/contract/opening/0")


def test_endpoint_is_end_expectation(tmp_project):
    shot = _contract_shot()
    tmp_project.save_shot(shot)
    [endpoint] = [e for e in compile_expectations(tmp_project, "S001")["expectations"]
                  if e["statement"] == shot.contract.endpoint[0]]
    assert endpoint["position"] == "end"
    assert endpoint["source_path"].endswith("/contract/endpoint/0")


def test_same_text_at_start_and_end_has_distinct_ids(tmp_project):
    shot = _contract_shot(opening=["same state"], endpoint=["same state"])
    tmp_project.save_shot(shot)
    rows = [e for e in compile_expectations(tmp_project, "S001")["expectations"]
            if e["statement"] == "same state"]
    assert {e["position"] for e in rows} == {"start", "end"}
    assert len({e["id"] for e in rows}) == 2


def test_action_is_not_expectation_when_author_marks_it_optional(tmp_project):
    shot = _contract_shot(acceptance={"action_required": False, "min_end_hold_ms": 0})
    tmp_project.save_shot(shot)
    statements = {e["statement"] for e in compile_expectations(tmp_project, "S001")["expectations"]}
    assert shot.action.main not in statements


def test_purpose_risk_and_director_metadata_do_not_affect_v3_hash():
    base = _contract_shot()
    changed = _contract_shot(
        purpose="Entirely different purpose",
        viewer_must_perceive="Different director note",
        risk={"primary": "different risk", "fallback_staging": "different fallback"},
        proof_shot=False,
    )
    assert compute_spec_hash(base, PIN_BIBLE, version=3) == compute_spec_hash(
        changed, PIN_BIBLE, version=3
    )


def test_scene_id_does_not_affect_v3_hash():
    a = _contract_shot()
    b = a.model_copy(update={"scene_id": "SC999"})
    assert compute_spec_hash(a, PIN_BIBLE, version=3) == compute_spec_hash(
        b, PIN_BIBLE, version=3
    )


def test_endpoint_affects_v3_spec_hash_but_not_v1_or_v2():
    a = _contract_shot(endpoint=["hand remains open"])
    b = _contract_shot(endpoint=["hand closes around coin"])
    assert compute_spec_hash(a, PIN_BIBLE, version=1) == compute_spec_hash(
        b, PIN_BIBLE, version=1
    )
    assert compute_spec_hash(a, PIN_BIBLE, version=2) == compute_spec_hash(
        b, PIN_BIBLE, version=2
    )
    assert compute_spec_hash(a, PIN_BIBLE, version=3) != compute_spec_hash(
        b, PIN_BIBLE, version=3
    )


def test_props_and_prop_bible_enter_v3_only():
    shot = _contract_shot()
    bible = deepcopy(PIN_BIBLE)
    bible["future_coin"] = {"name": "Future coin", "appearance": "brass"}
    assert "props" not in spec_payload(shot, bible, version=2)
    payload = spec_payload(shot, bible, version=3)
    assert payload["props"] == ["future_coin"]
    assert payload["prop_bible"]["future_coin"]["appearance"] == "brass"


def test_picture_contract_payload_excludes_non_picture_fields():
    payload = picture_contract_payload(_contract_shot())
    assert payload["opening"]
    assert payload["endpoint"]
    assert payload["performance"]["required"] == ["restrained breath"]
    assert payload["physics"]["avoid"] == ["coin merges with fingers"]
    assert payload["sound_cue"] == "coin click triggers the hand close"
    assert payload["min_end_hold_ms"] == 500
    for excluded in ("purpose", "viewer_must_perceive", "risk", "proof_shot"):
        assert excluded not in payload


def test_contract_prompt_sections_and_views_are_deterministic():
    bible = deepcopy(PIN_BIBLE)
    bible["future_coin"] = {"name": "Future coin", "appearance": "brass"}
    shot = _contract_shot()
    sections = prompt_contract_sections(shot, bible)
    assert "Future coin" in sections["props"]
    assert sections["opening"] == "coin rests in Lin's open left palm"
    assert sections["endpoint"] == "Lin closes her fingers around the intact coin"
    assert "500 ms" in sections["timing"]
    view = director_contract_view(shot)
    assert view["purpose"] == "Break trust"
    assert view["risk"]["fallback_staging"] == "cut on occlusion"


def test_contract_default_prompt_and_image_prompt_boundaries():
    bible = deepcopy(PIN_BIBLE)
    bible["future_coin"] = {"name": "Future coin", "appearance": "brass"}
    shot = _contract_shot()
    video = compile_prompt(shot, bible)
    image = compile_image_prompt(shot, bible)
    for expected in (
        "Props: Future coin",
        "Opening state: coin rests in Lin's open left palm",
        "Endpoint: Lin closes her fingers around the intact coin",
        "Performance: restrained breath",
        "Physics: coin remains rigid",
        "Timing: coin click triggers the hand close; hold the final state for at least 500 ms",
    ):
        assert expected in video
    assert "Opening state:" in image and "Props:" in image
    assert "Endpoint:" not in image and "Timing:" not in image


def test_new_custom_template_placeholders_are_additive():
    bible = deepcopy(PIN_BIBLE)
    bible["future_coin"] = {"name": "Future coin"}
    rendered = compile_prompt(
        _contract_shot(),
        bible,
        template="{props}|{opening}|{endpoint}|{performance}|{physics}|{timing}",
    )
    assert rendered == (
        "Future coin|coin rests in Lin's open left palm|"
        "Lin closes her fingers around the intact coin|restrained breath|"
        "coin remains rigid|coin click triggers the hand close; "
        "hold the final state for at least 500 ms"
    )


def test_negative_prompt_merges_contract_avoid_lists():
    assert compile_negative_prompt(_contract_shot()) == (
        "extra fingers, broad smile, coin merges with fingers"
    )


def test_director_prompt_prioritizes_non_provider_intent():
    text = compile_director_prompt(_contract_shot(), PIN_BIBLE)
    assert text.splitlines()[0] == "目的 Purpose: Break trust"
    assert "观众必须感知 Viewer must perceive: Lin recognizes the date" in text
    assert "主要风险 Primary risk: hand deformation" in text
    assert "替代调度 Fallback staging: cut on occlusion" in text


def test_legacy_v1_and_v2_hashes_are_pinned():
    shot = _legacy_shot()
    assert compute_spec_hash(shot, PIN_BIBLE, version=1) == (
        "sha256:1cac366de5997296a41c3090728b11353aeb422b69b13727e0cb25772bb58402"
    )
    assert compute_spec_hash(shot, PIN_BIBLE, version=2) == (
        "sha256:addf59c0eb9b52a9ec3edf529eafe49f8a0751d0ae4e89f63928cb6ac3354e90"
    )


def test_legacy_prompt_bytes_are_pinned():
    assert compile_prompt(_legacy_shot(), PIN_BIBLE) == (
        "Scene: desc: fluorescent aisle, name: Store\n"
        "Characters: Lin (appearance: short hair)\n"
        "Camera: close-up shot, slow push-in, eye-level angle\n"
        "Action: Lin lifts the coin\n"
        "Emotion: controlled shock\n"
        "must clearly show: 2036 is readable\n"
        "avoid: extra fingers\n"
        'Dialogue: "Impossible."'
    )


def test_legacy_expectation_digest_is_pinned(tmp_project):
    _install_pin_bible(tmp_project)
    tmp_project.save_shot(_legacy_shot())
    compiled = compile_expectations(tmp_project, "S001")
    assert compiled["schema"] == "manju.qc.expectations/v1"
    assert compiled["digest"] == (
        "sha256:b24f455d2802b830d03afe4ce1f904ed0868f941eef4612d1601abab3b46cf0a"
    )


def test_prompt_override_is_verbatim_and_new_contract_does_not_change_v3_hash():
    exact = "  custom {prompt}\nkeep spacing  "
    a = _contract_shot()
    a.generation.prompt_override = exact
    b = a.model_copy(deep=True)
    b.contract.endpoint = ["entirely different endpoint"]
    b.props = ["other_prop"]
    assert compile_prompt(a, PIN_BIBLE) == exact
    assert compile_image_prompt(a, PIN_BIBLE) == exact
    assert compute_spec_hash(a, PIN_BIBLE, version=3) == compute_spec_hash(
        b, PIN_BIBLE, version=3
    )


def test_old_v2_take_remains_fresh_after_contract_edit(
    tmp_project, make_take
):
    _install_pin_bible(tmp_project)
    shot = _contract_shot()
    tmp_project.save_shot(shot)
    v2_hash = compute_spec_hash(shot, tmp_project.load_bible(), version=2)
    take = make_take(tmp_project, "S001", v2_hash)
    tmp_project.update_shot_raw(
        "S001",
        lambda data: (
            data.setdefault("status", {}).__setitem__("selected_take", take.name),
            data.setdefault("contract", {}).__setitem__("endpoint", ["new endpoint"]),
        ),
    )
    # Simulate a take produced when v2 was current; no sidecar rewrite occurs.
    write_yaml(take.sidecar_path, take.sidecar.model_copy(update={"spec_version": 2}).model_dump(
        exclude_none=True
    ))
    assert evaluate_shot(tmp_project, tmp_project.load_shot("S001")).state is ShotState.FRESH
