"""Round-T — VIRTUAL trim: making `set_inout` leave real media HANDLES so the
handle-aware xfade family (TC) genuinely applies instead of always degrading to
dip-to-black.

Two halves, like test_transitions_looks.py:
  (a) pure / no ffmpeg — the additive TakeSidecar window is byte-stable at its
      whole-file defaults; the compiler folds the window into its fingerprint
      only when non-default; `duration: auto` is bounded by the window while
      rules still clamp; the clip's in-point is seeded from the window.
  (b) end-to-end (ffmpeg required) — a virtual trim hardlinks (or copies) the
      source and records the window; the visible result matches a re-encoded
      trim to the frame; and TWO virtually-trimmed takes with spare head+tail
      earn a REAL applied xfade (boundary segment + sidecar), where the same
      build with RE-ENCODED trims degrades to dip-to-black (the control).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.hashing import hash_value
from manju.core.models import (
    Dialogue,
    ProjectConfig,
    ShotSpec,
    ShotStatus,
    TakeSidecar,
    TimelineRules,
    TransitionSpec,
)
from manju.timeline.compiler import (
    CompileInput,
    ShotInput,
    compile_timeline,
    snap_to_frame_grid,
)

ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)

W, H, FPS = 192, 336, 24
FRAME_TOL_MS = 1000 / FPS + 5


# ============================================================ (a) pure / no ffmpeg


def test_take_sidecar_window_fields_additive_and_default_whole_file():
    """The window defaults describe the WHOLE file and never perturb an existing
    sidecar: a legacy dict validates to the defaults, exclude_none (the on-disk
    write path) drops the None out-point, and source_in_ms is the historical 0."""
    sc = TakeSidecar(provider="repair", spec_hash="manual")
    assert sc.source_in_ms == 0
    assert sc.source_out_ms is None
    legacy = TakeSidecar.model_validate({"provider": "wan", "spec_hash": "sha256:x"})
    assert legacy.source_in_ms == 0 and legacy.source_out_ms is None
    dumped = sc.model_dump(exclude_none=True)
    assert "source_out_ms" not in dumped  # None default is dropped on write
    assert dumped["source_in_ms"] == 0


def _si(shot_id: str = "S001", **over) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=shot_id, dialogue=Dialogue(speaker="linxia", text="")),
        take_name="take_01",
        take_source=f"media/gen/{shot_id}/take_01.mp4",
        take_duration_ms=over.pop("take_duration_ms", 1000),
        **over,
    )


def test_fingerprint_window_default_byte_stable_and_windowed_recompiles():
    """CONTROL: a whole-file take folds NOTHING into the fingerprint (byte-stable,
    the exact pre-window payload). A real window recompiles, and two different
    windows of the SAME length are still distinct compiles."""
    config = ProjectConfig(name="t")
    rules = TimelineRules()
    default = CompileInput(config=config, rules=rules, shots=[_si()])
    s = default.shots[0]
    expected = hash_value(
        {
            "fps": config.fps,
            "width": config.width,
            "height": config.height,
            # round U: transition_overrides folds in only when non-empty, so the
            # legacy payload strips the default (same stance as the window itself)
            "rules": {k: v for k, v in rules.model_dump().items()
                      if k != "transition_overrides"},
            "shots": [
                {
                    "id": s.shot.id,
                    "duration": s.shot.duration,
                    "dialogue": s.shot.dialogue.model_dump(),
                    "take": s.take_name,
                    "take_source": s.take_source,
                    "take_duration_ms": s.take_duration_ms,
                    "voice_source": s.voice_source,
                    "voice_duration_ms": s.voice_duration_ms,
                    "voice_timing": s.voice_timing,
                }
            ],
        }
    )
    assert default.fingerprint() == expected  # the pin: default adds nothing

    windowed = CompileInput(config=config, rules=rules,
                            shots=[_si(source_in_ms=400, source_out_ms=1400)])
    assert windowed.fingerprint() != expected

    other = CompileInput(config=config, rules=rules,
                         shots=[_si(source_in_ms=500, source_out_ms=1500)])
    assert other.fingerprint() != windowed.fingerprint()


def test_window_bounds_duration_auto_and_rules_still_clamp():
    """The window (out−in) is the source-material bound fed to `duration: auto`;
    the clip's in-point is seeded from it — and the timing rules still clamp the
    resulting length (a window shorter than min_shot is clamped UP)."""
    rules = TimelineRules()
    rules.timing.min_shot_ms = 500
    rules.timing.max_shot_ms = 10000
    inp = CompileInput(
        config=ProjectConfig(name="t"), rules=rules,
        shots=[_si(source_in_ms=400, source_out_ms=1400, take_duration_ms=1000)],
    )
    vc = compile_timeline(inp).tracks.video[0]
    assert vc.source_in_ms == 400
    assert vc.duration_ms == snap_to_frame_grid(1000, FPS)  # window drives length

    rules2 = TimelineRules()
    rules2.timing.min_shot_ms = 1500  # a rule longer than the window
    inp2 = CompileInput(
        config=ProjectConfig(name="t"), rules=rules2,
        shots=[_si(source_in_ms=400, source_out_ms=1400, take_duration_ms=1000)],
    )
    vc2 = compile_timeline(inp2).tracks.video[0]
    assert vc2.duration_ms == snap_to_frame_grid(1500, FPS)  # rule clamps up
    assert vc2.source_in_ms == 400  # window still seeds the in-point


# ============================================================ (b) end-to-end / ffmpeg


def _gen(dest: Path, *, seconds: float, freq: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(dest)],
        check=True, capture_output=True,
    )


def _probe(path: Path):
    from manju.media.probe import probe
    return probe(path)


def _probe_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()
    return int(round(float(out) * 1000))


def _avg_psnr(a: Path, b: Path) -> float:
    """Average PSNR between two equal-shape clips (dB; +inf when identical).
    Two normalized segments of the SAME source window read >>20dB; a wrong
    window (a different slice of the time-varying testsrc2) reads far lower."""
    # the psnr filter logs its summary ("... average:XX ...") at INFO level, so
    # error-only logging would swallow it — ask for info and grep the summary.
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(a), "-i", str(b),
         "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8",
    ).stderr
    m = re.search(r"average:([0-9.]+|inf)", err)
    assert m, err
    return float("inf") if m.group(1) == "inf" else float(m.group(1))


@pytest.fixture
def source_take(tmp_project):
    """tmp_project with one real 3.0s / 192x336 whole-file take on S001."""
    src = tmp_project.runtime_dir / "src.mp4"
    _gen(src, seconds=3.0, freq=330)
    take = tmp_project.register_take(
        "S001", src, TakeSidecar(provider="manual_import", spec_hash="manual"))
    return tmp_project, take


@ffmpeg
def test_virtual_trim_hardlinks_or_copies_and_records_window(source_take):
    from manju.media.repair_ops import set_inout_take

    project, take = source_take
    new = set_inout_take(project, "S001", take.name, 400, 1400)  # DEFAULT = virtual

    # lineage names the virtual mode + the window
    assert new.sidecar.params["op"] == "set_inout"
    assert new.sidecar.params["mode"] == "virtual"
    assert new.sidecar.params["in_ms"] == 400 and new.sidecar.params["out_ms"] == 1400
    # the window rides the sidecar; the take's declared duration is the window len
    assert new.sidecar.source_in_ms == 400
    assert new.sidecar.source_out_ms == 1400
    assert new.sidecar.probe.duration_ms == snap_to_frame_grid(1000, FPS)

    # the media is the WHOLE file (not a re-encoded region): full source duration,
    # and it is a HARDLINK (same inode) OR a byte-identical copy.
    assert abs(_probe(new.media_path).duration_ms - _probe(take.media_path).duration_ms) <= 2
    same_inode = os.stat(new.media_path).st_ino == os.stat(take.media_path).st_ino
    assert same_inode or new.media_path.read_bytes() == take.media_path.read_bytes()

    # append-only: source untouched, both takes present
    assert take.media_path.exists()
    assert {t.name for t in project.takes("S001")} == {take.name, new.name}


@ffmpeg
def test_virtual_trim_copy_fallback_when_hardlink_unavailable(source_take, monkeypatch):
    """When os.link raises (cross-device / unsupported fs) the virtual trim
    falls back to a byte-identical COPY — still the whole file, still windowed."""
    from manju.media import repair_ops
    from manju.media.repair_ops import set_inout_take

    def _no_link(*a, **k):
        raise OSError("cross-device link not permitted")

    monkeypatch.setattr(repair_ops.os, "link", _no_link)
    project, take = source_take
    new = set_inout_take(project, "S001", take.name, 400, 1400)  # virtual

    assert os.stat(new.media_path).st_ino != os.stat(take.media_path).st_ino  # a copy
    assert new.media_path.read_bytes() == take.media_path.read_bytes()  # identical bytes
    assert new.sidecar.source_in_ms == 400 and new.sidecar.source_out_ms == 1400


@ffmpeg
def test_virtual_and_reencode_visible_result_parity(source_take, tmp_path):
    """Rendered/normalized, a virtual trim and a re-encoded trim of the SAME
    [in, out) are the same picture: equal duration to the frame and a high-PSNR
    match (they show the same source window, just via a seek vs baked bytes)."""
    from manju.media.normalize import normalize_segment
    from manju.media.repair_ops import set_inout_take

    project, take = source_take
    reenc = set_inout_take(project, "S001", take.name, 400, 1400, mode="reencode")
    virt = set_inout_take(project, "S001", take.name, 400, 1400, mode="virtual")
    win = snap_to_frame_grid(1000, FPS)

    seg_r = tmp_path / "r.mp4"
    seg_v = tmp_path / "v.mp4"
    normalize_segment(reenc.media_path, seg_r, width=W, height=H, fps=FPS,
                      duration_ms=win, source_in_ms=reenc.sidecar.source_in_ms)
    normalize_segment(virt.media_path, seg_v, width=W, height=H, fps=FPS,
                      duration_ms=win, source_in_ms=virt.sidecar.source_in_ms)

    assert abs(_probe(seg_r).duration_ms - _probe(seg_v).duration_ms) <= FRAME_TOL_MS
    assert _avg_psnr(seg_v, seg_r) >= 20.0  # same window; a wrong slice reads far lower


# ---- the headline e2e: applied xfade from virtual trims, dip-to-black control ----


def _project_for(tmp_path: Path, name: str):
    from manju.core.container import Project

    project = Project.create(tmp_path / name, git_init=False)
    cfg = project.load_config()
    cfg.width, cfg.height, cfg.fps = W, H, FPS
    project.save_config(cfg)
    # write the minimal bible the shots reference
    from manju.core.yamlio import write_yaml
    write_yaml(project.root / "bible" / "scenes.yaml", {"sc": {"name": "s"}})
    write_yaml(project.root / "bible" / "characters.yaml", {"lin": {"name": "林"}})
    rules = project.load_rules()
    rules.transition_default = TransitionSpec(type="xfade_fade", duration_ms=300)
    rules.timing.min_shot_ms = 500  # keep the 1000ms window unclamped
    rules.captions.enabled = False
    project.save_rules(rules)
    return project


def _add_shot(project, sid: str, selected: str) -> None:
    shot = ShotSpec(id=sid, scene="sc", characters=["lin"], duration="auto",
                    dialogue=Dialogue(speaker="lin", text=""),
                    status=ShotStatus(selected_take=selected))
    project.save_shot(shot)
    idx = project.load_index()
    if sid not in idx.order:
        idx.order.append(sid)
        project.save_index(idx)


def _two_trimmed_shots(project, *, mode: str) -> None:
    """S001/S002 each: a 3s whole-file take, trimmed [400,1400) in `mode`, selected."""
    from manju.media.repair_ops import set_inout_take

    for i, sid in enumerate(("S001", "S002")):
        src = project.runtime_dir / f"src{i}.mp4"
        _gen(src, seconds=3.0, freq=330 + i * 220)
        take = project.register_take(
            sid, src, TakeSidecar(provider="manual_import", spec_hash="manual"))
        new = set_inout_take(project, sid, take.name, 400, 1400, mode=mode)
        _add_shot(project, sid, new.name)


@ffmpeg
def test_e2e_virtual_trims_apply_a_real_xfade(tmp_path):
    """Two imported takes, both VIRTUALLY trimmed with spare head+tail, xfade_fade
    requested → the cross-dissolve ACTUALLY applies: a boundary segment lands in
    the cache, the transitions sidecar records applied=True with no degrade
    warning, and the total duration is exact to the frame."""
    from manju.timeline.compiler import build_timeline
    from manju.media.probe import probe_duration_ms
    from manju.media.render import render_timeline

    project = _project_for(tmp_path, "虚拟")
    _two_trimmed_shots(project, mode="virtual")

    timeline, _, _ = build_timeline(project, probe_duration_ms)
    vids = timeline.tracks.video
    # the window plumbed all the way through: in-point seeded, length == window
    assert [v.source_in_ms for v in vids] == [400, 400]
    assert [v.duration_ms for v in vids] == [snap_to_frame_grid(1000, FPS)] * 2

    lines: list[str] = []
    out = render_timeline(project, timeline, target="final", log=lines.append)

    assert any("applied xfade_fade" in ln for ln in lines), lines
    assert not any("no handles" in ln for ln in lines), lines
    boundaries = list(project.segments_dir.glob("xfade_*.mp4"))
    assert len(boundaries) == 1, boundaries
    data = json.loads(out.with_suffix(".transitions.json").read_text(encoding="utf-8"))["transitions"]
    assert data[0]["boundary"] == "S001->S002"
    assert data[0]["applied"] is True
    assert abs(_probe_ms(out) - timeline.duration_ms) <= 1000.0 / FPS + 1


@ffmpeg
def test_e2e_reencoded_trims_degrade_to_dip_control(tmp_path):
    """CONTROL: the identical build with RE-ENCODED trims has no handles, so the
    xfade honestly degrades to dip-to-black — no boundary segment, a named
    warning, applied=False in the sidecar — proving the virtual trim is what
    earned the real transition above."""
    from manju.timeline.compiler import build_timeline
    from manju.media.probe import probe_duration_ms
    from manju.media.render import render_timeline

    project = _project_for(tmp_path, "重编码")
    _two_trimmed_shots(project, mode="reencode")

    timeline, _, _ = build_timeline(project, probe_duration_ms)
    assert [v.source_in_ms for v in timeline.tracks.video] == [0, 0]  # no head handles

    lines: list[str] = []
    out = render_timeline(project, timeline, target="final", log=lines.append)

    warn = [ln for ln in lines if "no handles; used dip-to-black" in ln]
    assert len(warn) == 1 and "S001->S002" in warn[0], lines
    assert not list(project.segments_dir.glob("xfade_*.mp4"))  # nothing applied
    data = json.loads(out.with_suffix(".transitions.json").read_text(encoding="utf-8"))["transitions"]
    assert data[0]["applied"] is False and data[0]["reason"]
    assert abs(_probe_ms(out) - timeline.duration_ms) <= 1000.0 / FPS + 1


@ffmpeg
def test_cli_repair_inout_defaults_virtual_and_reencode_flag(source_take, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    project, take = source_take
    # give S001 a selected take so the CLI can default --take to it
    _add_shot(project, "S001", take.name)
    monkeypatch.chdir(project.root)
    runner = CliRunner()

    # default (no --mode) → virtual: the new take carries a window
    res = runner.invoke(app, ["repair", "--op", "inout", "--shot", "S001",
                              "--in-ms", "400", "--out-ms", "1400", "--json"])
    assert res.exit_code == 0, res.output
    v = json.loads(res.output)["params"]
    assert v["mode"] == "virtual"
    vtake = project.get_take("S001", json.loads(res.output)["new_take"])
    assert vtake.sidecar.source_in_ms == 400 and vtake.sidecar.source_out_ms == 1400

    # --mode reencode → legacy path, no window on the take
    res2 = runner.invoke(app, ["repair", "--op", "inout", "--shot", "S001", "--take",
                               take.name, "--in-ms", "400", "--out-ms", "1400",
                               "--mode", "reencode", "--json"])
    assert res2.exit_code == 0, res2.output
    rtake = project.get_take("S001", json.loads(res2.output)["new_take"])
    assert json.loads(res2.output)["params"]["mode"] == "reencode"
    assert rtake.sidecar.source_in_ms == 0 and rtake.sidecar.source_out_ms is None
