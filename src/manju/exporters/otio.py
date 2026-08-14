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

Two representation paths, dispatched by the R2 rational echo on the timeline:

* **Int timelines** (every project today) — a RationalTime is expressed in
  frames at the integer timeline fps; millisecond values are converted with
  ``value = ms * fps / 1000`` at a ``float`` rate. This path is BYTE-IDENTICAL to
  every export written before the rational migration (an int timeline carries no
  echo, so :func:`_edit_rate_of` returns ``None`` and the code below takes the
  exact float formula it always has).
* **Rational timelines** (R2-compiled 1001-family projects, e.g. 24000/1001) —
  the ``rate`` is OTIO's own convention of a ``float64`` fps (``24000/1001`` →
  ``23.976023976023978``, the exact IEEE-754 double), but every ``value`` is an
  EXACT WHOLE-FRAME INTEGER: a video clip's duration comes straight from the
  compiler's ``VideoClip.duration_frames`` truth, and starts telescope by walking
  those frame counts — there is NO ``ms × fps`` float arithmetic and NO fractional
  frame value anywhere on this path (the millisecond grid cannot hold a 1001-family
  boundary exactly; the frame count can). Audio clips carry no ``duration_frames``,
  so their windows use :func:`~manju.core.timebase.ms_to_frames` (exact Fraction
  rounding to the nearest whole frame — still integer, still no float).

TimeRange/RationalTime objects are kept internally self-consistent
(available_range covers the used source_range) even though the lite export does
not probe real media.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.container import ProjectError
from ..core.models import AudioClip, Timeline, VideoClip
from ..core.timebase import Rate, Rounding, ms_to_frames
from ..core.yamlio import write_json

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = ["export_otio"]

OTIO_TARGET_VERSION = "0.15"  # JSON conventions we target


def _edit_rate_of(timeline: "Timeline") -> Rate | None:
    """The timeline's exact rational edit rate IFF it carries R2's rational echo,
    else ``None`` (the int path). Read defensively via ``getattr`` so a
    duck-typed/legacy timeline without the echo attribute stays on the
    byte-identical int path; a whole-number echo (``exact_int is not None``) also
    returns ``None`` — only a genuine 1001-family rate takes the frame path."""
    echo = getattr(timeline, "rate_echo", None)
    if echo is None:
        return None
    rate = getattr(echo, "rate", None)
    return rate if isinstance(rate, Rate) and rate.exact_int is None else None


class _Times:
    """Builds OTIO RationalTime/TimeRange dicts on the int path (today's float
    ``ms×fps/1000`` bytes, EXACTLY) or the rational path (exact integer frames).

    ``rate is None`` ⇒ int path; a :class:`~manju.core.timebase.Rate` ⇒ rational.
    The dict key order (``OTIO_SCHEMA``, ``rate``, ``value``) is identical on both
    paths so the int output is byte-for-byte what it always was."""

    __slots__ = ("fps", "rate")

    def __init__(self, fps: float, rate: Rate | None) -> None:
        self.fps = fps
        self.rate = rate

    def frames_of_ms(self, ms: int | float | None) -> int:
        """The exact nearest whole frame for ``ms`` on the rational grid
        (``ms_to_frames`` — exact Fraction rounding, never float ms×fps)."""
        return ms_to_frames(int(ms or 0), self.rate, Rounding.ROUND_HALF_UP)

    def from_ms(self, ms: int | float | None) -> dict[str, Any]:
        """RationalTime for a millisecond value. Int path: today's float frames
        at a float rate (byte-identical). Rational path: the exact nearest whole
        frame at the float64 rational rate."""
        ms = ms or 0
        if self.rate is None:
            return {
                "OTIO_SCHEMA": "RationalTime.1",
                "rate": float(self.fps),
                "value": round(float(ms) * float(self.fps) / 1000.0, 6),
            }
        return {
            "OTIO_SCHEMA": "RationalTime.1",
            "rate": self.rate.fps_float,  # e.g. 24000/1001 -> 23.976023976023978
            "value": self.frames_of_ms(ms),
        }

    def from_frames(self, frames: int) -> dict[str, Any]:
        """RationalTime for an EXACT whole-frame count (rational path only) — the
        ``VideoClip.duration_frames`` truth and telescoped starts land here."""
        return {
            "OTIO_SCHEMA": "RationalTime.1",
            "rate": self.rate.fps_float,
            "value": int(frames),
        }


