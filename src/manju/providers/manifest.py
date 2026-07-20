"""Provider manifests (§8.2) and the generic-cloud config shape (§8.6).

A new REST-shaped cloud API is onboarded by filling the ★ fields of a
``provider.yaml`` — no code. APIs with exotic auth/flows get a dedicated
Python adapter via the ``adapter: module:Class`` escape hatch; both paths
share the async task model, failure taxonomy, resume-polling and ledger.

Manifests live outside the project (keys and endpoints are machine
config, not project truth): ``~/.manju/providers/<id>/provider.yaml``,
overridable via ``MANJU_PROVIDERS_DIR`` (tests use this).
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from ..core.idents import UnsafeIdentifierError, validate_safe_segment
from ..core.models import ManjuModel
from ..core.yamlio import read_yaml

GENERIC_ADAPTER = "generic_cloud"
GENERIC_ASR_ADAPTER = "generic_asr"
GENERIC_TTS_ADAPTER = "generic_tts"
# module:Class escape-hatch adapters that ship with Manju (non-REST local
# generation). Their config lives in the dedicated sections below, and their
# `manju doctor` probes are the validate_for_generic() branches keyed on these.
COMFYUI_ADAPTER = "manju.providers.comfyui:ComfyUIProvider"
LOCAL_CMD_ADAPTER = "manju.providers.local_cmd:LocalCommandProvider"
JOB_STATES = ("queued", "running", "succeeded", "failed")


class AuthConfig(ManjuModel):
    key_env: str | None = None  # ★ env var holding the API key (never in-project, §8.2)
    header: str = "Authorization: Bearer {key}"


class SubmitConfig(ManjuModel):
    url: str  # ★
    method: str = "POST"
    # ★ JSON body; string values may carry {placeholders} (see generic_cloud)
    body_template: dict = Field(default_factory=dict)
    job_id_path: str  # ★ mini-JSONPath into the submit response
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # P0 WP2 (additive, optional): the ONLY submit HTTP statuses this provider
    # treats as a DEFINITE remote rejection (the request reached the server and
    # was refused with NO side effect — so a fallback provider is safe). There is
    # no global "these 4xx are always rejected" assumption anymore: a post-send
    # status NOT in this list is conservatively OUTCOME_UNKNOWN (the request MAY
    # have created a billable job). ``None``/absent declares nothing, so every
    # existing manifest loads unchanged and its delivered request is byte-identical.
    definite_rejection_statuses: list[int] | None = None

    @field_validator("definite_rejection_statuses")
    @classmethod
    def _valid_reject_statuses(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        for s in v:
            if isinstance(s, bool) or not isinstance(s, int) or not (400 <= s <= 599):
                raise ValueError(
                    f"submit.definite_rejection_statuses 只能是 400–599 的 HTTP "
                    f"错误码(实际 {s!r})"
                )
        return v


class PollConfig(ManjuModel):
    url: str  # ★ '{job_id}' placeholder
    status_path: str  # ★
    status_map: dict[str, str] = Field(default_factory=dict)  # ★ remote -> queued/running/succeeded/failed
    result_url_path: str | None = None  # ★ where the finished media URL lives
    reason_path: str | None = None  # failure reason text (defaults to whole body)
    cost_path: str | None = None  # per-job cost if the API reports it


class FailureConfig(ManjuModel):
    # §8.1: a response matching any of these markers is a content-review
    # rejection — first-class, never auto-retried.
    content_rejected_when: list[str] = Field(default_factory=list)


class LimitsConfig(ManjuModel):
    max_duration_ms: int | None = None
    max_resolution: str | None = None
    max_concurrent: int = 1
    rate_limit_per_min: int = 6  # enforced engine-side (§8.2), not trusted to remote 429s
    # Reference-image/-video budget (goal item 9). BOTH default None: with no
    # budget configured the delivery path is byte-identical to today (the
    # providers/refbudget allocator is a no-op and no `budget` block is
    # recorded). When set, the delivery adapters send only the budget-allocated
    # subset — a priority-ordered pick with every omission explained in 中文,
    # auditable per take via ref_delivery.budget. These are a MANIFEST-level
    # policy, distinct from refs.max_images/max_videos (the per-field API-shape
    # cap): the budget decides WHICH refs survive across every field.
    max_ref_images: int | None = None
    max_ref_videos: int | None = None

    # Round W (issue #25): ``max_concurrent`` <= 0 is not a real concurrency
    # cap, and generic_cloud._throttle() already treats
    # ``rate_limit_per_min <= 0`` as "no throttling" — i.e. an unvalidated 0
    # (or negative) silently disables the engine-side rate limit the docstring
    # above says exists (§8.2). Both are load-bearing spend/throughput knobs,
    # so a broken manifest must fail load/probe (`manju doctor` /
    # `manju providers check`), not silently under-throttle.
    @field_validator("max_concurrent")
    @classmethod
    def _positive_max_concurrent(cls, v: int) -> int:
        if v < 1:
            raise ValueError(
                f"limits.max_concurrent 必须 >= 1(实际 {v!r})— 并发上限为 0 或负数会让"
                "该 provider 的所有请求都排不上队;改为 >= 1 的整数"
            )
        return v

    @field_validator("rate_limit_per_min")
    @classmethod
    def _positive_rate_limit(cls, v: int) -> int:
        if v < 1:
            raise ValueError(
                f"limits.rate_limit_per_min 必须 >= 1(实际 {v!r})— 引擎把 <= 0 当成"
                "\"不限流\",会绕过 §8.2 的限流保护;改为 >= 1 的整数"
            )
        return v


class CostConfig(ManjuModel):
    per_second: float = 0.0
    per_call: float = 0.0
    currency: str = "CNY"

    # Round W (issue #25): a negative cost would make budget estimation and
    # the ask_before spend confirmation UNDER-count real spend (or even net
    # negative), defeating both. 0 stays legal (free/local-style providers).
    @field_validator("per_second", "per_call")
    @classmethod
    def _nonneg_cost(cls, v: float, info) -> float:
        if v < 0:
            raise ValueError(
                f"cost.{info.field_name} 不能为负数(实际 {v!r})— 负成本会让预算估算和"
                "花费确认失真;改为 >= 0 的数字"
            )
        return v


class TtsConfig(ManjuModel):
    """How to read synthesized audio out of a TTS response (M3, §8.4: most
    TTS APIs are synchronous — submit IS the result, the degenerate form).
    Exactly one of audio_url_path / audio_b64_path should be filled."""

    audio_url_path: str | None = None  # ★ mini-JSONPath to a downloadable URL
    audio_b64_path: str | None = None  # ★ or: base64 audio inline in the response
    audio_format: str = "wav"  # extension for the registered voice take
    language: str = "zh"


class AsrConfig(ManjuModel):
    """How to read transcript segments out of an ASR response (M4 plugin slot,
    Niren-CASR-style: 导入真人素材 → 转录字幕). The audio goes into the
    body_template via the ``{audio_b64}`` placeholder; APIs needing multipart
    or presigned uploads take the dedicated-adapter escape hatch.

    WP3 optional word-level keys: when the vendor returns word timestamps,
    set ``words_path`` + ``word_*_key``; ``manju align --asr`` prefers them
    for finer ``.timing.json``. Absent → segment-level only (byte-identical).
    """

    segments_path: str = "$.data.segments"  # ★ mini-JSONPath to the segment list
    text_key: str = "text"  # ★ keys within one segment object
    start_key: str = "start"
    end_key: str = "end"
    time_unit: str = "ms"  # "ms" | "s" — remote timestamps are converted to ms
    language: str = "zh"
    # WP3: optional word-level timestamps (default None = unused)
    words_path: str | None = None
    word_text_key: str = "text"
    word_start_key: str = "start"
    word_end_key: str = "end"


class ComfyConfig(ManjuModel):
    """ComfyUI adapter config (§8.6 module:Class escape hatch).

    ``workflow_file`` is an API-format graph the user exported from ComfyUI
    ("Save (API Format)") — nodes keyed by id with ``class_type``/``inputs``.
    ``input_map`` writes the shot's values into specific node inputs:
    ``"6.text": "{prompt}"`` sets node 6's ``text`` input. A bare
    ``{placeholder}`` yields the TYPED value (so ``"3.seed": "{seed}"`` stays an
    int); embedded placeholders format as text (``"5.size": "{width}x{height}"``).
    Placeholders: prompt / width / height / fps / duration_s / duration_ms /
    seed / image / shot_id, plus any shot ``generation.params`` verbatim.
    """

    base_url: str = "http://127.0.0.1:8188"
    workflow_file: str | None = None  # ★ project-relative API-format workflow JSON
    input_map: dict[str, str] = Field(default_factory=dict)  # "node_id.input" -> template
    poll_interval_s: float = 1.0  # /history poll cadence (no websocket, §scope)
    poll_timeout_s: float = 600.0
    output_node: str | None = None  # restrict the output pick to one node id
    # preference order across every node's outputs: first video wins, else an
    # animated gif/sequence, else a still image (each entry has filename/subfolder/type)
    output_prefer: list[str] = Field(default_factory=lambda: ["videos", "gifs", "images"])


IMAGE_MODES = ("none", "base64_field", "url_field", "multipart")
VIDEO_MODES = ("none", "url_field")
# First/last-frame delivery (goal item 12, round U). The capability string a
# provider advertises to opt in, the two real body shapes, and the encodings
# reused from the image modes.
FIRST_LAST_CAPABILITY = "first_last_frame"
FIRST_LAST_MODES = ("none", "fields", "array")
FIRST_LAST_ENCODINGS = ("base64_field", "url_field", "multipart")

# AI_IDE_18 WP5 (addendum ruling 6): the optional lip-sync capability token a
# provider advertises to opt into ``lip_sync(video, audio, subject, params)``.
# ``capabilities`` is a free ``list[str]`` (additive — no schema change), so a
# manifest simply lists ``lip_sync`` to route lip-sync jobs to it. The request
# binds exact video+audio hashes and an explicit subject selector; the output is
# a new append-only take (qc/lipsync.py). It flows through the STANDARD
# GenerationRequest / admission / evidence machinery, so UNKNOWN + reconcile come
# for free. The real qualification rung for lip_sync lands in AI_IDE_14's matrix
# as UNTESTED until a real provider is exercised (honest — no cloud account here).
LIP_SYNC_CAPABILITY = "lip_sync"

# AI_IDE_19 (addendum ruling 1 & 6): two more optional capability tokens on the
# SAME free ``capabilities`` list (additive — no schema change). A cloud video-
# understanding provider advertises ``media_analysis`` to opt into producing a
# ``manju.media-analysis/v1`` derived evidence document (media/analysis.py) — real
# cloud analysis lands only behind AI_IDE_14's qualification, fixture analysis
# first (contract §2). A video provider advertises ``generative_bridge`` to opt
# into transition-bridge generation (build/bridge.py) through the STANDARD paid
# path. Both are UNTESTED in AI_IDE_14's matrix until a real provider is exercised
# (honest — no cloud account here); the gates refuse ANALYZER_NOT_QUALIFIED /
# BRIDGE_NOT_QUALIFIED rather than silently proceed.
MEDIA_ANALYSIS_CAPABILITY = "media_analysis"
GENERATIVE_BRIDGE_CAPABILITY = "generative_bridge"


class RefsConfig(ManjuModel):
    """How a generic_cloud provider receives reference INPUTS (goal item 7).

    Reference images reach cloud video/image APIs in one of three real shapes:
    a base64 data-URI field (Runway ``promptImage``), a public URL field (Kling
    ``image_url`` accepts either), or a multipart file part. Video refs are
    almost always URL-only.

    ``field`` / ``video_field`` are mini-JSONPaths INTO the rendered body where
    the ref value is written (e.g. ``$.image_url``). ``data_uri`` controls the
    base64 shape: ``true`` embeds ``data:<mime>;base64,…`` (Runway); ``false``
    sends the raw base64 string (Kling forbids the prefix). ``multipart_field``
    is the file-part name when ``image_mode: multipart``.
    """

    image_mode: str = "none"           # none | base64_field | url_field | multipart
    field: str | None = None           # ★ jsonpath into body_template for image refs
    max_images: int = 1
    data_uri: bool = True              # base64_field: data-URI (Runway) vs raw (Kling)
    mime: str | None = None            # override MIME for data_uri (else guessed)
    multipart_field: str = "image"     # multipart: file-part form field name
    video_mode: str = "none"           # none | url_field
    video_field: str | None = None     # ★ jsonpath into body_template for video refs
    max_videos: int = 1

    # First/last-frame delivery (goal item 12, round U). When a shot carries
    # keyframes at BOTH start and end whose images resolve to real local files
    # AND the provider advertises the ``first_last_frame`` capability, the two
    # frames are delivered here. Two real body shapes, both configurable exactly
    # like the fields above:
    #   fields : two JSONPaths — ``first_frame_field`` + ``last_frame_field``
    #            (Kling-style ``image`` + ``image_tail``);
    #   array  : one JSONPath ``frames_field`` receiving ``[first, last]``
    #            (Runway-style array).
    # ``first_last_encoding`` reuses the image encodings: ``base64_field``
    # (honours ``data_uri`` / ``mime``), ``url_field``, or ``multipart`` (the two
    # frames ride as file parts named by ``first_last_multipart_first`` /
    # ``_last``). Default (mode ``none``) is a strict no-op — an unset manifest
    # sends a byte-identical request, so existing manifests are unaffected.
    first_last_mode: str = "none"                  # none | fields | array
    first_frame_field: str | None = None           # ★ jsonpath (fields mode) start frame
    last_frame_field: str | None = None            # ★ jsonpath (fields mode) end frame
    frames_field: str | None = None                # ★ jsonpath (array mode) [first, last]
    first_last_encoding: str = "base64_field"      # base64_field | url_field | multipart
    first_last_multipart_first: str = "image"      # multipart part name, start frame
    first_last_multipart_last: str = "image_tail"  # multipart part name, end frame


class IdempotencyConfig(ManjuModel):
    """DR06 ruling 10 — declared provider-native idempotency. ``mode: header``
    injects the derived key (sha256 over namespace+provider_id+submission_id,
    NEVER a secret) into the named request ``field`` on submit, so a re-dispatch
    of the SAME submission dedupes remotely (no second side effect). Default
    ``mode: none`` is a strict no-op — old manifests are byte-identical."""

    mode: str = "none"          # none | header
    field: str | None = None    # ★ header name, e.g. "Idempotency-Key"


class SubmissionConfig(ManjuModel):
    """DR06 — the additive per-provider submission policy. Only ``idempotency``
    lives here today; the section is absent on every existing manifest, so its
    default is a pure no-op."""

    idempotency: IdempotencyConfig = Field(default_factory=IdempotencyConfig)


class DataHandlingConfig(ManjuModel):
    """AI_IDE_14 WP4 (§8) — DECLARED data-handling facts, additive + all
    optional so every existing manifest loads byte-identically. These are human-
    decision material surfaced VERBATIM in the qualification report tagged
    ``declared``; a technical canary can NEVER verify them as true, so they must
    never be presented as observed fact (contract §8, test 19). ``source_ref``
    is where the operator read them (a docs URL / dated note), recorded so the
    claim is traceable — never treated as proof."""

    region: str | None = None            # e.g. "cn-shanghai" / "us-east"
    retention: str | None = None         # e.g. "30d" / "deleted on request"
    training_opt_out: bool | None = None  # is content excluded from training?
    deletion_url: str | None = None      # documented deletion entry point
    source_ref: str | None = None        # where these facts were read (audit)


class LocalCmdConfig(ManjuModel):
    """Local-command adapter config: drive any local generator CLI (§8.6).

    ``command`` is shlex-split; ``{out}`` (required) is a temp path the tool must
    write. Optional placeholders: prompt / width / height / fps / duration_s /
    seed / image / refs_dir. Each templated word stays ONE argv element — a
    ``{prompt}`` with spaces is never word-split (mirrors ``agents.build_command``).
    """

    command: str | None = None  # ★ e.g. "svd --prompt {prompt} --seconds {duration_s} -o {out}"
    timeout_s: float = 300.0
    output_ext: str = ".mp4"  # extension of the file the tool writes to {out}


class ProviderManifest(ManjuModel):
    id: str
    type: str = "video"  # video | image | tts | asr | vision
    adapter: str = GENERIC_ADAPTER  # or "module.path:ClassName" escape hatch
    capabilities: list[str] = Field(default_factory=list)
    # `manju providers disable <id>` flips this — a disabled provider is never
    # selected by the fallback chain or a routing strategy, and naming it in a
    # shot's explicit generation.provider is a build error (§8.4/goal-1). The
    # manifest still loads and doctor still probes it, so the flag is reversible
    # with `manju providers enable <id>` — it is a policy switch, not a delete.
    disabled: bool = False
    # Optional zero-cost reachability endpoint for `manju providers check --live`
    # (a GET that costs nothing — a health/ping/models route, NEVER a paid
    # generate call). Left unset, --live simply skips the network probe.
    ping_url: str | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    submit: SubmitConfig | None = None
    poll: PollConfig | None = None
    failure: FailureConfig = Field(default_factory=FailureConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    comfyui: ComfyConfig = Field(default_factory=ComfyConfig)
    local_cmd: LocalCmdConfig = Field(default_factory=LocalCmdConfig)
    refs: RefsConfig = Field(default_factory=RefsConfig)
    # DR06 (ruling 10): additive, default no-op — provider-native idempotency
    # declaration. Absent on every existing manifest, so byte-identical.
    submission: SubmissionConfig = Field(default_factory=SubmissionConfig)
    # AI_IDE_14 WP4: additive, all-optional DECLARED data-handling facts
    # (region/retention/training opt-out/deletion/source). Absent on every
    # existing manifest, so byte-identical; surfaced verbatim by the
    # qualification report, never verified by a canary (§8).
    data_handling: DataHandlingConfig = Field(default_factory=DataHandlingConfig)

    def validate_for_generic(self) -> list[str]:
        """Config problems that would only surface when money is at stake —
        `manju doctor` calls this so a bad fill fails before the first spend."""
        problems: list[str] = []
        if self.adapter == GENERIC_TTS_ADAPTER:
            if self.submit is None:
                problems.append("submit section is required for generic_tts")
            # EXACTLY one audio source (XOR), not just "at least one": a manifest
            # setting BOTH used to validate silently while _audio_from gave the
            # base64 path precedence, so an apparently-unused audio_url_path did
            # nothing and doctor never flagged the ambiguity.
            if bool(self.tts.audio_url_path) == bool(self.tts.audio_b64_path):
                if self.tts.audio_url_path:
                    problems.append(
                        "set EXACTLY one of tts.audio_url_path / tts.audio_b64_path "
                        "(both are set — base64 would silently win)"
                    )
                else:
                    problems.append(
                        "tts.audio_url_path or tts.audio_b64_path is required for generic_tts"
                    )
            if self.poll is not None and "{job_id}" not in self.poll.url:
                problems.append("poll.url must contain {job_id}")
        if self.adapter == GENERIC_ASR_ADAPTER:
            if self.submit is None:
                problems.append("submit section is required for generic_asr")
            # poll is optional: most ASR APIs are synchronous — submit IS the
            # result (§8.4 degenerate form); async ones fill poll as usual
            if self.poll is not None and "{job_id}" not in self.poll.url:
                problems.append("poll.url must contain {job_id}")
        if self.adapter == GENERIC_ADAPTER:
            if self.submit is None:
                problems.append("submit section is required for generic_cloud")
            if self.poll is None:
                problems.append("poll section is required for generic_cloud")
            if self.poll is not None:
                if "{job_id}" not in self.poll.url:
                    problems.append("poll.url must contain {job_id}")
                bad = [v for v in self.poll.status_map.values() if v not in JOB_STATES]
                if bad:
                    problems.append(f"status_map values must be one of {JOB_STATES}, got {bad}")
            problems.extend(self._refs_problems())
        if self.adapter == COMFYUI_ADAPTER:
            # doctor probe (§8.6): a bad ComfyUI fill fails HERE, not at first run.
            if not self.comfyui.workflow_file:
                problems.append(
                    "comfyui.workflow_file is required "
                    "(export a workflow with 'Save (API Format)')"
                )
            if not self.comfyui.base_url.startswith(("http://", "https://")):
                problems.append(
                    f"comfyui.base_url does not look like an HTTP(S) URL: "
                    f"{self.comfyui.base_url}"
                )
            if self.comfyui.poll_interval_s <= 0:
                problems.append("comfyui.poll_interval_s must be > 0")
        if self.adapter == LOCAL_CMD_ADAPTER:
            cmd = self.local_cmd.command
            if not cmd:
                problems.append("local_cmd.command is required")
            else:
                if "{out}" not in cmd:
                    problems.append("local_cmd.command must contain the {out} placeholder")
                try:
                    # the ONE command-splitting owner (providers/local_cmd) —
                    # the doctor probing with POSIX shlex mangled Windows paths
                    # (`C:\tools\gen.exe` → `C:toolsgen.exe`), reporting a
                    # working provider as "binary not found on PATH"
                    from .local_cmd import _split_command

                    words = _split_command(cmd)
                except ValueError as exc:
                    words = []
                    problems.append(f"local_cmd.command is not valid shell syntax: {exc}")
                # doctor probe (§scope): the command head must exist on PATH
                if words and not shutil.which(words[0]):
                    problems.append(f"local_cmd.command binary {words[0]!r} not found on PATH")
        for section, url in (("submit", self.submit and self.submit.url),
                             ("poll", self.poll and self.poll.url)):
            if url and not url.startswith(("http://", "https://")):
                problems.append(f"{section}.url does not look like an HTTP(S) URL: {url}")
        if self.auth.key_env and not os.environ.get(self.auth.key_env):
            problems.append(f"auth.key_env {self.auth.key_env} is not set in the environment")
        return problems

    def _refs_problems(self) -> list[str]:
        """Reference-delivery config sanity (goal item 7). A bad `refs:` fill
        fails at `manju doctor`, not at the first paid submit. Default
        (image_mode/video_mode = none) is always clean, so existing manifests
        are unaffected."""
        r = self.refs
        out: list[str] = []
        if r.image_mode not in IMAGE_MODES:
            out.append(f"refs.image_mode must be one of {IMAGE_MODES}, got {r.image_mode!r}")
        if r.video_mode not in VIDEO_MODES:
            out.append(f"refs.video_mode must be one of {VIDEO_MODES}, got {r.video_mode!r}")
        if r.image_mode in ("base64_field", "url_field") and not r.field:
            out.append(f"refs.field (jsonpath into body_template) is required "
                       f"when refs.image_mode is {r.image_mode}")
        if r.video_mode == "url_field" and not r.video_field:
            out.append("refs.video_field is required when refs.video_mode is url_field")
        # First/last-frame delivery (goal item 12). Default mode `none` is clean.
        if r.first_last_mode not in FIRST_LAST_MODES:
            out.append(f"refs.first_last_mode must be one of {FIRST_LAST_MODES}, "
                       f"got {r.first_last_mode!r}")
        elif r.first_last_mode != "none":
            if r.first_last_encoding not in FIRST_LAST_ENCODINGS:
                out.append(f"refs.first_last_encoding must be one of "
                           f"{FIRST_LAST_ENCODINGS}, got {r.first_last_encoding!r}")
            if r.first_last_mode == "fields" and not (r.first_frame_field
                                                      and r.last_frame_field):
                out.append("refs.first_frame_field AND refs.last_frame_field are "
                           "required when refs.first_last_mode is fields")
            if r.first_last_mode == "array" and not r.frames_field:
                out.append("refs.frames_field is required when refs.first_last_mode "
                           "is array")
            if FIRST_LAST_CAPABILITY not in self.capabilities:
                out.append(
                    f"refs.first_last_mode is set but capabilities does not list "
                    f"{FIRST_LAST_CAPABILITY!r} — add it so the首尾帧任务 is routed here"
                )
        return out


def providers_dir() -> Path:
    override = os.environ.get("MANJU_PROVIDERS_DIR")
    return Path(override) if override else Path.home() / ".manju" / "providers"


def provider_manifest_dir(provider_id: str) -> Path:
    """``providers_dir() / <id>`` — validated (goal item 72) so a
    ``provider_id`` coming from ``manju providers add/enable/disable/show``
    can never carry ``../``, an absolute path, or another path-segment
    escape into the user-level providers directory. Raises ``ValueError``
    (a 中文 explanation) instead of ``UnsafeIdentifierError`` so CLI call
    sites can catch it the same way they already catch other bad-argument
    ``ValueError``s."""
    try:
        validate_safe_segment(provider_id, label="provider_id")
    except UnsafeIdentifierError as exc:
        raise ValueError(str(exc)) from exc
    return providers_dir() / provider_id


def load_manifests() -> tuple[dict[str, ProviderManifest], list[str]]:
    """Scan provider.yaml files. Broken manifests never break the registry —
    they are collected as errors for `manju doctor` to surface."""
    manifests: dict[str, ProviderManifest] = {}
    errors: list[str] = []
    root = providers_dir()
    if not root.is_dir():
        return manifests, errors
    for manifest_path in sorted(root.glob("*/provider.yaml")):
        label = f"{manifest_path.parent.name}/provider.yaml"
        try:
            data = read_yaml(manifest_path) or {}
            manifest = ProviderManifest.model_validate(data)
        except (ValidationError, OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        if manifest.id in manifests:
            errors.append(f"{label}: duplicate provider id '{manifest.id}'")
            continue
        manifests[manifest.id] = manifest
    return manifests, errors


def estimate_cost(manifest: ProviderManifest, duration_ms: int, candidates: int = 1) -> float:
    """§8.3 dry-run estimate: shots × candidates × duration × unit price."""
    per_take = manifest.cost.per_call + manifest.cost.per_second * (duration_ms / 1000.0)
    return round(per_take * candidates, 6)


# --------------------------------------------------------------- scaffolding
# `manju providers add` writes a fully-commented template per adapter — every
# ★ line is a field to fill; keys are named, never inlined (§8.2). Structure is
# already valid so the file round-trips through load_manifests() the moment it
# lands (a bad *fill* fails at `manju providers check`, not here).

# short adapter names the CLI accepts -> the adapter string stored in the manifest
ADAPTER_ALIASES: dict[str, str] = {
    "generic_cloud": GENERIC_ADAPTER,
    "generic_tts": GENERIC_TTS_ADAPTER,
    "generic_asr": GENERIC_ASR_ADAPTER,
    "comfyui": COMFYUI_ADAPTER,
    "local_cmd": LOCAL_CMD_ADAPTER,
}

# default adapter per provider `--type`
_DEFAULT_ADAPTER_FOR_TYPE: dict[str, str] = {
    "video": "generic_cloud",
    "image": "generic_cloud",
    "tts": "generic_tts",
    "asr": "generic_asr",
}

PROVIDER_TYPES = ("video", "image", "tts", "asr")


def default_adapter_for_type(type_: str) -> str:
    return _DEFAULT_ADAPTER_FOR_TYPE.get(type_, "generic_cloud")


def _header(pid: str, type_: str, adapter_full: str) -> str:
    return (
        f"# ~/.manju/providers/{pid}/provider.yaml — Manju provider manifest (§8.2/§8.6)\n"
        f"#\n"
        f"# ★-marked fields are the ones to fill in. The API key NEVER lives in this\n"
        f"# file (§8.2): put it in the environment variable named by auth.key_env and\n"
        f"# Manju reads it at run time. Validate your fill offline, before any spend:\n"
        f"#   manju providers check {pid}\n"
        f"#\n"
        f"id: {pid}\n"
        f"type: {type_}\n"
        f"adapter: {adapter_full}\n"
        f"disabled: false\n"
    )


def _generic_cloud_template(pid: str, type_: str) -> str:
    env = f"{pid.upper().replace('-', '_')}_API_KEY"
    return _header(pid, type_, GENERIC_ADAPTER) + f"""\
