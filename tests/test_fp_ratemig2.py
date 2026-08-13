"""FP rational-edit-rate migration, STAGE 2 (R2): the OPT-IN rational build
spine through compiler → render → cache keys, with INT-PROJECT BYTE-IDENTITY as
the loop's product.

R1 landed the truth field + accessor (``ProjectConfig.edit_rate`` /
``Project.edit_rate``). R2 makes the rational path REAL: the compiler snaps a
1001-family project onto an exact CUMULATIVE-BOUNDARY frame grid (stamping each
clip's ``duration_frames``), the timeline echoes its exact ``{num, den}``, the
render feeds ffmpeg the native ``-r num/den`` / ``fps=num/den`` at every
injection point, and the cache keys gain a distinct rational component. Every
INT project stays BYTE-IDENTICAL — timelines, keys, and ffmpeg command lines —
which is what this suite proves alongside the rational correctness pins.

Discipline: the R2 build spine (compiler/render/graph) reads the exact rate
through the typed ``frame_rate`` resolvers, never the raw ``edit_rate`` field —
the R1 surface pin (``tests/test_fp_ratemig1.py``) still holds.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.hashing import cache_key, hash_file
from manju.core.models import (
    Dialogue,
    EditRate,
    ProjectConfig,
    ShotSpec,
    Timeline,
    TimelineTracks,
    TimelineRules,
    VideoClip,
)
from manju.core.timebase import Rate, Rounding, frames_to_ms, ms_to_frames
import manju.media.normalize as normalize_mod
import manju.media.render as render_mod
from manju.media.render import _segment_cache_key, final_content_key, render_timeline
import manju.timeline.compiler as compiler_mod
from manju.timeline.compiler import (
    CompileInput,
    ShotInput,
    _RationalFrameGrid,
    _resolve_duration_frames,
    compile_timeline,
    snap_to_frame_grid,
    snap_to_frame_grid_rational,
)

NTSC = Rate.from_fraction(24000, 1001)          # 23.976
NTSC30 = Rate.from_fraction(30000, 1001)        # 29.97
ONE_FRAME_MS = Fraction(1000 * 1001, 24000)     # exact 23.976 frame period ≈ 41.708 ms


def _shot(sid: str, *, duration="auto", take: int | None = 4000,
          voice: int | None = None) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=sid, duration=duration,
                      dialogue=Dialogue(speaker="linxia", text="雨夜")),
        take_name="take_01", take_source=f"gen/{sid}/take_01.mp4",
        take_duration_ms=take, voice_source=None, voice_duration_ms=voice,
    )


def _compile(config: ProjectConfig, shots: list[ShotInput]) -> Timeline:
    return compile_timeline(CompileInput(config=config, rules=TimelineRules(), shots=shots))


# =====================================================================
# 1. INT BYTE-IDENTITY — the loop's product
# =====================================================================

def test_int_compile_is_byte_identical_no_rational_keys():
    """A default int-fps compile carries NO rational surface: no edit_rate echo,
    no duration_frames, and it is deterministic — exactly today's timeline."""
    config = ProjectConfig(name="t", fps=24)
    tl = _compile(config, [_shot("S001"), _shot("S002", voice=2000)])
    j = tl.model_dump_json()
    for token in ("edit_rate", "rate_echo", "duration_frames"):
        assert token not in j, f"{token} leaked into an int timeline: {j}"
    assert tl.fps == 24 and tl.rate_echo is None
    assert all(v.duration_frames is None for v in tl.tracks.video)
    # durations are the historical snap values (audio-drives-picture + FIX-B)
    assert [(v.shot, v.duration_ms) for v in tl.tracks.video] == [
        ("S001", 4000), ("S002", 2500)
    ]
    # determinism: same input → byte-identical JSON
    assert _compile(ProjectConfig(name="t", fps=24),
                    [_shot("S001"), _shot("S002", voice=2000)]).model_dump_json() == j


