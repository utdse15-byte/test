"""§7 render pipeline: normalize (cached) → concat → transitions → overlay/subs
→ audio mix → mux.

The pipeline is deliberately segmented (never one giant filter_complex over the
whole timeline): each selected take becomes a content-addressed segment in
``renders/segments/``, so replacing a single take rebuilds only that segment
(and its transition neighbours) — incremental rendering is a free by-product of
the cache key (§7, §14).

Transition model for M0: a "fade" ``transition_out`` becomes a dip-to-black —
fade-out on the tail of the segment and fade-in on the head of the next. This is
baked into the (cached) per-segment render, with the fade durations folded into
the cache key. A true xfade/acrossfade seam pipeline (§7 step ②) is deferred.

    TODO(M1+): render dedicated xfade/acrossfade seam segments between clips for
    a real cross-dissolve instead of the deterministic dip-to-black used here.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, hash_file, short_hash
from ..core.models import OverlayClip, Timeline, VideoClip
from .card import find_font
from .ffmpeg import MediaError, default_log, run_ffmpeg
from .normalize import normalize_segment

Log = Callable[[str], None] | None


def _even(n: int) -> int:
    n = max(2, int(n))
    return n if n % 2 == 0 else n - 1


def _escape_filter_path(path: Path | str) -> str:
    """Escape a path for use inside a filtergraph option value (e.g. the ass=
    filter). Chinese/UTF-8 characters need no escaping; only the filtergraph
    metacharacters do. Handles Windows ``C:`` drive colons too (§14)."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _title_card_filters(
    overlays: list[OverlayClip], *, out_w: int, tmp_dir: Path, font: Path | None
) -> list[str]:
    """Chained ``drawtext`` filter(s) for the title-card overlay layer (§7 ④).

    One drawtext per ``title_card`` overlay, burned in the final pass exactly
    like the subtitles (never in the per-segment cache). The text is written to
    a UTF-8 textfile and passed via ``textfile=`` — the escaping-free approach
    from card.py — and shown only within its window via the timeline ``enable``
    expression. The "chapter" template is large centred white text over a
    semi-transparent black box at the upper third; any other (unknown) template
    falls back to that same style. ``fontsize`` tracks the frame the text is
    drawn on (``out_w``) so proxy and final look proportionally identical."""
    fontsize = max(12, out_w // 10)
    filters: list[str] = []
    for k, ov in enumerate(overlays):
        txt = tmp_dir / f"title_{k}.txt"
        txt.write_text(ov.text, encoding="utf-8")
        start_s = ov.start_ms / 1000.0
        end_s = (ov.start_ms + ov.duration_ms) / 1000.0
        opts = [
            f"textfile={_escape_filter_path(txt)}",
            "fontcolor=white",
            f"fontsize={fontsize}",
            "x=(w-text_w)/2",
            "y=h*0.28",
            "box=1",
            "boxcolor=black@0.45",
            "boxborderw=24",
            f"enable='between(t,{start_s:.3f},{end_s:.3f})'",
        ]
        if font is not None:
            opts.append(f"fontfile={_escape_filter_path(font)}")
        filters.append("drawtext=" + ":".join(opts))
    return filters


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
    run_ffmpeg(
        [
            "-i", str(src_seg),
            "-vf", ",".join(vfilters),
            "-af", ",".join(afilters) if afilters else "anull",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(dest),
        ],
        log=log,
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
    must, and a cache hit is left byte-for-byte untouched (mtime preserved).
    """
    src = project.resolve(clip.source)
    if not src.exists():
        raise MediaError(f"take source not found for {clip.shot}/{clip.take}: {clip.source}")
    key = cache_key(
        hash_file(src), width, height, fps, clip.duration_ms, target, fade_in_ms, fade_out_ms
    )
    seg_path = project.segments_dir / f"{short_hash(key)}.mp4"
    if seg_path.exists():
        return seg_path
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


def _build_audio_graph(
    timeline: Timeline, project: Project, *, target: str, total_s: float
) -> tuple[list[str], list[str], str]:
    """Return (extra_input_args, filter_statements, audio_output_label).

    Audio bus starts from the concatenated segment audio ([0:a]), mixes in each
    voice clip (adelay to its start), then each music clip (gain, trim to the
    timeline length, tail fade, optional sidechain ducking keyed by the voice
    bus). loudnorm is applied only for the "final" target (skipped for proxy).
    """
    voice_clips = list(timeline.tracks.voice)
    music_clips = list(timeline.tracks.music)

    inputs: list[str] = []
    stmts: list[str] = []
    mix_labels: list[str] = []

    stmts.append("[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[base]")
    mix_labels.append("[base]")

    # ---- voices ---------------------------------------------------------
    voice_labels: list[str] = []
    for k, clip in enumerate(voice_clips):
        idx = 1 + k
        inputs += ["-i", str(project.resolve(clip.source))]
        delay = max(0, clip.start_ms)
        chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if clip.duration_ms is not None:
            # Bound the voice to its clip length so an over-long take cannot push
            # the film past timeline.duration_ms (picture is the master, §6).
            chain += ["apad", f"atrim=0:{clip.duration_ms / 1000.0:.3f}"]
        if delay > 0:
            chain.append(f"adelay={delay}:all=1")
        lbl = f"[voice{k}]"
        stmts.append(f"[{idx}:a]{','.join(chain)}{lbl}")
        voice_labels.append(lbl)

    ducked = [c for c in music_clips if c.ducking and voice_labels]
    voice_for_mix: str | None = None
    ducking_keys: list[str] = []
    if voice_labels:
        if len(voice_labels) == 1:
            voicebus = voice_labels[0]
        else:
            voicebus = "[voicebus]"
            stmts.append("".join(voice_labels) + f"amix=inputs={len(voice_labels)}:normalize=0[voicebus]")
        n_keys = len(ducked)
        if n_keys == 0:
            voice_for_mix = voicebus
        else:
            outs = [f"[vb{i}]" for i in range(1 + n_keys)]
            stmts.append(f"{voicebus}asplit={1 + n_keys}" + "".join(outs))
            voice_for_mix = outs[0]
            ducking_keys = outs[1:]
        mix_labels.append(voice_for_mix)

    # ---- music ----------------------------------------------------------
    key_iter = iter(ducking_keys)
    music_base = 1 + len(voice_clips)
    for k, clip in enumerate(music_clips):
        idx = music_base + k
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
            stmts.append(
                f"{pre}{key}sidechaincompress=threshold=0.05:ratio=8:attack=5:release=250{out}"
            )
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


def render_timeline(
    project: Project,
    timeline: Timeline,
    *,
    target: str = "final",
    out_path: Path | None = None,
    ass_file: Path | None = None,
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

    # timeline.tracks.overlay (title cards) is burned in the final composition
    # pass below (§7 step ④), alongside the subtitles — never in the per-segment
    # cache, so segment cache keys stay untouched.
    title_cards = [o for o in timeline.tracks.overlay if o.kind == "title_card"]

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
        if title_cards:
            font = find_font()
            for filt in _title_card_filters(
                title_cards, out_w=out_w, tmp_dir=tmp_dir, font=font
            ):
                vchain += "," + filt
        vchain += ",format=yuv420p[vout]"
        filter_complex = ";".join([vchain, *audio_stmts])

        if target == "final":
            enc = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]
        else:
            enc = ["-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "96k", "-ar", "48000"]

        run_ffmpeg(
            ["-i", str(concat_mp4), *extra_inputs,
             "-filter_complex", filter_complex,
             "-map", "[vout]", "-map", aout,
             # Clamp to the timeline length: the picture track (exact-duration
             # segments) is the master; this trims any audio tail (mix priming,
             # bounded voice/music) to a deterministic total.
             "-t", f"{total_s:.3f}",
             *enc, str(out_path)],
            log=log,
        )

    return out_path
