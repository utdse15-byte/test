"""Round-T — handle-aware real transitions (xfade family) and color looks.

Two halves, like test_audio_policy.py / test_overlay.py:
  (a) pure/no-ffmpeg — the additive models, the deterministic look filter
      strings (intensity 0 == none), the reading contract for bible/style.yaml's
      look, and the content-key discipline (default byte-identical vs a control;
      a look re-keys the FINAL but NOT the segments).
  (b) end-to-end (ffmpeg required) — an applied xfade on imported footage WITH
      spare handles (total duration exact to the frame; boundary segment cached
      and reused; changing the transition type re-renders ONLY the boundary;
      audio continuity across the boundary), the degrade-to-dip warning on
      handle-less boundaries, look presets that render + re-key, and idempotency
      with a look set.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.hashing import cache_key
from manju.core.models import (
    LOOK_PRESETS,
    TRANSITION_TYPES,
    LookSpec,
    Timeline,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from manju.core.yamlio import write_yaml

# ============================================================ (a) pure / no ffmpeg


def test_video_clip_source_in_ms_is_additive_default_zero():
    vc = VideoClip(shot="S1", take="t", source="a.mp4", start_ms=0, duration_ms=1000)
    assert vc.source_in_ms == 0
    # the field round-trips and does not appear "loud" in a default dump value
    assert vc.model_dump()["source_in_ms"] == 0


def test_transition_types_curated_and_default_unchanged():
    assert TransitionSpec().type == "fade" and TransitionSpec().duration_ms == 300
    for t in ("cut", "xfade_fade", "xfade_slideleft", "xfade_slideright",
              "xfade_wipeleft", "xfade_circleopen"):
        assert t in TRANSITION_TYPES
    # unknown types are accepted (lenient truth) — never crash a build over a typo
    assert TransitionSpec(type="wobble").type == "wobble"


def test_look_spec_validation():
    assert LookSpec().preset == "none" and LookSpec().active is False
    assert LookSpec(preset="warm", intensity=0.0).active is False  # intensity 0 == none
    assert LookSpec(preset="warm", intensity=0.6).active is True
    for p in LOOK_PRESETS:
        LookSpec(preset=p)  # all valid
    with pytest.raises(Exception):
        LookSpec(preset="sepia")
    with pytest.raises(Exception):
        LookSpec(intensity=1.5)
    with pytest.raises(Exception):
        LookSpec(intensity=-0.1)


def test_look_filter_none_and_intensity_zero_are_empty():
    from manju.media.render import _look_filter

    assert _look_filter(LookSpec()) == ""
    assert _look_filter(LookSpec(preset="none", intensity=1.0)) == ""
    for p in ("warm", "cool", "bw", "film", "vivid"):
        assert _look_filter(LookSpec(preset=p, intensity=0.0)) == ""


def test_look_filter_presets_are_deterministic_and_neutral_at_low_intensity():
    from manju.media.render import _LOOK_BUILDERS, _look_filter

    for p, build in _LOOK_BUILDERS.items():
        s = _look_filter(LookSpec(preset=p, intensity=1.0))
        assert s and s == build(1.0)  # deterministic, matches the builder
        # every knob returns to neutral as intensity → 0 (identity chain)
        near0 = build(1e-9)
        assert "saturation=1" in near0 or "colorbalance=rs=0" in near0 or near0 == build(0.0)
    # spot-check the documented warm string
    assert _look_filter(LookSpec(preset="warm", intensity=1.0)) == (
        "colorbalance=rs=0.3:bs=-0.3:rm=0.15:bm=-0.15:rh=0.1:bh=-0.1,eq=saturation=1.1"
    )
    # bw at full strength is fully desaturated
    assert "saturation=0" in _look_filter(LookSpec(preset="bw", intensity=1.0))


def test_load_look_reading_contract(tmp_path):
    """bible/style.yaml's top-level `look:` mapping is THE reading contract."""
    from manju.media.render import load_look

    project = Project.create(tmp_path / "看", git_init=False)
    # absent look → none
    assert load_look(project).preset == "none"
    # a real look reads back
    write_yaml(project.root / "bible" / "style.yaml",
               {"global_style": {"mood": "x"}, "look": {"preset": "film", "intensity": 0.7}})
    look = load_look(project)
    assert look.preset == "film" and abs(look.intensity - 0.7) < 1e-9 and look.active
    # a malformed look degrades to none (a style typo never crashes a build)
    write_yaml(project.root / "bible" / "style.yaml", {"look": {"preset": "nope"}})
    assert load_look(project).preset == "none"


