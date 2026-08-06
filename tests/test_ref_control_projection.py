"""Reference ownership changes only the unresolved default prompt variables."""

from __future__ import annotations

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.base import GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider
from manju.providers.manifest import ProviderManifest
from manju.providers.prompt import compile_prompt
from manju.providers.refs import resolve_refs


def _ref(project, name: str) -> str:
    path = project.root / "media" / "refs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"reference-" + name.encode("ascii"))
    return project.relpath(path)


def _set_bible(project) -> None:
    write_yaml(project.root / "bible" / "characters.yaml", {
        "linxia": {
            "name": "Mara",
            "face": "freckled face, gray eyes, cropped black hair",
            "costume": "fixed red wool coat with brass buttons",
            "voice": "quiet and measured",
        }
    })
    write_yaml(project.root / "bible" / "scenes.yaml", {
        "convenience_store": {
            "name": "Emergency Arcade",
            "description": "a fixed underground shopping arcade",
            "lighting": "fixed cold fluorescent ceiling grid",
        }
    })


def _controlled_shot(tmp_project, add_shot, role: str, *, name: str = "owner.png",
                     subject_ref: str | None = None, **overrides):
    ref = _ref(tmp_project, name)
    binding = {"ref": ref, "controls": [role]}
    if subject_ref:
        binding["subject_ref"] = subject_ref
    generation = dict(overrides.pop("generation", {}) or {})
    params = dict(generation.get("params", {}) or {})
    params["refs"] = [binding]
    generation["params"] = params
    shot = add_shot(
        tmp_project,
        "S010",
        generation=generation,
        **overrides,
    )
    return shot, resolve_refs(tmp_project, shot, tmp_project.load_bible())


def test_no_declared_transfer_keeps_legacy_prompt(tmp_project, add_shot):
    _set_bible(tmp_project)
    ref = _ref(tmp_project, "legacy.png")
    shot = add_shot(
        tmp_project, "S010",
        action={"main": "walks toward the emergency exit", "emotion": "wary"},
        generation={"params": {"refs": [ref]}},
    )
    bible = tmp_project.load_bible()
    legacy = compile_prompt(shot, bible)
    refset = resolve_refs(tmp_project, shot, bible)
    assert compile_prompt(shot, bible, refset=refset) == legacy


def test_identity_control_suppresses_identity_description(tmp_project, add_shot):
    _set_bible(tmp_project)
    shot, refset = _controlled_shot(
        tmp_project, add_shot, "character_identity", subject_ref="character:linxia"
    )
    prompt = compile_prompt(shot, tmp_project.load_bible(), refset=refset)
    assert "Mara" in prompt and "linxia" in prompt
    assert "freckled face" not in prompt
    assert "fixed red wool coat" in prompt


def test_costume_control_suppresses_costume_description(tmp_project, add_shot):
    _set_bible(tmp_project)
    shot, refset = _controlled_shot(
        tmp_project, add_shot, "costume", subject_ref="character:linxia"
    )
    prompt = compile_prompt(shot, tmp_project.load_bible(), refset=refset)
    assert "freckled face" in prompt
    assert "fixed red wool coat" not in prompt


def test_costume_control_suppresses_ambiguous_appearance_prose(
    tmp_project, add_shot
):
    write_yaml(tmp_project.root / "bible" / "characters.yaml", {
        "linxia": {
            "name": "Mara",
            "appearance": "cropped hair, fixed red coat, brass buttons",
        }
    })
    shot, refset = _controlled_shot(
        tmp_project, add_shot, "costume", subject_ref="character:linxia"
    )
    prompt = compile_prompt(shot, tmp_project.load_bible(), refset=refset)
    assert "fixed red coat" not in prompt


def test_location_control_suppresses_location_bible(tmp_project, add_shot):
    _set_bible(tmp_project)
    shot, refset = _controlled_shot(
        tmp_project,
        add_shot,
        "location",
        contract={"opening": ["power outage leaves only a handheld work light"]},
    )
    prompt = compile_prompt(shot, tmp_project.load_bible(), refset=refset)
    assert "Emergency Arcade" not in prompt
    assert "fixed underground shopping arcade" not in prompt
    assert "power outage leaves only a handheld work light" in prompt