capabilities: [text_to_video]        # ★ what this API can do — drives routing + fallback
auth:
  key_env: {env}   # ★ env var holding the API key (never the key itself, §8.2)
  header: 'Authorization: Bearer {{key}}'
submit:
  url: https://api.example.com/v1/videos     # ★ the create-task endpoint
  method: POST
  body_template:                             # ★ request body; {{placeholders}} filled per shot
    prompt: '{{prompt}}'
    duration: '{{duration_s}}'
    size: '{{width}}x{{height}}'
    seed: '{{seed}}'
  job_id_path: $.data.task_id                # ★ where the job id lives in the submit response
poll:
  url: https://api.example.com/v1/videos/{{job_id}}   # ★ status endpoint ({{job_id}} required)
  status_path: $.data.status                 # ★ where the status string lives
  status_map:                                # ★ remote status -> queued|running|succeeded|failed
    PENDING: queued
    PROCESSING: running
    SUCCEEDED: succeeded
    FAILED: failed
  result_url_path: $.data.video_url          # ★ where the finished media URL lives
failure:
  content_rejected_when: [contentPolicy, risk_control]  # markers meaning 审核拒绝 (never retried)
limits:
  max_concurrent: 1
  rate_limit_per_min: 6         # enforced engine-side, not trusted to remote 429s
