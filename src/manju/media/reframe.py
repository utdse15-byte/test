"""AI_IDE_19 WP4 — Smart Reframe: derived ROI tracks → explicit crop keyframes.

A pure, deterministic compiler (contract §7): ROI tracks (from analysis evidence
or explicit manual entries) become crop keyframes a human can edit in the Board /
NLE. The rules the contract mandates all live here:

* **crop 跳变限速** — the crop window's motion is rate-limited (declared max px/s),
  so a subject that jumps produces a smooth pan, never a teleport (§11 row 7);
* **安全区** — subtitle / logo / key-object safe boxes are kept fully inside the
  crop window (§11 row 8);
* **多人冲突 → UNKNOWN/needs_manual** — subjects that cannot share one crop window
  are not framed by a guess; the compile reports ``needs_manual`` (§7);
* **crop 不可行 → blanking/pillarbox** — when a safe box cannot fit any crop
  window (or the target aspect is unreachable), the compile falls back to a
  blanking / pillarbox strategy rather than fabricating a crop (§11 row 9).

FORMAT_ONLY invariant (§7, addendum ruling 4): reframe is PURE GEOMETRY. It never
changes duration, segment selection, audio or subtitle semantics — exactly the
axes AI_IDE_13C's ``timeline_semantic_digest`` covers and width/height are the
axes it EXCLUDES. So a 9:16 reframe of a 16:9 master shares the base's semantic
digest; :func:`reframe_preserves_semantics` proves it via 13C's own invariant
checker. The keyframes land as a human-editable framing artifact adopted through
13C's external-framing path (inert until adopted — :func:`adopt_reframe_artifact`).

Executable (13C recorded framing as ``declared_only``; here it EXECUTES):
:func:`ffmpeg_crop_expr` compiles the keyframes to a deterministic ffmpeg ``crop``
expression and :func:`execute_reframe` produces a NEW append-only take (the
repair-ops precedent — a small local filtergraph, never touching render.py).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

# compile-status tokens
OK = "ok"
NEEDS_MANUAL = "needs_manual"   # multi-subject conflict → UNKNOWN, human resolves
BLANKING = "blanking"           # crop infeasible → blanking / pillarbox fallback


class ReframeError(RuntimeError):
    """A reframe could not be compiled or executed. ``str()`` carries no secret."""


# ------------------------------------------------------------------ geometry


def _crop_size(source_wh: tuple[int, int], target_wh: tuple[int, int]) -> tuple[int, int, bool]:
    """The crop-window size (even px) in SOURCE space that matches the target
    aspect by REMOVING content (never upscaling). ``feasible`` is False when the
    target aspect cannot be reached from the source by cropping alone."""
    SW, SH = int(source_wh[0]), int(source_wh[1])
    TW, TH = int(target_wh[0]), int(target_wh[1])
    if min(SW, SH, TW, TH) <= 0:
        return 0, 0, False
    target_ar = TW / TH
    source_ar = SW / SH
    if target_ar <= source_ar:          # narrower/taller target → crop width
        ch, cw = SH, int(round(SH * target_ar))
    else:                               # wider target → crop height
        cw, ch = SW, int(round(SW / target_ar))
    cw -= cw % 2
    ch -= ch % 2
    feasible = 0 < cw <= SW and 0 < ch <= SH
    return cw, ch, feasible


def _safe_interval(boxes: list[dict], axis_lo_key: str, axis_span_key: str,
                   dim_px: int, window: int) -> tuple[float, float] | None:
    """The allowed top-left interval on one axis so EVERY safe box stays inside a
    ``window``-wide crop within ``[0, dim_px]``. ``None`` when impossible (a box
    is wider than the window, or boxes conflict) → blanking fallback."""
    lo, hi = 0.0, float(dim_px - window)
    if hi < lo:
        return None
    for b in boxes or []:
        bl = float(b.get(axis_lo_key, 0.0)) * dim_px
        bw = float(b.get(axis_span_key, 0.0)) * dim_px
        if bw > window:
            return None                 # box wider than any crop window
        # window x must satisfy  x <= bl  and  x + window >= bl + bw
        lo = max(lo, bl + bw - window)
        hi = min(hi, bl)
    return (lo, hi) if lo <= hi else None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _rate_limit(times_ms: list[int], targets: list[float], max_px_per_s: float) -> list[float]:
    """Walk the target positions in time order, limiting each step to
    ``max_px_per_s * dt``. Targets already lie in the safe interval, so the
    rate-limited path (which stays between consecutive targets) stays safe too."""
    if not targets:
        return []
    out = [float(targets[0])]
    for i in range(1, len(targets)):
        dt = max(1, int(times_ms[i]) - int(times_ms[i - 1])) / 1000.0
        step = max_px_per_s * dt
        delta = float(targets[i]) - out[-1]
        if abs(delta) > step:
            delta = step if delta > 0 else -step
        out.append(out[-1] + delta)
    return out


def _subject_centers(tracks: list[dict]) -> list[list[dict]]:
    return [t.get("keyframes") or [] for t in tracks if isinstance(t, dict)]


def compile_crop_keyframes(tracks: list[dict], *,
                           source_wh: tuple[int, int],
                           target_wh: tuple[int, int],
                           max_px_per_s: float,
                           safe_areas: list[dict] | None = None) -> dict[str, Any]:
    """Compile ROI ``tracks`` into crop keyframes for the ``target_wh`` frame.

    Returns ``{status, strategy, keyframes:[{t_ms,x,y,w,h}], source_wh, target_wh,
    max_px_per_s, changes, reason}``. ``status`` is one of :data:`OK`,
    :data:`NEEDS_MANUAL` (multi-subject conflict), :data:`BLANKING` (crop
    infeasible). ``changes`` always declares FORMAT_ONLY: duration/selection/
    audio/subtitle are never touched."""
    SW, SH = int(source_wh[0]), int(source_wh[1])
    changes = {"duration": False, "selection": False, "audio": False, "subtitle": False}
    cw, ch, feasible = _crop_size((SW, SH), target_wh)
    base = {"status": OK, "strategy": "crop", "keyframes": [],
            "source_wh": [SW, SH], "target_wh": [int(target_wh[0]), int(target_wh[1])],
            "max_px_per_s": float(max_px_per_s), "changes": changes, "reason": ""}

    if not feasible:
        # target aspect unreachable by cropping → pillarbox/letterbox the frame.
        strat = "pillarbox" if (target_wh[0] / target_wh[1]) < (SW / SH) else "letterbox"
        return {**base, "status": BLANKING, "strategy": strat,
                "reason": "target aspect not reachable by cropping — blanking fill"}

    safe_x = _safe_interval(safe_areas, "x", "w", SW, cw)
    safe_y = _safe_interval(safe_areas, "y", "h", SH, ch)
    if safe_x is None or safe_y is None:
        strat = "pillarbox" if (target_wh[0] / target_wh[1]) < (SW / SH) else "letterbox"
        return {**base, "status": BLANKING, "strategy": strat,
                "reason": "a safe area cannot fit inside any crop window — "
                          "blanking/pillarbox fallback"}

    per_track = _subject_centers(tracks)
    if not per_track or all(not kfs for kfs in per_track):
        # no ROI evidence → a centred crop (still a valid, honest default)
        per_track = [[{"t_ms": 0, "cx": 0.5, "cy": 0.5}]]

    # multi-subject conflict: subjects whose centres span more than the crop
    # window cannot share one frame → UNKNOWN / needs_manual (never a guess).
    n = min(len(kfs) for kfs in per_track)
    if len(per_track) > 1 and n > 0:
        for i in range(n):
            xs = [float(kfs[i].get("cx", 0.5)) * SW for kfs in per_track]
            ys = [float(kfs[i].get("cy", 0.5)) * SH for kfs in per_track]
            if (max(xs) - min(xs)) > cw or (max(ys) - min(ys)) > ch:
                return {**base, "status": NEEDS_MANUAL, "keyframes": [],
                        "reason": "multi-subject conflict — subjects do not fit one "
                                  "crop window; needs_manual"}

    # collapse (single subject, or a non-conflicting group's midpoint) to targets
    kf0 = per_track[0]
    times = [int(k.get("t_ms", 0)) for k in kf0]
    tgt_x, tgt_y = [], []
    for i, k in enumerate(kf0):
        cxs = [float(t[i].get("cx", 0.5)) for t in per_track if i < len(t)]
        cys = [float(t[i].get("cy", 0.5)) for t in per_track if i < len(t)]
        cx = sum(cxs) / len(cxs)
        cy = sum(cys) / len(cys)
        tgt_x.append(_clamp(cx * SW - cw / 2, safe_x[0], safe_x[1]))
        tgt_y.append(_clamp(cy * SH - ch / 2, safe_y[0], safe_y[1]))

    xs = _rate_limit(times, tgt_x, max_px_per_s)
    ys = _rate_limit(times, tgt_y, max_px_per_s)
    keyframes = [{"t_ms": times[i], "x": int(round(xs[i])), "y": int(round(ys[i])),
                  "w": cw, "h": ch} for i in range(len(times))]
    return {**base, "keyframes": keyframes}


# ------------------------------------------------------------- format-only proof


def reframe_artifact(compiled: dict[str, Any], *, base_timeline_digest: str | None,
                     source_hash: str, producer: str = "reframe") -> dict[str, Any]:
    """The human-editable crop-keyframe source artifact, shaped for AI_IDE_13C's
    external-framing adoption path. It BINDS the base timeline's semantic digest
    (the FORMAT_ONLY anchor) and is INERT until adopted (``drives_render`` False)."""
    return {
        "producer": producer,
        "source_hash": source_hash,
        "base_timeline_digest": base_timeline_digest,
        "strategy": compiled.get("strategy"),
        "status": compiled.get("status"),
        "keyframes": compiled.get("keyframes", []),
        "target_wh": compiled.get("target_wh"),
        "adopted": False,
        "drives_render": False,
    }


def adopt_reframe_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Adopt the artifact into source (the explicit human step). Only now does it
    drive render — the same boundary 13C draws for any external framing artifact."""
    return {**artifact, "adopted": True, "drives_render": True}


