"""Frame preview service (round-T): still frames pulled from any project media,
cached in a disposable runtime dir.

Two jobs, both single ffmpeg grabs that back the GUI trim/scrub control and the
cover picker:

- :func:`extract_frame` — one JPEG at a timestamp, cached and content-addressed
  so the scrubber (and the debugging CLI) never re-encode the same frame twice.
- :func:`frame_strip` — a row of evenly-spaced thumbnails for a scrub strip,
  produced in ONE ffmpeg invocation (an ``fps`` selection filter), not N.

Discipline mirrors media/webpreview.py: the cache lives under ``.manju/frames``
— derived, disposable, §3 (``manju gc`` may wipe it, everything here rebuilds on
the next request). Sources are strictly read-only. The one difference from the
webpreview key is deliberate and matches the spec: entries are addressed by the
source's *content hash* (not path+mtime+size), so identical bytes at two paths
share one frame and an in-place rewrite naturally lands on a fresh entry.

This service only PREVIEWS. It is NOT the cover's source of truth — packaging's
``cover.frame_ms`` stays the single writer of the film's cover (media/packaging.
py); a picker uses these previews to CHOOSE a frame_ms, then writes it through
the existing packaging path.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, hash_file, short_hash
from .ffmpeg import MediaError, atomic_output, default_log, run_ffmpeg
from .probe import probe

# JPEG quality for previews: 3 is visibly clean while staying small (the qc
# review-frame and packaging cover use the same neighbourhood).
_QV = ["-q:v", "3"]
_STRIP_WIDTH = 160  # default scrub-strip thumbnail width


def frames_cache_dir(project_root: Path) -> Path:
    """The frame cache: ``<root>/.manju/frames`` (§3 disposable runtime)."""
    return Path(project_root) / ".manju" / "frames"


def _resolve_source(project: Project, source_relpath: str | Path) -> Path:
    """Project-relative media path → an existing absolute file (rejects escapes
    above the root the same way :meth:`Project.resolve` does)."""
    abspath = project.resolve(source_relpath)  # raises ProjectError on escape
    if not abspath.is_file():
        raise MediaError(f"frame source not found: {source_relpath}")
    return abspath


def _frame_len_ms(fps: float | None) -> int:
    """Ceil of one frame in ms (defaults to 24fps when fps is unknown)."""
    rate = int(round(fps)) if fps else 24
    return -(-1000 // max(1, rate))  # ceil(1000/fps)


def extract_frame(
    project: Project, source_relpath: str | Path, at_ms: int, *,
    width: int | None = None,
) -> Path:
    """Grab one frame of ``source_relpath`` at ``at_ms`` as a cached JPEG.

    The cache name is ``sha256(source content hash, at_ms, width)`` under
    ``.manju/frames`` — a cache hit returns the existing file untouched (no
    probe, no ffmpeg). On a miss the seek is clamped inside the clip (a too-late
    ``at_ms`` still yields the last whole frame, never an empty extract) and a
    fast input seek (``-ss`` before ``-i``) pulls a single frame. ``width``
    scales to that pixel width (aspect kept, height rounded even); ``None`` keeps
    native size. The write is atomic (temp + ``os.replace``).
    """
    abspath = _resolve_source(project, source_relpath)
    at_ms = max(0, int(at_ms))
    key = short_hash(cache_key(hash_file(abspath), at_ms, width))
    cache = frames_cache_dir(project.root)
    dest = cache / f"{key}.jpg"
    if dest.exists():
        return dest  # content-addressed hit: no re-encode

    # Miss: probe once to clamp the seek inside the film.
    info = probe(abspath)
    seek_ms = at_ms
    if info.duration_ms:
        seek_ms = min(at_ms, max(0, info.duration_ms - _frame_len_ms(info.fps)))

    vf = f"scale={int(width)}:-2" if width else None
    log = default_log(project.root, "frames")
    cache.mkdir(parents=True, exist_ok=True)
    with atomic_output(dest) as tmp:
        args: list[str] = ["-ss", _sec(seek_ms), "-i", abspath, "-frames:v", "1"]
        if vf:
            args += ["-vf", vf]
        args += ["-update", "1", *_QV, str(tmp)]
        run_ffmpeg(args, log=log)
        if not Path(tmp).is_file() or Path(tmp).stat().st_size == 0:
            raise MediaError(
                f"no frame extracted from {source_relpath} at {at_ms}ms"
            )
    return dest


def frame_strip(
    project: Project, source_relpath: str | Path, count: int = 10,
    width: int = _STRIP_WIDTH,
) -> list[Path]:
    """Evenly-spaced scrub-strip thumbnails of ``source_relpath``.

    Exactly ``count`` JPEGs sampled across the clip in ONE ffmpeg pass — an
    ``fps=count/duration`` selection filter, not ``count`` separate seeks. The
    strip is cached (content hash + count + width) so a re-open of the scrubber
    is free; a source too short to yield ``count`` distinct frames pads with its
    last frame so the caller always gets a ``count``-long list. Frames land under
    ``.manju/frames`` next to the single-frame cache.
    """
    abspath = _resolve_source(project, source_relpath)
    count = max(1, int(count))
    width = int(width) if width else _STRIP_WIDTH

    prefix = short_hash(cache_key(hash_file(abspath), "strip", count, width))
    cache = frames_cache_dir(project.root)
    dests = [cache / f"{prefix}_{i:03d}.jpg" for i in range(count)]
    if all(d.exists() for d in dests):
        return dests  # content-addressed hit

    info = probe(abspath)
    if not info.duration_ms or info.duration_ms <= 0:
        raise MediaError(
            f"cannot build a strip: unknown duration for {source_relpath}"
        )
    rate = count / (info.duration_ms / 1000.0)
    vf = f"fps={rate:.6f}"
    if width:
        vf += f",scale={width}:-2"
    log = default_log(project.root, "frames")
    cache.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=cache) as td:
        pattern = str(Path(td) / "f_%04d.jpg")
        run_ffmpeg(
            ["-i", abspath, "-vf", vf, "-frames:v", str(count), *_QV, pattern],
            log=log,
        )
        produced = sorted(Path(td).glob("f_*.jpg"))
        if not produced:
            raise MediaError(f"no strip frames extracted from {source_relpath}")
        for i in range(count):
            if i < len(produced):
                os.replace(produced[i], dests[i])
            else:  # short source: pad with the last real frame
                shutil.copy2(dests[i - 1], dests[i])
    return dests


def gc_frames(project_root: Path) -> int:
    """Delete every cached frame/strip; return bytes freed. Everything here is
    rebuildable on demand (§3), so this is always safe."""
    cache = frames_cache_dir(project_root)
    if not cache.is_dir():
        return 0
    freed = 0
    for entry in cache.iterdir():
        try:
            if entry.is_file():
                size = entry.stat().st_size
                entry.unlink()
                freed += size
        except OSError:
            continue
    return freed


def _sec(ms: int | float) -> str:
    return f"{max(0.0, ms / 1000.0):.3f}"
