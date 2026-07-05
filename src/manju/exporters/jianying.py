"""JianYing (剪映) draft exporter — M1 SKELETON (§13 M1, §14 top external risk).

WHAT THIS IS
------------
A deterministic writer that emits a ``draft_content.json`` shaped like a
JianYing / 剪映专业版 draft: a canvas config, a microsecond duration, and
``materials`` + ``tracks`` for video, audio and text (captions). Ids are
generated with ``uuid5(NAMESPACE_URL, <deterministic name>)`` so re-exporting an
unchanged timeline produces a byte-identical, diff-stable file.

WHAT THIS IS NOT (yet)
----------------------
This is a stand-in pending real ``pyJianYingDraft`` integration against a
*pinned* JianYing version. The real draft schema is large, versioned, and — per
§14 — the single biggest external risk in the whole system: 剪映专业版's newer
releases encrypt / change the draft format, and ``pyJianYingDraft`` only tracks
specific versions. The integration plan (§14) is therefore:

  * install the ``pyJianYingDraft``-verified 剪映 build and DISABLE auto-update;
  * lint every export (see :func:`lint_draft`) and keep exporter snapshot tests;
  * treat this exporter as ONE optional exit only.

``final.mp4`` + external SRT + OTIO are the always-available fallback exits: a
draft that will not open never blocks shipping the film. Do not hand-tune the
field names below against a moving 剪映 build — swap the whole body for
``pyJianYingDraft`` calls when M1 lands.

UNITS: JianYing measures time in MICROSECONDS. Every duration/offset here is
``milliseconds * 1000``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.models import AudioClip, CaptionLine, Timeline, VideoClip
from ..core.yamlio import atomic_write_text, read_json, write_json

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = ["export_jianying", "lint_draft"]

US_PER_MS = 1000
# Any positive duration below this many µs (1 ms) cannot be real media and is
# almost certainly a millisecond value that missed its ×1000 µs conversion.
MIN_PLAUSIBLE_US = 1000
# Timerange overlap / duration reconciliation tolerance.
DURATION_TOLERANCE_US = 1000


def _uid(*parts: Any) -> str:
    """Stable id from a deterministic name (diff-stable re-exports)."""
    name = "manju:jianying:" + ":".join(str(p) for p in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _ratio(width: int, height: int) -> str:
    from math import gcd

    g = gcd(int(width), int(height)) or 1
    return f"{int(width) // g}:{int(height) // g}"


def _abs_path(project: "Project", source: str) -> str:
    """Absolute POSIX path for a project-relative source (JianYing wants
    absolute paths; ids stay diff-stable regardless)."""
    try:
        return project.resolve(source).as_posix()
    except Exception:
        return Path(source).as_posix()


def _us(ms: int | float | None) -> int:
    return int(round((ms or 0) * US_PER_MS))


def _build_draft(project: "Project", timeline: Timeline) -> dict[str, Any]:
    config = project.load_config()
    name = config.name
    width, height = timeline.width, timeline.height

    video_materials: list[dict[str, Any]] = []
    audio_materials: list[dict[str, Any]] = []
    text_materials: list[dict[str, Any]] = []

    video_segments: list[dict[str, Any]] = []
    text_segments: list[dict[str, Any]] = []

    # --- video track -------------------------------------------------------
    for clip in timeline.tracks.video:
        assert isinstance(clip, VideoClip)
        mat_id = _uid("video-material", clip.shot, clip.take)
        seg_id = _uid("video-segment", clip.shot, clip.take, clip.start_ms)
        dur_us = _us(clip.duration_ms)
        video_materials.append(
            {
                "id": mat_id,
                "type": "video",
                "material_name": f"{clip.shot}/{clip.take}",
                "path": _abs_path(project, clip.source),
                "duration": dur_us,
                "width": width,
                "height": height,
            }
        )
        video_segments.append(
            {
                "id": seg_id,
                "material_id": mat_id,
                "target_timerange": {"start": _us(clip.start_ms), "duration": dur_us},
                "source_timerange": {"start": 0, "duration": dur_us},
            }
        )

    # --- audio tracks ------------------------------------------------------
    # Voice and music go on SEPARATE audio tracks: BGM spans the whole picture
    # and would otherwise permanently overlap every voice clip within a single
    # track (JianYing supports multiple audio tracks, so this is faithful and
    # keeps the overlap lint meaningful).
    voice_segments: list[dict[str, Any]] = []
    music_segments: list[dict[str, Any]] = []

    def _add_audio(clip: AudioClip, kind: str, idx: int,
                   segments: list[dict[str, Any]]) -> None:
        mat_id = _uid("audio-material", kind, idx, clip.source)
        seg_id = _uid("audio-segment", kind, idx, clip.start_ms)
        dur_us = _us(clip.duration_ms)
        audio_materials.append(
            {
                "id": mat_id,
                "type": "audio",
                "material_name": Path(clip.source).name,
                "path": _abs_path(project, clip.source),
                "duration": dur_us,
            }
        )
        segments.append(
            {
                "id": seg_id,
                "material_id": mat_id,
                "target_timerange": {"start": _us(clip.start_ms), "duration": dur_us},
                "source_timerange": {"start": 0, "duration": dur_us},
            }
        )

    for idx, clip in enumerate(timeline.tracks.voice):
        _add_audio(clip, "voice", idx, voice_segments)
    for idx, clip in enumerate(timeline.tracks.music):
        _add_audio(clip, "music", idx, music_segments)

    # --- text track (captions) --------------------------------------------
    for idx, cap in enumerate(timeline.tracks.captions):
        assert isinstance(cap, CaptionLine)
        mat_id = _uid("text-material", idx, cap.start_ms)
        seg_id = _uid("text-segment", idx, cap.start_ms)
        dur_us = _us(max(0, cap.end_ms - cap.start_ms))
        text_materials.append(
            {
                "id": mat_id,
                "type": "text",
                "content": cap.text,
                "speaker": cap.speaker,
            }
        )
        text_segments.append(
            {
                "id": seg_id,
                "material_id": mat_id,
                "target_timerange": {"start": _us(cap.start_ms), "duration": dur_us},
            }
        )

    tracks: list[dict[str, Any]] = [
        {"id": _uid("track", "video"), "type": "video", "segments": video_segments},
        {"id": _uid("track", "audio", "voice"), "type": "audio", "segments": voice_segments},
        {"id": _uid("track", "audio", "music"), "type": "audio", "segments": music_segments},
        {"id": _uid("track", "text"), "type": "text", "segments": text_segments},
    ]

    return {
        "id": _uid("draft", name),
        "canvas_config": {"width": width, "height": height, "ratio": _ratio(width, height)},
        "duration": _us(timeline.duration_ms),  # MICROSECONDS
        "fps": timeline.fps,
        "materials": {
            "videos": video_materials,
            "audios": audio_materials,
            "texts": text_materials,
        },
        "tracks": tracks,
        "manju": {
            "skeleton": True,
            "note": "M1 skeleton pending pyJianYingDraft integration (§13/§14).",
            "compiled_from": timeline.meta.compiled_from,
        },
    }


# ----------------------------------------------------------------- linting


def _iter_segments(track: dict[str, Any]) -> list[dict[str, Any]]:
    segs = track.get("segments")
    return segs if isinstance(segs, list) else []


def lint_draft(draft_path: Path, project: "Project") -> list[str]:
    """Return a list of problems with a draft (empty list == clean).

    Checks (§9 technical layer, draft lint):
      * referenced media paths that do not exist on disk;
      * segments whose target timeranges overlap within a single track;
      * declared ``duration`` vs the last segment end (tolerance 1000µs);
      * suspiciously-small durations that do not look like microseconds.
    """
    problems: list[str] = []
    try:
        draft = read_json(draft_path)
    except Exception as exc:  # unreadable / not JSON
        return [f"draft unreadable: {exc}"]
    if not isinstance(draft, dict):
        return ["draft is not a JSON object"]

    materials = draft.get("materials") or {}
    tracks = draft.get("tracks") or []

    # 1) missing media --------------------------------------------------
    for group in ("videos", "audios"):
        for mat in materials.get(group, []) if isinstance(materials, dict) else []:
            if not isinstance(mat, dict):
                continue
            path = mat.get("path")
            if not path:
                problems.append(f"material {mat.get('id')}: missing 'path'")
                continue
            p = Path(path)
            if not p.is_absolute():
                try:
                    p = project.resolve(path)
                except Exception:
                    pass
            if not p.exists():
                problems.append(f"missing media: {path}")

    # 2/3) per-track overlap + suspicious durations ---------------------
    max_end_us = 0
    for track in tracks if isinstance(tracks, list) else []:
        if not isinstance(track, dict):
            continue
        ttype = track.get("type", "?")
        spans: list[tuple[int, int, str]] = []
        for seg in _iter_segments(track):
            if not isinstance(seg, dict):
                continue
            tr = seg.get("target_timerange") or {}
            start = int(tr.get("start", 0) or 0)
            dur = int(tr.get("duration", 0) or 0)
            end = start + dur
            max_end_us = max(max_end_us, end)
            if 0 < dur < MIN_PLAUSIBLE_US:
                problems.append(
                    f"{ttype} segment {seg.get('id')}: duration {dur}µs is "
                    f"suspiciously small (looks like ms, not µs)"
                )
            spans.append((start, end, str(seg.get("id"))))
        spans.sort()
        for (s0, e0, id0), (s1, e1, id1) in zip(spans, spans[1:]):
            if s1 < e0 - DURATION_TOLERANCE_US:
                problems.append(
                    f"{ttype} track: segments {id0} and {id1} overlap "
                    f"([{s0},{e0}] vs [{s1},{e1}] µs)"
                )

    # 4) declared duration vs actual last segment end -------------------
    declared = int(draft.get("duration", 0) or 0)
    if 0 < declared < MIN_PLAUSIBLE_US:
        problems.append(
            f"draft duration {declared}µs is suspiciously small (looks like ms, not µs)"
        )
    if max_end_us and abs(declared - max_end_us) > DURATION_TOLERANCE_US:
        problems.append(
            f"declared duration {declared}µs != last segment end {max_end_us}µs "
            f"(tolerance {DURATION_TOLERANCE_US}µs)"
        )

    return problems


def _render_report(draft_path: Path, problems: list[str]) -> str:
    lines = [
        "# JianYing draft export report",
        "",
        f"- draft: `{draft_path.name}`",
        f"- problems: {len(problems)}",
        "",
        "> This is an M1 skeleton export (§13/§14). The JianYing draft format is "
        "the top external drift risk; `final.mp4` + SRT + OTIO are the always-"
        "available fallback exits.",
        "",
    ]
    if not problems:
        lines.append("No problems found — draft passed lint.")
    else:
        lines.append("## Problems")
        lines.append("")
        lines.extend(f"{i}. {p}" for i, p in enumerate(problems, start=1))
    return "\n".join(lines) + "\n"


def export_jianying(project: "Project", timeline: Timeline) -> Path:
    """Write ``exports/jianying/<project name>/draft_content.json`` and, next to
    it, ``export_report.md`` (the :func:`lint_draft` results). Returns the draft
    path; problems are surfaced via the report file, not the return value.
    """
    config = project.load_config()
    draft_dir = project.exports_dir / "jianying" / config.name
    draft_path = draft_dir / "draft_content.json"

    draft = _build_draft(project, timeline)
    write_json(draft_path, draft)

    problems = lint_draft(draft_path, project)
    atomic_write_text(draft_dir / "export_report.md", _render_report(draft_path, problems))
    return draft_path