def _fake_clip(project: Project, name: str, nbytes: int) -> str:
    """A tiny FAKE media file (distinct bytes) that exists for hash_file; the
    content-key discipline tests never probe or render."""
    p = project.imports_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake" + bytes([nbytes % 256]) * 32 + name.encode())
    return project.relpath(p)


def _fade_timeline(project: Project) -> Timeline:
    a = _fake_clip(project, "a.mp4", 1)
    b = _fake_clip(project, "b.mp4", 2)
    return Timeline(
        fps=24, width=256, height=448, duration_ms=2000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S1", take="t", source=a, start_ms=0, duration_ms=1000,
                      transition_out=TransitionSpec(type="fade", duration_ms=250)),
            VideoClip(shot="S2", take="t", source=b, start_ms=1000, duration_ms=1000),
        ]),
    )


def test_content_key_byte_identical_to_the_pre_round_t_formula(tmp_path):
    """CONTROL: a vanilla timeline (fade transitions, no in-points, no look)
    produces EXACTLY the pre-round-T content key — the plan == _fade_params, the
    default source_in_ms is stripped from the hashed payload, and no look key is
    added. If this drifts, every existing final would silently re-render."""
    from manju.media.render import (
        _audio_input_hashes, _enc_params, _fade_params, _overlay_image_hashes,
        _segment_cache_key, final_content_key,
    )

    project = Project.create(tmp_path / "对照", git_init=False)
    tl = _fade_timeline(project)
    clips = list(tl.tracks.video)

    # reconstruct the exact old formula (fade params via _fade_params; timeline
    # dump with the additive source_in_ms:0 stripped; no look).
    old_seg_keys = [
        _segment_cache_key(project, c, width=256, height=448, fps=24, target="final",
                           fade_in_ms=_fade_params(clips, i)[0],
                           fade_out_ms=_fade_params(clips, i)[1])
        for i, c in enumerate(clips)
    ]
    tl_payload = tl.model_dump(exclude={"meta"})
    for vc in tl_payload["tracks"]["video"]:
        # strip ALL round-T additive fields — the pre-round-T dump had none of
        # them (source_in_ms from this cluster; gain/mute from the audio-edit
        # cluster, integrated together)
        vc.pop("source_in_ms", None)
        vc.pop("source_gain_db", None)
        vc.pop("source_mute", None)
    old_payload = {
        "timeline": tl_payload,
        "segments": old_seg_keys,
        "ass": None,
        "audio": _audio_input_hashes(project, tl),
        "encoding": _enc_params("final"),
        "target": "final",
    }
    assert not _overlay_image_hashes(project, tl)  # no branding → no extra key
    expected = cache_key(old_payload)
    assert final_content_key(project, tl, ass_file=None, target="final") == expected


