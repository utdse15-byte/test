"""Narrative authoring contracts stored in project truth YAML."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from .idents import validate_safe_segment
from .model_base import ManjuModel


class SceneCharacterState(ManjuModel):
    knowledge: list[str] = Field(default_factory=list)
    intention: list[str] = Field(default_factory=list)
    emotion_residue: list[str] = Field(default_factory=list)
    body: list[str] = Field(default_factory=list)
    props: dict[str, str] = Field(default_factory=dict)


class SceneContract(ManjuModel):
    format: Literal["manju.scene-contract/v1"] = "manju.scene-contract/v1"
    id: str
    title: str = ""
    location_ref: str | None = None
    time: str = ""
    purpose: str = ""
    entry_state: dict[str, SceneCharacterState] = Field(default_factory=dict)
    irreversible_change: list[str] = Field(default_factory=list)
    exit_state: dict[str, SceneCharacterState] = Field(default_factory=dict)
    carry_forward: list[str] = Field(default_factory=list)
    proof_scene: bool = False

    @model_validator(mode="before")
    @classmethod
    def _scene_has_no_shot_owner(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "shots" in value:
            raise ValueError(
                "SceneContract must not contain 'shots'; membership is derived from "
                "shots/index.yaml order and ShotSpec.scene_id"
            )
        return value

    @model_validator(mode="after")
    def _safe_id(self) -> "SceneContract":
        validate_safe_segment(self.id, label="scene_id")
        return self


class RequiredAvoid(ManjuModel):
    required: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class SoundContract(ManjuModel):
    cue: str = ""
    diegetic: list[str] = Field(default_factory=list)
    music: str = ""


class ControlContract(ManjuModel):
    production_method: Literal[
        "text_gen",
        "image_to_video",
        "start_end_frame",
        "motion_reference",
        "video_to_video",
        "2d_previs",
        "3d_previs",
        "live_action",
        "hybrid",
        "still_plus_motion",
        "composite",
        "manual",
    ] | None = None
    motion_source: Literal[
        "prompt",
        "live_action",
        "2d",
        "3d",
        "video_ref",
        "keyframes",
        "manual",
    ] | None = None
    primary_uncertainty: str = ""


class RiskContract(ManjuModel):
    primary: str = ""
    fallback_staging: str = ""


class AcceptanceContract(ManjuModel):
    action_required: bool = True
    min_end_hold_ms: int = 0


class SubjectStateFact(ManjuModel):
    statement: str
    subject_ref: str | None = None


def canonical_subject_scope(value: Any, *, role: str | None = None) -> str | None:
    """Canonical typed scope; the id portion remains case-preserving."""
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    for prefix, canonical in (
        ("character:", "character"), ("character/", "character"),
        ("prop:", "prop"), ("prop/", "prop"),
        ("asset:", "asset"), ("asset/", "asset"),
        ("scene:", "scene"), ("scene/", "scene"),
    ):
        if lowered.startswith(prefix):
            subject_id = raw[len(prefix):].strip()
            if not subject_id:
                return None
            if canonical == "asset" and role in {
                "character_identity", "face", "costume", "pose", "motion"
            }:
                canonical = "character"
            elif canonical == "asset" and role == "prop":
                canonical = "prop"
            return f"{canonical}:{subject_id}"
    return raw


def state_fact_statement(fact: str | SubjectStateFact | Mapping[str, Any]) -> str:
    if isinstance(fact, str):
        return fact
    if isinstance(fact, SubjectStateFact):
        return fact.statement
    if isinstance(fact, Mapping):
        return str(fact.get("statement", fact.get("text", "")) or "")
    return str(fact)


def state_fact_subject_scope(
    fact: str | SubjectStateFact | Mapping[str, Any],
) -> str | None:
    if isinstance(fact, str):
        return None
    if isinstance(fact, SubjectStateFact):
        return canonical_subject_scope(fact.subject_ref)
    if isinstance(fact, Mapping):
        return canonical_subject_scope(fact.get("subject_ref"))
    return None


def state_fact_payload(
    fact: str | SubjectStateFact | Mapping[str, Any],
) -> str | dict[str, str]:
    """Stable JSON/YAML projection; legacy string facts stay exact strings."""
    if isinstance(fact, str):
        return fact
    payload = {"statement": state_fact_statement(fact)}
    scope = state_fact_subject_scope(fact)
    if scope:
        payload["subject_ref"] = scope
    return payload


class ShotContract(ManjuModel):
    purpose: str = ""
    viewer_must_perceive: str = ""
    opening: list[str | SubjectStateFact] = Field(default_factory=list)
    endpoint: list[str] = Field(default_factory=list)
    performance: RequiredAvoid = Field(default_factory=RequiredAvoid)
    physics: RequiredAvoid = Field(default_factory=RequiredAvoid)
    sound: SoundContract = Field(default_factory=SoundContract)
    control: ControlContract = Field(default_factory=ControlContract)
    risk: RiskContract = Field(default_factory=RiskContract)
    acceptance: AcceptanceContract = Field(default_factory=AcceptanceContract)
    proof_shot: bool = False


def shot_prop_refs(shot: Mapping[str, Any]) -> list[str]:
    """Merge new ``props`` and legacy ``prop:`` locks in stable authored order."""
    refs: list[str] = []

    props = shot.get("props")
    for prop_id in props if isinstance(props, list) else []:
        if isinstance(prop_id, str) and prop_id and prop_id not in refs:
            refs.append(prop_id)

    continuity = shot.get("continuity")
    locks = continuity.get("locks") if isinstance(continuity, Mapping) else None
    for entry in locks if isinstance(locks, list) else []:
        if not isinstance(entry, str) or not entry.startswith("prop:"):
            continue
        prop_id = entry.split(":", 1)[1].strip()
        if prop_id and prop_id not in refs:
            refs.append(prop_id)
    return refs


def uses_new_and_legacy_props(shot: Mapping[str, Any]) -> bool:
    """Whether a shot uses both prop-registration forms during migration."""
    props = shot.get("props")
    continuity = shot.get("continuity")
    locks = continuity.get("locks") if isinstance(continuity, Mapping) else None
    return bool(props) and any(
        isinstance(entry, str) and entry.startswith("prop:") and entry[5:].strip()
        for entry in locks if isinstance(locks, list)
    )
