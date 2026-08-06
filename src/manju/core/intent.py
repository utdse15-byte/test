"""Pure deterministic projections from authored shot intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import Camera, ShotSpec


@dataclass(frozen=True)
class IntentAssertion:
    kind: str
    polarity: Literal["present", "absent"]
    statement: str
    check: Literal["external_visual", "external_consistency"]
    source_path: str
    position: Literal["any", "start", "end"] = "any"


_SHOT_SIZE = {
    "extreme_wide": "extreme wide shot",
    "wide": "wide shot",
    "medium": "medium shot",
    "close_up": "close-up shot",
    "extreme_close_up": "extreme close-up shot",
}
_MOVEMENT = {
    "static": "static camera",
    "slow_push_in": "slow push-in",
    "push_in": "push in",
    "pull_out": "pull out",
    "pan_left": "pan left",
    "pan_right": "pan right",
    "tilt_up": "tilt up",
    "tilt_down": "tilt down",
    "handheld": "handheld camera",
    "tracking": "tracking shot",
    "orbit": "orbiting camera",
    "zoom_in": "zoom in",
    "zoom_out": "zoom out",
}
_ANGLE = {
    "eye_level": "eye-level angle",
    "low_angle": "low angle",
    "high_angle": "high angle",
    "birds_eye": "bird's-eye view",
    "worms_eye": "worm's-eye view",
    "dutch": "dutch angle",
    "over_shoulder": "over-the-shoulder angle",
}


def _sorted_keys(value: dict) -> list:
    def key(item: Any):
        if isinstance(item, bool):
            return (1, str(item))
        if isinstance(item, (int, float)):
            return (0, item)
        return (1, str(item))

    return sorted(value, key=key)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(text for text in (_fmt(item) for item in value) if text)
    if isinstance(value, dict):
        return ", ".join(
            f"{key}: {_fmt(value[key])}"
            for key in _sorted_keys(value)
            if _fmt(value[key])
        )
    return str(value).strip()


def _excerpt(entry: Any, *, skip: tuple[str, ...] = ("locked",)) -> str:
    if not isinstance(entry, dict):
        return ""
    parts: list[str] = []
    for key in _sorted_keys(entry):
        if key in skip:
            continue
        rendered = _fmt(entry[key])
        if rendered:
            parts.append(f"{key}: {rendered}")
    return ", ".join(parts)


def _normalized_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _costume_key(value: Any) -> bool:
    key = _normalized_key(value)
    return any(token in key for token in (
        "costume", "clothing", "clothes", "wardrobe", "outfit", "attire",
        "garment", "accessories", "uniform", "服装", "服饰", "衣着", "着装",
        "衣服",
    ))


def _composite_appearance_key(value: Any) -> bool:
    """Visual prose that may mix costume with identity and cannot be split safely."""
    return _normalized_key(value) in {
        "appearance", "description", "look", "visual_description",
        "外观", "形象", "造型", "外貌描述",
    }


def _lighting_key(value: Any) -> bool:
    key = _normalized_key(value)
    return any(token in key for token in (
        "lighting", "light_design", "illumination", "color_temperature",
        "灯光", "照明", "光线", "色温",
    ))


def _control_items(refset: Any | None, role: str) -> list[Any]:
    if refset is None:
        return []
    return [
        item for item in (getattr(refset, "items", ()) or ())
        if role in (getattr(item, "controls", ()) or ())
    ]


def _owned(refset: Any | None, *roles: str) -> bool:
    return any(_control_items(refset, role) for role in roles)


def _subject_matches(subject_ref: str | None, asset_id: str) -> bool:
    if not subject_ref:
        return True
    subject = str(subject_ref).strip()
    for prefix in ("character:", "character/", "asset:", "asset/"):
        if subject.startswith(prefix):
            subject = subject[len(prefix):]
            break
    return subject == asset_id


def _asset_owned(refset: Any | None, asset_id: str, *roles: str) -> bool:
    owners = [item for role in roles for item in _control_items(refset, role)]
    return any(_subject_matches(getattr(item, "subject_ref", None), asset_id)
               for item in owners)


def _asset_field(
    ids: list[str],
    bible: dict[str, dict[str, Any]],
    *,
    authored_names: bool,
    refset: Any | None = None,
) -> str:
    parts: list[str] = []
    for asset_id in ids:
        entry = bible.get(asset_id) or {}
        entry = entry if isinstance(entry, dict) else {}
        name = str(entry.get("name") or asset_id).strip()
        identity_owned = _asset_owned(
            refset, asset_id, "character_identity", "face"
        )
        costume_owned = _asset_owned(refset, asset_id, "costume")
        projected = entry
        if identity_owned:
            projected = {
                key: value for key, value in entry.items()
                if key in ("name", "locked", "voice", "personality", "behavior")
                or (_costume_key(key) and not costume_owned)
            }
        elif costume_owned:
            projected = {
                key: value for key, value in entry.items()
                if not (_costume_key(key) or _composite_appearance_key(key))
            }
        rest = _excerpt(projected, skip=("locked", "name"))
        if identity_owned and name != asset_id:
            name = f"{name} [id: {asset_id}]"
        if authored_names:
            parts.append(f"{name} ({rest})" if rest else name)
        else:
            parts.append(f"{name} ({rest})" if rest else name)
    return "; ".join(parts)


def _camera_field(camera: Camera, *, refset: Any | None = None) -> str:
    def phrase(mapping: dict[str, str], value: str | None) -> str:
        value = (value or "").strip()
        return mapping.get(value, value.replace("_", " ")) if value else ""

    framing_owned = _owned(refset, "framing")
    motion_owned = _owned(refset, "motion")
    return ", ".join(part for part in (
        "" if framing_owned else phrase(_SHOT_SIZE, camera.shot_size),
        "" if motion_owned else phrase(_MOVEMENT, camera.movement),
        "" if framing_owned else phrase(_ANGLE, camera.angle),
    ) if part)


def _prefixed(label: str, items: list[Any] | None) -> str:
    values = [text for text in (_fmt(item) for item in (items or [])) if text]
    return f"{label}: {', '.join(values)}" if values else ""


def picture_contract_payload(shot: ShotSpec) -> dict[str, Any]:
    """Picture-bearing v3 fields only; director notes never restage a take."""
    contract = shot.contract
    if contract is None:
        return {}

    payload: dict[str, Any] = {}
    if contract.opening:
        payload["opening"] = list(contract.opening)
    if contract.endpoint:
        payload["endpoint"] = list(contract.endpoint)
    for name, value in (
        ("performance", contract.performance),
        ("physics", contract.physics),
    ):
        section: dict[str, Any] = {}
        if value.required:
            section["required"] = list(value.required)
        if value.avoid:
            section["avoid"] = list(value.avoid)
        if section:
            payload[name] = section
    if contract.sound.cue:
        payload["sound_cue"] = contract.sound.cue
    if contract.acceptance.min_end_hold_ms > 0:
        payload["min_end_hold_ms"] = contract.acceptance.min_end_hold_ms
    return payload


def prompt_contract_sections(
    shot: ShotSpec,
    bible: dict[str, dict[str, Any]],
    *,
    refset: Any | None = None,
) -> dict[str, str]:
    """Compile prompt sections without I/O or adding unauthored facts."""
    contract = shot.contract
    prop_ids = list(dict.fromkeys(shot.props or []))
    performance_required = contract.performance.required if contract else []
    physics_required = contract.physics.required if contract else []
    avoid = list(shot.quality.avoid)
    if contract:
        avoid.extend(contract.performance.avoid)
        avoid.extend(contract.physics.avoid)

    timing: list[str] = []
    if contract and contract.sound.cue:
        timing.append(contract.sound.cue)
    if contract and contract.acceptance.min_end_hold_ms > 0:
        timing.append(
            "hold the final state for at least "
            f"{contract.acceptance.min_end_hold_ms} ms"
        )

    scene_entry = (bible.get(shot.scene) or {}) if shot.scene else {}
    if _owned(refset, "location", "background"):
        scene = ""
    elif _owned(refset, "lighting") and isinstance(scene_entry, dict):
        scene = _excerpt({
            key: value for key, value in scene_entry.items()
            if not _lighting_key(key)
        })
    else:
        scene = _excerpt(scene_entry)

    return {
        "scene": scene,
        "characters": _asset_field(
            shot.characters, bible, authored_names=True, refset=refset
        ),
        "props": _asset_field(prop_ids, bible, authored_names=True),
        "camera": _camera_field(shot.camera, refset=refset),
        "opening": (
            "" if _owned(refset, "pose", "motion")
            else (_fmt(contract.opening) if contract else "")
        ),
        "action": (shot.action.main or "").strip(),
        "emotion": (shot.action.emotion or "").strip(),
        "endpoint": _fmt(contract.endpoint) if contract else "",
        "performance": _fmt(performance_required),
        "physics": _fmt(physics_required),
        "must_show": _prefixed("must clearly show", shot.quality.must_show),
        "avoid": _prefixed("avoid", avoid),
        "dialogue": (shot.dialogue.text or "").strip(),
        "timing": "; ".join(timing),
    }


def expectation_assertions(shot: ShotSpec) -> list[IntentAssertion]:
    """Compile only explicit authored acceptance promises from one shot."""
    assertions: list[IntentAssertion] = []

    def add(
        kind: str,
        polarity: Literal["present", "absent"],
        statement: str,
        check: Literal["external_visual", "external_consistency"],
        source_path: str,
        position: Literal["any", "start", "end"] = "any",
    ) -> None:
        if isinstance(statement, str) and statement.strip():
            assertions.append(
                IntentAssertion(kind, polarity, statement, check, source_path, position)
            )

    for index, statement in enumerate(shot.quality.must_show):
        add("must_show", "present", statement, "external_visual", f"quality.must_show[{index}]")
    for index, statement in enumerate(shot.quality.avoid):
        add("avoid", "absent", statement, "external_visual", f"quality.avoid[{index}]")
    for index, statement in enumerate(shot.continuity.locks):
        add("lock", "present", statement, "external_consistency", f"continuity.locks[{index}]")

    contract = shot.contract
    if contract is None:
        return assertions
    for index, statement in enumerate(contract.opening):
        add(
            "opening", "present", statement, "external_consistency",
            f"contract.opening[{index}]", "start",
        )
    if contract.acceptance.action_required:
        add("action", "present", shot.action.main, "external_visual", "action.main")
    for index, statement in enumerate(contract.endpoint):
        add(
            "endpoint", "present", statement, "external_consistency",
            f"contract.endpoint[{index}]", "end",
        )
    for section_name, section in (
        ("performance", contract.performance),
        ("physics", contract.physics),
    ):
        for index, statement in enumerate(section.required):
            add(
                f"{section_name}_required", "present", statement, "external_visual",
                f"contract.{section_name}.required[{index}]",
            )
        for index, statement in enumerate(section.avoid):
            add(
                f"{section_name}_avoid", "absent", statement, "external_visual",
                f"contract.{section_name}.avoid[{index}]",
            )
    if contract.acceptance.min_end_hold_ms > 0:
        add(
            "end_hold",
            "present",
            f"hold final state for at least {contract.acceptance.min_end_hold_ms} ms",
            "external_consistency",
            "contract.acceptance.min_end_hold_ms",
            "end",
        )
    return assertions


def director_contract_view(shot: ShotSpec) -> dict[str, Any]:
    """Human/AI director summary; never provider input or a staleness source."""
    contract = shot.contract
    if contract is None:
        return {
            "action": shot.action.model_dump(),
            "camera": shot.camera.model_dump(),
            "must_show": list(shot.quality.must_show),
        }
    return {
        "purpose": contract.purpose,
        "viewer_must_perceive": contract.viewer_must_perceive,
        "opening": list(contract.opening),
        "action": shot.action.model_dump(),
        "endpoint": list(contract.endpoint),
        "performance": contract.performance.model_dump(),
        "physics": contract.physics.model_dump(),
        "sound": contract.sound.model_dump(),
        "control": contract.control.model_dump(exclude_none=True),
        "risk": contract.risk.model_dump(),
        "acceptance": contract.acceptance.model_dump(),
        "proof_shot": contract.proof_shot,
    }