def test_look_rekeys_the_final_but_not_the_segments(tmp_path):
    from manju.media.render import (
        _plan_transitions, _segment_cache_key, final_content_key,
    )

    project = Project.create(tmp_path / "调色", git_init=False)
    tl = _fade_timeline(project)
    clips = list(tl.tracks.video)

    def seg_keys():
        _b, fades = _plan_transitions(project, clips, 24, log=None)
        return [_segment_cache_key(project, c, width=256, height=448, fps=24,
                                   target="final", fade_in_ms=fades[i][0],
                                   fade_out_ms=fades[i][1]) for i, c in enumerate(clips)]

    key_none = final_content_key(project, tl, ass_file=None, target="final")
    segs_none = seg_keys()

    # intensity 0 == none: byte-identical content key
    write_yaml(project.root / "bible" / "style.yaml", {"look": {"preset": "warm", "intensity": 0}})
    assert final_content_key(project, tl, ass_file=None, target="final") == key_none

    # an active look re-keys the FINAL …
    write_yaml(project.root / "bible" / "style.yaml", {"look": {"preset": "warm", "intensity": 0.5}})
    key_warm = final_content_key(project, tl, ass_file=None, target="final")
    assert key_warm != key_none
    # … but NOT the segments (look is a final-pass filter, not baked per-segment)
    assert seg_keys() == segs_none
    # a different intensity is a different key
    write_yaml(project.root / "bible" / "style.yaml", {"look": {"preset": "warm", "intensity": 0.9}})
    assert final_content_key(project, tl, ass_file=None, target="final") != key_warm


def test_plan_cut_is_a_hard_cut_no_fade_no_boundary(tmp_path):
    """A `cut` transition bakes NO fade and creates NO boundary — a hard cut."""
    from manju.media.render import _plan_transitions

    project = Project.create(tmp_path / "切", git_init=False)
    a = _fake_clip(project, "a.mp4", 1)
    b = _fake_clip(project, "b.mp4", 2)
    clips = [
        VideoClip(shot="S1", take="t", source=a, start_ms=0, duration_ms=1000,
                  transition_out=TransitionSpec(type="cut", duration_ms=300)),
        VideoClip(shot="S2", take="t", source=b, start_ms=1000, duration_ms=1000),
    ]
    boundaries, fades = _plan_transitions(project, clips, 24, log=None)
    assert boundaries == []                # cut is not an xfade boundary
    assert fades == [(0, 0), (0, 0)]       # no dip-to-black anywhere


def test_plan_xfade_without_real_handles_degrades_to_dip(tmp_path):
    """No probeable source / no in-point → the plan degrades the xfade to a dip
    (bakes the fade halves) and marks the boundary NOT applied, naming a reason."""
    from manju.media.render import _plan_transitions

    project = Project.create(tmp_path / "无手柄", git_init=False)
    a = _fake_clip(project, "a.mp4", 1)  # fake bytes → unprobeable → no tail handle
    b = _fake_clip(project, "b.mp4", 2)
    clips = [
        VideoClip(shot="S1", take="t", source=a, start_ms=0, duration_ms=1000,
                  transition_out=TransitionSpec(type="xfade_fade", duration_ms=300)),
        VideoClip(shot="S2", take="t", source=b, start_ms=1000, duration_ms=1000),
    ]
    boundaries, fades = _plan_transitions(project, clips, 24, log=None)
    assert len(boundaries) == 1 and boundaries[0].applied is False
    assert boundaries[0].reason  # names why it degraded
    # degraded xfade bakes dip-to-black halves, exactly like a plain fade(300)
    assert fades[0][1] == 150 and fades[1][0] == 150


# ============================================================ (b) end-to-end / ffmpeg

ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)

W, H, FPS = 192, 336, 24


def _gen_source(dest: Path, *, seconds: float, freq: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(dest)],
        check=True, capture_output=True,
    )


def _probe_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(round(float(out) * 1000))


def _capturing_log():
    lines: list[str] = []
    return lines, lines.append


def _applied_timeline(project: Project, *, xfade="xfade_fade", win_ms=1000,
                      in_ms=400, seconds=3.0) -> Timeline:
    """Three imported clips, each with a real in-point (spare HEAD) AND a source
    that runs well past the window (spare TAIL): both handles are real footage,
    so every interior xfade is APPLIED."""
    clips = []
    for i in range(3):
        src = project.imports_dir / f"clip{i}.mp4"
        _gen_source(src, seconds=seconds, freq=330 + i * 220)
        clips.append(VideoClip(
            shot=f"S{i+1}", take="t", source=project.relpath(src),
            start_ms=i * win_ms, duration_ms=win_ms, source_in_ms=in_ms,
            transition_out=(TransitionSpec(type=xfade, duration_ms=300) if i < 2 else None),
        ))
    return Timeline(fps=FPS, width=W, height=H, duration_ms=3 * win_ms,
                    tracks=TimelineTracks(video=clips))