def test_int_snap_and_resolve_are_untouched():
    """snap_to_frame_grid + _resolve_duration_ms keep their FIX-B pins verbatim
    (R2 adds a SIBLING, never edits the int path)."""
    assert snap_to_frame_grid(1200, 24) == 1208
    assert snap_to_frame_grid(2500, 24) == 2500
    assert snap_to_frame_grid(1, 24) == 42
    assert snap_to_frame_grid(1000, 30) == 1000
    from manju.timeline.compiler import _resolve_duration_ms
    rules = TimelineRules()
    assert _resolve_duration_ms(_shot("S", duration="auto", voice=100), rules, 24) == 1208
    assert _resolve_duration_ms(_shot("S", duration="auto", voice=20000), rules, 24) == 10000


def test_int_segment_cache_key_is_byte_identical(tmp_project):
    """The int segment key is EXACTLY the pre-R2 formula — the R2 rational
    component (rate_key) defaults None and appends nothing."""
    project = tmp_project
    src = project.root / "seg_src.bin"
    src.write_bytes(b"deterministic-segment-source-bytes")
    clip = VideoClip(shot="S001", take="take_01", source="seg_src.bin",
                     start_ms=0, duration_ms=4000)
    key = _segment_cache_key(project, clip, width=1080, height=1920, fps=24,
                             target="final", fade_in_ms=0, fade_out_ms=0)
    assert key == cache_key(hash_file(src), 1080, 1920, 24, 4000, "final", 0, 0)
    # explicitly passing rate_key=None is identical (the int default)
    assert key == _segment_cache_key(project, clip, width=1080, height=1920, fps=24,
                                     target="final", fade_in_ms=0, fade_out_ms=0,
                                     rate_key=None)


def test_rational_walker_never_entered_for_int_project(monkeypatch):
    """SPY pin: an int compile never even CONSTRUCTS the rational boundary
    walker — the int path is structurally untouched."""
    hits: list[int] = []
    real_init = _RationalFrameGrid.__init__

    def spy(self, *a, **k):
        hits.append(1)
        real_init(self, *a, **k)

    monkeypatch.setattr(_RationalFrameGrid, "__init__", spy)
    _compile(ProjectConfig(name="t", fps=24), [_shot("S001"), _shot("S002", voice=2000)])
    assert hits == [], "the rational walker was entered for an int project"
    # a rational compile DOES construct exactly one walker
    _compile(ProjectConfig(name="t", fps=24, edit_rate={"num": 24000, "den": 1001}),
             [_shot("S001")])
    assert hits == [1]


# =====================================================================
# 2. RATIONAL COMPILE — duration_frames + cumulative-boundary drift
# =====================================================================

# Hand-computed whole-frame counts at 24000/1001 (auto path, default clamp
# [1200, 10000] ms → [29, 240] frames; explicit path unclamped). Verified in the
# R2 report: ms_to_frames(raw, 24000/1001, ROUND_HALF_UP) then frame-space clamp.
_NTSC_AUTO_FRAMES = {
    2000: 48, 2600: 62, 4000: 96, 5005: 120,
    20000: 240,   # over max → clamped to max_frames = ms_to_frames(10000) = 240
    500: 29, 100: 29,  # under min → clamped to min_frames = ms_to_frames(1200) = 29
}


def test_rational_duration_frames_are_hand_computed():
    config = ProjectConfig(name="t", fps=24, edit_rate={"num": 24000, "den": 1001})
    for raw, exp_frames in _NTSC_AUTO_FRAMES.items():
        tl = _compile(config, [_shot("S001", take=raw)])
        vc = tl.tracks.video[0]
        assert vc.duration_frames == exp_frames, (raw, vc.duration_frames, exp_frames)
        # the clip's exact frame length is what timebase says for that rate
        assert vc.duration_frames == max(
            1, max(ms_to_frames(1200, NTSC), min(ms_to_frames(10000, NTSC),
                                                 ms_to_frames(raw, NTSC))))
    # explicit numeric duration wins, unclamped: 2.5s → 60 whole frames
    tl = _compile(config, [_shot("S001", duration=2.5)])
    assert tl.tracks.video[0].duration_frames == 60
    assert _resolve_duration_frames(_shot("S", duration=2.5), TimelineRules(), NTSC) == 60


