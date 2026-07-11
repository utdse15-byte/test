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


# ------------------------------------------------- deterministic frame plan (WP1)

# AI_IDE_15 §5 WP1: the minimal per-shot review-frame plan is first / 25% / 50% /
# 75% / last. The plan is a PURE function of the media's duration + fps + content
# hash, so it is repeatable and the generated frames are derivatives (contract:
# "抽帧计划必须由 duration/fps/hash 决定,可重复;生成的 frame/contact sheet 是派生物").
FRAME_PLAN_SCHEMA = "manju.qc.frame_plan/v1"
FRAME_PLAN_LABELS = ("first", "p25", "mid", "p75", "last")
# scene-change slots are ONLY added when the existing tooling already detects
# scenes. Core has black/freeze detectors but NO scene-change detector, and this
# batch does not add scdet (addendum ruling 2) — recorded, never faked.
_SCENE_SLOTS_SKIPPED = {
    "status": "SKIPPED_WITH_EVIDENCE",
    "reason": ("no scene-change detector exists in core (only black/freeze "
               "detectors); adding scdet is out of scope for this batch — "
               "scene-change frame slots are recorded as skipped, never faked"),
}


def frame_plan(duration_ms: int | None, fps: float | None,
               media_hash: str | None) -> dict:
    """The deterministic review-frame plan for one clip (§5 WP1).

    Returns ``{schema, positions:[{label, at_ms}], plan_digest, scene_change_slots,
    duration_ms, fps}``. PURE: the positions are a function of ``duration_ms``
    alone (first / 25% / 50% / 75% / last); ``fps`` only sets the LAST extractable
    frame (``duration - one frame``) so 'last' resolves to a real frame exactly as
    :func:`extract_frame`'s clamp does — never an empty extract past EOF. The
    ``plan_digest`` folds in ``media_hash`` so a regenerated take (new bytes) has a
    distinct plan identity even at an identical duration. Extraction of each
    position stays the EXISTING content-addressed ``.manju/frames`` cache
    (:func:`extract_frame`) — this adds a plan, never a second cache.

    A clip with an unknown/zero duration degrades to a single ``first`` frame at
    0ms (honest: nothing else is derivable), never an invented mid/last.
    """
    dur = int(duration_ms or 0)
    if dur <= 0:
        positions = [{"label": "first", "at_ms": 0}]
    else:
        last = max(0, dur - _frame_len_ms(fps))
        raw = {"first": 0, "p25": dur // 4, "mid": dur // 2,
               "p75": (dur * 3) // 4, "last": last}
        # keep the canonical order; clamp every interior point below 'last' so a
        # very short clip never orders p75 after last.
        positions = [{"label": lbl, "at_ms": min(raw[lbl], last) if lbl != "first" else 0}
                     for lbl in FRAME_PLAN_LABELS]
    plan_digest = short_hash(cache_key(
        FRAME_PLAN_SCHEMA, media_hash or "", dur, int(round(fps)) if fps else 0,
        [(p["label"], p["at_ms"]) for p in positions]))
    return {
        "schema": FRAME_PLAN_SCHEMA,
        "duration_ms": dur or None,
        "fps": fps,
        "positions": positions,
        "plan_digest": plan_digest,
        "scene_change_slots": dict(_SCENE_SLOTS_SKIPPED),
    }


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

    The cache name is ``sha256(source content hash, CLAMPED at_ms, width)``
    under ``.manju/frames``: the seek is clamped inside the clip (a too-late
    ``at_ms`` still yields the last whole frame, never an empty extract) BEFORE
    the key is derived from it (round-W #36) — otherwise several different
    too-late ``at_ms`` values that all clamp to the same last frame would each
    mint their own cache file for byte-identical JPEGs (a GUI scrubber/cover
    picker dragging past the end is exactly the high-frequency case that hits
    this). A cache hit returns the existing file untouched — no ffmpeg
    extraction — but computing the key itself always costs one cheap ffprobe
    (duration) either way, same as the content-hash it was already paying.
    ``width`` scales to that pixel width (aspect kept, height rounded even);
    ``None`` keeps native size. The write is atomic (temp + ``os.replace``).
    """
    abspath = _resolve_source(project, source_relpath)
    at_ms = max(0, int(at_ms))
    # round-W #36: probe FIRST and clamp at_ms to the media's real duration
    # BEFORE the cache key is derived from it — the old order hashed the raw
    # (possibly past-EOF) request time, so N different too-late at_ms values
    # that all clamp to the same last frame minted N distinct cache files for
    # byte-identical JPEGs (a GUI scrubber/cover-picker dragging past the end
    # is exactly the high-frequency case that hits this). Clamping first means
    # every request that resolves to the same actual frame shares one entry.
    info = probe(abspath)
    seek_ms = at_ms
    if info.duration_ms:
        seek_ms = min(at_ms, max(0, info.duration_ms - _frame_len_ms(info.fps)))
    key = short_hash(cache_key(hash_file(abspath), seek_ms, width))
    cache = frames_cache_dir(project.root)
    dest = cache / f"{key}.jpg"
    if dest.exists():
        return dest  # content-addressed hit: no re-encode

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