@ffmpeg
def test_applied_xfade_preserves_total_duration_to_the_frame(tmp_path):
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "转场", git_init=False)
    tl = _applied_timeline(project)
    lines, log = _capturing_log()
    out = render_timeline(project, tl, target="final", log=log)

    final_ms = _probe_ms(out)
    frame_ms = 1000.0 / FPS
    assert abs(final_ms - tl.duration_ms) <= frame_ms + 1, (final_ms, tl.duration_ms)
    # both interior boundaries were APPLIED (real handles), and the log says so
    applied = [ln for ln in lines if "applied xfade_fade" in ln]
    assert len(applied) == 2, lines
    # a boundary segment landed in the content-addressed segment cache
    boundaries = list(project.segments_dir.glob("xfade_*.mp4"))
    assert len(boundaries) == 2, boundaries
    # and the render recorded a transitions sidecar (all applied)
    sidecar = out.with_suffix(".transitions.json")
    data = json.loads(sidecar.read_text(encoding="utf-8"))["transitions"]
    assert [t["applied"] for t in data] == [True, True]
    assert data[0]["boundary"] == "S1->S2"


@ffmpeg
def test_applied_xfade_boundary_cache_reused_and_type_change_rerenders_only_boundary(tmp_path):
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "缓存", git_init=False)
    tl = _applied_timeline(project, xfade="xfade_fade")
    render_timeline(project, tl, target="final")

    base_before = {p.name: p.stat().st_mtime_ns
                   for p in project.segments_dir.glob("*.mp4") if not p.name.startswith("xfade_")}
    xfade_before = {p.name: p.stat().st_mtime_ns
                    for p in project.segments_dir.glob("xfade_*.mp4")}
    assert len(xfade_before) == 2

    # rebuild identical (force past the content-key skip): every cache is reused,
    # nothing re-encodes (mtime preserved) — boundary segments included.
    render_timeline(project, tl, target="final", force=True)
    xfade_after = {p.name: p.stat().st_mtime_ns
                   for p in project.segments_dir.glob("xfade_*.mp4")}
    assert xfade_after == xfade_before, "boundary segment cache must be reused verbatim"

    # change ONLY the transition type → base segments reused, boundary re-rendered.
    tl2 = _applied_timeline(project, xfade="xfade_circleopen")
    render_timeline(project, tl2, target="final", force=True)
    base_after = {p.name: p.stat().st_mtime_ns
                  for p in project.segments_dir.glob("*.mp4") if not p.name.startswith("xfade_")}
    for name, mtime in base_before.items():
        assert base_after.get(name) == mtime, f"base segment {name} must NOT re-render"
    xfade_new = set(p.name for p in project.segments_dir.glob("xfade_*.mp4"))
    assert xfade_new - set(xfade_before), "a new transition type mints new boundary segments"


@ffmpeg
def test_applied_xfade_audio_is_continuous_across_the_boundary(tmp_path):
    """No gap / no drop to silence across the acrossfade at the cut."""
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "音频", git_init=False)
    tl = _applied_timeline(project)
    out = render_timeline(project, tl, target="final")

    # window centred on the S1->S2 cut (1000ms), ±180ms around the boundary
    win = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.82", "-to", "1.18",
         "-i", str(out), "-af", "silencedetect=noise=-45dB:d=0.05,astats=metadata=1",
         "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    # no silence period detected in the boundary window (the acrossfade never
    # opens a gap) and overall RMS is finite (audio present throughout)
    assert "silence_start" not in win, win
    assert "RMS level dB: -inf" not in win, win