def test_rational_min_max_clamp_is_in_frame_space():
    """Edge pins: the min/max shot clamp is applied to FRAMES (not ms then
    converted), so a raw below/above the window snaps to the clamp's frame
    count."""
    rules = TimelineRules()
    assert _resolve_duration_frames(_shot("S", take=100), rules, NTSC) == 29    # min
    assert _resolve_duration_frames(_shot("S", take=1200), rules, NTSC) == 29   # exactly min
    assert _resolve_duration_frames(_shot("S", take=10000), rules, NTSC) == 240  # exactly max
    assert _resolve_duration_frames(_shot("S", take=50000), rules, NTSC) == 240  # over max
    # the clamp bounds themselves are the frame images of the ms bounds
    assert ms_to_frames(1200, NTSC, Rounding.ROUND_HALF_UP) == 29
    assert ms_to_frames(10000, NTSC, Rounding.ROUND_HALF_UP) == 240


def test_rational_timeline_total_telescopes_from_frame_boundaries():
    """The compiled total_ms equals the exact rounded boundary of the cumulative
    frame count — i.e. the per-clip ms durations telescope to a single rounded
    total (that is WHY the cumulative error is bounded)."""
    config = ProjectConfig(name="t", fps=24, edit_rate={"num": 24000, "den": 1001})
    tl = _compile(config, [_shot("A", take=2000), _shot("B", take=4000),
                           _shot("C", take=2600), _shot("D", take=5005)])
    cum_frames = sum(v.duration_frames for v in tl.tracks.video)
    assert tl.duration_ms == frames_to_ms(cum_frames, NTSC, Rounding.ROUND_HALF_UP)
    assert tl.duration_ms == sum(v.duration_ms for v in tl.tracks.video)


def test_snap_to_frame_grid_rational_cumulative_error_within_half_ms_every_prefix():
    """The rational sibling keeps the CUMULATIVE boundary error ≤ ½ ms at EVERY
    prefix, forever — proven over a long varied sequence."""
    frames = [48, 96, 62, 120, 29, 240, 55, 71, 33, 100] * 40  # 400 clips
    durs = snap_to_frame_grid_rational(frames, NTSC)
    assert len(durs) == len(frames)
    cum_frames = 0
    cum_ms = 0
    for f, d in zip(frames, durs):
        cum_frames += f
        cum_ms += d
        exact = Fraction(cum_frames * 1000 * NTSC.denominator, NTSC.numerator)
        assert abs(cum_ms - exact) <= Fraction(1, 2), (cum_frames, cum_ms, float(exact))
    # per-clip ms wobbles ±1 around the true frame duration, but never drifts
    assert max(abs(d - float(f * ONE_FRAME_MS)) for f, d in zip(frames, durs)) <= 1.0 + 1e-9


def test_rational_two_hour_drift_bounded_vs_per_clip_independent_contrast():
    """2h of 23.976 clips: cumulative-boundary snapping keeps |error| ≤ ½ ms,
    while rounding each clip's frame duration to ms INDEPENDENTLY drifts far past
    a whole frame — the reason cumulative-boundary snapping exists.

    N=14401 clips of 12 frames each (~0.5 s, the pathological ½-ms-per-clip
    case). Numbers pinned from the R2 report."""
    F, N = 12, 14401
    frames = [F] * N
    total_frames = F * N
    true_end = Fraction(total_frames * 1000 * NTSC.denominator, NTSC.numerator)

    # cumulative-boundary (what the compiler does)
    boundary = snap_to_frame_grid_rational(frames, NTSC)
    boundary_end = sum(boundary)
    boundary_err = boundary_end - true_end
    assert abs(boundary_err) <= Fraction(1, 2)
    assert boundary_err == Fraction(1, 2)         # exact worst case here
    # and the worst PREFIX is also within ½ ms
    cum_f = cum_ms = 0
    worst = Fraction(0)
    for d in boundary:
        cum_f += F
        cum_ms += d
        worst = max(worst, abs(cum_ms - Fraction(cum_f * 1000 * 1001, 24000)))
    assert worst <= Fraction(1, 2)

    # per-clip INDEPENDENT rounding (the naive approach) drifts past a frame
    per = frames_to_ms(F, NTSC, Rounding.ROUND_HALF_UP)   # 501 (exact 500.5)
    assert per == 501
    independent_end = per * N
    independent_err = independent_end - true_end
    assert independent_err == Fraction(14401, 2)          # 7200.5 ms
    assert abs(independent_err) > ONE_FRAME_MS             # > one 23.976 frame
    assert independent_err / ONE_FRAME_MS > 172           # ~172.6 frames of drift


