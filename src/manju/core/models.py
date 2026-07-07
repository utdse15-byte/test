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
    # Which preset kit scaffolded this project ("generic" = plain `manju new`).
    # Purely a record: presets pre-fill files at creation time and never bind
    # the project afterwards — everything they wrote stays hand-editable.
    preset: str = "generic"
    # Agent CLI for `manju auto`: a known name (claude/codex/gemini/qwen/aider)
    # or a template like "claude -p {prompt}". None → MANJU_AGENT env, then a
    # PATH probe over the known agents (src/manju/agents.py).
    agent: str | None = None


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
    # director annotations per take (Frame.io-style review notes, kept as
    # truth text §3: one reviewable YAML line per note, empty text deletes).
    # HUMAN truth — never AI-overwritten; lives inside the shot file so
    # `manju check` validates it and gc/pack never touch it.
    take_notes: dict[str, str] = Field(default_factory=dict)


class SourceAudio(ManjuModel):
    """Per-shot control over the imported footage's OWN sound (round-T).

    A human's clip may carry usable diegetic audio (an actor's line, room
    tone) or unwanted noise the mixer wants down or gone. ``gain_db`` shifts
    that source track's level; ``mute`` drops it entirely. Both are folded into
    the segment-normalize cache key ONLY when non-default, so an untouched shot
    keeps a byte-identical segment (its cached render is reused) and a changed
    one re-normalizes exactly one segment (§7, §14 incremental design).

    Deliberately NOT part of spec_payload (core/spec.py): the footage's own
    audio never restages the PICTURE, so tweaking it must never make a video
    take look stale.
    """

    gain_db: float = 0.0
    mute: bool = False


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
    # Imported-footage own-audio control (round-T). Default (0 dB, unmuted) is a
    # no-op: the segment cache key omits it, so today's projects are byte-stable.
    source_audio: SourceAudio = Field(default_factory=SourceAudio)
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
    # the CANONICAL spec dict (core/spec.spec_payload) captured at generation
    # time — the evidence behind "why is this stale": diffing it against the
    # current payload names the exact fields that moved (build/stale). Advisory
    # and purely additive: absent (None, dropped by exclude_none on write) on
    # takes made before this landed and on manual imports (never stale), so it
    # never perturbs spec_hash or the content key of an existing take.
    spec_snapshot: dict[str, Any] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    remote: RemoteJobInfo | None = None
    compiled_prompt: str | None = None
    probe: ProbeInfo | None = None
    qc: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None  # original path for manual imports
    created_at: str | None = None


class VoiceTakeSidecar(ManjuModel):
    """Lineage of a generated VOICE take (M3 TTS). voice_hash is the
    staleness anchor (core.spec.compute_voice_hash): text + speaker + the
    speaker's voice-shaping bible fields — mirroring spec_hash for video.
    A hand-dropped voice file without a sidecar is treated like a manual
    take: used as-is and never auto-invalidated (§4.3)."""

    provider: str
    voice_hash: str
    params: dict[str, Any] = Field(default_factory=dict)
    remote: RemoteJobInfo | None = None
    probe: ProbeInfo | None = None
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
    # BGM in-point + fade-in (round-T). ``start_offset_ms`` seeks INTO the source
    # before it is laid under the picture (skip a long intro / land on the drop);
    # ``fade_in_ms`` ramps it up from silence at the film's start. Both default
    # to 0 — an untouched project renders a byte-identical audio filtergraph
    # (pinned in tests) — and travel onto the compiled AudioClip.
    start_offset_ms: int = 0
    fade_in_ms: int = 0
    # Sidechain ducking shape (render.py _build_audio_graph). Defaults are the
    # historical hardcoded constants — a project that never touches them renders
    # a byte-identical filtergraph (pinned in tests). threshold/ratio are the
    # compressor knee/amount; attack/release are in milliseconds.
    duck_threshold: float = 0.05
    duck_ratio: float = 8.0
    duck_attack_ms: int = 5
    duck_release_ms: int = 250


class SfxClipSpec(ManjuModel):
    """One sound effect placed on the timeline by the audio policy.

    `at` anchors the clip: "" means an absolute offset from t=0;
    "shot:<id>" the start of that shot's clip; "shot:<id>:end" its end.
    `offset_ms` is added to the anchor either way.
    """

    source: str  # project-relative audio path (a human asset — never AI-touched)
    at: str = ""
    offset_ms: int = 0
    gain_db: float = -6.0


class AmbientRules(ManjuModel):
    """A looped bed (room tone / atmosphere) under the whole film."""

    source: str | None = None
    gain_db: float = -24.0
    ducking: bool = False
    fade_out_ms: int = 1000
    # In-point + fade-in, same knobs (and same 0 defaults) as MusicRules — an
    # ambient bed can also seek into its source and fade up from silence (§7 ⑤).
    start_offset_ms: int = 0
    fade_in_ms: int = 0
    # Sidechain ducking shape, same knobs (and same historical defaults) as
    # MusicRules — an ambient bed ducks under speech exactly like BGM (§7 ⑤).
    duck_threshold: float = 0.05
    duck_ratio: float = 8.0
    duck_attack_ms: int = 5
    duck_release_ms: int = 250


