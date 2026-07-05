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
from pathlib import Path

from pydantic import Field, ValidationError

from ..core.models import ManjuModel
from ..core.yamlio import read_yaml

GENERIC_ADAPTER = "generic_cloud"
GENERIC_ASR_ADAPTER = "generic_asr"
GENERIC_TTS_ADAPTER = "generic_tts"
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


class CostConfig(ManjuModel):
    per_second: float = 0.0
    per_call: float = 0.0
    currency: str = "CNY"


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
    or presigned uploads take the dedicated-adapter escape hatch."""

    segments_path: str = "$.data.segments"  # ★ mini-JSONPath to the segment list
    text_key: str = "text"  # ★ keys within one segment object
    start_key: str = "start"
    end_key: str = "end"
    time_unit: str = "ms"  # "ms" | "s" — remote timestamps are converted to ms
    language: str = "zh"


class ProviderManifest(ManjuModel):
    id: str
    type: str = "video"  # video | image | tts | vision
    adapter: str = GENERIC_ADAPTER  # or "module.path:ClassName" escape hatch
    capabilities: list[str] = Field(default_factory=list)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    submit: SubmitConfig | None = None
    poll: PollConfig | None = None
    failure: FailureConfig = Field(default_factory=FailureConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)

    def validate_for_generic(self) -> list[str]:
        """Config problems that would only surface when money is at stake —
        `manju doctor` calls this so a bad fill fails before the first spend."""
        problems: list[str] = []
        if self.adapter == GENERIC_TTS_ADAPTER:
            if self.submit is None:
                problems.append("submit section is required for generic_tts")
            if not (self.tts.audio_url_path or self.tts.audio_b64_path):
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
        for section, url in (("submit", self.submit and self.submit.url),
                             ("poll", self.poll and self.poll.url)):
            if url and not url.startswith(("http://", "https://")):
                problems.append(f"{section}.url does not look like an HTTP(S) URL: {url}")
        if self.auth.key_env and not os.environ.get(self.auth.key_env):
            problems.append(f"auth.key_env {self.auth.key_env} is not set in the environment")
        return problems


def providers_dir() -> Path:
    override = os.environ.get("MANJU_PROVIDERS_DIR")
    return Path(override) if override else Path.home() / ".manju" / "providers"


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