# =====================================================================
# 3. CACHE KEYS — int unchanged; rational distinct + deterministic
# =====================================================================

def test_segment_keys_int_unchanged_rational_distinct_and_deterministic(tmp_project):
    project = tmp_project
    src = project.root / "seg.bin"
    src.write_bytes(b"seg-bytes-xyz")
    clip = VideoClip(shot="S1", take="t", source="seg.bin", start_ms=0, duration_ms=2002)
    common = dict(width=1080, height=1920, fps=24, target="final",
                  fade_in_ms=0, fade_out_ms=0)
    int_key = _segment_cache_key(project, clip, **common)
    ntsc_key = _segment_cache_key(project, clip, rate_key="24000/1001", **common)
    ntsc30_key = _segment_cache_key(project, clip, rate_key="30000/1001", **common)
    # int key is the pre-R2 literal; rational keys differ from int and each other
    assert int_key == cache_key(hash_file(src), 1080, 1920, 24, 2002, "final", 0, 0)
    assert ntsc_key != int_key and ntsc30_key != int_key and ntsc_key != ntsc30_key
    # deterministic: same rate → same key
    assert ntsc_key == _segment_cache_key(project, clip, rate_key="24000/1001", **common)


@pytest.fixture
def _tiny_project(tmp_path):
    """A project with two tiny real source clips (identical content, so the
    segment cache collapses to one normalize) for content-key + ffmpeg-arg pins."""
    project = Project.create(tmp_path / "rate", git_init=False)
    return project


def _fake_take(project: Project, name: str) -> str:
    p = project.imports_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"deterministic-take-" + name.encode())
    return project.relpath(p)


def _timeline(project: Project, *, rational: bool) -> Timeline:
    a = _fake_take(project, "a.mp4")
    b = _fake_take(project, "b.mp4")
    echo = EditRate(num=24000, den=1001) if rational else None
    return Timeline(
        fps=24, width=1080, height=1920, duration_ms=4004 if rational else 4000,
        rate_echo=echo,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S1", take="t", source=a, start_ms=0,
                      duration_ms=2002 if rational else 2000,
                      duration_frames=48 if rational else None),
            VideoClip(shot="S2", take="t", source=b, start_ms=2002 if rational else 2000,
                      duration_ms=2002 if rational else 2000,
                      duration_frames=48 if rational else None),
        ]),
    )


def test_final_content_key_int_deterministic_rational_distinct(_tiny_project):
    project = _tiny_project
    int_tl = _timeline(project, rational=False)
    k_int = final_content_key(project, int_tl, ass_file=None, target="final")
    assert k_int == final_content_key(project, int_tl, ass_file=None, target="final")
    rat_tl = _timeline(project, rational=True)
    k_rat = final_content_key(project, rat_tl, ass_file=None, target="final")
    assert k_rat == final_content_key(project, rat_tl, ass_file=None, target="final")
    assert k_rat != k_int, "rational content key must differ from the int project"