def reframe_preserves_semantics(base_timeline_digest: str | None,
                                artifact: dict[str, Any]) -> list[dict]:
    """The FORMAT_ONLY proof (contract §7): the reframe changes only geometry, so
    the artifact's bound base digest must equal the base master's. Reuses 13C's
    own invariant checker — an empty list means the reframe is format-only."""
    from ..build.delivery import check_format_only_invariant
    return check_format_only_invariant(base_timeline_digest,
                                       artifact.get("base_timeline_digest"))


# --------------------------------------------------------------- executable crop


def _pl_expr(times_s: list[float], vals: list[float]) -> str:
    """A piecewise-linear ffmpeg expression over ``t`` (seconds): hold the first
    value before the first key, interpolate between keys, hold the last after."""
    if len(vals) == 1:
        return f"{vals[0]:.2f}"
    expr = f"{vals[-1]:.2f}"
    for i in range(len(vals) - 1, 0, -1):
        t0, t1, v0, v1 = times_s[i - 1], times_s[i], vals[i - 1], vals[i]
        if t1 <= t0:
            seg = f"{v0:.2f}"
        else:
            slope = (v1 - v0) / (t1 - t0)
            seg = f"({v0:.2f}+({slope:.4f})*(t-{t0:.3f}))"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return f"if(lt(t,{times_s[0]:.3f}),{vals[0]:.2f},{expr})"


