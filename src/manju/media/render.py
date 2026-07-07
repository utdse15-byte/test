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

Design note — why not xfade/acrossfade seams (§7 ② names them): a true
cross-dissolve needs media HANDLES — extra frames beyond the cut point on
both sides. Manju's takes are generated/normalized to exactly their timeline
duration, so there are no handles: crossfading would either shorten the film
by the overlap (desyncing the audio-driven caption/voice timings, §6) or
replay frames around the cut (visible stutter). Dip-to-black is the honest
deterministic transition under exact-duration takes. If cross-dissolves are
ever wanted, the change belongs in the compiler/providers first (allocate
+transition_ms handles at generation and carry source in-points on clips),
not here.

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
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, hash_file, short_hash
from ..core.models import AudioClip, OverlayClip, Timeline, VideoClip
from ..core.yamlio import atomic_write_text
from .card import find_font
from .ffmpeg import MediaError, atomic_output, default_log, run_ffmpeg
from .normalize import normalize_segment
from .probe import probe_duration_ms

Log = Callable[[str], None] | None


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
) -> str:
    """The one segment-key formula, shared by the segment cache and the
    final content key (FIX-A) so they can never drift apart."""
    src = project.resolve(clip.source)
    return cache_key(
        hash_file(src), width, height, fps, clip.duration_ms, target, fade_in_ms, fade_out_ms
    )


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
) -> Path:
    """Return the cached segment path for one clip, rendering it if absent.

    Cache key = source content hash + normalization params + target + fade
    params, so a changed take (or a changed transition) rebuilds only what it
    must, and a (readable) cache hit is left byte-for-byte untouched (mtime
    preserved). Writes into the cache are atomic (see module design note), so
    an unreadable hit can only be legacy pre-durability debris or external
    damage — it is evicted and rebuilt rather than poisoning every future final.
    """
    src = project.resolve(clip.source)
    if not src.exists():
        raise MediaError(f"take source not found for {clip.shot}/{clip.take}: {clip.source}")
    key = _segment_cache_key(
        project, clip, width=width, height=height, fps=fps, target=target,
        fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms,
    )
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

    if fade_in_ms == 0 and fade_out_ms == 0:
        normalize_segment(
            src, seg_path, width=width, height=height, fps=fps,
            duration_ms=clip.duration_ms, log=log,
        )
        return seg_path

    # Normalize to a temp, then bake fades into the cached segment.
    with tempfile.TemporaryDirectory(dir=seg_path.parent) as tmp:
        base = Path(tmp) / "base.mp4"
        normalize_segment(
            src, base, width=width, height=height, fps=fps,
            duration_ms=clip.duration_ms, log=log,
        )
        _apply_fades(
            base, seg_path, duration_ms=clip.duration_ms,
            fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms, log=log,
        )
    return seg_path


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
        if clip.start_ms > 0:
            chain.append(f"adelay={clip.start_ms}:all=1")
        if clip.gain_db:
            chain.append(f"volume={clip.gain_db}dB")
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
        if clip.start_ms > 0:
            chain.append(f"adelay={clip.start_ms}:all=1")
        if clip.gain_db:
            chain.append(f"volume={clip.gain_db}dB")
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
    width, height, fps = timeline.width, timeline.height, timeline.fps
    clips = list(timeline.tracks.video)
    seg_keys = [
        _segment_cache_key(
            project, clip, width=width, height=height, fps=fps, target=target,
            fade_in_ms=_fade_params(clips, i)[0], fade_out_ms=_fade_params(clips, i)[1],
        )
        if project.resolve(clip.source).exists()
        else f"missing:{clip.source}"
        for i, clip in enumerate(clips)
    ]
    payload = {
        "timeline": timeline.model_dump(exclude={"meta"}),  # meta is provenance, not content
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
    return cache_key(payload)


def _read_key_sidecar(media_path: Path) -> str | None:
    sidecar = media_path.with_suffix(".key.json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return str(data.get("final_key", "")) or None
    except (json.JSONDecodeError, OSError):
        return None


def _write_key_sidecar(media_path: Path, content_key: str, target: str) -> None:
    from datetime import datetime, timezone

    # Atomic like every other truth-adjacent text file (a torn sidecar would
    # only cause a harmless re-render, but the discipline is uniform: §3).
    atomic_write_text(
        media_path.with_suffix(".key.json"),
        json.dumps(
            {"final_key": content_key, "target": target,
             "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2,
        ) + "\n",
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
) -> Path:
    if target not in ("final", "proxy"):
        raise MediaError(f"unknown render target: {target!r} (expected 'final' or 'proxy')")
    if log is None:
        log = default_log(project.root, "render")

    video_clips = list(timeline.tracks.video)
    if not video_clips:
        raise MediaError("timeline has no video clips to render")

    width, height, fps = timeline.width, timeline.height, timeline.fps
    if target == "final":
        out_w, out_h = width, height
    else:
        out_w, out_h = _even(width // 2), _even(height // 2)

    # FIX-A: idempotent renders. Identical content key -> reuse instead of
    # re-encoding; --force overrides. Finals compare against the latest
    # final_vN's sidecar; the (overwritable) proxy compares against its own.
    content_key = final_content_key(project, timeline, ass_file=ass_file, target=target)
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

    # ① + ② normalize each clip into a cached segment (fades baked in). --
    segments: list[Path] = []
    for i, clip in enumerate(video_clips):
        fade_in_ms, fade_out_ms = _fade_params(video_clips, i)
        segments.append(
            _build_segment(
                project, clip, width=width, height=height, fps=fps, target=target,
                fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms, log=log,
            )
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
        list_file = tmp_dir / "concat.txt"
        list_file.write_text("".join(_concat_line(s) for s in segments), encoding="utf-8")
        concat_mp4 = tmp_dir / "concat.mp4"
        run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(concat_mp4)],
            log=log,
        )

        # ④ + ⑤ + ⑥ single final pass: scale (+ burn subtitles) + audio mix + mux.
        extra_inputs, audio_stmts, aout = _build_audio_graph(
            timeline, project, target=target, total_s=total_s
        )
        vchain = f"[0:v]scale={out_w}:{out_h}:flags=bicubic,setsar=1"
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
            img_stmts.append(f"{last_label}fps={fps},format=yuv420p[vout]")
            filter_complex = ";".join([vchain, *img_stmts, *audio_stmts])
        else:
            # FIX-B: force the output onto the project frame grid — without an
            # explicit fps the concat of segments can drift the deduced rate
            # (observed pre-fix: r_frame_rate=143/6 instead of 24/1).
            vchain += f",fps={fps},format=yuv420p[vout]"
            filter_complex = ";".join([vchain, *audio_stmts])

        enc = _enc_params(target)

        with atomic_output(out_path, must_not_exist=fresh_final) as tmp_out:
            run_ffmpeg(
                ["-i", str(concat_mp4), *extra_inputs, *img_inputs,
                 "-filter_complex", filter_complex,
                 "-map", "[vout]", "-map", aout,
                 # Clamp to the timeline length: the picture track (exact-duration
                 # segments) is the master; this trims any audio tail (mix priming,
                 # bounded voice/music) to a deterministic total.
                 "-t", f"{total_s:.3f}",
                 *enc, str(tmp_out)],
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
    _write_key_sidecar(out_path, content_key, target)

    # Round-S: an engine-minted final also carries a snapshot of the timeline it
    # was rendered from, so `manju compare` has per-shot ground truth. Additive
    # and excluded from the content key; only for fresh final_vN (never the
    # overwritable proxy or a caller-supplied out_path).
    if fresh_final:
        _write_timeline_sidecar(out_path, timeline)

    return out_path
