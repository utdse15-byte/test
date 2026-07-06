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
"""

from __future__ import annotations

import json
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
        # info cards ride lower (§13-14) so they never collide with a title card
        # pinned to the upper third; both share this one burn path.
        is_info = ov.kind == "info_card"
        y_expr = "h*0.72" if is_info else "h*0.28"
        info_fontsize = max(12, out_w // 16)
        opts = [
            f"textfile={_escape_filter_path(txt)}",
            "fontcolor=white",
            f"fontsize={info_fontsize if is_info else fontsize}",
            "x=(w-text_w)/2",
            f"y={y_expr}",
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
    must, and a cache hit is left byte-for-byte untouched (mtime preserved).
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
            stmts.append(
                f"{pre}{key}sidechaincompress=threshold=0.05:ratio=8:attack=5:release=250{out}"
            )
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

    media_path.with_suffix(".key.json").write_text(
        json.dumps(
            {"final_key": content_key, "target": target,
             "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _latest_final_with_key(project: Project) -> tuple[Path, str] | None:
    finals = sorted(project.final_dir.glob("final_v*.mp4"))
    if not finals:
        return None
    latest = finals[-1]
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

    # timeline.tracks.overlay (title cards + packaging info cards) is burned in
    # the final composition pass below (§7 step ④ / §13-14), alongside the
    # subtitles — never in the per-segment cache, so segment cache keys stay
    # untouched. The burn keys on kind: title_card and info_card share the path.
    burn_overlays = [
        o for o in timeline.tracks.overlay if o.kind in ("title_card", "info_card")
    ]

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
        if burn_overlays:
            font = find_font()
            for filt in _title_card_filters(
                burn_overlays, out_w=out_w, tmp_dir=tmp_dir, font=font
            ):
                vchain += "," + filt
        # FIX-B: force the output onto the project frame grid — without an
        # explicit fps the concat of segments can drift the deduced rate
        # (observed pre-fix: r_frame_rate=143/6 instead of 24/1).
        vchain += f",fps={fps},format=yuv420p[vout]"
        filter_complex = ";".join([vchain, *audio_stmts])

        enc = _enc_params(target)

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

    # FIX-A: every render carries its content key so the next build can prove
    # it is already up to date (finals: append-only sidecars; proxy: its own).
    _write_key_sidecar(out_path, content_key, target)

    return out_path
