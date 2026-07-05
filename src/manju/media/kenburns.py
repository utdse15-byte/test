"""§8.4 local fallback: still image → slow push-in (Ken Burns) video.

Network-independent — pure FFmpeg zoompan — so the fallback chain always has a
terminal that can produce motion from a single reference frame. A silent stereo
track is included so the output is uniform with everything else in the pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .ffmpeg import run_ffmpeg
from .normalize import ANULLSRC

Log = Callable[[str], None] | None


def kenburns(
    image: Path,
    dest: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration_ms: int,
    zoom_from: float = 1.0,
    zoom_to: float = 1.12,
    log: Log = None,
) -> Path:
    image = Path(image)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    dur_s = duration_ms / 1000.0
    total_frames = max(2, int(round(dur_s * fps)))
    # Linear zoom from zoom_from to zoom_to over the output frames. Feeding a
    # single input frame makes 'on' (output frame index) the clean driver.
    span = total_frames - 1
    zoom_expr = f"{zoom_from}+({zoom_to}-{zoom_from})*on/{span}"
    # Cover the frame first, upscale for zoom headroom, then zoompan.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,"
        f"scale={width * 4}:{height * 4},"
        f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={width}x{height}:fps={fps},"
        f"format=yuv420p"
    )

    run_ffmpeg(
        [
            "-i", str(image),
            "-f", "lavfi", "-t", f"{dur_s:.3f}", "-i", ANULLSRC,
            "-filter_complex", f"[0:v]{vf}[v]",
            "-map", "[v]", "-map", "1:a",
            "-t", f"{dur_s:.3f}",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(dest),
        ],
        log=log,
    )
    return dest
