"""Voice staleness (M3) — the exact §4.3 conservatism, applied to sound.

The anchor is ``compute_voice_hash`` (FIX-F): dialogue text + speaker + the
speaker's voice-shaping bible fields. The system fills gaps (missing voice is
synthesized when a TTS manifest is configured) but never overturns a decision
— a stale voice take is flagged, not regenerated; a hand-dropped voice file
(no sidecar) is manual and never auto-invalidated.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.models import ShotSpec
from ..core.spec import VOICE_VERSION, compute_voice_hash


class VoiceState(str, Enum):
    NOT_NEEDED = "not_needed"  # no dialogue on this shot
    MISSING = "missing"  # dialogue but no voice take -> synthesize on build
    FRESH = "fresh"
    STALE = "stale"  # voice_hash changed -> flag only (§4.3)
    MANUAL = "manual"  # newest take has no sidecar: hand-dropped, never invalidated


@dataclass
class VoiceStatus:
    shot_id: str
    state: VoiceState
    voice_hash: str
    media: Path | None = None
    note: str = ""


def _current_voice_descriptor() -> dict[str, Any] | None:
    """Best-effort resolution of 'the TTS provider a build would pick TODAY'
    (round W, review #60) — the same default `get_tts_provider(None)` the
    synthesis path (build/graph._plan_voice) uses. ``None`` when no TTS
    manifest is configured or resolution fails for any reason: voice v2
    staleness then degrades to comparing against a provider-less payload —
    advisory resolution must never crash `manju status`/`check`."""
    try:
        from ..providers.tts import get_tts_provider, voice_provider_descriptor

        return voice_provider_descriptor(get_tts_provider(None))
    except Exception:
        return None


def evaluate_voice(project: Project, shot: ShotSpec,
                   bible: dict[str, dict[str, Any]] | None = None) -> VoiceStatus:
    bible = bible if bible is not None else project.load_bible()
    # The hash a freshly-synthesized voice take would record TODAY (always the
    # latest VOICE_VERSION + today's resolved provider) — shown for
    # MISSING/NOT_NEEDED/MANUAL and as the "current" figure in status views.
    descriptor = _current_voice_descriptor()
    latest = compute_voice_hash(shot, bible, version=VOICE_VERSION, provider=descriptor)
    if not shot.dialogue.text:
        return VoiceStatus(shot.id, VoiceState.NOT_NEEDED, latest)
    voices = project.voice_takes(shot.id)
    if not voices:
        return VoiceStatus(shot.id, VoiceState.MISSING, latest)
    media, sidecar = voices[-1]  # newest wins (append-only, §3)
    if sidecar is None:
        return VoiceStatus(shot.id, VoiceState.MANUAL, latest, media,
                           note="hand-dropped voice file (no sidecar)")
    # §4.3 conservatism (review #60): judge an EXISTING take by the version it
    # was synthesized under — a pre-round-W take (None -> 1) is compared
    # against the v1 payload forever (no provider/manifest sensitivity, no
    # mass restage); only a take recorded at v2+ gets today's resolved
    # provider folded into the comparison.
    take_version = sidecar.voice_hash_version or 1
    take_provider = descriptor if take_version >= 2 else None
    current = compute_voice_hash(shot, bible, version=take_version, provider=take_provider)
    if sidecar.voice_hash == current:
        return VoiceStatus(shot.id, VoiceState.FRESH, latest, media)
    return VoiceStatus(
        shot.id, VoiceState.STALE, latest, media,
        note="dialogue/voice reference changed after this take was synthesized",
    )


def evaluate_all_voices(project: Project) -> list[VoiceStatus]:
    bible = project.load_bible()
    return [evaluate_voice(project, project.load_shot(sid), bible)
            for sid in project.shot_ids()]


# ------------------------------------------------- WINCLI-P0-003 network門

# `ask_before` token for the SEPARATE network/data-transfer risk category. A
# cloud TTS (Edge / generic) streams private dialogue off-box even at ZERO cost,
# so the §8.3 spend gate (which keys on estimated_cost > 0) never fires for it;
# `build --yes` / `voice --yes` grant SPEND consent, never egress/privacy consent
# (WINCLI-P0-003). A project that adds this token to project.yaml ask_before
# refuses network TTS until network出站 is explicitly allowed. Opt-in and additive:
# absent the token, behaviour is byte-identical to today.
NETWORK_EGRESS_TOKEN = "external_data_transfer"


class VoiceEgressWaiting(RuntimeError):
    """A network TTS synthesis was refused pending explicit external-data-transfer
    consent (WINCLI-P0-003). Carries the resolved providers + their egress targets
    so the caller can list每个远端 provider / 目的地 / 数据类型. ``reason`` is the
    stable token for envelopes/records."""

    def __init__(self, message: str, *, providers: list[Any], targets: list[dict[str, Any]]):
        super().__init__(message)
        self.providers = providers
        self.targets = targets
        self.reason = NETWORK_EGRESS_TOKEN


def tts_egress_target(provider: Any) -> dict[str, Any]:
    """Describe the off-box egress a resolved TTS provider performs — ``{provider,
    destination, data}`` (WINCLI-P0-003). A generic manifest exposes its submit
    URL host; the Edge module:Class adapter has no submit URL, so its declared
    ``NETWORK_EGRESS_ENDPOINT`` names Microsoft's endpoint. TTS is cloud-only
    (decision 5), so every configured TTS provider is treated as network egress."""
    manifest = getattr(provider, "manifest", None)
    submit = getattr(manifest, "submit", None) if manifest is not None else None
    url = getattr(submit, "url", None) if submit is not None else None
    if url:
        try:
            from urllib.parse import urlsplit

            destination = urlsplit(str(url)).netloc or str(url)
        except Exception:
            destination = str(url)
    elif getattr(provider, "NETWORK_EGRESS_ENDPOINT", None):
        destination = str(provider.NETWORK_EGRESS_ENDPOINT)
    else:
        destination = "第三方 TTS 云端(remote TTS cloud)"
    return {"provider": getattr(provider, "id", None),
            "destination": destination, "data": "台词文本 dialogue text"}


def network_egress_gate(project: Project, providers: list[Any], *,
                        allow_network: bool) -> None:
    """WINCLI-P0-003 network/privacy gate — a risk category SEPARATE from the
    §8.3 spend gate (same waiting-user style). Cloud TTS streams private dialogue
    off-box even at zero cost, so the spend ``assume_yes`` must NOT double as
    egress consent. When ``project.yaml`` ask_before lists
    :data:`NETWORK_EGRESS_TOKEN` and ``allow_network`` is not granted, refuse
    before any byte leaves the box, naming每个 provider + destination + data type.
    Opt-in: absent the token, this is a no-op (additive, no behaviour change)."""
    if allow_network:
        return
    try:
        ask_before = project.load_config().ask_before
    except Exception:
        return  # a config read failure must never turn the gate into a crash
    if NETWORK_EGRESS_TOKEN not in ask_before:
        return
    provs = [p for p in providers if p is not None]
    if not provs:
        return
    targets = [tts_egress_target(p) for p in provs]
    names = ", ".join(str(t["provider"]) for t in targets)
    dests = ", ".join(str(t["destination"]) for t in targets)
    raise VoiceEgressWaiting(
        f"waiting_user: 配音会把台词文本发送到第三方 TTS 云端(provider: {names};"
        f"目的地: {dests})——命中 ask_before={NETWORK_EGRESS_TOKEN}"
        "(网络出站/隐私是独立于费用的风险类别,零成本也要过网络门)。"
        "确认允许网络出站后重试。",
        providers=[t["provider"] for t in targets], targets=targets)