def test_motion_control_preserves_intent_and_endpoint(tmp_project, add_shot):
    _set_bible(tmp_project)
    shot, refset = _controlled_shot(
        tmp_project,
        add_shot,
        "motion",
        action={"main": "reaches the emergency exit", "emotion": "determined"},
        contract={
            "opening": ["kneeling with both hands on the floor"],
            "endpoint": ["standing at the emergency exit"],
            "physics": {
                "required": ["the loose cable keeps its weight"],
                "avoid": ["foot sliding"],
            },
        },
    )
    prompt = compile_prompt(shot, tmp_project.load_bible(), refset=refset)
    assert "kneeling with both hands on the floor" not in prompt
    assert "reaches the emergency exit" in prompt
    assert "determined" in prompt
    assert "standing at the emergency exit" in prompt
    assert "the loose cable keeps its weight" in prompt
    assert "foot sliding" in prompt


def test_framing_control_does_not_suppress_camera_movement(tmp_project, add_shot):
    shot, refset = _controlled_shot(
        tmp_project,
        add_shot,
        "framing",
        camera={"shot_size": "close_up", "movement": "pan_left", "angle": "high_angle"},
    )
    prompt = compile_prompt(shot, tmp_project.load_bible(), refset=refset)
    assert "pan left" in prompt
    assert "close-up shot" not in prompt
    assert "high angle" not in prompt


def test_lighting_control_suppresses_fixed_bible_lighting(tmp_project, add_shot):
    _set_bible(tmp_project)
    shot, refset = _controlled_shot(tmp_project, add_shot, "lighting")
    prompt = compile_prompt(shot, tmp_project.load_bible(), refset=refset)
    assert "Emergency Arcade" in prompt
    assert "fixed cold fluorescent ceiling grid" not in prompt


def test_ignore_never_claims_control(tmp_project, add_shot):
    _set_bible(tmp_project)
    ref = _ref(tmp_project, "ignored.png")
    shot = add_shot(
        tmp_project, "S010",
        generation={"params": {"refs": [{"ref": ref, "ignore": ["character_identity"]}]}},
    )
    bible = tmp_project.load_bible()
    legacy = compile_prompt(shot, bible)
    refset = resolve_refs(tmp_project, shot, bible)
    assert compile_prompt(shot, bible, refset=refset) == legacy
    assert "freckled face" in legacy


class _NoTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        raise AssertionError("provider transport must not be reached")


def test_closed_role_conflict_blocks_before_transport(
    tmp_project, add_shot, monkeypatch
):
    first = _ref(tmp_project, "identity-a.png")
    second = _ref(tmp_project, "identity-b.png")
    bindings = [
        {"ref": first, "controls": ["character_identity"]},
        {"ref": second, "controls": ["character_identity"]},
    ]
    shot = add_shot(
        tmp_project, "S010", generation={"params": {"refs": bindings}}
    )
    req = GenerationRequest(
        project=tmp_project,
        shot=shot,
        bible=tmp_project.load_bible(),
        spec_hash="sha256:test",
        duration_ms=1000,
        params={"refs": bindings},
    )
    monkeypatch.setenv("CONTROL_TEST_KEY", "secret")
    manifest = ProviderManifest.model_validate({
        "id": "control_test",
        "type": "video",
        "adapter": "generic_cloud",
        "auth": {"key_env": "CONTROL_TEST_KEY"},
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
    })
    transport = _NoTransport()
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda _: None)

    with pytest.raises(ProviderFailure, match="character_identity") as exc:
        provider.generate(req)
    assert first in str(exc.value) and second in str(exc.value)
    assert exc.value.detail["code"] == "reference_control_conflict"
    assert transport.requests == []


def test_prompt_override_is_never_rewritten(tmp_project, add_shot):
    override = "  MODEL-SPECIFIC prompt\nkeep exact spacing  "
    shot, refset = _controlled_shot(
        tmp_project,
        add_shot,
        "character_identity",
        generation={"prompt_override": override},
    )
    assert compile_prompt(shot, tmp_project.load_bible(), refset=refset) == override