def test_stray_duration_frames_on_int_timeline_is_ignored_in_key(_tiny_project):
    """A hand-edited INT timeline that carries a stray duration_frames keys
    IDENTICALLY to one without it — the field is ignored on the int grid."""
    project = _tiny_project
    clean = _timeline(project, rational=False)
    strayed = clean.model_copy(deep=True)
    for vc in strayed.tracks.video:
        vc.duration_frames = 999  # hand-edited nonsense on an int project
    assert (final_content_key(project, clean, ass_file=None, target="final")
            == final_content_key(project, strayed, ass_file=None, target="final"))


# =====================================================================
# 4. TIMELINE ECHO — round-trip; int has none; compat fixture untouched
# =====================================================================

def test_timeline_edit_rate_echo_round_trips_and_int_has_none():
    int_tl = Timeline(fps=24, width=1080, height=1920, duration_ms=4000)
    assert int_tl.rate_echo is None and "edit_rate" not in int_tl.model_dump_json()
    assert int_tl.frame_rate == Rate.from_fraction(24, 1)

    rat_tl = Timeline(fps=24, width=1080, height=1920, duration_ms=2002,
                      rate_echo=EditRate(num=24000, den=1001))
    dumped = rat_tl.model_dump()
    assert "rate_echo" not in dumped and dumped["edit_rate"] == {"num": 24000, "den": 1001}
    assert rat_tl.frame_rate == NTSC
    # hand-edited round-trip: an `edit_rate`-bearing timeline loads + re-emits it,
    # and one hand-edited WITHOUT touching edit_rate keeps it
    loaded = Timeline.model_validate({
        "fps": 24, "width": 1080, "height": 1920, "duration_ms": 2002,
        "edit_rate": {"num": 24000, "den": 1001},
        "tracks": {"video": [{"shot": "S1", "take": "t", "source": "a.mp4",
                              "start_ms": 0, "duration_ms": 2002,
                              "duration_frames": 48}]},
    })
    assert loaded.rate_echo == EditRate(num=24000, den=1001)
    assert loaded.frame_rate == NTSC
    assert loaded.tracks.video[0].duration_frames == 48
    assert loaded.model_dump()["edit_rate"] == {"num": 24000, "den": 1001}


def test_compat_timeline_fixture_still_loads_without_a_rate_echo(tmp_path):
    """Mirror of test_fp_compat: the pre-migration timeline.json fixture loads
    with NO rate_echo/duration_frames and its bytes are untouched by load."""
    fixture = (Path(__file__).resolve().parent / "fixtures" / "compat" / "timeline.json")
    dest = tmp_path / "timeline.json"
    dest.write_bytes(fixture.read_bytes())
    before = hashlib.sha256(dest.read_bytes()).hexdigest()
    tl = Timeline.model_validate_json(dest.read_bytes())
    assert tl.rate_echo is None and tl.frame_rate == Rate.from_fraction(tl.fps, 1)
    assert all(v.duration_frames is None for v in tl.tracks.video)
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == before


# =====================================================================
# 5. ffmpeg ARGS — rational carries -r/fps num/den at every injection point;
#    int command lines untouched. (Real renders; skipped without ffmpeg.)
# =====================================================================

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
ffmpeg_only = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe required")

_W, _H, _FPS = 192, 336, 24


def _gen_real_source(dest: Path, *, seconds: float = 1.0, freq: int = 330) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={_W}x{_H}:rate={_FPS}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(dest)],
        check=True, capture_output=True,
    )


def _real_timeline(project: Project, *, rational: bool) -> Timeline:
    s0 = project.imports_dir / "c0.mp4"
    s1 = project.imports_dir / "c1.mp4"
    _gen_real_source(s0, freq=330)
    _gen_real_source(s1, freq=550)
    echo = EditRate(num=24000, den=1001) if rational else None
    dur = 2002 if rational else 2000
    frames = 48 if rational else None
    return Timeline(
        fps=_FPS, width=_W, height=_H, duration_ms=2 * dur, rate_echo=echo,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S1", take="t", source=project.relpath(s0),
                      start_ms=0, duration_ms=dur, duration_frames=frames),
            VideoClip(shot="S2", take="t", source=project.relpath(s1),
                      start_ms=dur, duration_ms=dur, duration_frames=frames),
        ]),
    )


