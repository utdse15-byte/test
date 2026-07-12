"""Core data models (§4). Every truth file has a Pydantic model; `manju check`
validates all of them plus referential integrity and locks.

Models are forgiving on extra keys (humans and agents both edit these files),
strict on the fields the engine actually computes with.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from .idents import validate_safe_segment

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


# Concurrency quality modes (goal item 14). A project MAY pin a default build
# mode; the `manju build --mode` flag always wins over this. Purely additive and
# default-absent: `build: None` on the ProjectConfig means "engine default"
# (serial generation, no routing bias, today's retry counts) — so a project.yaml
# WITHOUT a build: section behaves byte-identically to before this landed.
BUILD_MODE_NAMES = ("quality", "balanced", "speed")


class BuildConfig(ManjuModel):
    # None = leave it to the engine default (byte-identical to pre-modes);
    # quality | balanced | speed pick the concurrency/retry/strategy-bias knobs
    # in build/modes.py. Validated here so a typo surfaces at `manju check`.
    mode: str | None = None

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, v: str | None) -> str | None:
        if v is None or v in BUILD_MODE_NAMES:
            return v
        raise ValueError(f"build.mode must be one of {BUILD_MODE_NAMES} or unset, got {v!r}")


class ProjectConfig(ManjuModel):
    name: str
    width: int = 1080
    height: int = 1920
    fps: int = 24
    mode: Literal["manual", "copilot", "autopilot"] = "copilot"
    # Default build mode (goal 14); None keeps today's behaviour. `--mode` wins.
    build: BuildConfig | None = None
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

    # Round W (issue #2/#6): fps/width/height are load-bearing for frame math —
    # fps=0 divides-by-zero in the timeline compiler's frame-grid snap
    # (snap_to_frame_grid), and a non-positive width/height is not a real
    # resolution. `manju check` now rejects these at project.yaml load instead
    # of letting a bad config pass check and crash build/compile. Every real
    # project already has fps/width/height > 0 (the scaffolded defaults are
    # 1080x1920@24), so this is byte-identical for every healthy project.
    @field_validator("width", "height", "fps")
    @classmethod
    def _positive_dims(cls, v: int, info) -> int:
        if v <= 0:
            raise ValueError(
                f"project.{info.field_name} 必须是正整数(实际 {v!r})— fps/width/height "
                "为 0 或负数时,时间线编译的逐帧对齐(frame-grid snap)会除零崩溃,分辨率"
                "也不合法;请把 project.yaml 里的这个字段改成大于 0 的整数"
            )
        return v


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

    # Round W (issue #2): a negative/zero candidate count is meaningless (the
    # provider is asked to generate 0 or a negative number of takes) and used
    # to pass through silently into cost estimation and provider requests.
    @field_validator("candidates")
    @classmethod
    def _positive_candidates(cls, v: int) -> int:
        if v < 1:
            raise ValueError(
                f"generation.candidates 必须 >= 1(实际 {v!r})— 0 或负数没有意义"
                "(相当于让 provider 生成 0 个或负数个候选);请改成 >= 1 的整数"
            )
        return v


REVIEW_STATES = ("needs_review", "in_progress", "approved")


class ShotStatus(ManjuModel):
    selected_take: str | None = None
    approved: bool = False
    # Frame.io-style THREE-STATE review (round U, goal item 3). Additive and
    # byte-stable: default ``None`` means "legacy" — read from ``approved``.
    # Values: needs_review | in_progress | approved. Writing it SYNCS the legacy
    # ``approved`` bool (approved == review=="approved") so every existing reader
    # stays correct. Never part of spec_payload (status is excluded, core/spec.py)
    # — a review change never restages a take or marks it stale. Dropped by
    # ``exclude_none`` on write, so a shot that never sets it is byte-identical.
    review: str | None = None
    # director annotations per take (Frame.io-style review notes, kept as
    # truth text §3: one reviewable YAML line per note, empty text deletes).
    # HUMAN truth — never AI-overwritten; lives inside the shot file so
    # `manju check` validates it and gc/pack never touch it.
    take_notes: dict[str, str] = Field(default_factory=dict)

    @field_validator("review")
    @classmethod
    def _known_review(cls, v: str | None) -> str | None:
        if v is None or v in REVIEW_STATES:
            return v
        raise ValueError(
            f"status.review must be one of {REVIEW_STATES} or unset, got {v!r}"
        )

    @property
    def review_state(self) -> str:
        """The EFFECTIVE three-state review: the explicit ``review`` when set,
        else derived from the legacy ``approved`` bool (True → ``approved``,
        else ``needs_review``). This is what the storyboard 审批 chip renders."""
        if self.review in REVIEW_STATES:
            return self.review
        return "approved" if self.approved else "needs_review"


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
    # Human-readable routing TIER (goal item 15): a director tag like ``draft`` /
    # ``review`` / ``key_shot`` that a routing.yaml ``tiers:`` section maps to a
    # provider priority list, and that routing rules may ``match: {tier: ...}``.
    # A dedicated top-level field (NOT generation.params) is the chosen additive
    # path precisely because spec_payload (core/spec.py) does NOT include it —
    # tagging a shot's tier is a routing choice, so it must never restage the
    # PICTURE or mark existing takes stale. Default None → byte-identical.
    tier: str | None = None
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

    @field_validator("id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        # goal item 11: the id is used as a path segment (shots/<id>.yaml,
        # media/gen/<id>/...) everywhere downstream — validated here so a
        # hand-authored or agent-written shot file can never carry a
        # traversal/absolute-path id past `manju check`.
        return validate_safe_segment(v, label="shot_id")

    @field_validator("locked", mode="before")
    @classmethod
    def _coerce_locked(cls, v: Any) -> dict[str, str]:
        if v is None:
            return {}
        if isinstance(v, list):
            return {str(path): "" for path in v}
        return v

    # Round W (issue #2/#6): a numeric duration <= 0 is not a real shot length —
    # it can make the compiled clip non-advancing (captions/cursor arithmetic
    # assumes forward progress) or outright negative. "auto" (the Literal
    # branch) is untouched — this only bounds the EXPLICIT numeric-seconds form.
    @field_validator("duration")
    @classmethod
    def _positive_duration(cls, v: float | str) -> float | str:
        if isinstance(v, str):
            return v  # "auto" — Literal already rejects any other string
        if v <= 0:
            raise ValueError(
                f"shot.duration 必须大于 0 秒(实际 {v!r})— 0 或负数时长的镜头无法放上"
                "时间线;请改成正数秒数,或删掉该字段/写 \"auto\" 交给引擎按配音+padding 计算"
            )
        return v


class ShotIndex(ManjuModel):
    """shots/index.yaml — shot order plus global defaults."""

    order: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)

    @field_validator("order")
    @classmethod
    def _safe_order(cls, v: list[str]) -> list[str]:
        # goal item 11: every order entry becomes `shots_dir / f"{id}.yaml"`
        # (Project.shot_path) — validated here so a hand-edited index.yaml
        # can never smuggle a traversal id into the shot list (`manju check`
        # reports it as a normal validation error, not a crash).
        for sid in v:
            validate_safe_segment(sid, label="shots/index.yaml order 条目")
        return v


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
    # VIRTUAL TRIM (round-T): the take's WINDOW into its own media file — the
    # [source_in_ms, source_out_ms) region a virtual ``set_inout`` selected
    # WITHOUT re-encoding. The defaults describe the WHOLE file (in 0, out None =
    # to the end), so every take made before this landed — and every generative
    # take — reads back byte-for-byte the same and never participates in a hash
    # (spec_hash is the picture's; the window is a media edit, not a restage).
    # A virtual trim mints a take whose media is a HARDLINK of the source file
    # with these two set, leaving spare HEAD (in>0) and TAIL (out<file_duration)
    # material — the real handles a cross-dissolve needs (media/render.py). The
    # compiler reads them to bound the clip's source material and to seed
    # ``VideoClip.source_in_ms``; the render seeks to the in-point.
    source_in_ms: int = 0
    source_out_ms: int | None = None
    # round W (review #37/#16): which SPEC_VERSION payload `spec_hash` was
    # computed under (core/spec.py). None (dropped by exclude_none on write)
    # means "made before this landed" — read as version 1 EVERYWHERE, so an
    # existing take's freshness is judged by v1 forever (§4.3 conservatism,
    # no mass restage). The generation path (providers/base.py Provider.
    # _register) stamps every NEW take with the current SPEC_VERSION.
    spec_version: int | None = None
    # 08_10_12C WP3: creative lineage for an EXPLICIT recipe-replay redo
    # (`manju redo --from-take X`) — the parent take's name. This is what lets
    # the DERIVED candidate-family view join a redo to its creative family
    # even across a source edit (a redo emits no attempt evidence — registry
    # evidence is None outside a run — so the sidecar is the only durable
    # carrier). Additive and default-absent (dropped by exclude_none on
    # write): every pre-existing take reads back byte-identical, and the
    # field never participates in spec_hash or any content key. Provenance
    # only — never a build/cache/render input.
    redo_of: str | None = None

    # Round W (issue #22): a negative in-point or a reversed/zero-length window
    # (out <= in) is not a legal trim — ``set_inout_take`` (media/repair_ops.py)
    # already enforces exactly this rule before it ever writes a sidecar, so
    # every engine-written take already satisfies it (byte-identical for real
    # projects). This closes the loophole where a hand-edited take_NN.yaml could
    # carry a reversed window that the compiler used to silently squash to 1ms
    # instead of surfacing as an error (core/container.Project.takes degrades a
    # sidecar that fails this to a safe whole-file window + a visible .error).
    @model_validator(mode="after")
    def _check_source_window(self) -> "TakeSidecar":
        if self.source_in_ms < 0:
            raise ValueError(
                f"source_in_ms 不能为负数(实际 {self.source_in_ms})— 裁剪窗口的起点必须 "
                ">= 0;用 manju repair --op inout 重新设置窗口,或修正该 take 的 sidecar 文件"
            )
        if self.source_out_ms is not None and self.source_out_ms <= self.source_in_ms:
            raise ValueError(
                f"source_out_ms({self.source_out_ms})必须大于 source_in_ms"
                f"({self.source_in_ms})— 反向或零长度的裁剪窗口不合法,不能被静默压成 1ms;"
                "用 manju repair --op inout 重新设置窗口,或修正该 take 的 sidecar 文件"
            )
        return self


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
    # Round U voice-repair lineage (media/voicefix, goal item 11): a voice take
    # minted by the repair loop records which take it replaced (``repaired_from``)
    # and that it is an audio repair (``audio_repaired``). Both default to None so
    # a normal synthesis / hand-dropped voice drops them via exclude_none — every
    # voice sidecar written before this landed is byte-for-byte identical, and the
    # voice_hash (staleness anchor) is untouched.
    repaired_from: str | None = None
    audio_repaired: bool | None = None
    # round W (review #60): which VOICE_VERSION payload `voice_hash` was
    # computed under (core/spec.py). None (dropped by exclude_none) means
    # "made before this landed" — read as version 1, so an existing voice
    # take's freshness stays judged by v1 forever (§4.3 conservatism). The
    # synthesis path (providers/tts.py, providers/edge_tts.py) stamps every
    # NEW voice take with the current VOICE_VERSION.
    voice_hash_version: int | None = None


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


# Transition types the render understands (round-T). "fade" is the historical
# dip-to-black; "cut" is a hard cut (no fade); the "xfade_*" family are real
# handle-aware cross-dissolves (media/render.py). The set is kept small and
# curated. Validation stays LENIENT (an unknown type renders as a hard cut)
# because TransitionSpec is human/agent-edited truth and must never crash a
# build over a typo — the compiler records whatever was requested verbatim.
TRANSITION_TYPES = (
    "fade",           # dip-to-black (default; per-segment fade halves)
    "cut",            # hard cut, no fade
    "xfade_fade",     # cross-dissolve (ffmpeg xfade=fade)
    "xfade_slideleft",
    "xfade_slideright",
    "xfade_wipeleft",
    "xfade_circleopen",
)


class TransitionSpec(ManjuModel):
    """A transition on a clip's out-edge. ``type`` defaults to the historical
    ``fade`` (dip-to-black); see ``TRANSITION_TYPES`` for the curated set. The
    ``xfade_*`` family is applied by the render ONLY when real media handles
    exist on both sides, else it honestly degrades to dip-to-black (§7,
    render.py). Additive: the default (fade/300) is byte-identical to before."""

    type: str = "fade"
    duration_ms: int = 300

    # Round W (issue #2/#6): ``type`` stays lenient (see the module comment
    # above — an unknown type is a harmless render-time degrade to dip-to-
    # black), but a NEGATIVE duration is not a typo the render can shrug off —
    # it has no physical meaning. 0 stays legal (an instant/no-op transition,
    # already exercised by the GUI's "cut" picker).
    @field_validator("duration_ms")
    @classmethod
    def _nonneg_duration_ms(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                f"transition.duration_ms 不能为负数(实际 {v!r})— 负的转场时长没有物理"
                "意义;改成 >= 0 的整数(0 表示瞬时切,等价硬切)"
            )
        return v


# ------------------------------------------------------------------ color look

LOOK_PRESETS = ("none", "warm", "cool", "bw", "film", "vivid")


class LookSpec(ManjuModel):
    """A deterministic color look applied in the FINAL/proxy pass (round-T).

    Read from ``bible/style.yaml``'s top-level ``look:`` mapping (the reading
    contract lives in one place: ``media/render.py:load_look``). The default
    (preset ``none`` / intensity 0) is a strict no-op: the look filter chain is
    omitted from the video chain AND from the final content key, so a project
    that never sets a look renders byte-for-byte identically to before. Each
    preset is a fixed eq/colorbalance chain whose strength interpolates toward
    neutral with ``intensity`` (so intensity 0 == none); the exact strings are
    documented on ``media/render.py:_look_filter``."""

    preset: str = "none"
    intensity: float = 1.0

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, v: str) -> str:
        if v not in LOOK_PRESETS:
            raise ValueError(f"look.preset must be one of {LOOK_PRESETS}, got {v!r}")
        return v

    @field_validator("intensity")
    @classmethod
    def _clamp_intensity(cls, v: float) -> float:
        v = float(v)
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"look.intensity must be in [0, 1], got {v!r}")
        return v

    @property
    def active(self) -> bool:
        """A look that actually changes any pixel: a real preset at >0 strength."""
        return self.preset != "none" and self.intensity > 0.0


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

    # Round X (agent XG): explicit ASS burn-in style knobs
    # (exporters/srt_ass.py's `_resolve_style` / `compile_ass` already read
    # font/size/margin_v/primary_colour off `captions.model_dump()` — they were
    # only reachable via ManjuModel's `extra="allow"` (a hand-edited
    # rules.yaml), untyped and unbounded. Declaring them here makes the same
    # knobs (plus outline/alignment, which the ASS writer used to hardcode)
    # typed, bounded and GUI-writable. Every field defaults to None, which
    # `_resolve_style` treats exactly like an absent key — an unset project
    # renders BYTE-IDENTICAL ASS/SRT output (pinned in
    # tests/test_edit_v3.py::test_caption_style_default_is_byte_identical).
    font: str | None = None
    size: int | None = None
    primary_colour: str | None = None
    margin_v: int | None = None
    outline: int | None = None
    alignment: int | None = None

    # Round W (issue #2/#6): the caption splitter's per-cue budget is
    # ``max_chars_per_line * max_lines`` — at 0 the budget is 0, and the split
    # loop used to advance by 0 characters per iteration (a non-advancing loop).
    # The compiler now also floors the budget defensively (timeline/compiler.py
    # _split_caption/_timed_captions), but a value that can never produce a
    # sane caption is a config error, not something to silently tolerate.
    @field_validator("max_chars_per_line", "max_lines")
    @classmethod
    def _positive_caption_bounds(cls, v: int, info) -> int:
        if v < 1:
            raise ValueError(
                f"captions.{info.field_name} 必须 >= 1(实际 {v!r})— 为 0 时字幕拆分算法"
                "每轮消耗 0 个字符,可能陷入不推进的死循环;请改成 >= 1 的整数"
            )
        return v

    # Round X (agent XG): bound the new style knobs — these are GUI-edited
    # values (字幕样式 panel), so a bad one should fail fast at the model
    # rather than silently corrupt the burned ASS. None (unset) always passes.
    @field_validator("size", "outline")
    @classmethod
    def _nonneg_optional(cls, v: int | None, info) -> int | None:
        if v is not None and v < 0:
            raise ValueError(
                f"captions.{info.field_name} 不能为负数(实际 {v!r})")
        return v

    @field_validator("margin_v")
    @classmethod
    def _nonneg_margin_v(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"captions.margin_v 不能为负数(实际 {v!r})— 底部安全区边距 >= 0")
        return v

    @field_validator("alignment")
    @classmethod
    def _valid_alignment(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 9):
            raise ValueError(
                f"captions.alignment 必须在 1-9 之间(ASS numpad 布局,实际 {v!r})")
        return v

    @field_validator("primary_colour")
    @classmethod
    def _valid_primary_colour(cls, v: str | None) -> str | None:
        import re as _re

        if v is not None and not _re.fullmatch(r"&H[0-9A-Fa-f]{6,8}", v):
            raise ValueError(
                f"captions.primary_colour 必须是 ASS 颜色格式 &HAABBGGRR(实际 {v!r})")
        return v


class TitleCardRules(ManjuModel):
    enabled: bool = False
    text: str = ""
    duration_ms: int = 1500
    template: str = "chapter"


class TimelineRules(ManjuModel):
    mode: Literal["compiled", "manual"] = "compiled"
    timing: TimingRules = Field(default_factory=TimingRules)
    transition_default: TransitionSpec | None = Field(default_factory=TransitionSpec)
    # Round U: per-boundary overrides. Key = the id of the segment whose
    # OUT-edge the boundary is (a shot id, or "__intro__"/"__outro__" for the
    # packaging cards); value replaces transition_default on that one boundary.
    # An explicit null (or ``type: cut``) means a hard cut. The empty default
    # is byte-identical to before; unknown keys are inert at compile time and
    # surfaced by QC as advisories, never a crash (same stance as TRANSITION_TYPES).
    transition_overrides: dict[str, TransitionSpec | None] = Field(default_factory=dict)
    music: MusicRules = Field(default_factory=MusicRules)
    audio: AudioMixRules = Field(default_factory=AudioMixRules)
    captions: CaptionRules = Field(default_factory=CaptionRules)
    title_card: TitleCardRules = Field(default_factory=TitleCardRules)


# ----------------------------------------------------------- packaging.yaml


# Round X (agent XG): named style-preset combos for intro/outro cards
# (media/card.py + media/html_card.py render the actual look; "" is the
# historical hard-coded look, kept first so it is always the byte-identical
# default). 简约黑 mono_black, 白底大字 white_big, 暖色渐变 warm_gradient,
# 霓虹 neon — see media/card.py:CARD_STYLE_PRESETS / media/html_card.py:
# CARD_STYLE_PRESETS for the actual font-scale / background / text-position
# knobs each name maps to.
PACKAGING_CARD_PRESETS = ("", "mono_black", "white_big", "warm_gradient", "neon")


class PackagingCard(ManjuModel):
    """An intro/outro card that becomes a real segment in the timeline."""

    enabled: bool = False
    template: str = "chapter"  # card template; html_card preferred, drawtext floor (§8.4)
    text: str = ""
    subtext: str = ""
    duration_ms: int = 2000
    # Round X (agent XG): a named preset combo layered on top of `template`
    # (font scale / bg colour-or-gradient / text position). "" (default) is a
    # strict no-op — the renderer's historical hard-coded look, so an untouched
    # packaging.yaml renders BYTE-IDENTICAL card assets (pinned in
    # tests/test_edit_v3.py) and — because timeline/packaging.py's
    # packaging_card_hash strips this key at its default — keeps the SAME
    # content-addressed asset path too (no spurious re-key).
    style_preset: str = ""

    @field_validator("style_preset")
    @classmethod
    def _known_style_preset(cls, v: str) -> str:
        if v not in PACKAGING_CARD_PRESETS:
            raise ValueError(
                f"packaging card.style_preset must be one of {PACKAGING_CARD_PRESETS}, got {v!r}")
        return v


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
    # Source in-point (round-T): the offset INTO the source where this clip's
    # timeline window begins. Default 0 = read the source from its head (today's
    # behaviour, byte-identical — omitted from the content-key payload when 0).
    # A value >0 means imported footage was trimmed (set_inout) leaving spare
    # HEAD material before the window: that is the real handle a cross-dissolve
    # needs on the incoming side. The compiler does not populate this yet (a
    # future `set_inout` will); it is carried here so the render can honour it.
    source_in_ms: int = 0

    # Round W (issue #2/#6): NOT bounded here on purpose. Timeline/VideoClip
    # stays lenient at the model layer — same stance as TRANSITION_TYPES above
    # — because timeline.json is compiled AND hand-editable (mode: manual, §6)
    # and a bad value must load into a real, itemized QC finding (shot id +
    # suggestion, qc.json/qc.md) rather than a raw parse-time crash that stops
    # at the FIRST bad field with no report at all. A non-positive duration_ms
    # is already a hard QC error naming the shot — see
    # qc/checks.py:_technical_timeline_conflicts (pinned by
    # tests/test_round_q.py::test_conflict_zero_duration_clip).


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

    # Round W (issue #2/#6): NOT bounded here — same "compiled + hand-editable,
    # QC catches it" stance as VideoClip.duration_ms above. A negative
    # duration_ms is flagged by qc/checks.py:_technical_timeline_conflicts
    # (added round W) instead of a raw parse-time crash on load.


# FP loop I (roadmap §5.5): curated caption-cue role vocabulary — record +
# advise, never block. Deliberately lenient at the model (timeline.json is
# compiled AND hand-editable truth, same stance as TRANSITION_TYPES): an
# unknown role string parses fine and is surfaced as a STRUCTURED warn by
# qc/checks.py:_technical_captions at check time (never silent, never a
# blocker) and as a ROLE_UNKNOWN advisory in qc/captions_access.py.
CAPTION_ROLES = ("translation", "sdh", "forced", "lyrics", "speaker_label")


class CaptionLine(ManjuModel):
    start_ms: int
    end_ms: int
    text: str
    speaker: str = ""
    # Owning shot id (WP1 impact spine). Stamped by the compiler; empty on
    # legacy timelines / human SRT cues. Internal only — SRT/ASS exporters
    # ignore it. First recompile with this field moves the timeline
    # fingerprint once (byte-identity rule §1.5, round-O precedent).
    shot: str = ""
    # FP loop I (§5.5): OPTIONAL role (see CAPTION_ROLES above). None — the
    # default — is DROPPED from serialization by the wrap serializer below, so
    # a role-less project's timeline.json / model dumps stay BYTE-IDENTICAL
    # (save_timeline uses plain model_dump(); without the drop this field
    # would land as `"role": null` in every recompiled timeline — stricter
    # than the round-O `shot` precedent: no fingerprint move at all). A set
    # role flows VERBATIM into the ASS Name field (exporters/srt_ass.py) and
    # the delivery caption artifact rows (build/delivery.py); SRT/VTT have no
    # role slot, so there the role stays truth-side only.
    role: str | None = None

    @model_serializer(mode="wrap")
    def _drop_default_role(self, handler):
        data = handler(self)
        if isinstance(data, dict) and data.get("role") is None:
            data.pop("role", None)
        return data


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

    # Round W (issue #2/#6): fps/width/height NOT bounded here — same
    # lenient-at-model stance as VideoClip.duration_ms above (timeline.json is
    # compiled AND hand-editable, §6 manual mode). A hand-corrupted fps<=0
    # would ZeroDivisionError the moment anything called
    # timeline/compiler.snap_to_frame_grid on it; that helper now floors fps
    # defensively (never raises), and qc/checks.py:_technical_timeline_conflicts
    # reports a non-positive fps/width/height as a named QC error instead of a
    # raw parse-time crash on load.


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


# ---- round U (UA): asset matrix ------------------------------------------
# Additive read-model schema over the EXISTING bible files (characters/scenes/
# props/voices/style.yaml). These are OPTIONAL productization fields a human or
# agent may add to ANY bible entry to drive the asset matrix (goal item 5) and
# @mention resolution (goal item 6). A bible entry WITHOUT them is unchanged —
# this is not a parallel store, the bible YAML stays the single source of truth
# (§3, §4). Every field defaults empty, so :class:`AssetEntry` parsed from a
# plain bible entry (name + free-form docs) carries no extra data.
#
# Byte-stability: this model is only ever READ by ``core/assets.py`` — it is
# never dumped back to a truth file, and it does not participate in any content
# key or fingerprint. spec_payload (core/spec.py) hashes the RAW bible dict, not
# this model, so adding this class changes no existing hash. A project that
# never writes aliases/relations/default_position/locked_fields keys renders
# byte-for-byte identically. (Adding those keys to an entry does move the entry
# dict and therefore the picture spec_hash of the shots that reference it — that
# is the engine's pre-existing "any bible edit restages" rule, unchanged here.)


class AssetEntry(BibleEntry):
    """The asset-matrix view of a bible entry: a :class:`BibleEntry` plus the
    optional round-U productization fields. ``extra='allow'`` (from ManjuModel)
    keeps every free-form doc field (description, appearance, ref_image, …) as
    model extras, so validating a real bible entry through this class never
    loses data and never rejects a plain entry.

    - ``aliases``          alternate names/handles the @mention system resolves.
    - ``relations``        typed-but-free-form links to other entries: a mapping
                           of a relation verb (located_in / owner / uses / …) to
                           an id or list of ids. Only the SHAPE is validated —
                           the verbs are open and the targets are not checked to
                           exist (a dangling target is surfaced by the matrix, it
                           is never a hard error).
    - ``default_position`` a free-form staging hint (e.g. "frame-left").
    - ``locked_fields``    entry field names the author considers authoritative
                           (advisory metadata surfaced by the matrix; the real
                           lock discipline lives in ``locked`` on shots/bible)."""

    aliases: list[str] = Field(default_factory=list)
    relations: dict[str, Any] = Field(default_factory=dict)
    default_position: str | None = None
    locked_fields: list[str] = Field(default_factory=list)

    @field_validator("aliases", "locked_fields", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v else []
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v if x not in (None, "")]
        raise ValueError("must be a string or a list of strings")

    @field_validator("relations", mode="before")
    @classmethod
    def _validate_relations_shape(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("relations must be a mapping of relation -> id(s)")
        out: dict[str, Any] = {}
        for verb, target in v.items():
            if isinstance(target, (list, tuple)):
                vals = [str(t) for t in target if t not in (None, "")]
                if vals:
                    out[str(verb)] = vals
            elif target not in (None, ""):
                out[str(verb)] = str(target)
        return out

# ---- round U (UF): keyframes -----------------------------------------------
# Multi-image storyboard / keyframe sequence support (goal item 12). Everything
# for this feature lives in this ONE appended block — the ShotSpec class body
# above is deliberately untouched: the ``keyframes`` field is injected onto it
# below via model_fields + model_rebuild.
#
# Byte-identity contract (tested): a shot with no ``keyframes`` key is unchanged
# EVERYWHERE — spec_hash, voice_hash, content keys, normalized segments — because
# keyframes are NOT part of ``core/spec.spec_payload`` (they guide first/last
# frame video tasks and the storyboard grid; they never restage the picture that
# spec_hash anchors). The default is an empty list, so an untouched project keeps
# a byte-identical shape.

KEYFRAME_POSITIONS = ("start", "mid", "end")


class KeyframeSpec(ManjuModel):
    """One frame in a shot's storyboard / keyframe sequence (goal item 12).

    Either an anchored ``position`` (``start`` | ``mid`` | ``end``) OR an
    explicit ``at_ms`` timestamp locates the frame; ``image`` is what the frame
    should look like — a project-relative path, an ``http(s)://`` URL, or a
    bible asset id (character/scene) whose ``ref_image`` is used; ``prompt`` is
    a short text beat describing the moment. Every field is optional (defaults
    ``None``) so a hand-authored partial keyframe still validates — the truth
    file stays forgiving (§4).

    goal item 17: an ABSOLUTE local path, or a relative path that resolves
    outside the project root, is REFUSED at resolution time (same containment
    guard refs.py uses) — it is never read from disk, so it can never leak an
    outside-project file into a cloud provider request.
    """

    position: Literal["start", "mid", "end"] | None = None
    at_ms: int | None = None
    image: str | None = None
    prompt: str | None = None

    @property
    def role(self) -> str | None:
        """``start`` / ``mid`` / ``end`` from ``position``; else derived from
        ``at_ms`` (0 or negative ⇒ ``start``). ``None`` when neither field
        locates the frame. The first/last-frame task reader keys off this."""
        if self.position in KEYFRAME_POSITIONS:
            return self.position
        if self.at_ms is not None and self.at_ms <= 0:
            return "start"
        return None


# Inject ``keyframes: list[KeyframeSpec] = []`` onto ShotSpec WITHOUT editing the
# class body (round-U additive contract). A shot with no keyframes reads back an
# empty list and the field never enters a hash, so every existing key/render is
# byte-identical. Guarded so a re-import can never double-inject / re-rebuild.
if "keyframes" not in ShotSpec.model_fields:
    from pydantic.fields import FieldInfo as _FieldInfo

    ShotSpec.model_fields["keyframes"] = _FieldInfo(
        annotation=list[KeyframeSpec], default_factory=list
    )
    ShotSpec.model_rebuild(force=True)
