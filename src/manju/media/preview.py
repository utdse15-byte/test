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
