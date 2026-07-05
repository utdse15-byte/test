"""FIX-F: pin the spec_hash intent (dialogue is voice-side, not picture-side)
and introduce the voice-input hash for M3 TTS staleness."""

from __future__ import annotations

from manju.core.models import ShotSpec
from manju.core.spec import compute_spec_hash

BIBLE = {
    "linxia": {"name": "林夏", "look": "短发,深色风衣",
               "voice": "低沉、克制", "voice_ref": "media/refs/linxia.wav"},
    "convenience_store": {"name": "便利店", "mood": "雨夜"},
}


def _shot(**overrides) -> ShotSpec:
    data = {
        "id": "S001", "scene": "convenience_store", "characters": ["linxia"],
        "dialogue": {"speaker": "linxia", "text": "这不可能。"},
    }
    data.update(overrides)
    return ShotSpec.model_validate(data)


# ------------------------------------------------ pin: video spec_hash intent


def test_dialogue_text_does_not_change_video_spec_hash():
    """PINNED INTENT (review §3): dialogue drives the VOICE, not the picture —
    editing a line must never mark a generated video take stale (§4.3)."""
    base = compute_spec_hash(_shot(), BIBLE)
    edited = compute_spec_hash(
        _shot(dialogue={"speaker": "linxia", "text": "完全不同的新台词。"}), BIBLE
    )
    assert base == edited


def test_picture_fields_do_change_video_spec_hash():
    base = compute_spec_hash(_shot(), BIBLE)
    assert compute_spec_hash(_shot(action={"main": "换了动作"}), BIBLE) != base


# ---------------------------------------------------- FIX-F: voice input hash


def test_voice_hash_changes_with_dialogue_text():
    from manju.core.spec import compute_voice_hash

    base = compute_voice_hash(_shot(), BIBLE)
    edited = compute_voice_hash(
        _shot(dialogue={"speaker": "linxia", "text": "新台词。"}), BIBLE
    )
    assert base != edited
    assert base.startswith("sha256:")


def test_voice_hash_changes_with_speaker_voice_reference():
    from manju.core.spec import compute_voice_hash

    base = compute_voice_hash(_shot(), BIBLE)
    changed_voice = dict(BIBLE)
    changed_voice["linxia"] = {**BIBLE["linxia"], "voice": "尖锐、急促"}
    assert compute_voice_hash(_shot(), changed_voice) != base
    changed_ref = dict(BIBLE)
    changed_ref["linxia"] = {**BIBLE["linxia"], "voice_ref": "media/refs/other.wav"}
    assert compute_voice_hash(_shot(), changed_ref) != base


def test_voice_hash_ignores_picture_side_changes():
    """Camera moves, action text, and non-voice bible fields (look) must NOT
    invalidate a voice take — that is exactly the M3 staleness split."""
    from manju.core.spec import compute_voice_hash

    base = compute_voice_hash(_shot(), BIBLE)
    assert compute_voice_hash(
        _shot(camera={"shot_size": "wide", "movement": "pan"}), BIBLE
    ) == base
    assert compute_voice_hash(_shot(action={"main": "换动作"}), BIBLE) == base
    changed_look = dict(BIBLE)
    changed_look["linxia"] = {**BIBLE["linxia"], "look": "长发,浅色大衣"}
    assert compute_voice_hash(_shot(), changed_look) == base


def test_voice_hash_stable_and_order_independent():
    from manju.core.spec import compute_voice_hash

    reordered = {k: BIBLE[k] for k in reversed(list(BIBLE))}
    assert compute_voice_hash(_shot(), BIBLE) == compute_voice_hash(_shot(), reordered)