def _render_capturing_args(project: Project, tl: Timeline) -> tuple[Path, list[list[str]]]:
    """Render for real while recording every ffmpeg arg list (render + normalize
    both call the module-level run_ffmpeg)."""
    captured: list[list[str]] = []
    real_render = render_mod.run_ffmpeg
    real_norm = normalize_mod.run_ffmpeg

    def rec_render(args, **kw):
        captured.append([str(a) for a in args])
        return real_render(args, **kw)

    def rec_norm(args, **kw):
        captured.append([str(a) for a in args])
        return real_norm(args, **kw)

    render_mod.run_ffmpeg = rec_render
    normalize_mod.run_ffmpeg = rec_norm
    try:
        out = render_timeline(project, tl, target="final", log=lambda _m: None)
    finally:
        render_mod.run_ffmpeg = real_render
        normalize_mod.run_ffmpeg = real_norm
    return out, captured


@ffmpeg_only
def test_int_ffmpeg_command_lines_carry_no_rational_rate(tmp_path):
    """An int project's built ffmpeg args carry ONLY the int nominal fps — no
    `-r`, no `num/den` fraction anywhere — exactly as before R2."""
    project = Project.create(tmp_path / "int", git_init=False)
    out, captured = _render_capturing_args(project, _real_timeline(project, rational=False))
    flat = [" ".join(c) for c in captured]
    assert captured, "no ffmpeg invocations captured"
    assert not any("-r" in c for c in captured), "int render must not emit -r"
    assert not any("/1001" in s for s in flat), "int render must carry no rational fraction"
    assert any("fps=24," in s for s in flat), "int render still snaps to the int fps grid"
    # the int output is a whole-number rate
    assert _probe_r_frame_rate(out) in ("24/1", "24000/1000", "24")


@ffmpeg_only
def test_rational_ffmpeg_carries_native_rate_at_every_injection_point(tmp_path):
    """A 24000/1001 project feeds ffmpeg the NATIVE rational rate at every
    injection point the audit found: the segment normalize `fps=`, the final
    composition `fps=` node, and an explicit `-r num/den` on the final encode.
    The output's r_frame_rate is exactly 24000/1001."""
    project = Project.create(tmp_path / "ntsc", git_init=False)
    out, captured = _render_capturing_args(project, _real_timeline(project, rational=True))
    flat = [" ".join(c) for c in captured]

    # (a) segment normalize encodes at the native rate (fps= node in its vf)
    assert any("fps=24000/1001" in s and "scale=" in s and "pad=" in s for s in flat), \
        "segment normalize did not carry fps=24000/1001"
    # (b) the final composition snaps onto the native rate
    assert any("fps=24000/1001,format=yuv420p[vout]" in s for s in flat), \
        "final composition fps= node did not carry the native rate"
    # (c) the final encode stamps the container rate explicitly: -r 24000/1001
    assert any(
        "-r" in c and "24000/1001" in c and c[c.index("-r") + 1] == "24000/1001"
        for c in captured
    ), "final encode did not carry -r 24000/1001"
    # end-to-end: the real output plays back at exactly 24000/1001
    assert _probe_r_frame_rate(out) == "24000/1001"


def _probe_r_frame_rate(path: Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()


@ffmpeg_only
def test_int_render_output_is_unchanged_whole_number_rate(tmp_path):
    """Byte-identity evidence at the OUTPUT level: an int render still produces a
    whole-number-rate final (the existing render suites are the pre-R2 golden)."""
    project = Project.create(tmp_path / "int2", git_init=False)
    out = render_timeline(project, _real_timeline(project, rational=False),
                          target="final", log=lambda _m: None)
    assert _probe_r_frame_rate(out) in ("24/1", "24000/1000", "24")
