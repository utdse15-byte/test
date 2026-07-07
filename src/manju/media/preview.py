"""Import-time previews (§11: `manju import` 自动转码代理/缩略图/波形).

Thumbnails and waveforms are derived artifacts, so they live in the
disposable runtime dir (.manju/thumbs — §3: delete it, rebuild it). The
"proxy" third of the §11 triple is deliberately lazy: the render pipeline's
normalized segment cache (§7 ①) IS the proxy, produced on first build
instead of eagerly at import time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.hashing import hash_file

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac"}


def make_preview(src: Path, thumbs_dir: Path) -> Path | None:
    """Best-effort preview: video/image -> 320px jpg poster, audio -> waveform
    png. Returns the preview path or None (never raises — a preview is a
    convenience, not a build input)."""
    src = Path(src)
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    try:
        if suffix in VIDEO_EXTS or suffix in IMAGE_EXTS:
            dest = thumbs_dir / (src.stem + ".jpg")
            args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
            if suffix in VIDEO_EXTS:
                args += ["-ss", "0.5", "-i", str(src)]
            else:
                args += ["-i", str(src)]
            args += ["-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5", str(dest)]
        elif suffix in AUDIO_EXTS:
            dest = thumbs_dir / (src.stem + "_wave.png")
            args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(src),
                    "-filter_complex", "showwavespic=s=320x96:colors=white",
                    "-frames:v", "1", str(dest)]
        else:
            return None
        proc = subprocess.run(args, capture_output=True)
        return dest if proc.returncode == 0 and dest.exists() else None
    except OSError:
        return None


def find_duplicate_import(imports_dir: Path, candidate: Path) -> Path | None:
    """First existing file under imports/ whose content equals ``candidate``'s.

    Imports are sacred (§3): nothing is ever deduped destructively — this only
    enables `manju import` to warn "已导入过 / already imported: <name>" before
    minting yet another full copy of the same rushes (§11 import conveniences).
    Size is the fast path; equality is decided by sha256
    (:func:`manju.core.hashing.hash_file`). Deterministic: candidates are
    scanned in sorted path order. Returns None when there is no duplicate,
    the dir is empty/missing, or the candidate is unreadable.
    """
    imports_dir, candidate = Path(imports_dir), Path(candidate)
    if not imports_dir.is_dir() or not candidate.is_file():
        return None
    try:
        size = candidate.stat().st_size
    except OSError:
        return None
    candidate_hash: str | None = None
    for existing in sorted(imports_dir.rglob("*")):
        try:
            if not existing.is_file() or existing.samefile(candidate):
                continue
            if existing.stat().st_size != size:
                continue  # size fast path: different size can't be identical
            if candidate_hash is None:
                candidate_hash = hash_file(candidate)
            if hash_file(existing) == candidate_hash:
                return existing
        except OSError:
            continue  # a vanished/unreadable entry is never a duplicate
    return None
