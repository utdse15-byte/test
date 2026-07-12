"""§7 render pipeline: normalize (cached) → concat → transitions → overlay/subs
→ audio mix → mux.

The pipeline is deliberately segmented (never one giant filter_complex over the
whole timeline): each selected take becomes a content-addressed segment in
``renders/segments/``, so replacing a single take rebuilds only that segment
(and its transition neighbours) — incremental rendering is a free by-product of
the cache key (§7, §14).

Transition model: a "fade" ``transition_out`` becomes a dip-to-black —
fade-out on the tail of the segment and fade-in on the head of the next. This
is baked into the (cached) per-segment render, with the fade durations folded
into the cache key, so §7 ②'s structural intent (transitions rendered as
cached units, incremental by construction) holds.

Transition model — xfade family, honestly bounded (round-T): the design note
below still stands (a true cross-dissolve needs media HANDLES — extra frames
beyond the cut on both sides). Round-T implements the honest version rather
than pretending: an ``xfade_*`` transition is APPLIED only when real handle
footage exists on BOTH sides of the cut — the outgoing clip's source runs past
its window end (a duration clamp left spare TAIL) and the incoming clip carries
a source in-point (a set_inout left spare HEAD, ``VideoClip.source_in_ms``). It
then renders a content-addressed BOUNDARY segment (tailA+handle ⨯ headB+handle,
xfade + acrossfade) and restructures the concat as [A shortened][boundary][B
shortened] — the overlap consumes HANDLE material, never timeline time, so the
total duration and every downstream audio/caption offset are preserved to the
frame (the incoming clip's window-start frame still lands at the same timeline
instant). When either handle is missing (generative card/kenburns takes are
exactly window-length; imported footage without a clamp/in-point) the transition
DEGRADES to the existing dip-to-black and a build warning names the boundary
("no handles; used dip-to-black"). Applied-vs-degraded is a RENDER-TIME fact
(recorded in the log + a ``.transitions.json`` sidecar); the compiler only ever
records the REQUESTED spec (timeline purity). FUTURE WORK: generative takes are
regenerable and could be re-rendered ``+handle`` longer on demand to earn a real
cross-dissolve — deliberately out of scope this round (correctness first).

Color look (round-T): a deterministic eq/colorbalance chain (``_look_filter``)
read from ``bible/style.yaml``'s ``look:`` mapping is appended to the FINAL/proxy
video chain BEFORE the subtitle burn and folded into the final content key —
changing the look re-renders the final but NOT the (look-free) segments. The
default (preset none / intensity 0) is a strict no-op: no filter, absent from
the content key, byte-identical to before.

Design note — why not xfade/acrossfade seams UNCONDITIONALLY (§7 ② names them):
a true cross-dissolve needs media HANDLES — extra frames beyond the cut point on
both sides. Manju's GENERATED takes are normalized to exactly their timeline
duration, so they have no handles: crossfading them would either shorten the
film by the overlap (desyncing the audio-driven caption/voice timings, §6) or
replay frames around the cut (visible stutter). Dip-to-black is the honest
deterministic transition under exact-duration takes; the round-T xfade path
above applies only where the handles are REAL imported footage.

Design note — media write durability (R3, OPTIMIZATION-ASSESSMENT §3 #2/#3):
media follows the same temp+replace discipline as text truth (core/yamlio.py).
Every ffmpeg artifact bound for a durable location — a cached segment, the
proxy, a final — is encoded to a same-directory temp and swapped in with
``os.replace`` only on success (``ffmpeg.atomic_output``; the dot-prefixed temp
keeps the real extension last so muxer inference works, and never matches the
``final_v*.mp4`` / exact-path lookups). A crash mid-encode used to leave a
truncated file *at* the destination: for segments that debris became a
permanent ``seg_path.exists()`` cache hit embedded in every future final; for
finals it burned a version name on garbage that status/board surface as the
latest 成片; for the proxy it destroyed the previous good copy while the stale
``.key.json`` kept vouching for the truncated bytes. Now the destination only
ever receives complete files, and the key sidecar (FIX-A) is written strictly
AFTER the mp4 is in place. Finals stay append-only: the swap refuses to
overwrite an existing ``final_vN``. Legacy/corrupt cache entries (from before
this discipline, or externally damaged) cannot be trusted just because they
exist, so ``_build_segment`` gates every cache hit with a cheap ffprobe
readability probe — a truncated MP4 has no moov atom, so it is evicted and
rebuilt rather than poisoning every future final.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, hash_file, short_hash
from ..core.models import AudioClip, LookSpec, OverlayClip, Timeline, VideoClip
from ..core.yamlio import atomic_write_text, read_yaml
from .card import find_font
from .ffmpeg import MediaError, atomic_output, default_log, run_ffmpeg
from .normalize import normalize_segment
from .probe import probe_duration_ms

Log = Callable[[str], None] | None

# The segment encode profile (matches normalize._encode_args): every piece that
# lands in the concat list — cached segment, boundary segment, or a build-time
# trim of a segment — is encoded with THESE params so the concat demuxer can
# stitch them with a stream copy (the whole pipeline already relies on
# independently-encoded same-profile segments concatenating cleanly).
_SEG_ENC = [
    "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
]


def _even(n: int) -> int:
    n = max(2, int(n))
    return n if n % 2 == 0 else n - 1


def _escape_filter_path(path: Path | str) -> str:
    """Escape a path for use inside a filtergraph option value (e.g. the ass=
    filter). Chinese/UTF-8 characters need no escaping; only the filtergraph
    metacharacters do. Handles Windows ``C:`` drive colons too (§14)."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _text_overlay_style(ov: OverlayClip, *, out_w: int) -> list[str]:
    """The middle drawtext options (colour/size/position/box) for one TEXT
    overlay, keyed on ``kind``. The title_card/info_card branches reproduce the
    historical options byte-for-byte; branding kinds (badge/cta/text-watermark)
    add their own placement (§7 ④ / round-Q)."""
    kind = ov.kind
    if kind == "info_card":
        # info cards ride away from the upper-third title card (§13-14); the
        # semantic subkind picks the band: chapter high, info centre, role as
        # a lower third. All share this one burn path.
        y_by_subkind = {"chapter": "h*0.16", "info": "h*0.45", "role": "h*0.72"}
        y_expr = y_by_subkind.get(ov.subkind, "h*0.72")
        return [
            "fontcolor=white", f"fontsize={max(12, out_w // 16)}",
            "x=(w-text_w)/2", f"y={y_expr}",
            "box=1", "boxcolor=black@0.45", "boxborderw=24",
        ]
    if kind == "badge":
        # a small rounded 角标 chip in a corner (inset ~3% of frame width)
        inset = max(8, round(out_w * 0.03))
        xy = {
            "tl": (f"{inset}", f"{inset}"),
            "tr": (f"w-text_w-{inset}", f"{inset}"),
            "bl": (f"{inset}", f"h-text_h-{inset}"),
            "br": (f"w-text_w-{inset}", f"h-text_h-{inset}"),
        }
        x_expr, y_expr = xy.get(ov.corner or "tl", xy["tl"])
        return [
            "fontcolor=white", f"fontsize={max(12, out_w // 24)}",
            f"x={x_expr}", f"y={y_expr}",
            "box=1", "boxcolor=black@0.55", "boxborderw=12",
        ]
    if kind == "cta":
        # closing-window call-to-action chip, centred horizontally
        y_expr = "(h-text_h)/2" if ov.position == "center" else "h*0.86"
        return [
            "fontcolor=white", f"fontsize={max(12, out_w // 14)}",
            "x=(w-text_w)/2", f"y={y_expr}",
            "box=1", "boxcolor=black@0.55", "boxborderw=18",
        ]
    if kind == "watermark":
        # large, centred, translucent text watermark. diagonal_tile degrades to
        # centre (round-Q: tiling is deliberately not over-engineered).
        op = _fmt_num(ov.opacity if ov.opacity else 0.35)
        size = ov.size_pct if ov.size_pct else 30.0
        return [
            f"fontcolor=white@{op}", f"fontsize={max(12, round(out_w * size / 100.0))}",
            "x=(w-text_w)/2", "y=(h-text_h)/2",
        ]
    # title_card (and any unknown text kind) → the historical chapter style
    return [
        "fontcolor=white", f"fontsize={max(12, out_w // 10)}",
        "x=(w-text_w)/2", "y=h*0.28",
        "box=1", "boxcolor=black@0.45", "boxborderw=24",
    ]


