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