def ffmpeg_crop_expr(compiled: dict[str, Any], *, fps: int) -> str:
    """A deterministic ffmpeg ``crop`` filter with time-varying x/y (this is what
    makes the framing EXECUTABLE, where 13C only recorded it). ``fps`` is accepted
    for callers that pin a frame grid; the expression itself is keyed on ``t``."""
    if compiled.get("status") != OK:
        raise ReframeError(f"cannot build a crop expression for status "
                           f"{compiled.get('status')!r} (use the blanking path)")
    kfs = compiled["keyframes"]
    cw, ch = kfs[0]["w"], kfs[0]["h"]
    times_s = [k["t_ms"] / 1000.0 for k in kfs]
    x_expr = _pl_expr(times_s, [float(k["x"]) for k in kfs])
    y_expr = _pl_expr(times_s, [float(k["y"]) for k in kfs])
    return f"crop={cw}:{ch}:{x_expr}:{y_expr}"


_ENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-movflags", "+faststart"]


def execute_reframe(project: Any, shot: str, take: str, compiled: dict[str, Any], *,
                    target_wh: tuple[int, int], fps: int | None = None):
    """Execute the compiled crop into a NEW append-only take (the repair-ops
    precedent — a small self-contained filtergraph, never touching render.py). The
    crop preserves duration and copies audio, so the result is a FORMAT variant."""
    from ..core.container import ProjectError
    from ..media.ffmpeg import default_log, run_ffmpeg
    from ..media.probe import probe
    from ..core.models import TakeSidecar

    if compiled.get("status") != OK:
        raise ReframeError(f"reframe status {compiled.get('status')!r} is not "
                           f"executable — resolve it (needs_manual/blanking) first")
    src = project.get_take(shot, take)
    if src is None or src.media_path is None or not src.media_path.exists():
        raise ProjectError(f"take media missing: {shot}/{take}")
    fps = fps or (project.load_config().fps or 25)
    crop = ffmpeg_crop_expr(compiled, fps=fps)
    TW, TH = int(target_wh[0]), int(target_wh[1])
    vf = f"{crop},scale={TW}:{TH},setsar=1"

    d = Path(tempfile.mkdtemp(prefix="manju_reframe_"))
    out = d / "out.mp4"
    try:
        info = probe(src.media_path)
        args = ["-i", src.media_path, "-vf", vf]
        args += ["-map", "0:a"] if (info and info.has_audio) else ["-an"]
        args += [*_ENC, str(out)]
        run_ffmpeg(args, log=default_log(project.root, "reframe"))
        try:
            out_probe = probe(out)
        except Exception:
            out_probe = None
        sidecar = TakeSidecar(
            provider="reframe",
            spec_hash=src.sidecar.spec_hash,
            spec_version=src.sidecar.spec_version,
            params={"op": "reframe", "source_take": src.name,
                    "target_wh": [TW, TH], "strategy": compiled.get("strategy"),
                    "keyframes": compiled.get("keyframes")},
            probe=out_probe,
        )
        return project.register_take(shot, out, sidecar, move=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
