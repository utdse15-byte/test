"""Core data models (§4). Every truth file has a Pydantic model; `manju check`
validates all of them plus referential integrity and locks.

Models are forgiving on extra keys (humans and agents both edit these files),
strict on the fields the engine actually computes with.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SHOT_SIZES = (
    "extreme_wide",
    "wide",
    "medium",
    "close_up",
    "extreme_close_up",
)

FALLBACK_STEPS = (
    "image_to_video",
    "first_last_frame",
    "still_frame_motion",
    "comic_panel",
    "caption_card",
)


class ManjuModel(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)


# ---------------------------------------------------------------- project.yaml


class BudgetConfig(ManjuModel):
    limit: float | None = None
    currency: str = "CNY"


class ProjectConfig(ManjuModel):
    name: str
    width: int = 1080
    height: int = 1920
    fps: int = 24
    mode: Literal["manual", "copilot", "autopilot"] = "copilot"
    ask_before: list[str] = Field(
        default_factory=lambda: ["expensive_generation", "final_export", "lock_change"]
    )
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    export_profiles: list[str] = Field(default_factory=lambda: ["srt"])


# ------------------------------------------------------------------- ShotSpec


class Camera(ManjuModel):
    shot_size: str = "medium"
    movement: str = "static"
    angle: str = "eye_level"

    @field_validator("shot_size")
    @classmethod
    def _known_shot_size(cls, v: str) -> str:
        if v not in SHOT_SIZES:
            raise ValueError(f"shot_size must be one of {SHOT_SIZES}, got {v!r}")
        return v


class Action(ManjuModel):
    main: str = ""
    emotion: str = ""


class Dialogue(ManjuModel):
    speaker: str = ""
    text: str = ""


class Continuity(ManjuModel):
    prev: str | None = None
    locks: list[str] = Field(default_factory=list)


class Quality(ManjuModel):
    must_show: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class Generation(ManjuModel):
    strategy: str = "best_available"  # or "manual" (wait for human import)
    candidates: int = 1
    fallback: list[str] = Field(
        default_factory=lambda: ["image_to_video", "still_frame_motion", "caption_card"]
    )
    prompt_override: str | None = None
    provider: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ShotStatus(ManjuModel):
    selected_take: str | None = None
    approved: bool = False


class ShotSpec(ManjuModel):
    id: str
    scene: str | None = None
    characters: list[str] = Field(default_factory=list)
    duration: float | Literal["auto"] = "auto"  # seconds when numeric
    camera: Camera = Field(default_factory=Camera)
    action: Action = Field(default_factory=Action)
    dialogue: Dialogue = Field(default_factory=Dialogue)
    continuity: Continuity = Field(default_factory=Continuity)
    quality: Quality = Field(default_factory=Quality)
    generation: Generation = Field(default_factory=Generation)
    status: ShotStatus = Field(default_factory=ShotStatus)
    # Value-hash locks (§5): dotted path -> sha256 of the canonical value at
    # lock time. A hand-written bare list is accepted but flagged "unsealed"
    # by `manju check` until `manju lock` seals it with real hashes.
    locked: dict[str, str] = Field(default_factory=dict)

    @field_validator("locked", mode="before")
    @classmethod
    def _coerce_locked(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if isinstance(v, list):
            return {str(path): "" for path in v}
        return v


class ShotIndex(ManjuModel):
    """shots/index.yaml — shot order plus global defaults."""

    order: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------- Take sidecar


class ProbeInfo(ManjuModel):
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None


class RemoteJobInfo(ManjuModel):
    job_id: str | None = None
    cost: float | None = None
    currency: str = "CNY"


class TakeSidecar(ManjuModel):
    provider: str
    spec_hash: str  # "sha256:..." or "manual" for human imports (§4.3)
    params: dict[str, Any] = Field(default_factory=dict)
    remote: RemoteJobInfo | None = None
    compiled_prompt: str | None = None
    probe: ProbeInfo | None = None
    qc: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None  # original path for manual imports
    created_at: str | None = None


# ---------------------------------------------------------------------- Bible


class BibleEntry(ManjuModel):
    """Characters / scenes / props are free-form docs with optional locks."""

    name: str | None = None
    locked: dict[str, str] = Field(default_factory=dict)

    @field_validator("locked", mode="before")
    @classmethod
    def _coerce_locked(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if isinstance(v, list):
            return {str(path): "" for path in v}
        return v


# ------------------------------------------------------------ timeline/rules


class TransitionSpec(ManjuModel):
    type: str = "fade"
    duration_ms: int = 300


class TimingRules(ManjuModel):
    padding_before_ms: int = 200
    padding_after_ms: int = 300
    min_shot_ms: int = 1200
    max_shot_ms: int = 10000
    default_shot_ms: int = 3000


class MusicRules(ManjuModel):
    source: str | None = None
    gain_db: float = -18.0
    ducking: bool = True
    fade_out_ms: int = 1500


class CaptionRules(ManjuModel):
    enabled: bool = True
    max_chars_per_line: int = 18
    max_lines: int = 2
    style: str = "default"


class TimelineRules(ManjuModel):
    mode: Literal["compiled", "manual"] = "compiled"
    timing: TimingRules = Field(default_factory=TimingRules)
    transition_default: TransitionSpec | None = Field(default_factory=TransitionSpec)
    music: MusicRules = Field(default_factory=MusicRules)
    captions: CaptionRules = Field(default_factory=CaptionRules)


# ------------------------------------------------------------- TimelineSpec


class VideoClip(ManjuModel):
    shot: str
    take: str
    source: str  # project-relative media path
    start_ms: int
    duration_ms: int
    transition_out: TransitionSpec | None = None


class OverlayClip(ManjuModel):
    kind: str = "title_card"
    template: str = "chapter"
    text: str = ""
    start_ms: int = 0
    duration_ms: int = 1500


class AudioClip(ManjuModel):
    source: str
    start_ms: int
    duration_ms: int | None = None
    gain_db: float = 0.0
    ducking: bool = False
    fade_out_ms: int = 0


class CaptionLine(ManjuModel):
    start_ms: int
    end_ms: int
    text: str
    speaker: str = ""


class TimelineTracks(ManjuModel):
    video: list[VideoClip] = Field(default_factory=list)
    overlay: list[OverlayClip] = Field(default_factory=list)
    voice: list[AudioClip] = Field(default_factory=list)
    music: list[AudioClip] = Field(default_factory=list)
    captions: list[CaptionLine] = Field(default_factory=list)


class TimelineMeta(ManjuModel):
    compiled_from: str = ""
    mode: Literal["compiled", "manual"] = "compiled"


class Timeline(ManjuModel):
    meta: TimelineMeta = Field(default_factory=TimelineMeta)
    fps: int = 24
    width: int = 1080
    height: int = 1920
    duration_ms: int = 0
    tracks: TimelineTracks = Field(default_factory=TimelineTracks)


def export_json_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schemas for docs and external validation (§12)."""
    return {
        "project": ProjectConfig.model_json_schema(),
        "shot": ShotSpec.model_json_schema(),
        "shot_index": ShotIndex.model_json_schema(),
        "take_sidecar": TakeSidecar.model_json_schema(),
        "timeline_rules": TimelineRules.model_json_schema(),
        "timeline": Timeline.model_json_schema(),
    }