def _title_card_filters(
    overlays: list[OverlayClip], *, out_w: int, tmp_dir: Path, font: Path | None
) -> list[str]:
    """Chained ``drawtext`` filter(s) for the TEXT overlay layer (§7 ④ / round-Q).

    One drawtext per text overlay — title_card, info_card, badge, cta and the
    text watermark — burned in the final pass exactly like the subtitles (never
    in the per-segment cache). The text is written to a UTF-8 textfile and
    passed via ``textfile=`` — the escaping-free approach from card.py — and
    shown only within its window via the timeline ``enable`` expression. Each
    kind supplies its own placement/box via :func:`_text_overlay_style`;
    ``fontsize`` tracks the frame the text is drawn on (``out_w``) so proxy and
    final look proportionally identical."""
    filters: list[str] = []
    for k, ov in enumerate(overlays):
        txt = tmp_dir / f"title_{k}.txt"
        txt.write_text(ov.text, encoding="utf-8")
        start_s = ov.start_ms / 1000.0
        end_s = (ov.start_ms + ov.duration_ms) / 1000.0
        opts = [
            f"textfile={_escape_filter_path(txt)}",
            *_text_overlay_style(ov, out_w=out_w),
            f"enable='between(t,{start_s:.3f},{end_s:.3f})'",
        ]
        if font is not None:
            opts.append(f"fontfile={_escape_filter_path(font)}")
        filters.append("drawtext=" + ":".join(opts))
    return filters


_TEXT_OVERLAY_KINDS = ("title_card", "info_card", "badge", "cta")


def _is_image_overlay(ov: OverlayClip) -> bool:
    """logo, or a watermark that carries an IMAGE (a human asset), burns via an
    ffmpeg overlay chain with a scaled extra input."""
    return ov.kind == "logo" or (ov.kind == "watermark" and bool(ov.source))


def _is_text_overlay(ov: OverlayClip) -> bool:
    """title_card/info_card/badge/cta, or a TEXT-only watermark, burns via
    drawtext (:func:`_title_card_filters`)."""
    if ov.kind in _TEXT_OVERLAY_KINDS:
        return True
    return ov.kind == "watermark" and not ov.source and bool(ov.text)


def _overlay_image_xy(ov: OverlayClip, margin: int) -> tuple[str, str]:
    """(x, y) overlay-filter expressions for one image overlay. In ``overlay``,
    ``W``/``H`` are the main frame dims and ``w``/``h`` the scaled overlay dims.
    A watermark is centred; a logo sits in its corner with the given inset."""
    if ov.kind == "watermark":  # image watermark (diagonal_tile degrades to centre)
        return "(W-w)/2", "(H-h)/2"
    corner = ov.corner or "tr"
    x = {"tl": f"{margin}", "bl": f"{margin}",
         "tr": f"W-w-{margin}", "br": f"W-w-{margin}"}.get(corner, f"W-w-{margin}")
    y = {"tl": f"{margin}", "tr": f"{margin}",
         "bl": f"H-h-{margin}", "br": f"H-h-{margin}"}.get(corner, f"{margin}")
    return x, y


def _image_overlay_graph(
    overlays: list[OverlayClip], *, project: Project, in_label: str,
    out_w: int, base_idx: int,
) -> tuple[list[str], list[str], str]:
    """Return (extra_image_inputs, filter_statements, final_video_label) that
    overlay each image (logo / image watermark) onto ``in_label`` (round-Q).

    Each image is a NEW ffmpeg ``-i`` input; the caller appends these AFTER the
    audio inputs, and ``base_idx`` is the first image input's index — so the
    audio graph's ``[n:a]`` indexing never shifts. Each input is scaled to
    ``size_pct`` of the frame width (aspect preserved), alpha-scaled by
    ``opacity`` (``format=rgba`` + ``colorchannelmixer``), then overlaid within
    its ``enable`` window at the corner/centre position."""
    inputs: list[str] = []
    stmts: list[str] = []
    cur = in_label
    for k, ov in enumerate(overlays):
        idx = base_idx + k
        inputs += ["-i", str(project.resolve(ov.source))]
        sw = max(2, round(out_w * (ov.size_pct or 12.0) / 100.0))
        scaled = f"[br_img{k}]"
        stmts.append(
            f"[{idx}:v]scale={sw}:-1,format=rgba,"
            f"colorchannelmixer=aa={_fmt_num(ov.opacity)}{scaled}"
        )
        margin = max(0, round(out_w * (ov.margin_pct or 0.0) / 100.0))
        x_expr, y_expr = _overlay_image_xy(ov, margin)
        start_s = ov.start_ms / 1000.0
        end_s = (ov.start_ms + ov.duration_ms) / 1000.0
        out_label = f"[br_ov{k}]"
        stmts.append(
            f"{cur}{scaled}overlay=x={x_expr}:y={y_expr}:"
            f"enable='between(t,{start_s:.3f},{end_s:.3f})'{out_label}"
        )
        cur = out_label
    return inputs, stmts, cur


def _fade_params(clips: list[VideoClip], i: int) -> tuple[int, int]:
    """(fade_in_ms, fade_out_ms) for clip i, each = neighbour transition / 2."""
    fade_out_ms = 0
    tout = clips[i].transition_out
    if tout is not None and tout.type == "fade":
        fade_out_ms = tout.duration_ms // 2
    fade_in_ms = 0
    if i > 0:
        prev = clips[i - 1].transition_out
        if prev is not None and prev.type == "fade":
            fade_in_ms = prev.duration_ms // 2
    return fade_in_ms, fade_out_ms


def _apply_fades(
    src_seg: Path, dest: Path, *, duration_ms: int, fade_in_ms: int, fade_out_ms: int, log: Log
) -> None:
    """Bake dip-to-black fades onto an already-normalized segment."""
    vfilters: list[str] = []
    afilters: list[str] = []
    if fade_in_ms > 0:
        d = fade_in_ms / 1000.0
        vfilters.append(f"fade=t=in:st=0:d={d:.3f}")
        afilters.append(f"afade=t=in:st=0:d={d:.3f}")
    if fade_out_ms > 0:
        st = max(0.0, (duration_ms - fade_out_ms) / 1000.0)
        d = fade_out_ms / 1000.0
        vfilters.append(f"fade=t=out:st={st:.3f}:d={d:.3f}")
        afilters.append(f"afade=t=out:st={st:.3f}:d={d:.3f}")
    vfilters.append("format=yuv420p")
    with atomic_output(dest) as tmp_out:
        run_ffmpeg(
            [
                "-i", str(src_seg),
                "-vf", ",".join(vfilters),
                "-af", ",".join(afilters) if afilters else "anull",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                str(tmp_out),
            ],
            log=log,
        )


def _toolchain_key_component(
    project: Project, config: "ProjectConfig | None" = None,
) -> dict[str, str] | None:
    """S4 (user item 7): the STRICTLY OPT-IN toolchain key component.

    ``None`` — the answer for every project whose ``project.yaml`` never sets
    ``cache_toolchain_keys`` — appends NOTHING at any key site, so a
    not-opted-in project's keys stay byte-identical forever (and no collector
    is even probed). An opted-in project gets a sorted token→fact map over
    EXACTLY its declared tokens (validation already rejected everything that
    cannot change output bytes, so this map can never force a meaningless
    rebuild):

    * ``"ffmpeg"`` → the ``-version`` FIRST line verbatim (honest ``"missing"``
      when the tool is absent — a fact, never a crash);
    * ``"fonts"``  → the drawtext burn-font content hash (honest ``"unknown"``
      when unlocatable).

    Facts come from the process-cached collectors (one probe per process, spy-
    pinned) so computing N keys never re-runs a subprocess. Pass an already-
    loaded ``config`` to skip the project.yaml read (build/graph does)."""
    cfg = config if config is not None else project.load_config()
    tokens = getattr(cfg, "cache_toolchain_keys", None)
    if not tokens:
        return None
    from ..core.toolchain import cached_drawtext_font_hash, cached_tool_version_line

    component: dict[str, str] = {}
    for token in sorted(set(tokens)):
        if token == "ffmpeg":
            component["ffmpeg"] = cached_tool_version_line("ffmpeg")
        elif token == "fonts":
            component["fonts"] = cached_drawtext_font_hash()
    return component


