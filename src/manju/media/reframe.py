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
NEEDS_MANUAL = "needs_manual"   # multi-subject conflict / low-confidence ROI → human
BLANKING = "blanking"           # crop infeasible → blanking / pillarbox fallback
CENTER_CROP_FALLBACK = "center_crop_fallback"   # no ROI → honest centred crop, NOT a
                                                # smart success (addendum ruling 5, M12)

# an ROI track whose declared confidence is below this is not framed by a guess.
_DEFAULT_MIN_ROI_CONFIDENCE = 0.25


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


def _normalize_keyframes(kfs: list[dict]) -> list[dict]:
    """Sort a track's keyframes by ``t_ms`` and drop exact-duplicate timestamps
    (keep first — deterministic), so the series is strictly increasing in time
    (addendum ruling 5, M10/M14). Unsorted input is normalized, never trusted."""
    out: list[dict] = []
    seen: set[int] = set()
    for k in sorted((k for k in kfs if isinstance(k, dict)),
                    key=lambda k: int(k.get("t_ms", 0))):
        t = int(k.get("t_ms", 0))
        if t in seen:
            continue
        seen.add(t)
        out.append(k)
    return out


def _sample_center(kfs: list[dict], t: int) -> tuple[float, float]:
    """The ``(cx, cy)`` of a normalized track at time ``t`` by linear
    interpolation between the bracketing keyframes (held flat before the first /
    after the last). Multi-track alignment samples every track at a SHARED
    timestamp with this — joining by time, never by array index (M11)."""
    if not kfs:
        return 0.5, 0.5
    first, last = kfs[0], kfs[-1]
    if t <= int(first.get("t_ms", 0)):
        return float(first.get("cx", 0.5)), float(first.get("cy", 0.5))
    if t >= int(last.get("t_ms", 0)):
        return float(last.get("cx", 0.5)), float(last.get("cy", 0.5))
    for i in range(1, len(kfs)):
        t0, t1 = int(kfs[i - 1].get("t_ms", 0)), int(kfs[i].get("t_ms", 0))
        if t0 <= t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            cx = float(kfs[i - 1].get("cx", 0.5)) + f * (
                float(kfs[i].get("cx", 0.5)) - float(kfs[i - 1].get("cx", 0.5)))
            cy = float(kfs[i - 1].get("cy", 0.5)) + f * (
                float(kfs[i].get("cy", 0.5)) - float(kfs[i - 1].get("cy", 0.5)))
            return cx, cy
    return float(last.get("cx", 0.5)), float(last.get("cy", 0.5))


def compile_crop_keyframes(tracks: list[dict], *,
                           source_wh: tuple[int, int],
                           target_wh: tuple[int, int],
                           max_px_per_s: float,
                           safe_areas: list[dict] | None = None,
                           min_confidence: float = _DEFAULT_MIN_ROI_CONFIDENCE) -> dict[str, Any]:
    """Compile ROI ``tracks`` into crop keyframes for the ``target_wh`` frame.

    Returns ``{status, strategy, keyframes:[{t_ms,x,y,w,h}], source_wh, target_wh,
    max_px_per_s, changes, reason}``. ``status`` is one of :data:`OK`,
    :data:`NEEDS_MANUAL` (multi-subject conflict OR low-confidence ROI, M13),
    :data:`BLANKING` (crop infeasible), or :data:`CENTER_CROP_FALLBACK` (no ROI —
    an honest centred crop, never a smart success, M12). Per-track keyframes are
    normalized (sorted, deduped, strictly increasing in time, M10/M14) and
    multiple tracks are aligned by TIMESTAMP, never by array index (M11).
    ``changes`` always declares FORMAT_ONLY: duration/selection/audio/subtitle are
    never touched."""
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

    # normalize every track's keyframes (sort by time, dedup) and drop the empties
    norm_tracks = [(t, _normalize_keyframes(t.get("keyframes") or []))
                   for t in tracks if isinstance(t, dict)]
    norm_tracks = [(t, kfs) for (t, kfs) in norm_tracks if kfs]

    # no ROI evidence → an EXPLICIT centred-crop fallback (never a smart success).
    if not norm_tracks:
        x = _clamp(SW / 2 - cw / 2, safe_x[0], safe_x[1])
        y = _clamp(SH / 2 - ch / 2, safe_y[0], safe_y[1])
        return {**base, "status": CENTER_CROP_FALLBACK, "strategy": "center_crop",
                "keyframes": [{"t_ms": 0, "x": int(round(x)), "y": int(round(y)),
                               "w": cw, "h": ch}],
                "reason": "no ROI evidence — explicit centre-crop fallback, not a "
                          "smart crop (resolve manually to adopt)"}

    # low-confidence ROI → not framed by a guess → needs_manual (M13).
    for t, _ in norm_tracks:
        conf = t.get("confidence")
        if conf is not None:
            try:
                if float(conf) < float(min_confidence):
                    return {**base, "status": NEEDS_MANUAL, "keyframes": [],
                            "reason": f"low-confidence ROI (confidence {float(conf):.2f}"
                                      f" < {float(min_confidence):.2f}) — needs_manual"}
            except (TypeError, ValueError):
                pass

    # a unified, strictly-increasing timestamp grid across ALL tracks (M11/M14).
    grid = sorted({int(k.get("t_ms", 0)) for _, kfs in norm_tracks for k in kfs})

    # multi-subject conflict: at ANY shared timestamp the subjects' centres span
    # more than the crop window → cannot share one frame → needs_manual (no guess).
    if len(norm_tracks) > 1:
        for t in grid:
            xs = [_sample_center(kfs, t)[0] * SW for _, kfs in norm_tracks]
            ys = [_sample_center(kfs, t)[1] * SH for _, kfs in norm_tracks]
            if (max(xs) - min(xs)) > cw or (max(ys) - min(ys)) > ch:
                return {**base, "status": NEEDS_MANUAL, "keyframes": [],
                        "reason": "multi-subject conflict — subjects do not fit one "
                                  "crop window; needs_manual"}

    # collapse to per-timestamp targets (the non-conflicting group's midpoint),
    # clamped into the safe interval, then rate-limited over real positive dt.
    tgt_x, tgt_y = [], []
    for t in grid:
        centers = [_sample_center(kfs, t) for _, kfs in norm_tracks]
        cx = sum(c[0] for c in centers) / len(centers)
        cy = sum(c[1] for c in centers) / len(centers)
        tgt_x.append(_clamp(cx * SW - cw / 2, safe_x[0], safe_x[1]))
        tgt_y.append(_clamp(cy * SH - ch / 2, safe_y[0], safe_y[1]))

    xs = _rate_limit(grid, tgt_x, max_px_per_s)
    ys = _rate_limit(grid, tgt_y, max_px_per_s)
    keyframes = [{"t_ms": grid[i], "x": int(round(xs[i])), "y": int(round(ys[i])),
                  "w": cw, "h": ch} for i in range(len(grid))]
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
