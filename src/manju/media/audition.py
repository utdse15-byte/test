"""Audition render — voice + captions + music, no paid picture (WP2).

Pipeline helper used by ``manju build --target audition``:
1. Compile an in-memory timeline with ``allow_missing_takes`` (slate for
   missing video).
2. Never write ``timeline.json`` (the real timeline stays gated).
3. Render ``renders/audition/audition_vN.mp4`` with a content-key sidecar
   that uses slate params in place of segment source hashes so a second
   run is idempotent.

Slate clips are lavfi color + drawtext shot id, generated under
``.manju/audition/slates/`` (disposable runtime).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from ..core.container import Project
from ..core.hashing import cache_key, hash_file, short_hash
from ..core.models import Timeline, VideoClip
from ..core.yamlio import atomic_write_text
from .ffmpeg import MediaError, run_ffmpeg


def audition_dir(project: Project) -> Path:
    d = project.root / "renders" / "audition"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slate_dir(project: Project) -> Path:
    d = project.root / ".manju" / "audition" / "slates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_slate(
    project: Project,
    shot_id: str,
    *,
    duration_ms: int,
    width: int,
    height: int,
    fps: int,
) -> Path:
    """A cheap color slate mp4 labeled with ``shot_id``. Content-addressed
    by geometry+duration+label so rebuilds hit the cache."""
    dur_s = max(0.1, duration_ms / 1000.0)
    key = short_hash(cache_key({
        "shot": shot_id, "dur_ms": duration_ms,
        "w": width, "h": height, "fps": fps, "kind": "audition_slate",
    }), 12)
    dest = slate_dir(project) / f"{shot_id}_{key}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    # Safe drawtext: alnum/underscore only for the label overlay
    label = "".join(c if c.isalnum() or c in "-_" else "_" for c in shot_id)[:32]
    # Escape for ffmpeg drawtext
    vf = (
        f"drawtext=text='{label}':fontsize=48:fontcolor=white:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )
    # ffmpeg writes a TEMP sibling; only a fully-finished encode is published
    # (os.replace) at the content-addressed name. Writing dest directly let a
    # killed/failed encode leave a partial >0-byte file that the size>0 reuse
    # gate above then trusted FOREVER (and every rebuild re-served it).
    tmp = dest.with_name(dest.name + f".{os.getpid()}.part.mp4")
    args = [
        "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={width}x{height}:d={dur_s}:r={fps}",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={dur_s}",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-tune", "stillimage",
        "-c:a", "aac", "-shortest",
        str(tmp),
    ]
    try:
        try:
            run_ffmpeg(args, log_name="audition", subject=f"slate-{shot_id}")
        except Exception:
            # Floor: no drawtext (font may be missing on minimal hosts)
            tmp.unlink(missing_ok=True)
            args_plain = [
                "-f", "lavfi", "-i",
                f"color=c=0x1a1a2e:s={width}x{height}:d={dur_s}:r={fps}",
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={dur_s}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-tune", "stillimage",
                "-c:a", "aac", "-shortest",
                str(tmp),
            ]
            try:
                run_ffmpeg(args_plain, log_name="audition", subject=f"slate-{shot_id}")
            except Exception as exc2:
                raise MediaError(
                    f"audition slate 生成失败: {' '.join(str(exc2).split())[:300]}"
                ) from exc2
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise MediaError(f"audition slate 生成失败: ffmpeg 未产出 {tmp.name}")
        os.replace(tmp, dest)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return dest


def materialize_slates(project: Project, timeline: Timeline) -> Timeline:
    """Replace any ``__slate__`` / missing-source video clips with generated
    slate files. Returns a NEW timeline (does not mutate the argument in place
    beyond clip.source rewrites on a deep-ish copy via model_dump)."""
    data = timeline.model_dump()
    clips = data.get("tracks", {}).get("video", []) or []
    for clip in clips:
        src = str(clip.get("source") or "")
        take = str(clip.get("take") or "")
        shot = str(clip.get("shot") or "")
        need = (
            take == "__slate__"
            or src.startswith(".manju/audition/slates/")
            or src.startswith("__slate__")
            or (src and not (project.root / src).exists()
                and not Path(src).is_absolute())
        )
        # Only auto-slate explicit slate markers or missing sources for
        # non-packaging shots
        if take == "__slate__" or src.startswith("__slate__"):
            path = ensure_slate(
                project, shot or "shot",
                duration_ms=int(clip.get("duration_ms") or 1000),
                width=timeline.width, height=timeline.height, fps=timeline.fps,
            )
            clip["source"] = project.relpath(path)
            clip["take"] = "__slate__"
        elif src and not project.resolve(src).exists() and not shot.startswith("__"):
            # missing take media → slate for audition
            path = ensure_slate(
                project, shot or "shot",
                duration_ms=int(clip.get("duration_ms") or 1000),
                width=timeline.width, height=timeline.height, fps=timeline.fps,
            )
            clip["source"] = project.relpath(path)
            clip["take"] = "__slate__"
    return Timeline.model_validate(data)


def audition_content_key(
    project: Project,
    timeline: Timeline,
    *,
    ass_file: Path | None,
) -> str:
    """Like final_content_key but segment keys are slate-params (or real
    source hash when a real take is present) — so video generation is never
    required for a stable key."""
    from .render import _audio_input_hashes, _enc_params, load_look

    seg_keys: list[str] = []
    for clip in timeline.tracks.video:
        src_path = project.resolve(clip.source)
        if clip.take == "__slate__" or not src_path.exists():
            seg_keys.append(cache_key({
                "kind": "slate",
                "shot": clip.shot,
                "duration_ms": clip.duration_ms,
                "w": timeline.width,
                "h": timeline.height,
                "fps": timeline.fps,
            }))
        else:
            seg_keys.append(f"file:{hash_file(src_path)}")
    tl_payload = timeline.model_dump(exclude={"meta"})
    payload: dict[str, Any] = {
        "timeline": tl_payload,
        "segments": seg_keys,
        "ass": hash_file(ass_file) if ass_file and Path(ass_file).exists() else None,
        "audio": _audio_input_hashes(project, timeline),
        "encoding": _enc_params("final"),
        "target": "audition",
    }
    look = load_look(project)
    if look.active:
        payload["look"] = {"preset": look.preset, "intensity": look.intensity}
    return cache_key(payload)


def _next_audition_path(project: Project) -> Path:
    d = audition_dir(project)
    nums = []
    for p in d.glob("audition_v*.mp4"):
        stem = p.stem  # audition_vN
        try:
            nums.append(int(stem.split("_v", 1)[1]))
        except (IndexError, ValueError):
            pass
    n = max(nums, default=0) + 1
    return d / f"audition_v{n}.mp4"


def _read_key(path: Path) -> str | None:
    sc = path.with_suffix(".key.json")
    if not sc.exists():
        return None
    try:
        data = json.loads(sc.read_text(encoding="utf-8"))
        return str(data.get("final_key") or "") or None
    except (ValueError, OSError):
        return None


def _newest_audition(project: Project) -> Path | None:
    d = audition_dir(project)
    versions = []
    for p in d.glob("audition_v*.mp4"):
        try:
            n = int(p.stem.split("_v", 1)[1])
            versions.append((n, p))
        except (IndexError, ValueError):
            pass
    return max(versions, key=lambda t: t[0])[1] if versions else None


def render_audition(
    project: Project,
    timeline: Timeline,
    *,
    ass_file: Path | None = None,
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Materialize slates, then render via the normal final pipeline into
    ``renders/audition/``. Idempotent via audition content key."""
    from .ffmpeg import default_log
    from .render import _write_key_sidecar, render_timeline

    if log is None:
        log = default_log(project.root, "audition")

    tl = materialize_slates(project, timeline)
    key = audition_content_key(project, tl, ass_file=ass_file)

    if not force:
        newest = _newest_audition(project)
        if newest is not None and _read_key(newest) == key:
            log(f"audition up-to-date (content key match): reusing {newest.name}")
            return newest

    out = _next_audition_path(project)
    # Reuse the final renderer with an explicit out_path. render_timeline
    # still computes final_content_key for its own skip logic — we pass force
    # and out_path so it always encodes to our audition path.
    rendered = render_timeline(
        project, tl, target="final", out_path=out, ass_file=ass_file, force=True, log=log,
    )
    # Write OUR key (slate-aware), not the final content key
    _write_key_sidecar(rendered, key, "audition")
    log(f"audition written: {project.relpath(rendered)}")
    return rendered