def _segment_cache_key(
    project: Project,
    clip: VideoClip,
    *,
    width: int,
    height: int,
    fps: int,
    target: str,
    fade_in_ms: int,
    fade_out_ms: int,
    rate_key: str | None = None,
    toolchain_key: dict[str, str] | None = None,
) -> str:
    """The one segment-key formula, shared by the segment cache and the
    final content key (FIX-A) so they can never drift apart.

    ``fps`` stays the int nominal rate (byte-identical key INPUTS for every int
    project). ``rate_key`` (R2) is the canonical exact-rate string ("24000/1001")
    for a rational project ONLY — appended so a 1001-family project's segments
    are content-distinct from the int-nominal project's and from each other's
    rates, deterministically. ``None`` (every int project) appends nothing, so
    the produced key is byte-for-byte what it was before R2. ``toolchain_key``
    (S4) is the opt-in sorted token→fact map from
    :func:`_toolchain_key_component` — same drop-when-absent contract: ``None``
    (every not-opted-in project) appends nothing, byte-identical key."""
    src = project.resolve(clip.source)
    parts: list[object] = [
        hash_file(src), width, height, fps, clip.duration_ms, target, fade_in_ms, fade_out_ms
    ]
    # Round-T per-shot source audio + source in-point: each folds into the key
    # ONLY when non-default, so an untouched clip keeps a byte-identical key
    # (cached segment reused) while a changed one re-normalizes exactly one
    # segment / reads a different source window.
    if clip.source_mute:
        parts.append("srcmute")
    elif clip.source_gain_db:
        parts.append(f"srcgain:{clip.source_gain_db}")
    in_ms = getattr(clip, "source_in_ms", 0)
    if in_ms:
        parts.append(("source_in_ms", in_ms))
    # R2 rational edit rate: distinct + deterministic key for a 1001-family
    # project; absent (None) for every int project → byte-identical key.
    if rate_key is not None:
        parts.append(("rate", rate_key))
    # S4 opt-in toolchain facts: distinct + deterministic key for an opted-in
    # project; absent (None) for every other project → byte-identical key.
    if toolchain_key is not None:
        parts.append(("toolchain", toolchain_key))
    return cache_key(*parts)


def _build_segment(
    project: Project,
    clip: VideoClip,
    *,
    width: int,
    height: int,
    fps: int,
    target: str,
    fade_in_ms: int,
    fade_out_ms: int,
    log: Log,
    rate_key: str | None = None,
    rate_arg: str | int | None = None,
    toolchain_key: dict[str, str] | None = None,
) -> Path:
    """Return the cached segment path for one clip, rendering it if absent.

    Cache key = source content hash + normalization params + target + fade
    params, so a changed take (or a changed transition) rebuilds only what it
    must, and a (readable) cache hit is left byte-for-byte untouched (mtime
    preserved). Writes into the cache are atomic (see module design note), so
    an unreadable hit can only be legacy pre-durability debris or external
    damage — it is evicted and rebuilt rather than poisoning every future final.

    ``rate_key``/``rate_arg`` (R2) carry a rational edit rate: ``rate_key`` (the
    "24000/1001" key string) makes a 1001-family project's segment content-
    distinct, and ``rate_arg`` is the ffmpeg frame-rate token normalize encodes
    at (the exact ``num/den`` — ffmpeg's ``fps``/``-framerate`` take fractions).
    Both default to today's int behaviour (None → the int ``fps``), so an int
    project's key AND ffmpeg command line are byte-identical. ``toolchain_key``
    (S4) is the opt-in token→fact map; None (every not-opted-in project) keys
    byte-identically."""
    src = project.resolve(clip.source)
    if not src.exists():
        raise MediaError(f"take source not found for {clip.shot}/{clip.take}: {clip.source}")
    key = _segment_cache_key(
        project, clip, width=width, height=height, fps=fps, target=target,
        fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms, rate_key=rate_key,
        toolchain_key=toolchain_key,
    )
    enc_fps = rate_arg if rate_arg is not None else fps
    seg_path = project.segments_dir / f"{short_hash(key)}.mp4"
    if seg_path.exists():
        # Integrity gate before reuse: one cheap ffprobe (headers only). A
        # truncated encode has no moov atom → no readable duration → evict.
        if probe_duration_ms(seg_path) is not None:
            return seg_path
        if log is not None:
            log(
                f"segment cache: {seg_path.name} is unreadable "
                f"(truncated by an earlier crash?) — evicting and rebuilding"
            )
        seg_path.unlink(missing_ok=True)
    seg_path.parent.mkdir(parents=True, exist_ok=True)

    in_ms = getattr(clip, "source_in_ms", 0)
    if fade_in_ms == 0 and fade_out_ms == 0:
        normalize_segment(
            src, seg_path, width=width, height=height, fps=enc_fps,
            duration_ms=clip.duration_ms,
            source_gain_db=clip.source_gain_db, source_mute=clip.source_mute,
            source_in_ms=in_ms,
            log=log,
        )
        return seg_path

    # Normalize to a temp, then bake fades into the cached segment.
    with tempfile.TemporaryDirectory(dir=seg_path.parent) as tmp:
        base = Path(tmp) / "base.mp4"
        normalize_segment(
            src, base, width=width, height=height, fps=enc_fps,
            duration_ms=clip.duration_ms,
            source_gain_db=clip.source_gain_db, source_mute=clip.source_mute,
            source_in_ms=in_ms,
            log=log,
        )
        _apply_fades(
            base, seg_path, duration_ms=clip.duration_ms,
            fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms, log=log,
        )
    return seg_path


# ============================================================ color look (round-T)


def _look_num(x: float) -> str:
    """Deterministic compact number for a look filter value (fixed precision,
    trailing zeros stripped): 0.30*0.5 -> '0.15', 1.0 -> '1', 0.0 -> '0'. The
    formatting is stable run-to-run, so a look's filtergraph is content-keyable."""
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _look_warm(i: float) -> str:
    # Push reds up / blues down across shadows→highlights (warmer temperature),
    # a touch more saturation. i scales every shift from 0 (neutral) to full.
    return (
        f"colorbalance=rs={_look_num(0.30 * i)}:bs={_look_num(-0.30 * i)}"
        f":rm={_look_num(0.15 * i)}:bm={_look_num(-0.15 * i)}"
        f":rh={_look_num(0.10 * i)}:bh={_look_num(-0.10 * i)},"
        f"eq=saturation={_look_num(1 + 0.10 * i)}"
    )


def _look_cool(i: float) -> str:
    # Mirror of warm: reds down / blues up (cooler), slight saturation lift.
    return (
        f"colorbalance=rs={_look_num(-0.30 * i)}:bs={_look_num(0.30 * i)}"
        f":rm={_look_num(-0.12 * i)}:bm={_look_num(0.12 * i)}"
        f":rh={_look_num(-0.08 * i)}:bh={_look_num(0.08 * i)},"
        f"eq=saturation={_look_num(1 + 0.05 * i)}"
    )


def _look_bw(i: float) -> str:
    # Desaturate toward monochrome (saturation 1→0) with a hair more contrast.
    return f"eq=saturation={_look_num(1 - i)}:contrast={_look_num(1 + 0.10 * i)}"


def _look_film(i: float) -> str:
    # Filmic: gentle contrast + slight desaturation + lifted gamma, teal
    # shadows / warm highlights via colorbalance.
    return (
        f"eq=contrast={_look_num(1 + 0.15 * i)}:saturation={_look_num(1 - 0.15 * i)}"
        f":gamma={_look_num(1 - 0.05 * i)},"
        f"colorbalance=rs={_look_num(-0.10 * i)}:bs={_look_num(0.10 * i)}"
        f":rh={_look_num(0.12 * i)}:bh={_look_num(-0.12 * i)}"
    )


def _look_vivid(i: float) -> str:
    # Punchy: strong saturation + contrast + a whisker of brightness.
    return (
        f"eq=saturation={_look_num(1 + 0.50 * i)}:contrast={_look_num(1 + 0.20 * i)}"
        f":brightness={_look_num(0.02 * i)}"
    )


# Each preset is a fixed filter chain parameterised by intensity i∈(0,1]; at
# i→0 every knob returns to its neutral default, so intensity 0 == preset none.
_LOOK_BUILDERS: dict[str, Callable[[float], str]] = {
    "warm": _look_warm,
    "cool": _look_cool,
    "bw": _look_bw,
    "film": _look_film,
    "vivid": _look_vivid,
}


def load_look(project: Project) -> LookSpec:
    """THE reading contract for the color look (round-T): ``bible/style.yaml``'s
    top-level ``look:`` mapping → :class:`LookSpec`. A malformed/absent look
    degrades to none (a build is never crashed by a style typo, matching the
    caption-style reader). This is the single place style.yaml's look is read."""
    try:
        data = read_yaml(project.root / "bible" / "style.yaml")
    except Exception:
        return LookSpec()
    look = data.get("look") if isinstance(data, dict) else None
    if not isinstance(look, dict):
        return LookSpec()
    try:
        return LookSpec.model_validate(look)
    except Exception:
        return LookSpec()


def _look_filter(look: LookSpec) -> str:
    """The eq/colorbalance chain for a look, or "" for a no-op look (preset
    none or intensity 0). Appended to the final/proxy vchain before subtitles."""
    if not look.active:
        return ""
    builder = _LOOK_BUILDERS.get(look.preset)
    return builder(look.intensity) if builder else ""


# ==================================================== handle-aware transitions


