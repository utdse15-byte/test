"""Repair operations (round-Q, §9): deterministic ffmpeg fixes that turn one
take into a *new* take.

Every op is append-only (§3): it reads a source take, builds a corrected clip
with a single ffmpeg pass, and registers the result as a fresh ``take_NN`` via
:meth:`Project.register_take`. The source take is never touched. The new take's
sidecar carries ``provider="repair"`` and a ``params`` block recording the op,
its parameters and the source take name — the repair's lineage.

Durations are always snapped to the project frame grid (FIX-B, the same rule the
compiler and packaging use) so a repaired clip drops straight onto the timeline
without re-introducing off-grid drift.

    retime_take     speed up / slow down (setpts + an atempo chain)
    extend_take     lengthen by freezing the last frame, or padding black
    trim_take       shorten from the tail (frame-snapped)
    set_inout_take  crop to an exact [in, out) source region (TAKE IN/OUT), the
                    frame-accurate cut a GUI trim control drives
    crop_pad_take   fix an aspect mismatch: center-crop, or scale-fit onto a
                    blurred-background pad (the vertical-video treatment)

None of these edit ``media/render.py`` — repair is its own small filtergraph
vocabulary, kept deliberately separate from the mix/normalize pipeline.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..core.container import Project, ProjectError, TakeInfo
from ..core.models import ProbeInfo, TakeSidecar
from ..timeline.compiler import snap_to_frame_grid
from .ffmpeg import MediaError, default_log, run_ffmpeg
from .probe import probe

# atempo only accepts 0.5–2.0; anything outside is reached by chaining passes.
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0

# H.264 + yuv420p + AAC: the same broadly-compatible encode the rest of the
# pipeline uses; kept local so repair never reaches into render.py.
_ENC = [
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
    "-c:a", "aac", "-movflags", "+faststart",
]


# --------------------------------------------------------------- helpers


def _resolve_take(project: Project, shot: str, take: str) -> TakeInfo:
    src = project.get_take(shot, take)
    if src is None:
        raise ProjectError(f"take not found: {shot}/{take}")
    if src.media_path is None or not src.media_path.exists():
        raise ProjectError(f"take media missing on disk: {shot}/{take}")
    return src


def _src_probe(src: TakeInfo) -> ProbeInfo:
    info = probe(src.media_path)  # raises MediaError if unreadable
    if info.duration_ms is None:
        raise MediaError(f"cannot read source duration: {src.media_path}")
    return info


def _sec(ms: int | float) -> str:
    return f"{max(0.0, ms / 1000.0):.6f}"


def _atempo_chain(tempo: float) -> list[float]:
    """Factor a tempo multiplier into a chain of atempo values each within
    [0.5, 2.0] whose product is ``tempo`` (atempo's hard limit, §7)."""
    if tempo <= 0:
        raise MediaError(f"invalid tempo {tempo}")
    parts: list[float] = []
    while tempo > _ATEMPO_MAX:
        parts.append(_ATEMPO_MAX)
        tempo /= _ATEMPO_MAX
    while tempo < _ATEMPO_MIN:
        parts.append(_ATEMPO_MIN)
        tempo /= _ATEMPO_MIN
    parts.append(round(tempo, 6))
    return parts


def _register_repaired(
    project: Project,
    shot: str,
    src: TakeInfo,
    out_file: Path,
    *,
    op: str,
    params: dict,
) -> TakeInfo:
    """Probe the built clip and register it as a new take with repair lineage."""
    try:
        out_probe = probe(out_file)
    except MediaError:
        out_probe = None
    sidecar = TakeSidecar(
        provider="repair",
        # inherit the source's spec_hash so a repaired clip of an up-to-date take
        # is still 'fresh' w.r.t. the shot spec (§4.3); the repair did not change
        # the generative intent, only the media.
        spec_hash=src.sidecar.spec_hash,
        params={"op": op, "source_take": src.name, **params},
        probe=out_probe,
    )
    return project.register_take(shot, out_file, sidecar, move=True)


def _tmp_out(suffix: str = ".mp4") -> tuple[Path, Path]:
    d = Path(tempfile.mkdtemp(prefix="manju_repair_"))
    return d, d / f"out{suffix}"


# --------------------------------------------------------------- retime


def retime_take(project: Project, shot: str, take: str, factor: float) -> TakeInfo:
    """Speed the take up or down. ``factor`` is a *duration* multiplier: 0.9 →
    10% faster/shorter, 1.5 → 50% slower/longer. Video PTS is scaled by
    ``factor``; audio tempo by ``1/factor`` (chained atempo when outside
    [0.5, 2.0]). The output is snapped to the frame grid."""
    if factor <= 0:
        raise MediaError(f"retime factor must be > 0 (got {factor})")
    src = _resolve_take(project, shot, take)
    info = _src_probe(src)
    fps = project.load_config().fps or int(round(info.fps or 24)) or 24

    target_ms = snap_to_frame_grid(max(1, int(round(info.duration_ms * factor))), fps)
    # drive setpts from the snapped target so the output lands exactly on grid
    v_factor = target_ms / info.duration_ms
    log = default_log(project.root, "repair")

    d, out = _tmp_out()
    try:
        vchain = f"setpts={v_factor:.6f}*PTS,fps={fps}"
        args: list[str] = ["-i", src.media_path]
        if info.has_audio:
            atempo = ",".join(f"atempo={t}" for t in _atempo_chain(1.0 / factor))
            args += [
                "-filter_complex", f"[0:v]{vchain}[v];[0:a]{atempo}[a]",
                "-map", "[v]", "-map", "[a]",
            ]
        else:
            args += ["-vf", vchain, "-an"]
        args += ["-t", _sec(target_ms), *_ENC, str(out)]
        run_ffmpeg(args, log=log)
        return _register_repaired(
            project, shot, src, out,
            op="retime", params={"factor": factor, "target_ms": target_ms},
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------- extend


def extend_take(project: Project, shot: str, take: str, ms: int,
                mode: str = "freeze") -> TakeInfo:
    """Lengthen the take by ~``ms`` (snapped). ``mode="freeze"`` clones the last
    frame (tpad clone); ``mode="pad_black"`` appends black. Audio is padded with
    silence in both cases."""
    if mode not in ("freeze", "pad_black"):
        raise MediaError(f"extend mode must be freeze|pad_black (got {mode!r})")
    if ms <= 0:
        raise MediaError(f"extend ms must be > 0 (got {ms})")
    src = _resolve_take(project, shot, take)
    info = _src_probe(src)
    fps = project.load_config().fps or int(round(info.fps or 24)) or 24

    target_ms = snap_to_frame_grid(info.duration_ms + ms, fps)
    pad_ms = max(1, target_ms - info.duration_ms)
    log = default_log(project.root, "repair")

    stop_mode = "clone" if mode == "freeze" else "add:color=black"
    d, out = _tmp_out()
    try:
        vchain = f"tpad=stop_mode={stop_mode}:stop_duration={_sec(pad_ms)},fps={fps}"
        args: list[str] = ["-i", src.media_path]
        if info.has_audio:
            args += [
                "-filter_complex",
                f"[0:v]{vchain}[v];[0:a]apad=pad_dur={_sec(pad_ms)}[a]",
                "-map", "[v]", "-map", "[a]",
            ]
        else:
            args += ["-vf", vchain, "-an"]
        args += ["-t", _sec(target_ms), *_ENC, str(out)]
        run_ffmpeg(args, log=log)
        return _register_repaired(
            project, shot, src, out,
            op="extend", params={"ms": ms, "mode": mode, "target_ms": target_ms},
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------- trim


def trim_take(project: Project, shot: str, take: str, ms: int) -> TakeInfo:
    """Shorten the take from the tail by ~``ms`` (snapped). The result keeps at
    least one frame; a re-encode makes the cut frame-accurate."""
    if ms <= 0:
        raise MediaError(f"trim ms must be > 0 (got {ms})")
    src = _resolve_take(project, shot, take)
    info = _src_probe(src)
    fps = project.load_config().fps or int(round(info.fps or 24)) or 24

    frame_ms = 1000.0 / fps
    target_ms = snap_to_frame_grid(max(int(round(frame_ms)), info.duration_ms - ms), fps)
    if target_ms >= info.duration_ms:
        raise MediaError(
            f"trim of {ms}ms leaves nothing to remove from a {info.duration_ms}ms take"
        )
    log = default_log(project.root, "repair")

    d, out = _tmp_out()
    try:
        args: list[str] = ["-i", src.media_path, "-t", _sec(target_ms)]
        if not info.has_audio:
            args += ["-an"]
        args += ["-r", str(fps), *_ENC, str(out)]
        run_ffmpeg(args, log=log)
        return _register_repaired(
            project, shot, src, out,
            op="trim", params={"ms": ms, "target_ms": target_ms},
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------- set in/out (crop)


def set_inout_take(project: Project, shot: str, take: str,
                   in_ms: int, out_ms: int) -> TakeInfo:
    """TAKE IN/OUT: register a NEW take that is exactly the source's
    ``[in_ms, out_ms)`` region, re-encoded frame-accurately.

    The cut is a fast input seek (``-ss`` *before* ``-i``) combined with a
    frames-exact output trim (``-t`` on the snapped span + a forced ``-r`` frame
    rate). Modern ffmpeg makes an input seek accurate under a re-encode — it
    decodes from the preceding keyframe and drops frames until the target — so
    the output's first frame is the source frame at ``in_ms`` and the length is
    the snapped span, verified against probes by the tests. Because the seek is
    applied to every input stream and output PTS restart at zero, audio stays in
    lock-step with picture. Duration is snapped to the project frame grid so the
    cropped take drops onto the timeline without off-grid drift.

    Validation (clean one-line errors): ``0 <= in_ms < out_ms <= source
    duration`` and the region must span at least one whole frame. The source
    take is never touched (append-only, §3); lineage records
    ``op="set_inout"`` with ``in_ms``/``out_ms``/``source_take``.
    """
    in_ms = int(in_ms)
    out_ms = int(out_ms)
    if in_ms < 0:
        raise MediaError(f"in-ms must be >= 0 (got {in_ms})")
    if out_ms <= in_ms:
        raise MediaError(f"out-ms ({out_ms}) must be greater than in-ms ({in_ms})")

    src = _resolve_take(project, shot, take)
    info = _src_probe(src)
    if out_ms > info.duration_ms:
        raise MediaError(
            f"out-ms ({out_ms}) exceeds source duration ({info.duration_ms}ms)"
        )
    fps = project.load_config().fps or int(round(info.fps or 24)) or 24
    if round((out_ms - in_ms) * fps / 1000) < 1:
        raise MediaError(
            f"region [{in_ms},{out_ms}) is shorter than one frame at {fps}fps"
        )
    target_ms = snap_to_frame_grid(out_ms - in_ms, fps)
    log = default_log(project.root, "repair")

    d, out = _tmp_out()
    try:
        # -ss BEFORE -i: fast input seek, accurate under the re-encode below.
        args: list[str] = ["-ss", _sec(in_ms), "-i", src.media_path,
                           "-t", _sec(target_ms)]
        if not info.has_audio:
            args += ["-an"]
        args += ["-r", str(fps), *_ENC, str(out)]
        run_ffmpeg(args, log=log)
        return _register_repaired(
            project, shot, src, out,
            op="set_inout",
            params={"in_ms": in_ms, "out_ms": out_ms, "target_ms": target_ms},
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------- crop / pad


def crop_pad_take(project: Project, shot: str, take: str,
                  mode: str = "center_crop") -> TakeInfo:
    """Fix an aspect mismatch to the project's WxH. ``mode="center_crop"``
    scales to fill and crops the centre; ``mode="pad_blur"`` scales to fit and
    lays it over a blurred, filled copy of itself (the standard vertical-video
    background). Duration is unchanged (snapped)."""
    if mode not in ("center_crop", "pad_blur"):
        raise MediaError(f"croppad mode must be center_crop|pad_blur (got {mode!r})")
    src = _resolve_take(project, shot, take)
    info = _src_probe(src)
    config = project.load_config()
    w, h, fps = config.width, config.height, config.fps or int(round(info.fps or 24)) or 24
    target_ms = snap_to_frame_grid(info.duration_ms, fps)
    log = default_log(project.root, "repair")

    if mode == "center_crop":
        vfilter = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,fps={fps}"
        )
    else:  # pad_blur: blurred fill background + centred sharp foreground
        vfilter = (
            f"split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=luma_radius=40:luma_power=1,setsar=1[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[fgf];"
            f"[bgb][fgf]overlay=(W-w)/2:(H-h)/2,fps={fps}"
        )

    d, out = _tmp_out()
    try:
        args: list[str]
        if mode == "center_crop":
            args = ["-i", src.media_path, "-vf", vfilter]
        else:
            args = ["-i", src.media_path, "-filter_complex", f"[0:v]{vfilter}[v]",
                    "-map", "[v]"]
            args += ["-map", "0:a"] if info.has_audio else ["-an"]
        if mode == "center_crop" and not info.has_audio:
            args += ["-an"]
        args += ["-t", _sec(target_ms), *_ENC, str(out)]
        run_ffmpeg(args, log=log)
        return _register_repaired(
            project, shot, src, out,
            op="croppad", params={"mode": mode, "width": w, "height": h},
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)
