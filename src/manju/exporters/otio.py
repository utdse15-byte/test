"""OpenTimelineIO export — schema-lite, WITHOUT the ``opentimelineio`` package.

We hand-write JSON that follows OTIO 0.15's serialization conventions (the
``OTIO_SCHEMA`` tag + version scheme used by ``otio`` when it round-trips a
Timeline). This keeps OTIO as a dependency-free fallback exit (§14): even when
the JianYing draft format drifts, ``final.mp4`` + SRT + OTIO always get you out.

Structure produced (§3, exports/otio):

    Timeline.1
      global_start_time : RationalTime.1 (rate = fps)
      tracks            : Stack.1
        children[0]     : Track.1 kind="Video"  -> Clip.1 per video shot
        children[1]     : Track.1 kind="Audio"  -> Clip.1 per voice/music/sfx/ambient clip

Every RationalTime is expressed in frames at the timeline fps; millisecond
values are converted with ``value = ms * fps / 1000``. TimeRange/RationalTime
objects are kept internally self-consistent (available_range covers the used
source_range) even though the lite export does not probe real media.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.models import AudioClip, Timeline, VideoClip
from ..core.yamlio import write_json

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = ["export_otio"]

OTIO_TARGET_VERSION = "0.15"  # JSON conventions we target


def _rational_time(ms: int | float | None, fps: float) -> dict[str, Any]:
    ms = ms or 0
    return {
        "OTIO_SCHEMA": "RationalTime.1",
        "rate": float(fps),
        "value": round(float(ms) * float(fps) / 1000.0, 6),
    }


def _time_range(start_ms: int | float, duration_ms: int | float | None,
                fps: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational_time(start_ms, fps),
        "duration": _rational_time(duration_ms, fps),
    }


def _external_reference(target_url: str, duration_ms: int | float | None,
                        fps: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "ExternalReference.1",
        "target_url": target_url,
        # The lite export treats the whole clip length as the media's available
        # range (media is not probed); this stays self-consistent with source_range.
        "available_range": _time_range(0, duration_ms, fps),
        "metadata": {},
    }


def _video_clip(clip: VideoClip, fps: float) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": clip.shot,
        "source_range": _time_range(0, clip.duration_ms, fps),
        "media_reference": _external_reference(clip.source, clip.duration_ms, fps),
        "metadata": {"manju": {"shot": clip.shot, "take": clip.take}},
    }


def _audio_clip(clip: AudioClip, fps: float, kind: str) -> dict[str, Any]:
    name = Path(clip.source).stem or kind
    # OTIO has no loop semantics: an ambient bed is represented at its timeline
    # start/duration with the source referenced as-is; the loop intent is only
    # recorded in metadata (and only when set, so voice/music stay byte-stable).
    meta: dict[str, Any] = {"track": kind, "start_ms": clip.start_ms}
    if clip.loop:
        meta["loop"] = True
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": name,
        "source_range": _time_range(0, clip.duration_ms, fps),
        "media_reference": _external_reference(clip.source, clip.duration_ms, fps),
        "metadata": {"manju": meta},
    }


def _track(name: str, kind: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Track.1",
        "name": name,
        "kind": kind,
        "children": children,
        "metadata": {},
    }


def export_otio(project: "Project", timeline: Timeline) -> Path:
    """Write ``exports/otio/<project name>.otio`` (OTIO 0.15-flavoured JSON)."""
    config = project.load_config()
    fps = float(timeline.fps or config.fps or 24)

    video_children = [_video_clip(c, fps) for c in timeline.tracks.video]
    audio_children = [_audio_clip(c, fps, "voice") for c in timeline.tracks.voice]
    audio_children += [_audio_clip(c, fps, "music") for c in timeline.tracks.music]
    audio_children += [_audio_clip(c, fps, "sfx") for c in timeline.tracks.sfx]
    audio_children += [_audio_clip(c, fps, "ambient") for c in timeline.tracks.ambient]

    stack = {
        "OTIO_SCHEMA": "Stack.1",
        "name": "tracks",
        "children": [
            _track("Video", "Video", video_children),
            _track("Audio", "Audio", audio_children),
        ],
        "metadata": {},
    }

    doc: dict[str, Any] = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": config.name,
        "global_start_time": _rational_time(0, fps),
        "tracks": stack,
        "metadata": {
            "manju": {
                "otio_target_version": OTIO_TARGET_VERSION,
                "fps": timeline.fps,
                "width": timeline.width,
                "height": timeline.height,
                "duration_ms": timeline.duration_ms,
                "compiled_from": timeline.meta.compiled_from,
            }
        },
    }

    out = project.exports_dir / "otio" / f"{config.name}.otio"
    write_json(out, doc)
    return out