def _time_range(times: "_Times", start_ms: int | float,
                duration_ms: int | float | None) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": times.from_ms(start_ms),
        "duration": times.from_ms(duration_ms),
    }


def _require_contained_source(project: "Project", source: str, *, label: str) -> None:
    """goal item 78: refuse to write ``source`` into the OTIO document unless
    it stays inside the project root — the SAME containment semantics
    render.py's ``project.resolve(clip.source)`` already enforces for the
    rendered film. Unlike JianYing's ``_abs_path`` (which needs the resolved
    absolute path), OTIO's ``target_url`` keeps the ORIGINAL project-relative
    string byte-identically (existing exports pin this); this call is pure
    validation — its return value is discarded, only a containment failure
    matters."""
    try:
        project.resolve(source)
    except Exception as exc:
        raise ProjectError(
            f"导出失败:{label} 的素材路径超出项目边界或不合法: {source!r} — "
            "OTIO 导出不允许引用项目外文件(与渲染 render 的边界语义一致)。"
            "请先 `manju import` 把素材放进项目,再导出。"
        ) from exc


def _external_reference(times: "_Times", target_url: str,
                        duration_ms: int | float | None) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "ExternalReference.1",
        "target_url": target_url,
        # The lite export treats the whole clip length as the media's available
        # range (media is not probed); this stays self-consistent with source_range.
        "available_range": _time_range(times, 0, duration_ms),
        "metadata": {},
    }


def _video_clip(project: "Project", clip: VideoClip, times: "_Times") -> dict[str, Any]:
    _require_contained_source(project, clip.source, label=f"{clip.shot}/{clip.take}")
    # Round-T: the footage's own-audio level/mute travels in metadata, added ONLY
    # when non-default so an untouched clip exports byte-identically to before.
    meta: dict[str, Any] = {"shot": clip.shot, "take": clip.take}
    if clip.source_mute:
        meta["source_mute"] = True
    elif clip.source_gain_db:
        meta["source_gain_db"] = clip.source_gain_db
    # WP6: transition_out for roundtrip → rules.transition_overrides
    if clip.transition_out is not None:
        meta["transition_out"] = clip.transition_out.model_dump()
    # Round-W (#10): a virtual trim's source_in_ms is the render's real seek —
    # the OTIO source_range must start there too, not always at 0, or the NLE
    # opens a different picture than the one Manju rendered. Default 0 keeps
    # this byte-identical to before. available_range is widened to cover the
    # in-point + the window (still just 0..duration_ms when in_ms is 0), so the
    # two ranges stay internally self-consistent per the module's own contract.
    in_ms = clip.source_in_ms or 0
    if times.rate is not None:
        # R4 rational truth: the clip duration is the compiler's EXACT whole-frame
        # count (duration_frames), never ms→frame float math; the source in-point
        # is the nearest whole frame (0 when untrimmed); available_range widens to
        # in-point + window, all integers — starts telescope by walking these
        # frame counts, the same way the compiler's cumulative boundary does.
        in_frames = times.frames_of_ms(in_ms)
        dur_frames = clip.duration_frames
        if dur_frames is None:  # hand-assembled rational clip w/o the stamp
            dur_frames = times.frames_of_ms(clip.duration_ms)
        source_range = {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": times.from_frames(in_frames),
            "duration": times.from_frames(dur_frames),
        }
        media_reference = {
            "OTIO_SCHEMA": "ExternalReference.1",
            "target_url": clip.source,
            "available_range": {
                "OTIO_SCHEMA": "TimeRange.1",
                "start_time": times.from_frames(0),
                "duration": times.from_frames(in_frames + dur_frames),
            },
            "metadata": {},
        }
    else:
        source_range = _time_range(times, in_ms, clip.duration_ms)
        media_reference = _external_reference(times, clip.source, in_ms + clip.duration_ms)
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": clip.shot,
        "source_range": source_range,
        "media_reference": media_reference,
        "metadata": {"manju": meta},
    }


