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

from ..core.container import ProjectError
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


def _abs_path(project: "Project", source: str, *, label: str) -> str:
    """Absolute POSIX path for a project-relative source (JianYing wants
    absolute paths; ids stay diff-stable regardless).

    goal item 78: this used to fall back to the RAW source path (even an
    absolute/outside-project one) whenever ``project.resolve`` refused it —
    silently writing an outside-project path into the draft. It now refuses
    the whole export with a 中文 error naming the clip, the same containment
    semantics render.py's ``project.resolve(clip.source)`` already enforces —
    an exported draft must stay as self-contained as the rendered film."""
    try:
        return project.resolve(source).as_posix()
    except Exception as exc:
        raise ProjectError(
            f"导出失败:{label} 的素材路径超出项目边界或不合法: {source!r} — "
            "JianYing 导出不允许引用项目外文件(与渲染 render 的边界语义一致)。"
            "请先 `manju import` 把素材放进项目,再导出。"
        ) from exc


def _us(ms: int | float | None) -> int:
    return int(round((ms or 0) * US_PER_MS))


def _volume_from_db(gain_db: float) -> float:
    """Linear volume (JianYing's per-segment scale) from a dB gain — mirrors
    the native draft path (native_draft._volume_from_db), clamped to [0, 2]."""
    return max(0.0, min(2.0, 10 ** (gain_db / 20.0)))


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
        # Round-W (#10): a virtual trim's source_in_ms is where the internal
        # render actually seeks to — the draft's source_timerange must start
        # there too (default 0 keeps every untouched clip byte-identical), and
        # the material's own declared duration is widened to cover in-point +
        # window so JianYing does not think the file is shorter than what the
        # segment reads.
        in_us = _us(clip.source_in_ms)
        video_materials.append(
            {
                "id": mat_id,
                "type": "video",
                "material_name": f"{clip.shot}/{clip.take}",
                "path": _abs_path(project, clip.source, label=f"{clip.shot}/{clip.take}"),
                "duration": in_us + dur_us,
                "width": width,
                "height": height,
                # WP6: deterministic round-trip identity (uuid5 unaffected)
                "manju": {"shot": clip.shot, "take": clip.take, "kind": "video"},
            }
        )
        seg: dict[str, Any] = {
            "id": seg_id,
            "material_id": mat_id,
            "target_timerange": {"start": _us(clip.start_ms), "duration": dur_us},
            "source_timerange": {"start": in_us, "duration": dur_us},
            "manju": {"shot": clip.shot, "take": clip.take, "kind": "video"},
        }
        # Round-T: the footage's OWN audio level/mute rides the video segment,
        # emitted ONLY when non-default so a project that never touches it exports
        # a byte-identical draft. Mute -> volume 0 + a "muted" flag.
        if clip.source_mute:
            seg["volume"] = 0.0
            seg["muted"] = True
        elif clip.source_gain_db:
            seg["volume"] = _volume_from_db(clip.source_gain_db)
        video_segments.append(seg)

    # --- audio tracks ------------------------------------------------------
    # Voice, music, sfx and the ambient bed each go on their OWN audio track:
    # BGM/ambient span the whole picture and would otherwise permanently overlap
    # every voice/sfx clip within a single track (JianYing supports multiple
    # audio tracks, so this is faithful and keeps the overlap lint meaningful).
    # The sfx track carries both explicit SFX and per-cut transition sounds; the
    # ambient bed references its source over the film's span (no loop semantics
    # in a draft — same trim-to-source handoff as BGM).
    voice_segments: list[dict[str, Any]] = []
    music_segments: list[dict[str, Any]] = []
    sfx_segments: list[dict[str, Any]] = []
    ambient_segments: list[dict[str, Any]] = []

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
                "path": _abs_path(project, clip.source, label=f"{kind}[{idx}]"),
                "duration": dur_us,
            }
        )
        seg: dict[str, Any] = {
            "id": seg_id,
            "material_id": mat_id,
            "target_timerange": {"start": _us(clip.start_ms), "duration": dur_us},
            # Round-T: BGM/ambient in-point (start_offset_ms) becomes the source
            # in-point — default 0 keeps this byte-identical to before.
            "source_timerange": {"start": _us(clip.start_offset_ms), "duration": dur_us},
        }
        # Round-T: carry the clip's gain as a per-segment volume, ONLY when
        # non-default (0 dB -> volume 1.0, JianYing's default; omitted for
        # byte-stability). fade_in/out live in the render, not the draft schema.
        if clip.gain_db:
            seg["volume"] = _volume_from_db(clip.gain_db)
        segments.append(seg)

    for idx, clip in enumerate(timeline.tracks.voice):
        _add_audio(clip, "voice", idx, voice_segments)
    for idx, clip in enumerate(timeline.tracks.music):
        _add_audio(clip, "music", idx, music_segments)
    for idx, clip in enumerate(timeline.tracks.sfx):
        _add_audio(clip, "sfx", idx, sfx_segments)
    for idx, clip in enumerate(timeline.tracks.ambient):
        _add_audio(clip, "ambient", idx, ambient_segments)

    # --- text track (captions) --------------------------------------------
    for idx, cap in enumerate(timeline.tracks.captions):
        assert isinstance(cap, CaptionLine)
        mat_id = _uid("text-material", idx, cap.start_ms)
        seg_id = _uid("text-segment", idx, cap.start_ms)
        dur_us = _us(max(0, cap.end_ms - cap.start_ms))
        # WP6: deterministic manju stamp for caption round-trip identity
        cap_manju = {
            "kind": "caption",
            "shot": cap.shot or "",
            "cue_index": idx,
            "start_ms": cap.start_ms,
            "end_ms": cap.end_ms,
            "text": cap.text,
        }
        text_materials.append(
            {
                "id": mat_id,
                "type": "text",
                "content": cap.text,
                "speaker": cap.speaker,
                "manju": cap_manju,
            }
        )
        text_segments.append(
            {
                "id": seg_id,
                "material_id": mat_id,
                "target_timerange": {"start": _us(cap.start_ms), "duration": dur_us},
                "manju": cap_manju,
            }
        )

    tracks: list[dict[str, Any]] = [
        {"id": _uid("track", "video"), "type": "video", "segments": video_segments},
        {"id": _uid("track", "audio", "voice"), "type": "audio", "segments": voice_segments},
        {"id": _uid("track", "audio", "music"), "type": "audio", "segments": music_segments},
    ]
    # sfx/ambient tracks are emitted ONLY when the audio policy populated them,
    # so a project that uses neither exports a byte-identical draft to before
    # (voice/music/text stay exactly where they were).
    if sfx_segments:
        tracks.append(
            {"id": _uid("track", "audio", "sfx"), "type": "audio", "segments": sfx_segments}
        )
    if ambient_segments:
        tracks.append(
            {"id": _uid("track", "audio", "ambient"), "type": "audio", "segments": ambient_segments}
        )
    tracks.append({"id": _uid("track", "text"), "type": "text", "segments": text_segments})

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
    report = _render_report(draft_path, problems)
    # WP6: name the skeleton as the round-trip carrier
    report = (report.rstrip() + "\n\n## Round-trip\n"
              "编辑本 skeleton (`draft_content.json`) 后可用 "
              "`manju roundtrip <path>` 回写;原生草稿不可回环。\n")
    atomic_write_text(draft_dir / "export_report.md", report)
    # WP6: baseline next to export for diff isolation
    try:
        from ..build.roundtrip import write_baseline
        write_baseline(
            project, "jianying", config.name, draft,
            compiled_from=timeline.meta.compiled_from or "",
        )
    except Exception:
        pass  # baseline is derived; never fail export
    return draft_path
