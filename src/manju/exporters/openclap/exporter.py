"""Manju → OpenClap export (§8 出口层): a derived, deterministic snapshot.

This is one more optional exit, in the same spirit as ``exporters/otio.py`` and
``exporters/jianying.py``: a dependency-free, deterministic, atomic writer that
reads the COMPILED timeline (never re-doing take selection) and the project's
truth text, and emits a ``.clap`` document.

Faithfulness rules (from the WP spec):

- Only provably-mappable semantics become standard clap fields; everything else
  rides a namespaced ``x-manju`` extension key — we never invent standard
  fields and never fake a category.
- A clip source that escapes the project root is refused with the SAME
  containment semantics ``render.py``/``otio.py`` already enforce.
- Deterministic: the same project exports byte-identically twice — derived
  segment ids come from stable clip identity (never wall-clock, never a run
  id), and the gzip is written with ``mtime=0`` (see :func:`.io.write_clap`).
- No secrets: only stable, non-secret provenance (take id, provider name) is
  carried; provider manifests / env / absolute host paths are never read.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.container import ProjectError
from ...core.models import AudioClip, Timeline, VideoClip
from ...core.yamlio import read_yaml
from . import profile
from .io import write_clap
from .model import (
    ClapDocument,
    ClapEntity,
    ClapHeader,
    ClapMeta,
    ClapScene,
    ClapSegment,
)

if TYPE_CHECKING:
    from ...core.container import Project

__all__ = ["export_openclap"]

# Deterministic clap `track` numbers, one lane per Manju audio/video track.
_TRACK_VIDEO = 0
_TRACK_VOICE = 1
_TRACK_MUSIC = 2
_TRACK_SFX = 3
_TRACK_AMBIENT = 4


def _stable_id(*parts: Any) -> str:
    """A deterministic uuid5 for meta ids (diff-stable re-exports, like jianying)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "manju:openclap:" + ":".join(map(str, parts))))


def _orientation(width: int, height: int) -> str:
    if height > width:
        return "portrait"
    if width > height:
        return "landscape"
    return "square"


def _require_contained(project: "Project", source: str, *, label: str) -> None:
    """Refuse to write ``source`` into the document unless it stays inside the
    project root — identical containment semantics to
    ``otio._require_contained_source`` / ``render.py``'s ``project.resolve``."""
    try:
        project.resolve(source)
    except Exception as exc:
        raise ProjectError(
            f"导出失败:{label} 的素材路径超出项目边界或不合法: {source!r} — "
            "OpenClap 导出不允许引用项目外文件(与渲染 render 的边界语义一致)。"
            "请先 `manju import` 把素材放进项目,再导出。"
        ) from exc


def _linear_gain(gain_db: float) -> float:
    """Linear output-gain multiplier from a dB value (clap ``outputGain`` is a
    linear scale, default 1.0). Rounded so the serialization stays stable."""
    return round(10 ** (float(gain_db) / 20.0), 6)


def _shot_from_source(source: str) -> str | None:
    """The shot id embedded in a ``media/gen/<shot>/...`` source path, else None.

    Used only to derive stable non-video segment ids; it never reads the file.
    """
    parts = Path(source).parts
    if len(parts) >= 3 and parts[0] == "media" and parts[1] == "gen":
        return parts[2]
    return None


def _provenance(project: "Project", shot: str, take: str) -> tuple[str | None, str | None]:
    """``(compiled_prompt, provider)`` for a clip, cheaply and best-effort.

    Reads only the take sidecar Manju already recorded (no media hashing, no
    network); returns ``(None, None)`` for a synthetic/unregistered take. Only
    the compiled prompt and provider NAME are surfaced — never ``created_at``
    (wall-clock) or any secret.
    """
    try:
        info = project.get_take(shot, take)
    except Exception:
        return None, None
    if info is None:
        return None, None
    sidecar = info.sidecar
    prompt = sidecar.compiled_prompt if isinstance(sidecar.compiled_prompt, str) else None
    provider = sidecar.provider if isinstance(sidecar.provider, str) else None
    return prompt, provider


# ------------------------------------------------------------------ builders