# ffmpeg xfade transition name for each curated ``xfade_*`` spec type.
_XFADE_TRANSITIONS = {
    "xfade_fade": "fade",
    "xfade_slideleft": "slideleft",
    "xfade_slideright": "slideright",
    "xfade_wipeleft": "wipeleft",
    "xfade_circleopen": "circleopen",
}


def _n_frames(ms: float, fps: int) -> int:
    return max(0, round(ms * fps / 1000.0))


def _frames_ms(frames: int, fps: int) -> int:
    return round(frames * 1000.0 / fps)


@dataclass
class _Boundary:
    """A resolved interior boundary between clip ``i`` and clip ``i+1`` that
    carries an ``xfade_*`` request. ``applied`` xfades own a boundary segment
    and shorten their neighbours; degraded ones fell back to dip-to-black."""

    i: int
    requested: str
    duration_ms: int
    applied: bool
    half_ms: int = 0     # frame-aligned handle length taken from each side
    xfade: str = ""      # ffmpeg transition name (applied only)
    reason: str = ""     # why it degraded (names the boundary in the warning)


def _probe_source_ms(project: Project, clip: VideoClip, cache: dict[str, int | None]) -> int | None:
    src = project.resolve(clip.source)
    key = str(src)
    if key not in cache:
        cache[key] = probe_duration_ms(src) if src.exists() else None
    return cache[key]


def _xfade_half_ms(duration_ms: int, fps: int) -> int:
    """Handle length taken from EACH side = half the transition, snapped to a
    whole frame so the [A-half][boundary=2·half][B-half] restructuring stays
    frame-exact (the sum of frame counts is invariant)."""
    return _frames_ms(max(1, _n_frames(duration_ms / 2.0, fps)), fps)


def _plan_transitions(
    project: Project, clips: list[VideoClip], fps: int, *, log: Log = None
) -> tuple[list[_Boundary], list[tuple[int, int]]]:
    """Resolve every interior boundary and the per-segment fade params.

    Returns ``(xfade_boundaries, seg_fades)`` where ``seg_fades[i]`` is the
    ``(fade_in_ms, fade_out_ms)`` baked into clip ``i``'s cached segment. The
    baseline is exactly :func:`_fade_params` (so a timeline with only fade/cut
    transitions is byte-identical); the ONLY departures are ``xfade_*`` types:
    an applied xfade leaves both edges fade-free (the boundary owns the
    dissolve), a degraded one bakes dip-to-black halves like a plain ``fade``.

    Deterministic and shared by the render AND :func:`final_content_key`, so the
    segment cache keys can never drift from what is actually rendered.
    """
    seg_fades = [list(_fade_params(clips, i)) for i in range(len(clips))]
    boundaries: list[_Boundary] = []
    src_cache: dict[str, int | None] = {}
    frame_ms = 1000.0 / fps if fps else 0.0

    for i in range(len(clips) - 1):
        tout = clips[i].transition_out
        if tout is None or tout.type not in _XFADE_TRANSITIONS:
            continue  # None / fade / cut / unknown → _fade_params already correct
        a, b = clips[i], clips[i + 1]
        half_ms = _xfade_half_ms(tout.duration_ms, fps)
        reason = ""
        # A clip must survive giving up a handle on BOTH edges (adjacent xfades),
        # so require 2·half of room; then both handles must be REAL footage.
        if a.duration_ms - 2 * half_ms < frame_ms or b.duration_ms - 2 * half_ms < frame_ms:
            reason = f"clip too short for a {tout.duration_ms}ms cross-dissolve"
        else:
            a_src = _probe_source_ms(project, a, src_cache)
            b_src = _probe_source_ms(project, b, src_cache)
            # Spare TAIL = whatever the source file has PAST the material this
            # clip lays down. The clip reads [source_in_ms, source_in_ms+duration)
            # and the boundary compositor lifts the handle from source_in_ms +
            # duration_ms - half onward, so the tail available is measured from
            # exactly that read edge — which, for a virtually-trimmed take (where
            # duration_ms tracks the window out−in), equals file_duration −
            # source_out_ms, the footage kept beyond the window's out-point. Spare
            # HEAD = source_in_ms, the footage kept before the window's in-point.
            # A virtual trim leaves BOTH >0, so it earns a real cross-dissolve; a
            # re-encoded trim (or a card exactly window-length) leaves both 0.
            a_tail = None if a_src is None else a_src - (a.source_in_ms + a.duration_ms)
            b_head = b.source_in_ms  # material before the incoming window start
            if a_src is None or a_tail < half_ms:
                reason = "no tail handle on the outgoing clip"
            elif b_head < half_ms:
                reason = "no head handle on the incoming clip"

        if not reason:
            boundaries.append(_Boundary(
                i=i, requested=tout.type, duration_ms=tout.duration_ms,
                applied=True, half_ms=half_ms, xfade=_XFADE_TRANSITIONS[tout.type],
            ))
        else:
            half = tout.duration_ms // 2  # dip-to-black, byte-identical to a "fade"
            seg_fades[i][1] = half
            seg_fades[i + 1][0] = half
            boundaries.append(_Boundary(
                i=i, requested=tout.type, duration_ms=tout.duration_ms,
                applied=False, reason=reason,
            ))
    return boundaries, [tuple(f) for f in seg_fades]


def _boundary_cache_key(
    project: Project, a: VideoClip, b: VideoClip, boundary: _Boundary,
    *, width: int, height: int, fps: int, target: str, rate_key: str | None = None,
    toolchain_key: dict[str, str] | None = None,
) -> str:
    """Content identity of a boundary segment: both neighbour segment keys
    (which already fold source hashes, dims, fps, durations, in-points and — R2 —
    the rational rate via ``rate_key``) plus the transition shape. Changing the
    transition TYPE re-keys only the boundary (the neighbours' fade-free segment
    keys are unchanged), so the boundary re-renders while the segment cache is
    reused. ``rate_key`` None (every int project) keeps this key byte-identical.
    ``toolchain_key`` (S4) rides the two neighbour segment keys exactly like
    ``rate_key`` does — None (every not-opted-in project) is byte-identical."""
    ka = _segment_cache_key(project, a, width=width, height=height, fps=fps,
                            target=target, fade_in_ms=0, fade_out_ms=0, rate_key=rate_key,
                            toolchain_key=toolchain_key)
    kb = _segment_cache_key(project, b, width=width, height=height, fps=fps,
                            target=target, fade_in_ms=0, fade_out_ms=0, rate_key=rate_key,
                            toolchain_key=toolchain_key)
    return cache_key("xfade-boundary", ka, kb, boundary.xfade,
                     boundary.duration_ms, boundary.half_ms, width, height, fps, target)


def _build_boundary_segment(
    project: Project, a: VideoClip, b: VideoClip, boundary: _Boundary,
    *, width: int, height: int, fps: int, target: str, log: Log,
    rate_key: str | None = None, rate_arg: str | int | None = None,
    toolchain_key: dict[str, str] | None = None,
) -> Path:
    """Render (or reuse) the content-addressed boundary segment for an applied
    xfade: the outgoing tail (last half real + half tail-handle) cross-dissolved
    with the incoming head (half head-handle + first half real), video via
    ``xfade`` and audio via ``acrossfade`` over the same window. Length = 2·half,
    which exactly replaces the ``half`` trimmed off each neighbour.

    ``rate_key``/``rate_arg`` (R2): the rational rate keys the boundary distinct
    and is the ffmpeg frame-rate token the layers normalize to + the ``fps=``
    node runs at. Both default to today's int behaviour (byte-identical)."""
    enc_fps = rate_arg if rate_arg is not None else fps
    key = _boundary_cache_key(project, a, b, boundary,
                              width=width, height=height, fps=fps, target=target,
                              rate_key=rate_key, toolchain_key=toolchain_key)
    seg_path = project.segments_dir / f"xfade_{short_hash(key)}.mp4"
    if seg_path.exists():
        if probe_duration_ms(seg_path) is not None:
            return seg_path
        if log is not None:
            log(f"boundary cache: {seg_path.name} unreadable — evicting and rebuilding")
        seg_path.unlink(missing_ok=True)
    seg_path.parent.mkdir(parents=True, exist_ok=True)

    half = boundary.half_ms
    layer_ms = 2 * half
    a_src = project.resolve(a.source)
    b_src = project.resolve(b.source)
    with tempfile.TemporaryDirectory(dir=seg_path.parent) as tmp:
        tmpd = Path(tmp)
        a_layer = tmpd / "a.mp4"
        b_layer = tmpd / "b.mp4"
        # Outgoing: start half BEFORE the window end → half real + half tail-handle.
        normalize_segment(a_src, a_layer, width=width, height=height, fps=enc_fps,
                          duration_ms=layer_ms,
                          source_in_ms=a.source_in_ms + a.duration_ms - half, log=log)
        # Incoming: start half BEFORE the window start → half head-handle + half real.
        normalize_segment(b_src, b_layer, width=width, height=height, fps=enc_fps,
                          duration_ms=layer_ms, source_in_ms=b.source_in_ms - half, log=log)
        d = layer_ms / 1000.0
        fc = (
            f"[0:v][1:v]xfade=transition={boundary.xfade}:duration={d:.3f}:offset=0,"
            f"fps={enc_fps},format=yuv420p[v];"
            f"[0:a][1:a]acrossfade=d={d:.3f}[a]"
        )
        with atomic_output(seg_path) as tmp_out:
            run_ffmpeg(
                ["-i", str(a_layer), "-i", str(b_layer), "-filter_complex", fc,
                 "-map", "[v]", "-map", "[a]", *_SEG_ENC, str(tmp_out)],
                log=log,
            )
    return seg_path


