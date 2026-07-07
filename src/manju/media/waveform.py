"""Audio waveform previews (round U): the picture of the sound, cached like
frames.

Two jobs backing the GUI's audio-editing surfaces (mixer rows, the 剪辑 strip,
subtitle/voice sync hints):

- :func:`waveform_png` — one ``showwavespic`` render of a source's audio as a
  PNG, content-addressed and cached, single ffmpeg pass.
- :func:`rms_levels` — ``buckets`` normalized loudness values (0.0–1.0) across
  the clip, as data: the sync-hint layer compares caption cue windows against
  actual speech energy (a cue over near-silence, or speech with no cue, is a
  hint), and clients too small for a PNG draw their own bars from it.

Discipline is exactly :mod:`manju.media.frames`: the cache lives under the
disposable ``.manju/frames`` runtime dir (``manju gc`` wipes it; everything
rebuilds on request), entries are addressed by the source's *content hash* so
identical bytes share one entry, sources are strictly read-only, and writes are
atomic. A source without an audio stream raises :class:`MediaError` with a
plain reason — callers decide whether that is normal (a silent take) or a bug.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, hash_file, short_hash
from .ffmpeg import MediaError, atomic_output, default_log, run_ffmpeg
from .frames import frames_cache_dir, _resolve_source
from .probe import probe

# showwavespic paints peak-per-column; a single neutral color on transparent
# lets both GUI themes composite it. Height stays small — it is a strip, not
# an oscilloscope.
_WAVE_COLOR = "#8ab4ff"


def waveform_png(
    project: Project, source_relpath: str | Path, *,
    width: int = 960, height: int = 96,
) -> Path:
    """Render ``source_relpath``'s audio as one cached waveform PNG.

    Cache name = ``sha256(content hash, "wave", width, height)`` under
    ``.manju/frames`` — a hit returns the file untouched. Mono-mixed
    (``showwavespic`` default) so voice/music sources read the same way.
    """
    abspath = _resolve_source(project, source_relpath)
    if not probe(abspath).has_audio:
        raise MediaError(f"no audio stream in {source_relpath} — 无音频轨,无法生成波形")

    w, h = max(16, int(width)), max(16, int(height))
    key = short_hash(cache_key(hash_file(abspath), "wave", w, h))
    cache = frames_cache_dir(project.root)
    dest = cache / f"{key}.png"
    if dest.exists():
        return dest

    log = default_log(project.root, "frames")
    cache.mkdir(parents=True, exist_ok=True)
    with atomic_output(dest) as tmp:
        run_ffmpeg(
            ["-i", abspath,
             "-filter_complex",
             f"aformat=channel_layouts=mono,showwavespic=s={w}x{h}:colors={_WAVE_COLOR}",
             "-frames:v", "1", "-f", "image2", "-c:v", "png", str(tmp)],
            log=log,
        )
        if not Path(tmp).is_file() or Path(tmp).stat().st_size == 0:
            raise MediaError(f"no waveform rendered from {source_relpath}")
    return dest


# The astats line we harvest: per-window overall RMS in dBFS. "-inf" = silence.
_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-inf)")

# Normalization floor: -60 dBFS and below reads as 0.0 (silence for speech
# purposes); 0 dBFS reads as 1.0.
_FLOOR_DB = -60.0


def rms_levels(
    project: Project, source_relpath: str | Path, *, buckets: int = 200,
) -> list[float]:
    """``buckets`` normalized RMS values (0.0–1.0) across the clip, cached.

    One ffmpeg pass: ``asetnsamples`` windows the stream so ``astats`` emits one
    RMS reading per window, harvested from the metadata printout. Values are
    normalized linearly from the −60 dBFS floor. The result is padded/truncated
    to exactly ``buckets`` entries so callers can index proportionally against
    the clip duration without re-checking length.
    """
    abspath = _resolve_source(project, source_relpath)
    info = probe(abspath)
    if not info.has_audio:
        raise MediaError(f"no audio stream in {source_relpath} — 无音频轨,无法计算响度")

    buckets = max(1, int(buckets))
    key = short_hash(cache_key(hash_file(abspath), "rms", buckets))
    cache = frames_cache_dir(project.root)
    dest = cache / f"{key}.json"
    if dest.exists():
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) == buckets:
                return [float(v) for v in data]
        except (ValueError, OSError):
            pass  # unreadable cache entry: fall through and rebuild (§3)

    # Window size: whole stream / buckets, in samples at the probed (or assumed
    # 44.1k) rate; asetnsamples requires a positive constant.
    duration_s = (info.duration_ms or 1000) / 1000.0
    sample_rate = 44100
    nsamples = max(256, int(duration_s * sample_rate / buckets))

    # run_ffmpeg pipes stderr into MediaError handling and returns nothing, so
    # the metadata printout goes to a scratch FILE (hex-only name: no filter
    # option escaping needed), read back after the pass.
    log = default_log(project.root, "frames")
    cache.mkdir(parents=True, exist_ok=True)
    meta_tmp = cache / f".{key}.astats-{os.getpid()}.txt"
    try:
        run_ffmpeg(
            ["-i", abspath,
             "-af",
             f"aformat=channel_layouts=mono:sample_rates={sample_rate},"
             f"asetnsamples=n={nsamples}:p=0,"
             "astats=metadata=1:reset=1,"
             f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file={meta_tmp}",
             "-f", "null", "-"],
            log=log,
        )
        text = meta_tmp.read_text(encoding="utf-8", errors="replace") if meta_tmp.exists() else ""
    finally:
        try:
            meta_tmp.unlink(missing_ok=True)
        except OSError:
            pass
    raw: list[float] = []
    for m in _RMS_RE.finditer(text):
        db = _FLOOR_DB if m.group(1) == "-inf" else float(m.group(1))
        raw.append(max(0.0, min(1.0, (db - _FLOOR_DB) / -_FLOOR_DB)))
    if not raw:
        raise MediaError(f"astats produced no RMS readings for {source_relpath}")

    # Resample to exactly `buckets` by proportional pick (cheap, deterministic).
    levels = [raw[min(len(raw) - 1, int(i * len(raw) / buckets))]
              for i in range(buckets)]
    cache.mkdir(parents=True, exist_ok=True)
    with atomic_output(dest) as tmp:
        Path(tmp).write_text(json.dumps(levels), encoding="utf-8")
    return levels