@ffmpeg
def test_handle_less_boundary_degrades_to_dip_with_a_named_warning(tmp_path):
    """Card/kenburns-style sources are exactly window-length (no spare) and have
    no in-point → the requested xfade honestly degrades to dip-to-black and the
    boundary is named in a build warning."""
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "降级", git_init=False)
    clips = []
    for i in range(2):
        src = project.imports_dir / f"exact{i}.mp4"
        _gen_source(src, seconds=1.0, freq=440 + i * 110)  # exactly the window, no handle
        clips.append(VideoClip(
            shot=f"S{i+1}", take="t", source=project.relpath(src),
            start_ms=i * 1000, duration_ms=1000,  # source_in_ms defaults to 0 → no head handle
            transition_out=(TransitionSpec(type="xfade_fade", duration_ms=300) if i == 0 else None),
        ))
    tl = Timeline(fps=FPS, width=W, height=H, duration_ms=2000,
                  tracks=TimelineTracks(video=clips))

    lines, log = _capturing_log()
    out = render_timeline(project, tl, target="final", log=log)

    warn = [ln for ln in lines if "no handles; used dip-to-black" in ln]
    assert len(warn) == 1 and "S1->S2" in warn[0], lines
    # no boundary segment was created (dip-to-black is baked per-segment)
    assert not list(project.segments_dir.glob("xfade_*.mp4"))
    # total still exact and the transitions sidecar records the degrade
    assert abs(_probe_ms(out) - tl.duration_ms) <= 1000.0 / FPS + 1
    data = json.loads(out.with_suffix(".transitions.json").read_text())["transitions"]
    assert data[0]["applied"] is False and data[0]["reason"]


@ffmpeg
def test_look_preset_renders_and_rekeys_final_intensity_zero_is_none(tmp_path):
    from manju.media.render import final_content_key, render_timeline

    project = Project.create(tmp_path / "外观", git_init=False)
    src = project.imports_dir / "one.mp4"
    _gen_source(src, seconds=1.0, freq=440)
    tl = Timeline(fps=FPS, width=W, height=H, duration_ms=1000,
                  tracks=TimelineTracks(video=[
                      VideoClip(shot="S1", take="t", source=project.relpath(src),
                                start_ms=0, duration_ms=1000)]))

    key_none = final_content_key(project, tl, ass_file=None, target="final")
    out_none = render_timeline(project, tl, target="final")
    assert _probe_ms(out_none) > 0

    # intensity 0 == none: same content key, so the render is a reuse (no new final)
    write_yaml(project.root / "bible" / "style.yaml", {"look": {"preset": "vivid", "intensity": 0}})
    assert final_content_key(project, tl, ass_file=None, target="final") == key_none

    # an active look re-keys and renders a NEW final that probes OK
    write_yaml(project.root / "bible" / "style.yaml", {"look": {"preset": "vivid", "intensity": 0.8}})
    key_vivid = final_content_key(project, tl, ass_file=None, target="final")
    assert key_vivid != key_none
    out_vivid = render_timeline(project, tl, target="final")
    assert out_vivid != out_none and _probe_ms(out_vivid) > 0
    # the proxy gets the look too (same chain, renders fine)
    proxy = render_timeline(project, tl, target="proxy")
    assert _probe_ms(proxy) > 0


@ffmpeg
def test_idempotent_build_with_a_look_set(tmp_path):
    """Two renders over identical inputs WITH a look → one final (content-key
    skip), matching FIX-A idempotency."""
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "幂等", git_init=False)
    src = project.imports_dir / "one.mp4"
    _gen_source(src, seconds=1.0, freq=440)
    write_yaml(project.root / "bible" / "style.yaml", {"look": {"preset": "film", "intensity": 0.6}})
    tl = Timeline(fps=FPS, width=W, height=H, duration_ms=1000,
                  tracks=TimelineTracks(video=[
                      VideoClip(shot="S1", take="t", source=project.relpath(src),
                                start_ms=0, duration_ms=1000)]))

    first = render_timeline(project, tl, target="final")
    second = render_timeline(project, tl, target="final")
    assert first == second, "identical inputs + look must reuse the same final"
    assert len(list(project.final_dir.glob("final_v*.mp4"))) == 1
