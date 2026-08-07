"""Narrative authoring contracts stored in project truth YAML."""

from __future__ import annotations

import warnings
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


# ---------------------------------------------------------------------------
# v5.0 dramatic/screen authoring (additive; v1 above is never reinterpreted)

SCENE_EXPERIENCE_ROLES = frozenset({
    "event", "reveal", "decision", "consequence", "orientation", "discovery",
    "observation", "withheld_reveal", "anticipation", "reaction", "aftermath",
    "geography", "boundary_shift", "distance_shift", "hold", "breath", "reset",
    "acceleration", "transition", "motif", "echo", "contrast", "metaphor",
    "ellipsis", "bridge", "offscreen_action", "memory", "dream",
    "sensory_subjective", "eyeline", "cutaway", "edit_handle", "owner_defined",
})

SCREEN_ROLES = SCENE_EXPERIENCE_ROLES
SCREEN_INTENT_SCHEMA = "manju.screen-intent/v1"


class ScreenIntent(ManjuModel):
    """Authoring-only screen experience projection nested in ShotContract."""

    role: str
    justification: str = ""
    experience_beat_refs: list[str] = Field(default_factory=list)
    primary_carrier: str = ""
    supporting_carriers: list[str] = Field(default_factory=list)
    attention: dict[str, str] = Field(default_factory=dict)
    duration: dict[str, str] = Field(default_factory=dict)
    relation_from_previous: dict[str, Any] = Field(default_factory=dict)
    ambiguity: dict[str, Any] = Field(default_factory=dict)
    motif_ref: str | None = None

    @model_validator(mode="after")
    def _warn_unknown_role(self) -> "ScreenIntent":
        if self.role not in SCREEN_ROLES:
            warnings.warn(
                f"unknown Screen Intent role {self.role!r}; retained as authored text",
                UserWarning,
                stacklevel=2,
            )
        return self


class SceneCharacterStateV2(ManjuModel):
    knowledge: list[str] = Field(default_factory=list)
    beliefs: list[str] = Field(default_factory=list)
    intentions: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)
    emotion_residue: list[str] = Field(default_factory=list)
    body: list[str] = Field(default_factory=list)
    props: dict[str, str] = Field(default_factory=dict)


class SceneChoiceV2(ManjuModel):
    subject_ref: str
    statement: str
    source_span_ref: str | None = None


class SceneCostV2(ManjuModel):
    statement: str
    delayed: bool = False
    source_span_ref: str | None = None


class SceneStateChangeV2(ManjuModel):
    subject_ref: str
    dimension: str
    before: str = ""
    after: str = ""
    audience_visibility: str = ""
    character_visibility: str = ""
    source_span_ref: str | None = None


class SceneThreadChangeV2(ManjuModel):
    thread_id: str
    operation: str
    statement: str = ""
    source_span_ref: str | None = None


class SceneExperienceBeat(ManjuModel):
    id: str
    role: str
    viewer_task: str = ""
    carriers: list[str] = Field(default_factory=list)
    required: bool = False
    source_span_refs: list[str] = Field(default_factory=list)
    duration_mode: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _warn_unknown_role(self) -> "SceneExperienceBeat":
        if self.role not in SCENE_EXPERIENCE_ROLES:
            warnings.warn(
                f"unknown Scene Experience role {self.role!r}; retained as authored text",
                UserWarning,
                stacklevel=2,
            )
        return self


class SceneExperienceDesign(ManjuModel):
    perceptual_question: str = ""
    temporal_mode: str = ""
    attention_curve: dict[str, Any] = Field(default_factory=dict)
    rhythm_curve: list[str] = Field(default_factory=list)
    channel_plan: dict[str, list[str]] = Field(default_factory=dict)
    sensory_progression: list[str] = Field(default_factory=list)
    motif_changes: list[dict[str, Any]] = Field(default_factory=list)
    ambiguity_protections: list[str] = Field(default_factory=list)
    transition_in: dict[str, Any] = Field(default_factory=dict)
    transition_out: dict[str, Any] = Field(default_factory=dict)
    beats: list[SceneExperienceBeat] = Field(default_factory=list)


class SceneDirectionV2(ManjuModel):
    audience_alignment: dict[str, Any] = Field(default_factory=dict)
    reveal_strategy: dict[str, Any] = Field(default_factory=dict)
    blocking: dict[str, Any] = Field(default_factory=dict)
    camera_logic: list[str] = Field(default_factory=list)
    sound_strategy: list[str] = Field(default_factory=list)
    production_strategy: list[str] = Field(default_factory=list)
    fallbacks: list[str] = Field(default_factory=list)
    experience: SceneExperienceDesign | None = None


class SceneDramaticChangeV2(ManjuModel):
    summary: str = ""
    choice: SceneChoiceV2 | None = None
    cost: SceneCostV2 | None = None
    state_changes: list[SceneStateChangeV2] = Field(default_factory=list)
    thread_changes: list[SceneThreadChangeV2] = Field(default_factory=list)


class SceneContractV2(ManjuModel):
    format: Literal["manju.scene-contract/v2"] = "manju.scene-contract/v2"
    id: str
    title: str = ""
    location_ref: str | None = None
    time: str = ""
    purpose: str = ""
    entry_state: dict[str, SceneCharacterStateV2] = Field(default_factory=dict)
    change: SceneDramaticChangeV2 = Field(default_factory=SceneDramaticChangeV2)
    exit_state: dict[str, SceneCharacterStateV2] = Field(default_factory=dict)
    carry_forward: list[str] = Field(default_factory=list)
    direction: SceneDirectionV2 | None = None
    coverage_exceptions: list[dict[str, Any]] = Field(default_factory=list)
    proof_scene: bool = False

    @model_validator(mode="before")
    @classmethod
    def _scene_has_no_shot_owner(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "shots" in value:
            raise ValueError(
                "SceneContract must not contain 'shots'; shot membership is derived"
            )
        return value

    @model_validator(mode="after")
    def _safe_id(self) -> "SceneContractV2":
        validate_safe_segment(self.id, label="scene_id")
        return self


def parse_scene_contract(value: Any) -> SceneContract | SceneContractV2:
    """Parse either historical v1 or additive v2 without changing v1 semantics."""
    if isinstance(value, (SceneContract, SceneContractV2)):
        return value
    if isinstance(value, Mapping) and value.get("format") == "manju.scene-contract/v2":
        return SceneContractV2.model_validate(value)
    return SceneContract.model_validate(value)


def scene_change_present(scene: SceneContract | SceneContractV2) -> bool:
    if isinstance(scene, SceneContractV2):
        return bool(scene.change.summary or scene.change.choice or scene.change.cost
                    or scene.change.state_changes or scene.change.thread_changes)
    return bool(scene.irreversible_change)


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
    screen: ScreenIntent | None = None
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
