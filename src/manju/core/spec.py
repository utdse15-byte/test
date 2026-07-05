"""spec_hash — the staleness anchor (§4.3).

Only fields that affect the rendered picture participate, after canonical
serialization: scene/characters (together with their Bible excerpts), camera,
action, quality, and generation parameters. `status`, `locked`, and notes are
deliberately excluded so that human decisions never make a take look stale.
"""

from __future__ import annotations

from typing import Any

from .hashing import hash_value
from .models import ShotSpec


def _bible_excerpt(bible: dict[str, dict[str, Any]], key: str | None) -> Any:
    if key is None:
        return None
    entry = dict(bible.get(key) or {})
    entry.pop("locked", None)  # lock bookkeeping is not part of the picture
    return entry


def spec_payload(shot: ShotSpec, bible: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The exact dict that gets hashed. Kept as a named function so tests can
    assert what does / does not participate."""
    bible = bible or {}
    generation = shot.generation.model_dump(exclude_none=False)
    return {
        "scene": shot.scene,
        "scene_bible": _bible_excerpt(bible, shot.scene),
        "characters": shot.characters,
        "character_bible": {c: _bible_excerpt(bible, c) for c in shot.characters},
        "camera": shot.camera.model_dump(),
        "action": shot.action.model_dump(),
        "quality": shot.quality.model_dump(),
        "duration": shot.duration,
        "generation": generation,
    }


def compute_spec_hash(shot: ShotSpec, bible: dict[str, dict[str, Any]] | None = None) -> str:
    return hash_value(spec_payload(shot, bible))


# --------------------------------------------------------- voice input hash

# FIX-F: dialogue drives the VOICE, not the picture — so it is deliberately
# absent from spec_payload above, and voice takes get their own staleness
# anchor. Only voice-shaping bible fields participate; changing a character's
# look must never invalidate their recorded lines.
VOICE_BIBLE_KEYS = ("voice", "voice_ref", "voice_sample", "voice_id", "tone")


def voice_payload(shot: ShotSpec, bible: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """What shapes a generated VOICE take (M3 TTS staleness anchor, FIX-F):
    the line itself, who speaks it, and the speaker's voice reference from the
    bible. Camera/action/quality are picture-side and excluded — the exact
    mirror of spec_payload's exclusion of dialogue.

    Not yet wired into any provider: TTS lands in M3 with a real cloud API;
    this function exists now so the staleness rule is pinned before wiring.
    """
    bible = bible or {}
    speaker_entry = bible.get(shot.dialogue.speaker) or {}
    voice_ref = {k: speaker_entry[k] for k in VOICE_BIBLE_KEYS if k in speaker_entry}
    return {
        "text": shot.dialogue.text,
        "speaker": shot.dialogue.speaker,
        "voice_ref": voice_ref,
    }


def compute_voice_hash(shot: ShotSpec, bible: dict[str, dict[str, Any]] | None = None) -> str:
    return hash_value(voice_payload(shot, bible))