cost:
  per_second: 0.0               # ★ price sheet — feeds `manju spend` and the `cheapest` strategy
  currency: CNY
# ping_url: https://api.example.com/health   # optional zero-cost GET for `providers check --live`
"""


def _generic_tts_template(pid: str, type_: str) -> str:
    env = f"{pid.upper().replace('-', '_')}_API_KEY"
    return _header(pid, "tts", GENERIC_TTS_ADAPTER) + f"""\
capabilities: [tts]
auth:
  key_env: {env}   # ★ env var holding the API key (never the key itself, §8.2)
  header: 'Authorization: Bearer {{key}}'
submit:                                       # most TTS is synchronous — submit IS the result
  url: https://api.example.com/v1/tts         # ★ the synthesis endpoint
  method: POST
  body_template:
    text: '{{prompt}}'                        # ★ the line to speak
    voice: default                            # ★ voice id
  job_id_path: $.id                           # ★ (only used by async TTS APIs)
tts:
  audio_url_path: $.data.audio_url            # ★ (or audio_b64_path for inline base64)
  audio_format: wav
  language: zh
cost:
  per_call: 0.0
  currency: CNY
"""


def _generic_asr_template(pid: str, type_: str) -> str:
    env = f"{pid.upper().replace('-', '_')}_API_KEY"
    return _header(pid, "asr", GENERIC_ASR_ADAPTER) + f"""\