def _video_segment(project: "Project", clip: VideoClip) -> dict[str, Any]:
    label = f"{clip.shot}/{clip.take}"
    _require_contained(project, clip.source, label=label)
    start = int(clip.start_ms)
    end = start + int(clip.duration_ms)
    prompt, provider = _provenance(project, clip.shot, clip.take)

    ext: dict[str, Any] = {"shot": clip.shot, "take": clip.take}
    if provider:
        ext["provider"] = provider
    if clip.source_mute:
        ext["sourceMute"] = True
    elif clip.source_gain_db:
        ext["sourceGainDb"] = float(clip.source_gain_db)
    if clip.source_in_ms:
        ext["sourceInMs"] = int(clip.source_in_ms)

    seg: dict[str, Any] = {
        "id": f"shot:{clip.shot}/video:main",
        "track": _TRACK_VIDEO,
        "category": "VIDEO",
        "startTimeInMs": start,
        "endTimeInMs": end,
        "assetDurationInMs": int(clip.duration_ms),
        "assetUrl": clip.source,
        "assetSourceType": "PATH",
        "outputType": "VIDEO",
    }
    if prompt:
        seg["prompt"] = prompt
    seg[profile.MANJU_EXT_KEY] = ext
    return seg


def _audio_segment(project: "Project", clip: AudioClip, *, track: int, category: str,
                   kind: str, seg_id: str) -> dict[str, Any]:
    name = Path(clip.source).stem or kind
    _require_contained(project, clip.source, label=f"{kind}:{name}")
    start = int(clip.start_ms)
    duration = int(clip.duration_ms) if clip.duration_ms is not None else 0

    ext: dict[str, Any] = {"track": kind}
    shot = _shot_from_source(clip.source)
    if shot:
        ext["shot"] = shot
    if clip.gain_db:
        ext["gainDb"] = float(clip.gain_db)
    if clip.loop:
        ext["loop"] = True

    seg: dict[str, Any] = {
        "id": seg_id,
        "track": track,
        "category": category,
        "startTimeInMs": start,
        "endTimeInMs": start + duration,
        "assetUrl": clip.source,
        "assetSourceType": "PATH",
        "outputType": "AUDIO",
    }
    if clip.duration_ms is not None:
        seg["assetDurationInMs"] = int(clip.duration_ms)
    if clip.gain_db:
        seg["outputGain"] = _linear_gain(clip.gain_db)
    seg[profile.MANJU_EXT_KEY] = ext
    return seg


