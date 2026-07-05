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
from ..core.spec import compute_voice_hash


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


def evaluate_voice(project: Project, shot: ShotSpec,
                   bible: dict[str, dict[str, Any]] | None = None) -> VoiceStatus:
    current = compute_voice_hash(shot, bible if bible is not None else project.load_bible())
    if not shot.dialogue.text:
        return VoiceStatus(shot.id, VoiceState.NOT_NEEDED, current)
    voices = project.voice_takes(shot.id)
    if not voices:
        return VoiceStatus(shot.id, VoiceState.MISSING, current)
    media, sidecar = voices[-1]  # newest wins (append-only, §3)
    if sidecar is None:
        return VoiceStatus(shot.id, VoiceState.MANUAL, current, media,
                           note="hand-dropped voice file (no sidecar)")
    if sidecar.voice_hash == current:
        return VoiceStatus(shot.id, VoiceState.FRESH, current, media)
    return VoiceStatus(
        shot.id, VoiceState.STALE, current, media,
        note="dialogue/voice reference changed after this take was synthesized",
    )


def evaluate_all_voices(project: Project) -> list[VoiceStatus]:
    bible = project.load_bible()
    return [evaluate_voice(project, project.load_shot(sid), bible)
            for sid in project.shot_ids()]