def _trim_segment(
    src: Path, dest: Path, *, start_ms: int, dur_ms: int, fps: int, log: Log,
    rate_arg: str | int | None = None,
) -> None:
    """Re-encode ``src`` to ``[start_ms, start_ms+dur_ms)`` with the segment
    profile so the result concats (stream-copy) with the cached segments. Used
    to shorten an applied xfade's neighbours by the handle they lent the
    boundary. Output seeking (``-ss`` after ``-i``) keeps the cut frame-accurate.

    ``rate_arg`` (R2) is the ffmpeg frame-rate token the ``fps=`` node runs at
    (the exact ``num/den`` for a rational project); None → the int ``fps``
    (byte-identical)."""
    enc_fps = rate_arg if rate_arg is not None else fps
    run_ffmpeg(
        ["-i", str(src), "-ss", f"{start_ms / 1000.0:.3f}", "-t", f"{dur_ms / 1000.0:.3f}",
         "-vf", f"fps={enc_fps},format=yuv420p", *_SEG_ENC, str(dest)],
        log=log,
    )


def _assemble_video_pieces(
    project: Project, clips: list[VideoClip], segments: list[Path],
    boundaries: list[_Boundary], *, width: int, height: int, fps: int,
    target: str, tmp_dir: Path, log: Log,
    rate_key: str | None = None, rate_arg: str | int | None = None,
    toolchain_key: dict[str, str] | None = None,
) -> list[Path]:
    """The ordered concat pieces once applied xfades restructure the track:
    each clip contributes its cached segment (untouched when it lends no handle)
    or a shortened trim, and every applied boundary segment is spliced in at its
    cut. Total frame count is invariant: each boundary is 2·half and removes
    exactly half from each neighbour. ``rate_key``/``rate_arg`` (R2) flow into
    the trims + boundary segments so a rational project's restructured pieces
    carry its exact rate; None (int) is byte-identical."""
    applied = {b.i: b for b in boundaries if b.applied}
    head_trim = {b.i + 1: b.half_ms for b in applied.values()}
    tail_trim = {b.i: b.half_ms for b in applied.values()}
    pieces: list[Path] = []
    for idx, clip in enumerate(clips):
        ht = head_trim.get(idx, 0)
        tt = tail_trim.get(idx, 0)
        if ht == 0 and tt == 0:
            pieces.append(segments[idx])  # untouched → the cached segment as-is
        else:
            piece = tmp_dir / f"piece_{idx:04d}.mp4"
            _trim_segment(segments[idx], piece, start_ms=ht,
                          dur_ms=clip.duration_ms - ht - tt, fps=fps, log=log,
                          rate_arg=rate_arg)
            pieces.append(piece)
        b = applied.get(idx)
        if b is not None:
            pieces.append(_build_boundary_segment(
                project, clips[idx], clips[idx + 1], b,
                width=width, height=height, fps=fps, target=target, log=log,
                rate_key=rate_key, rate_arg=rate_arg, toolchain_key=toolchain_key,
            ))
    return pieces


def _write_transitions_sidecar(media_path: Path, report: list[dict]) -> None:
    """Round-T: record the render-time transition facts (applied vs degraded,
    per boundary) next to a fresh final. Derived metadata — NOT part of the
    content key, written after the mp4/key sidecar like the timeline snapshot."""
    atomic_write_text(
        media_path.with_suffix(".transitions.json"),
        json.dumps({"transitions": report}, ensure_ascii=False, indent=2) + "\n",
    )


def _concat_line(path: Path) -> str:
    escaped = str(path).replace("'", r"'\''")
    return f"file '{escaped}'\n"


def _fmt_num(x: float | int) -> str:
    """Render a filter-graph number compactly: a whole-valued float loses its
    trailing ``.0`` so a float-typed default (``duck_ratio=8.0``) matches the
    historical hardcoded literal (``ratio=8``) byte-for-byte."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _bed_seek_head(clip: AudioClip) -> list[str]:
    """In-point filters for a music/ambient bed (round-T ``start_offset_ms``):
    trim the head of the SOURCE and rebase timestamps to zero so the bed begins
    at the seeked point (``atrim=start`` then ``asetpts``). Empty when the
    in-point is 0 — a default bed keeps a byte-identical filtergraph."""
    if clip.start_offset_ms > 0:
        off = clip.start_offset_ms / 1000.0
        return [f"atrim=start={off:.3f}", "asetpts=PTS-STARTPTS"]
    return []


def _bed_fade_in(clip: AudioClip) -> list[str]:
    """Fade-in filter for a bed (round-T ``fade_in_ms``): ramp up from silence
    over the clip's opening. Empty when 0 (byte-stable default)."""
    if clip.fade_in_ms > 0:
        return [f"afade=t=in:st=0:d={clip.fade_in_ms / 1000.0:.3f}"]
    return []


def _duck_filter(clip: AudioClip) -> str:
    """The sidechaincompress statement for one ducked clip, rendered from the
    clip's own knobs. Defaults (0.05 / 8.0 / 5 / 250) reproduce exactly the
    filtergraph the constants produced before these knobs existed."""
    return (
        f"sidechaincompress=threshold={_fmt_num(clip.duck_threshold)}"
        f":ratio={_fmt_num(clip.duck_ratio)}"
        f":attack={clip.duck_attack_ms}:release={clip.duck_release_ms}"
    )