def _bible_entities_and_scenes(
    project: "Project",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read bible characters (→ CHARACTER entities) and scenes (→ LOCATION
    entities + scene objects). Stable source id preserved on id/triggerName;
    every non-standard bible field rides ``x-manju``."""
    entities: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []

    def _read(name: str) -> dict[str, Any]:
        path = project.root / "bible" / f"{name}.yaml"
        if not path.exists():
            return {}
        data = read_yaml(path) or {}
        return data if isinstance(data, dict) else {}

    def _entity(key: str, entry: dict[str, Any], category: str) -> dict[str, Any]:
        ext = {k: v for k, v in entry.items() if k not in ("name", "description")}
        ent: dict[str, Any] = {
            "id": str(key),
            "category": category,
            "triggerName": str(key),
            "label": entry.get("name") if isinstance(entry.get("name"), str) else str(key),
        }
        if isinstance(entry.get("description"), str):
            ent["description"] = entry["description"]
        if ext:
            ent[profile.MANJU_EXT_KEY] = ext
        return ent

    for key, entry in _read("characters").items():
        if isinstance(entry, dict):
            entities.append(_entity(key, entry, "CHARACTER"))

    for key, entry in _read("scenes").items():
        if not isinstance(entry, dict):
            continue
        entities.append(_entity(key, entry, "LOCATION"))
        ext = {k: v for k, v in entry.items() if k != "name"}
        scene: dict[str, Any] = {
            "id": str(key),
            "scene": entry.get("name") if isinstance(entry.get("name"), str) else str(key),
        }
        if ext:
            scene[profile.MANJU_EXT_KEY] = {"kind": "scene", **ext}
        scenes.append(scene)

    return entities, scenes


def _meta(project: "Project", timeline: Timeline) -> dict[str, Any]:
    config = project.load_config()
    width = int(timeline.width or config.width)
    height = int(timeline.height or config.height)
    fps = int(timeline.fps or config.fps or 24)

    ext: dict[str, Any] = {"fps": fps}
    if timeline.meta.compiled_from:
        ext["compiledFrom"] = str(timeline.meta.compiled_from)
    if timeline.tracks.captions:
        ext["captions"] = [c.model_dump() for c in timeline.tracks.captions]
    if timeline.tracks.overlay:
        ext["overlays"] = [o.model_dump() for o in timeline.tracks.overlay]

    return {
        "id": _stable_id("meta", config.name),
        "title": config.name,
        "width": width,
        "height": height,
        "durationInMs": int(timeline.duration_ms),
        "orientation": _orientation(width, height),
        profile.MANJU_EXT_KEY: ext,
    }


def _build_segments(project: "Project", timeline: Timeline) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []

    # Video: one segment per clip, derived id shot:<id>/video:main (or /video:<n>
    # if a shot somehow carries more than one video clip — kept deterministic).
    seen: dict[str, int] = {}
    for clip in timeline.tracks.video:
        assert isinstance(clip, VideoClip)
        seg = _video_segment(project, clip)
        n = seen.get(clip.shot, 0)
        seen[clip.shot] = n + 1
        if n:
            seg["id"] = f"shot:{clip.shot}/video:{n + 1}"
        segments.append(seg)

    for clip in timeline.tracks.voice:
        shot = _shot_from_source(clip.source)
        seg_id = f"shot:{shot}/voice:main" if shot else f"voice:{Path(clip.source).stem}"
        segments.append(_audio_segment(project, clip, track=_TRACK_VOICE,
                                       category="DIALOGUE", kind="voice", seg_id=seg_id))

    for i, clip in enumerate(timeline.tracks.music, start=1):
        segments.append(_audio_segment(project, clip, track=_TRACK_MUSIC,
                                       category="MUSIC", kind="music",
                                       seg_id=f"music:bed:{i}"))

    for i, clip in enumerate(timeline.tracks.sfx, start=1):
        segments.append(_audio_segment(project, clip, track=_TRACK_SFX,
                                       category="SOUND", kind="sfx", seg_id=f"sfx:{i}"))

    for i, clip in enumerate(timeline.tracks.ambient, start=1):
        seg_id = "ambient:main" if i == 1 else f"ambient:{i}"
        segments.append(_audio_segment(project, clip, track=_TRACK_AMBIENT,
                                       category="SOUND", kind="ambient", seg_id=seg_id))

    return segments


def _resolve_output(project: "Project", output: str | Path | None) -> Path:
    config = project.load_config()
    if output is None:
        return project.exports_dir / "openclap" / f"{config.name}.clap"
    # OPENCLAP-P0-001: an explicit --output used to only check "still inside the
    # project", so it could truncate canonical truth / an import into gzip
    # (`read_clap` then read the wreckage back cleanly). Keep openclap output
    # project-internal AND confine it to exports/ via the shared safeio owner,
    # which refuses truth/config/imports, directories and symlink/junction leaves.
    from ...core.safeio import SafeOutError, checked_out_path

    out = Path(output)
    resolved = (out if out.is_absolute() else (project.root / out)).resolve()
    if not resolved.is_relative_to(project.root.resolve()):
        raise ProjectError(
            f"导出路径超出项目边界: {output!r} — OpenClap 输出必须落在项目内。")
    try:
        return checked_out_path(resolved, project_root=project.root,
                                inside_roots=("exports",))
    except SafeOutError as exc:
        raise ProjectError(str(exc)) from exc


def export_openclap(project: "Project", timeline: Timeline, *,
                    output: str | Path | None = None) -> Path:
    """Write a ``.clap`` snapshot of the compiled timeline. Returns the path.

    Default output: ``exports/openclap/<project name>.clap`` (project-relative,
    atomic, deterministic). ``output`` overrides the destination but must stay
    within the project root.
    """
    entities_raw, scenes_raw = _bible_entities_and_scenes(project)
    segments_raw = _build_segments(project, timeline)
    meta_raw = _meta(project, timeline)

    header_raw: dict[str, Any] = {
        "format": profile.FORMAT,
        "numberOfWorkflows": 0,
        "numberOfEntities": len(entities_raw),
        "numberOfScenes": len(scenes_raw),
        "numberOfSegments": len(segments_raw),
    }

    raw_items = [header_raw, meta_raw, *entities_raw, *scenes_raw, *segments_raw]
    document = ClapDocument(
        raw_items=raw_items,
        header=ClapHeader(header_raw),
        meta=ClapMeta(meta_raw),
        workflows=[],
        entities=[ClapEntity(r) for r in entities_raw],
        scenes=[ClapScene(r) for r in scenes_raw],
        segments=[ClapSegment(r) for r in segments_raw],
    )

    out = _resolve_output(project, output)
    write_clap(document, out)
    return out