class AudioMixRules(ManjuModel):
    """The project's default audio policy: how voice, SFX, ambient and
    transition sounds sit in the mix. BGM keeps its own MusicRules."""

    voice_gain_db: float = 0.0
    sfx: list[SfxClipSpec] = Field(default_factory=list)
    ambient: AmbientRules = Field(default_factory=AmbientRules)
    transition_sound: str | None = None  # one hit at every interior clip boundary
    transition_gain_db: float = -12.0


class CaptionRules(ManjuModel):
    enabled: bool = True
    max_chars_per_line: int = 18
    max_lines: int = 2
    style: str = "default"
    # §3: captions.srt 可手改并标记 manual — in manual mode the SRT is human
    # truth: the compiler's output goes to captions.generated.srt and the
    # burned ASS is recompiled FROM the human SRT (same takeover shape as §6)
    mode: Literal["compiled", "manual"] = "compiled"


class TitleCardRules(ManjuModel):
    enabled: bool = False
    text: str = ""
    duration_ms: int = 1500
    template: str = "chapter"


class TimelineRules(ManjuModel):
    mode: Literal["compiled", "manual"] = "compiled"
    timing: TimingRules = Field(default_factory=TimingRules)
    transition_default: TransitionSpec | None = Field(default_factory=TransitionSpec)
    music: MusicRules = Field(default_factory=MusicRules)
    audio: AudioMixRules = Field(default_factory=AudioMixRules)
    captions: CaptionRules = Field(default_factory=CaptionRules)
    title_card: TitleCardRules = Field(default_factory=TitleCardRules)


# ----------------------------------------------------------- packaging.yaml


class PackagingCard(ManjuModel):
    """An intro/outro card that becomes a real segment in the timeline."""

    enabled: bool = False
    template: str = "chapter"  # card template; html_card preferred, drawtext floor (§8.4)
    text: str = ""
    subtext: str = ""
    duration_ms: int = 2000


class CoverSpec(ManjuModel):
    """The film's cover image: a frame pulled from the final, or a card."""

    mode: Literal["frame", "card"] = "frame"
    frame_ms: int = 0  # frame mode: which timestamp of the final to grab
    text: str = ""  # card mode: cover title text
    template: str = "chapter"


class TeaserSpec(ManjuModel):
    """A short social-display cut sliced out of the current final."""

    enabled: bool = False
    from_ms: int = 0
    duration_ms: int = 5000


class InfoCardSpec(ManjuModel):
    """Chapter/role/info cards riding the existing overlay track.

    `at`/`offset_ms` use the same anchor grammar as SfxClipSpec.
    """

    kind: str = "chapter"  # chapter | role | info
    text: str = ""
    at: str = ""
    offset_ms: int = 0
    duration_ms: int = 1500
    template: str = "chapter"


# ---------------------------------------------- branding overlays (round-Q)
# All additive to PackagingSpec, all default-off: an untouched packaging.yaml
# stays a no-op (byte-identical timeline). These ride the existing overlay
# track and burn in the FINAL pass only (never a per-segment cache), so a
# human's shot media is never touched. Corner inset / watermark opacity follow
# CapCut/JianYing 角标 & watermark conventions (inset ~2-3% of frame width;
# watermark semi-transparent).


def _pct_0_100(v: float) -> float:
    """size_pct sanity: a scale that isn't a positive fraction of the frame is
    a config error (validated at the model, not QC — cleaner than a runtime
    check). 0 < v <= 100."""
    if not (0 < float(v) <= 100):
        raise ValueError(f"size_pct must be in (0, 100], got {v!r}")
    return float(v)


def _opacity_0_1(v: float) -> float:
    """opacity sanity: 0..1 inclusive (fully transparent .. fully opaque)."""
    if not (0.0 <= float(v) <= 1.0):
        raise ValueError(f"opacity must be in [0, 1], got {v!r}")
    return float(v)


class LogoSpec(ManjuModel):
    """A brand logo/角标 burned into a frame corner. ``image`` is a HUMAN asset
    (project-relative) — never generated, never rewritten by the engine."""

    enabled: bool = False
    image: str = ""  # project-relative human asset path
    corner: Literal["tl", "tr", "bl", "br"] = "tr"
    size_pct: float = 12.0   # of frame width
    margin_pct: float = 2.5  # corner inset, of frame width
    opacity: float = 1.0
    from_ms: int = 0
    duration_ms: int | None = None  # None = full film

    @field_validator("size_pct")
    @classmethod
    def _check_size(cls, v: float) -> float:
        return _pct_0_100(v)

    @field_validator("opacity")
    @classmethod
    def _check_opacity(cls, v: float) -> float:
        return _opacity_0_1(v)


