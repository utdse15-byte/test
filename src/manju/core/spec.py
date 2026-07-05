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
