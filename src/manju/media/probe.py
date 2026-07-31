"""ffprobe wrapper → :class:`ProbeInfo` (§7, §9 existence/technical layer).

Uses ``ffprobe -print_format json -show_format -show_streams``. fps comes from
``r_frame_rate`` parsed as a fraction; duration from ``format.duration`` with a
fall back to the first video stream's duration.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

from ..core.models import ProbeInfo
from .ffmpeg import MediaError

FFPROBE = "ffprobe"
# #55: ffprobe is a metadata read, not a render — it gets a HARD, short-ish
# default timeout (unlike run_ffmpeg's generous render default) so a
# corrupt/unreadable/network-mounted file can never hang check/build.
DEFAULT_PROBE_TIMEOUT_S = 60.0

# 战役③ (2026-07-31): on a REAL 80-shot film, run_qc re-probed 81 unchanged
# files on every no-op build (7.3s) AND on every /exports page load (the
# page runs QC). probe(path) has no project handle, so the cache root
# arrives ambiently — the same contextvar pattern as media/ffmpeg's
# cancel_scope — and ONLY scoped callers (run_qc) get disk caching; every
# other probe() call is byte-identical to before. The key is file identity
# (path + size + mtime_ns): gen media is append-only, so identity is sound
# by construction, and any in-place edit changes mtime/size and re-probes.
# The cache lives under .manju (disposable — wiping it costs a re-probe,
# never truth). Errors are NEVER cached: a failing probe raises exactly as
# before, every time.
_probe_cache_root: "contextvars.ContextVar[Path | None]" = contextvars.ContextVar(
    "manju_probe_cache_root", default=None)


@contextmanager
def probe_cache_scope(root: Path):
    """Enable identity-keyed disk caching of probe results under ``root``."""
    token = _probe_cache_root.set(Path(root))
    try:
        yield
    finally:
        _probe_cache_root.reset(token)


def _cache_slot(root: Path, path: Path, kind: str) -> tuple[Path, dict]:
    st = path.stat()
    ident = {"source": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    name = hashlib.sha1(f"{kind}|{path}".encode("utf-8")).hexdigest()
    return root / f"{name}.json", ident


def _cache_get(path: Path, kind: str) -> dict | None:
    root = _probe_cache_root.get()
    if root is None:
        return None
    try:
        slot, ident = _cache_slot(root, path, kind)
        meta = json.loads(slot.read_text(encoding="utf-8"))
        if meta.get("ident") == ident:
            return meta.get("data")
    except (OSError, ValueError):
        pass
    return None


def _cache_put(path: Path, kind: str, data: dict) -> None:
    root = _probe_cache_root.get()
    if root is None:
        return
    try:
        slot, ident = _cache_slot(root, path, kind)
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_text(json.dumps({"ident": ident, "data": data},
                                   ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def cached_media_fact(path: Path, kind: str) -> dict | None:
    """Identity-cached fact for ``path`` under the ambient scope — ``None``
    outside a scope or on any mismatch. Sibling probes (QC's astats) share
    this ONE cache seam instead of growing their own."""
    return _cache_get(Path(path), kind)


def store_media_fact(path: Path, kind: str, data: dict) -> None:
    """Record a fact for ``path`` in the ambient scope (no-op unscoped)."""
    _cache_put(Path(path), kind, data)


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
    cached = _cache_get(path, "probe")
    if cached is not None:
        return ProbeInfo.model_validate(cached)
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

    info = ProbeInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=fps,
        has_audio=audio is not None,
    )
    _cache_put(path, "probe", info.model_dump())
    return info


def probe_duration_ms(path: Path) -> int | None:
    """Duration in ms, or ``None`` when the file is unreadable (never raises)."""
    try:
        return probe(Path(path)).duration_ms
    except MediaError:
        return None
    except Exception:  # ffprobe missing, permission error, etc. — stay total
        return None