def _audio_clip(project: "Project", clip: AudioClip, times: "_Times", kind: str) -> dict[str, Any]:
    name = Path(clip.source).stem or kind
    _require_contained_source(project, clip.source, label=f"{kind}:{name}")
    # OTIO has no loop semantics: an ambient bed is represented at its timeline
    # start/duration with the source referenced as-is; the loop intent is only
    # recorded in metadata (and only when set, so voice/music stay byte-stable).
    meta: dict[str, Any] = {"track": kind, "start_ms": clip.start_ms}
    if clip.loop:
        meta["loop"] = True
    # Round-T: BGM/ambient in-point + fade-in noted in metadata, added ONLY when
    # non-default (voice/music/sfx and unchanged beds stay byte-identical).
    if clip.start_offset_ms:
        meta["start_offset_ms"] = clip.start_offset_ms
    if clip.fade_in_ms:
        meta["fade_in_ms"] = clip.fade_in_ms
    # Audio carries no duration_frames truth; on the rational path its window is
    # the exact nearest whole frame (ms_to_frames — integer, not float ms×fps),
    # and on the int path it is today's float ms×fps/1000 (byte-identical). Both
    # go through ``times`` so the two paths share one code path here.
    # The in-point becomes the source_range start (0 by default -> byte-stable).
    # available_range must CONTAIN that range: the _video_clip path widens its
    # media to in-point + window for exactly this reason, and the audio path must
    # too, or a bed with a non-zero in-point emits a source_range that reads past
    # its own declared media — violating this module's stated self-consistency
    # invariant. Built from the SAME two values the source_range uses, so
    # containment is EXACT on both the int and rational grids; a zero in-point is
    # byte-identical to before (available end = 0 + window).
    source_range = _time_range(times, clip.start_offset_ms, clip.duration_ms)
    available_range = {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": times.from_ms(0),
        "duration": {
            **source_range["duration"],
            "value": source_range["start_time"]["value"]
            + source_range["duration"]["value"],
        },
    }
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": name,
        "source_range": source_range,
        "media_reference": {
            "OTIO_SCHEMA": "ExternalReference.1",
            "target_url": clip.source,
            "available_range": available_range,
            "metadata": {},
        },
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


def export_otio(
    project: "Project",
    timeline: Timeline,
    *,
    baseline_warnings: list[str] | None = None,
) -> Path:
    """Write ``exports/otio/<project name>.otio`` (OTIO 0.15-flavoured JSON)."""
    config = project.load_config()
    fps = float(timeline.fps or config.fps or 24)
    # R4: an R2-compiled 1001-family timeline carries a rational echo — take the
    # exact-integer-frame path. Every int project (no echo) keeps the byte-
    # identical float ms×fps/1000 path.
    rate = _edit_rate_of(timeline)
    times = _Times(fps, rate)

    video_children = [_video_clip(project, c, times) for c in timeline.tracks.video]
    audio_children = [_audio_clip(project, c, times, "voice") for c in timeline.tracks.voice]
    audio_children += [_audio_clip(project, c, times, "music") for c in timeline.tracks.music]
    audio_children += [_audio_clip(project, c, times, "sfx") for c in timeline.tracks.sfx]
    audio_children += [_audio_clip(project, c, times, "ambient") for c in timeline.tracks.ambient]

    stack = {
        "OTIO_SCHEMA": "Stack.1",
        "name": "tracks",
        "children": [
            _track("Video", "Video", video_children),
            _track("Audio", "Audio", audio_children),
        ],
        "metadata": {},
    }

    manju_meta: dict[str, Any] = {
        "otio_target_version": OTIO_TARGET_VERSION,
        "fps": timeline.fps,
        "width": timeline.width,
        "height": timeline.height,
        "duration_ms": timeline.duration_ms,
        "compiled_from": timeline.meta.compiled_from,
    }
    if rate is not None:
        # Surface the exact rational truth for a rational project (additive,
        # rational-only — an int project's metadata is untouched/byte-identical).
        manju_meta["edit_rate"] = {"num": rate.numerator, "den": rate.denominator}

    doc: dict[str, Any] = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": config.name,
        "global_start_time": times.from_ms(0),
        "tracks": stack,
        "metadata": {"manju": manju_meta},
    }

    out = project.exports_dir / "otio" / f"{config.name}.otio"
    write_json(out, doc)
    # WP6: the carrier remains useful when the derived baseline cannot land,
    # but that degraded one-way state must be visible to the caller.
    from ..build.roundtrip import write_baseline_best_effort

    write_baseline_best_effort(
        project,
        "otio",
        out.stem,
        doc,
        compiled_from=str(timeline.meta.compiled_from or ""),
        warning_sink=baseline_warnings,
        carrier_label="OTIO",
    )
    return out
