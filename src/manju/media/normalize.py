"""§7 step ① — normalize any selected take into a uniform intermediate segment.

Every segment leaves this module with exactly the same shape so the concat
demuxer can stitch them with a stream copy: WxH (letter/pillar-boxed on black),
constant fps, yuv420p, and a single 48 kHz stereo audio stream (silence is
synthesized when the source has none, so downstream mixing never has to special
case a missing track).

``dest`` is written atomically (encode to a sibling temp, ``os.replace`` on
success — ``ffmpeg.atomic_output``): callers hand this module content-addressed
cache paths, and a crashed encode must never leave a truncated file there.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .ffmpeg import MediaError, atomic_output, run_ffmpeg
from .probe import probe

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
ANULLSRC = "anullsrc=channel_layout=stereo:sample_rate=48000"

Log = Callable[[str], None] | None


def _video_filter(width: int, height: int, fps: int, *, extend_stop_s: float | None = None) -> str:
    """scale-to-fit → pad-to-exact (black) → setsar=1 → fps → (clone-extend) → yuv420p."""
    parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        f"fps={fps}",
    ]
    if extend_stop_s is not None:
        # Freeze/clone the last frame to reach the target length (§7: short
        # sources are extended, not stretched). -t trims to the exact length.
        parts.append(f"tpad=stop_mode=clone:stop_duration={extend_stop_s:.3f}")
    parts.append("format=yuv420p")
    return ",".join(parts)


def _encode_args(dest: Path) -> list[str]:
    return [
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        str(dest),
    ]


def normalize_segment(
    src: Path,
    dest: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration_ms: int | None = None,
    log: Log = None,
) -> Path:
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    is_image = src.suffix.lower() in IMAGE_EXTS
    dur_s = (duration_ms / 1000.0) if duration_ms is not None else None

    args: list[str] = []

    if is_image:
        if dur_s is None:
            raise MediaError(f"normalize_segment: duration_ms is required for still image {src}")
        # Loop the still, synthesize matching silence, hold for the full length.
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur_s:.3f}", "-i", str(src)]
        args += ["-f", "lavfi", "-t", f"{dur_s:.3f}", "-i", ANULLSRC]
        vf = _video_filter(width, height, fps)
        args += ["-filter_complex", f"[0:v]{vf}[v]"]
        args += ["-map", "[v]", "-map", "1:a", "-t", f"{dur_s:.3f}"]
        with atomic_output(dest) as tmp_out:
            run_ffmpeg(args + _encode_args(tmp_out), log=log)
        return dest

    info = probe(src)
    has_audio = bool(info.has_audio)
    src_dur = info.duration_ms

    extend_s: float | None = None
    if dur_s is not None and (src_dur is None or src_dur < duration_ms):
        # Short source: clone the tail generously; -t below trims to exact.
        extend_s = dur_s
    vf = _video_filter(width, height, fps, extend_stop_s=extend_s)

    if has_audio:
        args += ["-i", str(src)]
        af_parts = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if dur_s is not None:
            af_parts.append("apad")  # pad audio so it too reaches the target length
        args += ["-filter_complex", f"[0:v]{vf}[v];[0:a]{','.join(af_parts)}[a]"]
        args += ["-map", "[v]", "-map", "[a]"]
    else:
        args += ["-i", str(src)]
        args += ["-f", "lavfi", "-i", ANULLSRC]
        args += ["-filter_complex", f"[0:v]{vf}[v]"]
        args += ["-map", "[v]", "-map", "1:a"]

    if dur_s is not None:
        args += ["-t", f"{dur_s:.3f}"]
    elif not has_audio:
        # anullsrc is infinite and there is no explicit length: stop with video.
        args += ["-shortest"]

    with atomic_output(dest) as tmp_out:
        run_ffmpeg(args + _encode_args(tmp_out), log=log)
    return dest