class WatermarkSpec(ManjuModel):
    """A semi-transparent watermark over the whole film — ``text`` OR ``image``.
    ``image`` (when set) is a HUMAN asset, never generated."""

    enabled: bool = False
    text: str = ""
    image: str = ""  # project-relative human asset path (overrides text if set)
    opacity: float = 0.35
    position: Literal["center", "diagonal_tile"] = "center"
    size_pct: float = 30.0  # of frame width

    @field_validator("size_pct")
    @classmethod
    def _check_size(cls, v: float) -> float:
        return _pct_0_100(v)

    @field_validator("opacity")
    @classmethod
    def _check_opacity(cls, v: float) -> float:
        return _opacity_0_1(v)


class BadgeSpec(ManjuModel):
    """A small rounded text chip (角标) in a corner — drawtext-based."""

    enabled: bool = False
    text: str = ""
    corner: Literal["tl", "tr", "bl", "br"] = "tl"
    from_ms: int = 0
    duration_ms: int | None = None  # None = full film


class CtaSpec(ManjuModel):
    """A call-to-action chip shown in the film's closing window."""

    enabled: bool = False
    text: str = "关注 / FOLLOW"
    at_end_ms: int = 3000  # window before the film ends
    position: Literal["bottom", "center"] = "bottom"


class PackagingSpec(ManjuModel):
    intro: PackagingCard = Field(default_factory=PackagingCard)
    outro: PackagingCard = Field(default_factory=PackagingCard)
    cover: CoverSpec = Field(default_factory=CoverSpec)
    teaser: TeaserSpec = Field(default_factory=TeaserSpec)
    info_cards: list[InfoCardSpec] = Field(default_factory=list)
    # Branding overlays (round-Q) — all additive, all default-off.
    logo: LogoSpec = Field(default_factory=LogoSpec)
    watermark: WatermarkSpec = Field(default_factory=WatermarkSpec)
    badge: BadgeSpec = Field(default_factory=BadgeSpec)
    cta: CtaSpec = Field(default_factory=CtaSpec)


# ------------------------------------------------------------- TimelineSpec


class VideoClip(ManjuModel):
    shot: str
    take: str
    source: str  # project-relative media path
    start_ms: int
    duration_ms: int
    transition_out: TransitionSpec | None = None
    # Imported-footage own-audio (round-T), carried from ShotSpec.source_audio by
    # the compiler. Applied at SEGMENT-normalize time (folded into the segment
    # cache key only when non-default), so the footage's own sound rides the
    # concatenated [0:a] bus at the level the mixer chose — or not at all. The
    # final content key carries these via the ordered segment keys, so a clip at
    # its defaults leaves both the segment cache AND the content key byte-stable.
    source_gain_db: float = 0.0
    source_mute: bool = False


class OverlayClip(ManjuModel):
    kind: str = "title_card"
    subkind: str = ""  # info_card semantic kind (chapter|role|info) → burn position
    template: str = "chapter"
    text: str = ""
    start_ms: int = 0
    duration_ms: int = 1500
    # Branding overlays (round-Q): logo/watermark/badge/cta ride this same
    # track. These fields carry the burn geometry the render needs; they stay at
    # their defaults for the pre-existing title_card/info_card kinds.
    source: str = ""       # image path for image overlays (logo, image watermark)
    corner: str = ""       # tl|tr|bl|br for logo/badge
    size_pct: float = 0.0  # branding scale, % of frame width
    margin_pct: float = 0.0  # branding corner inset, % of frame width
    opacity: float = 1.0   # branding opacity (0..1)
    position: str = ""     # watermark (center|diagonal_tile) / cta (bottom|center)


class AudioClip(ManjuModel):
    source: str
    start_ms: int
    duration_ms: int | None = None
    gain_db: float = 0.0
    ducking: bool = False
    fade_out_ms: int = 0
    loop: bool = False  # loop the source to fill duration_ms (ambient beds)
    # In-point + fade-in (round-T), carried from Music/AmbientRules by the
    # compiler. ``start_offset_ms`` seeks into the SOURCE before it is laid down
    # (head atrim + PTS reset); ``fade_in_ms`` ramps up from silence at the clip
    # start. Both default 0 — an unset clip renders exactly today's filtergraph.
    start_offset_ms: int = 0
    fade_in_ms: int = 0
    # Per-clip sidechain ducking shape, carried from Music/AmbientRules by the
    # compiler so the render renders each clip's own knobs. Defaults are the
    # historical constants: an unducked clip (or one compiled before these
    # fields existed) renders exactly today's filtergraph.
    duck_threshold: float = 0.05
    duck_ratio: float = 8.0
    duck_attack_ms: int = 5
    duck_release_ms: int = 250


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
    sfx: list[AudioClip] = Field(default_factory=list)
    ambient: list[AudioClip] = Field(default_factory=list)
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
        "packaging": PackagingSpec.model_json_schema(),
    }