capabilities: [asr]
auth:
  key_env: {env}   # ★ env var holding the API key (never the key itself, §8.2)
  header: 'Authorization: Bearer {{key}}'
submit:                                       # most ASR is synchronous — submit IS the result
  url: https://api.example.com/v1/asr         # ★ the transcribe endpoint
  method: POST
  body_template:
    audio: '{{audio_b64}}'                    # ★ the audio, base64-encoded, goes here
  job_id_path: $.id                           # ★ (only used by async ASR APIs)
asr:
  segments_path: $.data.segments              # ★ list of {{text, start, end}} objects
  text_key: text
  start_key: start
  end_key: end
  time_unit: ms                               # remote timestamps: "ms" | "s"
  language: zh
cost:
  per_call: 0.0
  currency: CNY
"""


def _comfyui_template(pid: str, type_: str) -> str:
    return _header(pid, type_, COMFYUI_ADAPTER) + """\
capabilities: [image_to_video, text_to_video]   # ★ what this graph produces
comfyui:
  base_url: http://127.0.0.1:8188
  workflow_file: comfyui/workflow_api.json    # ★ project-relative graph, "Save (API Format)"
  input_map:                                  # ★ "node_id.input_name" -> template
    '6.text': '{prompt}'                      # bare -> typed value; embedded -> string
    '3.seed': '{seed}'
  poll_interval_s: 1.0
  output_prefer: [videos, gifs, images]       # first video wins, else gif, else image