def _build_audio_graph(
    timeline: Timeline, project: Project, *, target: str, total_s: float
) -> tuple[list[str], list[str], str]:
    """Return (extra_input_args, filter_statements, audio_output_label).

    Audio bus starts from the concatenated segment audio ([0:a]), mixes in each
    voice clip (gain + adelay to its start), each SFX clip (gain + adelay, never
    ducked), each music clip and each ambient bed (gain, trim/loop to the
    timeline length, tail fade, optional sidechain ducking keyed by the voice
    bus). Ducking is generalized over music AND ambient: the voice bus is split
    into one key per ducked clip (music + ambient), so an ambient room-tone bed
    ducks under speech exactly like BGM (§7 ⑤). SFX are transient hits that
    neither duck nor key ducking. loudnorm is applied only for the "final"
    target (skipped for proxy).
    """
    voice_clips = list(timeline.tracks.voice)
    sfx_clips = list(timeline.tracks.sfx)
    music_clips = list(timeline.tracks.music)
    ambient_clips = list(timeline.tracks.ambient)

    inputs: list[str] = []
    stmts: list[str] = []
    mix_labels: list[str] = []
    # One running input index: input 0 is the concatenated video's audio; every
    # "-i" (or "-stream_loop … -i") we append below takes the next index. A
    # single counter keeps sfx/ambient additions from drifting the indices.
    next_idx = 1

    stmts.append("[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[base]")
    mix_labels.append("[base]")

    # ---- voices ---------------------------------------------------------
    voice_labels: list[str] = []
    for k, clip in enumerate(voice_clips):
        idx = next_idx
        next_idx += 1
        inputs += ["-i", str(project.resolve(clip.source))]
        delay = max(0, clip.start_ms)
        chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if clip.duration_ms is not None:
            # Bound the voice to its clip length so an over-long take cannot push
            # the film past timeline.duration_ms (picture is the master, §6).
            chain += ["apad", f"atrim=0:{clip.duration_ms / 1000.0:.3f}"]
        if clip.gain_db:
            chain.append(f"volume={clip.gain_db}dB")  # audio policy: voice level
        if delay > 0:
            chain.append(f"adelay={delay}:all=1")
        lbl = f"[voice{k}]"
        stmts.append(f"[{idx}:a]{','.join(chain)}{lbl}")
        voice_labels.append(lbl)

    # Ducking is keyed by the voice bus and generalized over music + ambient:
    # one split key per ducked clip on EITHER track (§7 ⑤).
    ducked_music = [c for c in music_clips if c.ducking] if voice_labels else []
    ducked_ambient = [c for c in ambient_clips if c.ducking] if voice_labels else []
    voice_for_mix: str | None = None
    ducking_keys: list[str] = []
    if voice_labels:
        if len(voice_labels) == 1:
            voicebus = voice_labels[0]
        else:
            voicebus = "[voicebus]"
            stmts.append("".join(voice_labels) + f"amix=inputs={len(voice_labels)}:normalize=0[voicebus]")
        n_keys = len(ducked_music) + len(ducked_ambient)
        if n_keys == 0:
            voice_for_mix = voicebus
        else:
            outs = [f"[vb{i}]" for i in range(1 + n_keys)]
            stmts.append(f"{voicebus}asplit={1 + n_keys}" + "".join(outs))
            voice_for_mix = outs[0]
            ducking_keys = outs[1:]
        mix_labels.append(voice_for_mix)

    key_iter = iter(ducking_keys)

    # ---- sfx (transient hits: gain + delay, no ducking) -----------------
    for k, clip in enumerate(sfx_clips):
        idx = next_idx
        next_idx += 1
        inputs += ["-i", str(project.resolve(clip.source))]
        chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if clip.gain_db:
            chain.append(f"volume={clip.gain_db}dB")
        if clip.start_ms > 0:
            chain.append(f"adelay={clip.start_ms}:all=1")
        out = f"[sfx{k}]"
        stmts.append(f"[{idx}:a]{','.join(chain)}{out}")
        mix_labels.append(out)

    # ---- music ----------------------------------------------------------
    for k, clip in enumerate(music_clips):
        idx = next_idx
        next_idx += 1
        inputs += ["-i", str(project.resolve(clip.source))]
        chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        chain += _bed_seek_head(clip)  # round-T in-point (byte-stable when 0)
        if clip.start_ms > 0:
            chain.append(f"adelay={clip.start_ms}:all=1")
        if clip.gain_db:
            chain.append(f"volume={clip.gain_db}dB")
        chain += _bed_fade_in(clip)  # round-T fade-in (byte-stable when 0)
        chain.append("apad")
        chain.append(f"atrim=0:{total_s:.3f}")
        if clip.fade_out_ms > 0:
            fo = clip.fade_out_ms / 1000.0
            st = max(0.0, total_s - fo)
            chain.append(f"afade=t=out:st={st:.3f}:d={fo:.3f}")
        pre = f"[mus{k}pre]"
        stmts.append(f"[{idx}:a]{','.join(chain)}{pre}")
        if clip.ducking and voice_for_mix is not None:
            key = next(key_iter)
            out = f"[mus{k}]"
            stmts.append(f"{pre}{key}{_duck_filter(clip)}{out}")
            mix_labels.append(out)
        else:
            mix_labels.append(pre)

    # ---- ambient bed (looped room tone, ducks like music when asked) ----
    for k, clip in enumerate(ambient_clips):
        idx = next_idx
        next_idx += 1
        if clip.loop:
            # -stream_loop -1 loops the file endlessly at the input; the atrim
            # to total_s below terminates it, so the graph always finishes.
            inputs += ["-stream_loop", "-1", "-i", str(project.resolve(clip.source))]
        else:
            inputs += ["-i", str(project.resolve(clip.source))]
        chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        chain += _bed_seek_head(clip)  # round-T in-point (byte-stable when 0)
        if clip.start_ms > 0:
            chain.append(f"adelay={clip.start_ms}:all=1")
        if clip.gain_db:
            chain.append(f"volume={clip.gain_db}dB")
        chain += _bed_fade_in(clip)  # round-T fade-in (byte-stable when 0)
        chain.append("apad")
        chain.append(f"atrim=0:{total_s:.3f}")
        if clip.fade_out_ms > 0:
            fo = clip.fade_out_ms / 1000.0
            st = max(0.0, total_s - fo)
            chain.append(f"afade=t=out:st={st:.3f}:d={fo:.3f}")
        pre = f"[amb{k}pre]"
        stmts.append(f"[{idx}:a]{','.join(chain)}{pre}")
        if clip.ducking and voice_for_mix is not None:
            key = next(key_iter)
            out = f"[amb{k}]"
            stmts.append(f"{pre}{key}{_duck_filter(clip)}{out}")
            mix_labels.append(out)
        else:
            mix_labels.append(pre)

    # ---- mix + loudnorm -------------------------------------------------
    if len(mix_labels) == 1:
        mixed = mix_labels[0]
    else:
        mixed = "[amixed]"
        stmts.append(
            "".join(mix_labels)
            + f"amix=inputs={len(mix_labels)}:normalize=0:dropout_transition=0[amixed]"
        )

    if target == "final":
        stmts.append(f"{mixed}loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
        aout = "[aout]"
    else:
        aout = mixed  # proxy skips loudnorm for speed
    return inputs, stmts, aout


def _enc_params(target: str) -> list[str]:
    if target == "final":
        return ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]
    return ["-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-ar", "48000"]


def _audio_input_hashes(project: Project, timeline: Timeline) -> list[str]:
    """Content hashes of every audio input, in track order. A missing file
    hashes as a marker instead of raising — the render itself will surface
    the real error; key computation must never be the thing that crashes."""
    hashes: list[str] = []
    for clip in [
        *timeline.tracks.voice,
        *timeline.tracks.music,
        *timeline.tracks.sfx,
        *timeline.tracks.ambient,
    ]:
        src = project.resolve(clip.source)
        hashes.append(hash_file(src) if src.exists() else f"missing:{clip.source}")
    return hashes


def _overlay_image_hashes(project: Project, timeline: Timeline) -> list[str]:
    """Content hashes of every burned image overlay (logo / image watermark),
    in overlay-track order — round-Q's visual analogue of _audio_input_hashes,
    so editing a logo/watermark PNG's bytes re-keys the final. A missing file
    hashes as a marker (never raises: key computation must not crash)."""
    hashes: list[str] = []
    for ov in timeline.tracks.overlay:
        if _is_image_overlay(ov):
            src = project.resolve(ov.source)
            hashes.append(hash_file(src) if src.exists() else f"missing:{ov.source}")
    return hashes


def _final_key_payload(
    project: Project,
    timeline: Timeline,
    *,
    ass_file: Path | None,
    target: str,
) -> dict:
    """The EXACT dict :func:`final_content_key` hashes, extracted verbatim.

    Kept as its own function so the key sidecar (WP3 e2) can record the very
    breakdown that produced the key — the real key input, never a parallel
    recomputation. CRITICAL: this must stay byte-identical to the historical
    inline payload; the content-key value of any existing project must not
    change (the key is derived as ``cache_key(payload)`` below, unchanged)."""
    width, height, fps = timeline.width, timeline.height, timeline.fps
    clips = list(timeline.tracks.video)
    # R2: a rational (1001-family) project folds its exact rate string into every
    # segment key so its content key is distinct from the int-nominal project's;
    # None for an int project → byte-identical key INPUTS. Read via the timeline's
    # frame_rate resolver (never the raw rational field — R1 surface pin). fps
    # stays the int nominal rate everywhere it already appears in the key.
    _rate = timeline.frame_rate
    rate_key = None if _rate.exact_int is not None else str(_rate)
    # S4: the opt-in toolchain component, computed ONCE per payload from the
    # project config (facts are process-cached — no per-key probes). None for
    # every not-opted-in project → nothing changes anywhere below.
    toolchain_key = _toolchain_key_component(project)
    # Round-T: the segment fade params come from the shared boundary plan so a
    # degraded xfade's baked dip-to-black is reflected here exactly as rendered.
    # For a timeline with only fade/cut transitions the plan == _fade_params, so
    # the seg keys (and this content key) are byte-identical to before.
    _boundaries, seg_fades = _plan_transitions(project, clips, fps, log=None)
    seg_keys = [
        _segment_cache_key(
            project, clip, width=width, height=height, fps=fps, target=target,
            fade_in_ms=seg_fades[i][0], fade_out_ms=seg_fades[i][1], rate_key=rate_key,
            toolchain_key=toolchain_key,
        )
        if project.resolve(clip.source).exists()
        else f"missing:{clip.source}"
        for i, clip in enumerate(clips)
    ]
    # Round-T: strip a default (0) source in-point from the hashed timeline so a
    # project that never trims a clip keeps a byte-identical content key (the
    # whole-file source hash inside each seg key already covers the bytes).
    tl_payload = timeline.model_dump(exclude={"meta"})  # meta is provenance, not content
    _int_rate = rate_key is None  # a whole-number edit rate (every int project)
    for vc in tl_payload.get("tracks", {}).get("video", []):
        if isinstance(vc, dict):
            if vc.get("source_in_ms", 0) == 0:
                vc.pop("source_in_ms", None)
            # source-audio rides the segment keys (round-T): always excluded
            vc.pop("source_gain_db", None)
            vc.pop("source_mute", None)
            # R2: duration_frames is meaningful ONLY on the rational grid. A
            # compiled int project never carries it (dropped), and a HAND-EDITED
            # int timeline that carries a stray value must have it IGNORED — strip
            # it so it cannot perturb the int content key (never silent: the
            # render also logs a structured advisory, see render_timeline). A
            # rational timeline keeps it (part of the distinct rational key).
            if _int_rate:
                vc.pop("duration_frames", None)
    payload = {
        # tl_payload excludes meta (provenance) and strips default source_in_ms;
        # the per-shot source-audio fields are excluded below for the same
        # reason: all three ride the ordered segment keys, so defaults leave
        # this content key byte-identical to before the fields existed.
        "timeline": tl_payload,
        "segments": seg_keys,
        "ass": hash_file(ass_file) if ass_file and Path(ass_file).exists() else None,
        "audio": _audio_input_hashes(project, timeline),
        "encoding": _enc_params(target),
        "target": target,
    }
    # Round-Q: only add the branding-image key when there ARE image overlays, so
    # a project with no branding keeps a byte-identical content key to today.
    img_hashes = _overlay_image_hashes(project, timeline)
    if img_hashes:
        payload["overlay_images"] = img_hashes
    # Round-T: the color look re-keys the FINAL (a look change re-renders the
    # final, not the segments). Folded in ONLY when active, so preset none /
    # intensity 0 keeps a byte-identical content key.
    look = load_look(project)
    if look.active:
        payload["look"] = {"preset": look.preset, "intensity": look.intensity}
    # S4: the opt-in toolchain component also keys the FINAL directly — the
    # final composition pass is itself an ffmpeg/burn-font product beyond the
    # segments (and a missing-source segment keys as a marker that folds no
    # facts). Appended ONLY when opted in, exactly like overlay_images/look, so
    # every not-opted-in project keeps a byte-identical content key.
    if toolchain_key is not None:
        payload["toolchain"] = toolchain_key
    return payload


