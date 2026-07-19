"""ffprobe wrapper → :class:`ProbeInfo` (§7, §9 existence/technical layer).

Uses ``ffprobe -print_format json -show_format -show_streams``. fps comes from
``r_frame_rate`` parsed as a fraction; duration from ``format.duration`` with a
fall back to the first video stream's duration.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core.models import ProbeInfo
from .ffmpeg import MediaError

FFPROBE = "ffprobe"
# #55: ffprobe is a metadata read, not a render — it gets a HARD, short-ish
# default timeout (unlike run_ffmpeg's generous render default) so a
# corrupt/unreadable/network-mounted file can never hang check/build.
DEFAULT_PROBE_TIMEOUT_S = 60.0


def _to_int_ms(seconds: str | float | None) -> int | None:
    if seconds is None:
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:  # NaN guard
        return None
    return int(round(value * 1000))


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "N/A"):
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        return float(rate)
    except (TypeError, ValueError):
        return None


def probe(path: Path, *, timeout: float | None = DEFAULT_PROBE_TIMEOUT_S) -> ProbeInfo:
    """Full technical probe. Raises :class:`MediaError` if the file is
    unreadable, ffprobe emits no parseable JSON, or it exceeds ``timeout``
    seconds (#55: a corrupt file / network mount must not hang forever;
    ``None`` disables the cap)."""
    path = Path(path)
    cmd = [
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(
            f"ffprobe 超时(>{timeout}s)for {path} — 可能是损坏媒体或网络挂载路径(goal W/#55)"
        ) from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise MediaError(f"ffprobe failed for {path}:\n{tail}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc

    streams = data.get("streams", []) or []
    fmt = data.get("format", {}) or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_ms = _to_int_ms(fmt.get("duration"))
    if duration_ms is None and video is not None:
        duration_ms = _to_int_ms(video.get("duration"))
    if duration_ms is None and audio is not None:
        duration_ms = _to_int_ms(audio.get("duration"))

    width = int(video["width"]) if video and video.get("width") is not None else None
    height = int(video["height"]) if video and video.get("height") is not None else None
    # r_frame_rate is preferred, but it can be the truthy-but-unusable string
    # "0/0" (a `... or ...` would short-circuit on it and never consult the
    # fallback), so fall through to avg_frame_rate only when the first is unusable.
    fps = None
    if video:
        fps = _parse_fps(video.get("r_frame_rate"))
        if fps is None:
            fps = _parse_fps(video.get("avg_frame_rate"))

    return ProbeInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=fps,
        has_audio=audio is not None,
    )


def probe_duration_ms(path: Path) -> int | None:
    """Duration in ms, or ``None`` when the file is unreadable (never raises)."""
    try:
        return probe(Path(path)).duration_ms
    except MediaError:
        return None
    except Exception:  # ffprobe missing, permission error, etc. — stay total
        return None