cost:
  per_call: 0.0                               # local generation: free (duration still recorded)
  currency: CNY
"""


def _local_cmd_template(pid: str, type_: str) -> str:
    return _header(pid, type_, LOCAL_CMD_ADAPTER) + """\
capabilities: [text_to_video]
local_cmd:
  command: 'mytool --prompt {prompt} --seconds {duration_s} -o {out}'  # ★ {out} is required
  timeout_s: 300
  output_ext: .mp4                            # extension of the file the tool writes to {out}
cost:
  per_call: 0.0
  currency: CNY
"""


_TEMPLATES = {
    GENERIC_ADAPTER: _generic_cloud_template,
    GENERIC_TTS_ADAPTER: _generic_tts_template,
    GENERIC_ASR_ADAPTER: _generic_asr_template,
    COMFYUI_ADAPTER: _comfyui_template,
    LOCAL_CMD_ADAPTER: _local_cmd_template,
}


def scaffold_template(pid: str, type_: str, adapter: str) -> str:
    """Return the fully-commented ``provider.yaml`` text for a new provider.

    ``adapter`` is a short alias (``generic_cloud``/``comfyui``/…) or a full
    ``module:Class`` string; the produced YAML round-trips through
    :func:`load_manifests`. Raises ``ValueError`` on an unknown adapter."""
    adapter_full = ADAPTER_ALIASES.get(adapter, adapter)
    builder = _TEMPLATES.get(adapter_full)
    if builder is None:
        raise ValueError(
            f"no scaffold template for adapter {adapter!r}; "
            f"known: {sorted(ADAPTER_ALIASES)}"
        )
    return builder(pid, type_)


# ------------------------------------------------------------ offline fix hints
# `manju providers check` turns each validate_for_generic() problem into a
# one-line, do-this-next hint. Unmatched problems pass through verbatim.
_FIX_HINTS: tuple[tuple[str, str], ...] = (
    ("is not set in the environment",
     "export the API key: `export {envvar}=...` (keys never live in the file, §8.2)"),
    ("submit section is required",
     "fill the ★ submit: section (url + body_template + job_id_path)"),
    ("poll section is required",
     "fill the ★ poll: section (url with {job_id} + status_path + status_map)"),
    ("poll.url must contain {job_id}",
     "add the {job_id} placeholder to poll.url"),
    ("status_map values must be one of",
     "map every remote status to queued|running|succeeded|failed in poll.status_map"),
    ("audio_url_path or tts.audio_b64_path",
     "set tts.audio_url_path (or tts.audio_b64_path) so Manju can read the audio out"),
    ("comfyui.workflow_file is required",
     "export a graph from ComfyUI with 'Save (API Format)' and point workflow_file at it"),
    ("comfyui.base_url does not look like",
     "set comfyui.base_url to your ComfyUI server, e.g. http://127.0.0.1:8188"),
    ("local_cmd.command is required",
     "set local_cmd.command to your generator CLI (include the {out} placeholder)"),
    ("must contain the {out} placeholder",
     "add {out} to local_cmd.command — the tool must write the result there"),
    ("not found on PATH",
     "install the tool or fix its name so it resolves on PATH"),
    ("does not look like an HTTP(S) URL",
     "use a full http:// or https:// URL"),
    ("first_frame_field AND refs.last_frame_field",
     "fill refs.first_frame_field + refs.last_frame_field (Kling image + image_tail)"),
    ("refs.frames_field is required",
     "set refs.frames_field to the array JSONPath (Runway [first,last])"),
    ("capabilities does not list 'first_last_frame'",
     "add first_last_frame to capabilities so the首尾帧任务 routes to this provider"),
)


def fix_hint(problem: str, manifest: "ProviderManifest | None" = None) -> str:
    """A one-line 'do this next' for a validate_for_generic() problem."""
    for needle, hint in _FIX_HINTS:
        if needle in problem:
            envvar = (manifest.auth.key_env if manifest and manifest.auth.key_env
                      else "PROVIDER_API_KEY")
            return hint.format(envvar=envvar)
    return problem


def reachability_probe(
    manifest: ProviderManifest, *, opener=None, timeout: float = 5.0
) -> tuple[bool | None, str]:
    """Cheap, zero-cost liveness check for `providers check --live` (§scope).

    ComfyUI uses ``GET {base_url}/system_stats``; any other adapter is probed
    only if it declares a ``ping_url`` (a free GET the operator vouches for —
    NEVER a paid generate call). Returns ``(ok, detail)`` where ``ok`` is
    ``None`` when there is nothing free to probe (the probe is skipped, never a
    failure). ``opener(url) -> response`` is injectable for tests."""
    import urllib.error
    import urllib.parse
    import urllib.request

    if manifest.adapter == COMFYUI_ADAPTER:
        url = manifest.comfyui.base_url.rstrip("/") + "/system_stats"
    elif manifest.ping_url:
        url = manifest.ping_url
    else:
        return None, "no zero-cost ping endpoint (set ping_url) — live probe skipped"

    def _default_open(u: str):
        # C36: same loopback no-proxy rule as generic_cloud.default_transport —
        # ComfyUI live probe must not go through corporate HTTP_PROXY.
        from .generic_cloud import _is_loopback_host

        host = urllib.parse.urlsplit(u).hostname
        if _is_loopback_host(host):
            return urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            ).open(u, timeout=timeout)
        return urllib.request.urlopen(u, timeout=timeout)

    _open = opener or _default_open
    try:
        resp = _open(url)
    except urllib.error.HTTPError as exc:
        return False, f"{url} -> HTTP {exc.code}"
    except Exception as exc:  # URLError / timeout / OSError: for a local server, unreachable
        return False, f"{url} -> unreachable ({exc})"
    code = getattr(resp, "status", None) or getattr(resp, "code", 200)
    try:
        resp.close()
    except Exception:
        pass
    if code and code >= 400:
        return False, f"{url} -> HTTP {code}"
    return True, f"{url} -> reachable (HTTP {code})"