def final_content_key(
    project: Project,
    timeline: Timeline,
    *,
    ass_file: Path | None,
    target: str,
) -> str:
    """FIX-A: the content identity of a would-be final —

        hash(normalized timeline JSON + ordered segment cache keys
             + ASS file hash + audio input hashes + encoding params + target)

    Two builds over identical inputs produce identical keys, so `manju build`
    can skip the render instead of minting final_vN+1 forever."""
    return cache_key(_final_key_payload(project, timeline, ass_file=ass_file, target=target))


def _key_inputs_breakdown(payload: dict) -> dict:
    """The final-key payload re-expressed with evidence-friendly field names
    (WP3 e2). A pure key rename of the SAME values that were hashed — so
    reconstructing the payload from this and re-hashing reproduces ``final_key``
    exactly (``cache_key`` sorts keys, so order is irrelevant). All values are
    already project-relative: segment/audio entries are content hashes or
    ``missing:<relative source>`` markers, and the timeline dump stores clip
    sources exactly as the timeline holds them (project-relative).

    ``timeline`` is the normalized timeline dump the key hashes — NOT the
    compile fingerprint (``meta.compiled_from`` is a different identity)."""
    inputs = {
        "timeline": payload["timeline"],
        "segment_keys": payload["segments"],
        "ass_sha256": payload["ass"],
        "audio": payload["audio"],
        "encoding": payload["encoding"],
        "target": payload["target"],
    }
    if "overlay_images" in payload:
        inputs["overlay_images"] = payload["overlay_images"]
    if "look" in payload:
        inputs["look"] = payload["look"]
    if "toolchain" in payload:                       # S4 opt-in component
        inputs["toolchain"] = payload["toolchain"]
    return inputs


