"""Anchor grammar shared by audio-policy SFX and packaging info cards.

An anchor names a point on the compiled timeline:

    ""                 → absolute: the offset alone
    "shot:<id>"        → the start of that shot's video clip
    "shot:<id>:start"  → same, explicit
    "shot:<id>:end"    → the end of that shot's video clip

`offset_ms` is added to the anchored point either way. Resolution is a pure
function of the already-compiled video track, so compiled timelines stay a
deterministic function of their specs (§6).
"""

from __future__ import annotations

from manju.core.models import VideoClip


def resolve_anchor(
    at: str, offset_ms: int, video_clips: list[VideoClip]
) -> int | None:
    """Return the anchored time in ms, or None when the anchor names a shot
    that is not on the video track (callers skip the item; QC reports it)."""
    if not at:
        return max(0, offset_ms)
    parts = at.split(":")
    if parts[0] != "shot" or len(parts) not in (2, 3):
        return None
    shot_id = parts[1]
    edge = parts[2] if len(parts) == 3 else "start"
    if edge not in ("start", "end"):
        return None
    for clip in video_clips:
        if clip.shot == shot_id:
            base = clip.start_ms + (clip.duration_ms if edge == "end" else 0)
            return max(0, base + offset_ms)
    return None