def _read_key_sidecar(media_path: Path) -> str | None:
    sidecar = media_path.with_suffix(".key.json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return str(data.get("final_key", "")) or None
    except (json.JSONDecodeError, OSError):
        return None


def _write_key_sidecar(media_path: Path, content_key: str, target: str, *,
                       run_id: str | None = None, payload: dict | None = None) -> None:
    from datetime import datetime, timezone

    data = {
        "final_key": content_key,
        "target": target,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # WP3 run-evidence (additive, evidence-only — nothing READS these to drive a
    # build; _read_key_sidecar consumes only final_key/target, kept intact above).
    # run_id correlates a final to its run record (e3); output_sha256 vouches for
    # the bytes actually on disk (e1); inputs exposes the exact key breakdown,
    # re-hashable straight back to final_key (e2). None run_id / payload → the
    # field is simply omitted, so other callers (locale, audition) are unchanged.
    if run_id is not None:
        data["run_id"] = run_id
    if payload is not None:
        if media_path.exists():
            data["output_sha256"] = hash_file(media_path)
        data["inputs"] = _key_inputs_breakdown(payload)
    # Atomic like every other truth-adjacent text file (a torn sidecar would
    # only cause a harmless re-render, but the discipline is uniform: §3).
    atomic_write_text(
        media_path.with_suffix(".key.json"),
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )


def _write_timeline_sidecar(media_path: Path, timeline: Timeline) -> None:
    """Round-S compare ground truth: persist the compiled timeline next to an
    engine-minted final as ``final_vN.timeline.json`` (canonical JSON of the
    timeline the final was rendered from).

    Derived metadata, NOT part of the content key (``final_content_key`` never
    reads it) and written strictly AFTER the mp4+key sidecar are in place — so
    build idempotency is untouched and a snapshot existing implies its final is
    complete. ``manju compare`` uses these snapshots for a per-shot diff; finals
    minted before this landed simply lack one and degrade to a key-only diff."""
    atomic_write_text(
        media_path.with_suffix(".timeline.json"),
        json.dumps(timeline.model_dump(), ensure_ascii=False, indent=2) + "\n",
    )


def _latest_final_with_key(project: Project) -> tuple[Path, str] | None:
    latest = project.newest_final_path()  # numeric: v10 beats v9
    if latest is None:
        return None
    key = _read_key_sidecar(latest)
    return (latest, key) if key is not None else None


def render_timeline(
    project: Project,
    timeline: Timeline,
    *,
    target: str = "final",
    out_path: Path | None = None,
    ass_file: Path | None = None,
    force: bool = False,
    log: Callable[[str], None] | None = None,
    run_id: str | None = None,  # WP3: evidence-only, recorded on the key sidecar
) -> Path:
    if target not in ("final", "proxy"):
        raise MediaError(f"unknown render target: {target!r} (expected 'final' or 'proxy')")
    if log is None:
        log = default_log(project.root, "render")

    video_clips = list(timeline.tracks.video)
    if not video_clips:
        raise MediaError("timeline has no video clips to render")

    width, height, fps = timeline.width, timeline.height, timeline.fps
    # R2 rational edit rate. ``fps`` stays the INT nominal rate for cache keys,
    # frame math (_plan_transitions/xfade handles) and byte-identity. A rational
    # (1001-family) project ALSO carries its exact rate: ``rate_arg`` is the
    # native ffmpeg frame-rate token ("24000/1001") the segment/boundary/trim/
    # final ``fps=`` nodes run at + the explicit ``-r`` on the final encode, and
    # ``rate_key`` makes its cache keys content-distinct. Read via the timeline's
    # frame_rate resolver (never the raw rational field — R1 surface pin). Both
    # None/int for an int project → byte-identical command lines AND keys.
    rate = timeline.frame_rate
    rational = rate.exact_int is None
    rate_arg: str | int = str(rate) if rational else fps
    rate_key: str | None = str(rate) if rational else None
    # R2: an int-rate timeline that carries a hand-edited stray duration_frames
    # has it IGNORED (the render keys/encodes off duration_ms + the int fps; the
    # field is stripped from the content key in _final_key_payload) — but never
    # SILENTLY: name the clips so a hand-editor knows it did nothing and how to
    # make it real (declare the project's rational edit rate + recompile).
    if not rational:
        stray = [c.shot for c in video_clips if getattr(c, "duration_frames", None) is not None]
        if stray:
            log(
                "advisory: duration_frames on an int-rate timeline is IGNORED "
                f"(clips {', '.join(stray)}) — it only applies to a rational edit "
                "rate; declare the project's rational rate (e.g. 24000/1001) and "
                "recompile to give these clips a real whole-frame length"
            )
    if target == "final":
        out_w, out_h = width, height
    else:
        out_w, out_h = _even(width // 2), _even(height // 2)

    # FIX-A: idempotent renders. Identical content key -> reuse instead of
    # re-encoding; --force overrides. Finals compare against the latest
    # final_vN's sidecar; the (overwritable) proxy compares against its own.
    payload = _final_key_payload(project, timeline, ass_file=ass_file, target=target)
    content_key = cache_key(payload)
    # S4: reuse the EXACT toolchain component the payload was keyed with for
    # the segment/boundary builds below (absent → None → byte-identical keys) —
    # one computation, and the built segment paths can never drift from the
    # seg keys inside the content key (FIX-A discipline).
    toolchain_key = payload.get("toolchain")
    if not force and out_path is None:
        if target == "final":
            latest = _latest_final_with_key(project)
            if latest is not None and latest[1] == content_key:
                log(f"final up-to-date (content key match): reusing {latest[0].name}")
                return latest[0]
        else:
            proxy_path = project.proxy_dir / "proxy.mp4"
            existing = _read_key_sidecar(proxy_path)
            if proxy_path.exists() and existing == content_key:
                log("proxy up-to-date (content key match): reusing proxy.mp4")
                return proxy_path

    total_ms = timeline.duration_ms or sum(c.duration_ms for c in video_clips)
    total_s = total_ms / 1000.0

    # Round-T: resolve interior boundaries first — this decides each segment's
    # baked fade params (an applied xfade leaves its edges fade-free; a degraded
    # one bakes dip-to-black) and which boundaries earn a real cross-dissolve.
    xfade_boundaries, seg_fades = _plan_transitions(project, video_clips, fps, log=log)

    # ① + ② normalize each clip into a cached segment (fades baked in). --
    segments: list[Path] = []
    for i, clip in enumerate(video_clips):
        fade_in_ms, fade_out_ms = seg_fades[i]
        segments.append(
            _build_segment(
                project, clip, width=width, height=height, fps=fps, target=target,
                fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms, log=log,
                rate_key=rate_key, rate_arg=rate_arg, toolchain_key=toolchain_key,
            )
        )

    # Record the render-time transition facts: applied xfades name their handle
    # length; degraded ones warn, naming the boundary (goal: every degrade is
    # visible). Timeline purity holds — the compiler recorded only the request.
    transitions_report: list[dict] = []
    for b in xfade_boundaries:
        a_shot, b_shot = video_clips[b.i].shot, video_clips[b.i + 1].shot
        pair = f"{a_shot}->{b_shot}"
        if b.applied:
            log(f"transition {pair}: applied {b.requested} "
                f"(real handles, {b.half_ms}ms each side)")
            transitions_report.append(
                {"boundary": pair, "requested": b.requested, "applied": True,
                 "half_ms": b.half_ms}
            )
        else:
            log(f"transition {pair}: no handles; used dip-to-black ({b.reason})")
            transitions_report.append(
                {"boundary": pair, "requested": b.requested, "applied": False,
                 "reason": b.reason}
            )

    # timeline.tracks.overlay (title cards + packaging info cards + round-Q
    # branding) is burned in the final composition pass below (§7 step ④ /
    # §13-14), alongside the subtitles — never in the per-segment cache, so
    # segment cache keys stay untouched. Text overlays (title_card/info_card/
    # badge/cta/text-watermark) burn via drawtext; image overlays (logo / image
    # watermark) burn via an ffmpeg overlay chain with extra scaled inputs.
    text_overlays = [o for o in timeline.tracks.overlay if _is_text_overlay(o)]
    image_overlays = [o for o in timeline.tracks.overlay if _is_image_overlay(o)]

    # An engine-minted final name is append-only (a fresh final_vN): the atomic
    # swap below must refuse to land on an existing file. A caller-supplied
    # out_path keeps its historical overwrite semantics (as does the proxy).
    fresh_final = out_path is None and target == "final"
    if out_path is None:
        out_path = project.next_final_path() if target == "final" else project.proxy_dir / "proxy.mp4"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=out_path.parent) as tmp:
        tmp_dir = Path(tmp)
        # ③ concat the uniform segments with the concat demuxer (stream copy).
        # Round-T: when applied xfades are present the track is restructured into
        # [A shortened][boundary][B shortened] pieces (same profile → still a
        # stream-copy concat); with none, this is exactly the segment list.
        if any(b.applied for b in xfade_boundaries):
            pieces = _assemble_video_pieces(
                project, video_clips, segments, xfade_boundaries,
                width=width, height=height, fps=fps, target=target,
                tmp_dir=tmp_dir, log=log, rate_key=rate_key, rate_arg=rate_arg,
                toolchain_key=toolchain_key,
            )
        else:
            pieces = segments
        list_file = tmp_dir / "concat.txt"
        list_file.write_text("".join(_concat_line(s) for s in pieces), encoding="utf-8")
        concat_mp4 = tmp_dir / "concat.mp4"
        run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(concat_mp4)],
            log=log,
        )

        # ④ + ⑤ + ⑥ single final pass: scale (+ look + burn subtitles) + audio mix + mux.
        extra_inputs, audio_stmts, aout = _build_audio_graph(
            timeline, project, target=target, total_s=total_s
        )
        vchain = f"[0:v]scale={out_w}:{out_h}:flags=bicubic,setsar=1"
        # Round-T: the deterministic color look rides here — after scale/setsar,
        # BEFORE the subtitle burn — for both final and proxy. "" for a no-op look.
        look_chain = _look_filter(load_look(project))
        if look_chain:
            vchain += "," + look_chain
        if ass_file is not None:
            vchain += f",ass={_escape_filter_path(ass_file)}"
        if text_overlays:
            font = find_font()
            for filt in _title_card_filters(
                text_overlays, out_w=out_w, tmp_dir=tmp_dir, font=font
            ):
                vchain += "," + filt

        img_inputs: list[str] = []
        if image_overlays:
            # Image overlays need named pads (overlay is a 2-input node), so the
            # linear drawtext chain terminates at [vbase] and each scaled image
            # is overlaid onto it. The image inputs are appended AFTER the audio
            # inputs (base_idx = 1 + audio input count) so NOTHING in the audio
            # graph's [n:a] indexing shifts. FIX-B fps/format snap is the final
            # node onto the last overlay output → [vout].
            vchain += "[vbase]"
            img_inputs, img_stmts, last_label = _image_overlay_graph(
                image_overlays, project=project, in_label="[vbase]",
                out_w=out_w, base_idx=1 + extra_inputs.count("-i"),
            )
            # R2: rational projects snap the final node onto the native num/den.
            img_stmts.append(f"{last_label}fps={rate_arg},format=yuv420p[vout]")
            filter_complex = ";".join([vchain, *img_stmts, *audio_stmts])
        else:
            # FIX-B: force the output onto the project frame grid — without an
            # explicit fps the concat of segments can drift the deduced rate
            # (observed pre-fix: r_frame_rate=143/6 instead of 24/1). R2: a
            # rational project uses its native rate token (fps=24000/1001).
            vchain += f",fps={rate_arg},format=yuv420p[vout]"
            filter_complex = ";".join([vchain, *audio_stmts])

        enc = _enc_params(target)
        # R2: a rational project also stamps the container's output frame rate
        # explicitly (-r num/den), belt-and-suspenders with the fps= node above.
        # Int projects add NO -r (byte-identical command line) — they already
        # rely on the fps= filter, exactly as before.
        rate_out = ["-r", str(rate_arg)] if rational else []

        with atomic_output(out_path, must_not_exist=fresh_final) as tmp_out:
            run_ffmpeg(
                ["-i", str(concat_mp4), *extra_inputs, *img_inputs,
                 "-filter_complex", filter_complex,
                 "-map", "[vout]", "-map", aout,
                 # Clamp to the timeline length: the picture track (exact-duration
                 # segments) is the master; this trims any audio tail (mix priming,
                 # bounded voice/music) to a deterministic total.
                 "-t", f"{total_s:.3f}",
                 *rate_out, *enc, str(tmp_out)],
                log=log,
            )
            # Encode complete, swap imminent. For the (overwritable) proxy, drop
            # the OLD sidecar first: a crash inside the swap window can then only
            # cost a re-render — a stale key must never vouch for bytes it does
            # not describe. (A failed encode never reaches this line, so the
            # previous good proxy + sidecar pair stays reusable.)
            if target != "final":
                out_path.with_suffix(".key.json").unlink(missing_ok=True)

    # FIX-A: every render carries its content key so the next build can prove
    # it is already up to date (finals: append-only sidecars; proxy: its own).
    # Written strictly AFTER os.replace has the mp4 fully in place — a sidecar
    # existing implies its mp4 is complete (see module design note).
    _write_key_sidecar(out_path, content_key, target, run_id=run_id, payload=payload)

    # Round-S: an engine-minted final also carries a snapshot of the timeline it
    # was rendered from, so `manju compare` has per-shot ground truth. Additive
    # and excluded from the content key; only for fresh final_vN (never the
    # overwritable proxy or a caller-supplied out_path).
    if fresh_final:
        _write_timeline_sidecar(out_path, timeline)
        # Round-T: record which xfades were applied vs degraded to dip-to-black.
        if transitions_report:
            _write_transitions_sidecar(out_path, transitions_report)

    return out_path
