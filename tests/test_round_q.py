"""Round Q: new QC checks (garbled captions, silent/clipping voice, timeline
conflicts) + repair operations (retime / extend / trim / croppad) + the
`manju repair --op` CLI surface.

Media is generated with ffmpeg lavfi, mirroring the existing suites. Every
ffmpeg-dependent test is gated so a toolless box still collects the file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.models import (
    AudioClip,
    CaptionLine,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from manju.qc.checks import (
    QCReport,
    _garbled_reason,
    _technical_garbled_captions,
    _technical_timeline_conflicts,
    _technical_voice_audio,
    _probe_audio_stats,
    run_qc,
)

ffmpeg_only = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]


# ------------------------------------------------------------- media helpers


def _wav(path: Path, kind: str, seconds: float = 1.2) -> Path:
    """kind: 'silent' | 'tone' (moderate) | 'clip' (over-driven)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "silent":
        src = ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={seconds}"]
        af: list[str] = []
    elif kind == "tone":
        src = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
        af = ["-af", "volume=-12dB"]
    elif kind == "clip":
        src = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
        af = ["-af", "volume=20dB"]
    else:  # pragma: no cover
        raise ValueError(kind)
    subprocess.run([*FF, *src, *af, "-t", str(seconds), str(path)], check=True)
    return path


def _clip_mp4(path: Path, w: int = 540, h: int = 960, seconds: float = 1.0,
              freq: int = 330) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [*FF, "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


def _vclip(shot: str, start: int, dur: int, *, trans: int | None = None) -> VideoClip:
    return VideoClip(
        shot=shot, take="take_01", source=f"media/gen/{shot}/take_01.mp4",
        start_ms=start, duration_ms=dur,
        transition_out=TransitionSpec(duration_ms=trans) if trans is not None else None,
    )


# =============================================================== garbled 乱码


def test_garbled_reason_heuristics():
    # firing cases
    assert _garbled_reason("字幕��") is not None            # U+FFFD
    assert _garbled_reason("你好世界".encode("utf-8").decode("latin-1")) is not None  # mojibake
    assert _garbled_reason("ab" + "".join(chr(c) for c in range(6)) + "cd") is not None  # control
    # non-firing: valid CJK / English / accented Latin / emoji
    assert _garbled_reason("凌晨三点,便利店的门铃响了。") is None
    assert _garbled_reason("Hello, world! It's fine.") is None
    assert _garbled_reason("Café déjà où l'été") is None
    assert _garbled_reason("字幕 🎬 好") is None
    assert _garbled_reason("") is None


def test_garbled_captions_fires_on_compiled_caption(tmp_project):
    tl = Timeline(tracks=TimelineTracks(captions=[
        CaptionLine(start_ms=0, end_ms=1000, text="正常字幕"),
        CaptionLine(start_ms=1000, end_ms=2000, text="损坏的��字幕"),
    ]))
    report = QCReport()
    _technical_garbled_captions(tmp_project, report, tl)
    errs = [i for i in report.items if i.level == "error" and "乱码" in i.message]
    assert len(errs) == 1
    assert "#1" in errs[0].message and errs[0].suggestion


def test_garbled_captions_clean_is_silent(tmp_project):
    tl = Timeline(tracks=TimelineTracks(captions=[
        CaptionLine(start_ms=0, end_ms=1000, text="凌晨三点"),
        CaptionLine(start_ms=1000, end_ms=2000, text="门铃响了"),
    ]))
    report = QCReport()
    _technical_garbled_captions(tmp_project, report, tl)
    assert not [i for i in report.items if "乱码" in i.message]


def test_garbled_manual_srt_fires(tmp_project):
    rules = tmp_project.load_rules()
    rules.captions.mode = "manual"
    tmp_project.save_rules(rules)
    srt = tmp_project.captions_dir / "captions.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    # a valid cue then a mojibake cue
    moji = "你好".encode("utf-8").decode("latin-1")
    srt.write_text(
        f"1\n00:00:00,000 --> 00:00:01,000\n正常\n\n"
        f"2\n00:00:01,000 --> 00:00:02,000\n{moji}\n",
        encoding="utf-8",
    )
    tl = Timeline(tracks=TimelineTracks())  # no compiled captions
    report = QCReport()
    _technical_garbled_captions(tmp_project, report, tl)
    assert [i for i in report.items if "captions.srt" in i.message and i.level == "error"]


# =============================================================== timeline conflicts


def test_conflict_overlapping_video_clips():
    tl = Timeline(tracks=TimelineTracks(video=[
        _vclip("S001", 0, 1000),
        _vclip("S002", 500, 1000),  # starts 500ms into S001 → 500ms overlap, no transition
    ]))
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    errs = [i for i in report.items if i.level == "error" and "overlap" in i.message]
    assert errs and "S002" in errs[0].subject


def test_conflict_transition_overlap_allowed():
    # 300ms overlap covered by a 300ms transition_out → NOT a conflict
    tl = Timeline(tracks=TimelineTracks(video=[
        _vclip("S001", 0, 1000, trans=300),
        _vclip("S002", 700, 1000),
    ]))
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    assert not [i for i in report.items if "overlap" in i.message]


def test_conflict_zero_duration_clip():
    tl = Timeline(tracks=TimelineTracks(video=[_vclip("S001", 0, 0)]))
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    assert [i for i in report.items if i.level == "error" and "non-positive" in i.message]


def test_conflict_caption_overlap_error_vs_warn():
    # 400ms overlap (> 120ms tol) → hard conflict error
    tl = Timeline(tracks=TimelineTracks(captions=[
        CaptionLine(start_ms=0, end_ms=1000, text="一"),
        CaptionLine(start_ms=600, end_ms=1600, text="二"),
    ]))
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    assert [i for i in report.items if i.level == "error" and "same line region" in i.message]


def test_conflict_clean_sequential_timeline_silent():
    tl = Timeline(tracks=TimelineTracks(
        video=[_vclip("S001", 0, 1000), _vclip("S002", 1000, 1000)],
        captions=[CaptionLine(start_ms=0, end_ms=900, text="一"),
                  CaptionLine(start_ms=1000, end_ms=1900, text="二")],
    ))
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    assert not [i for i in report.items if i.level == "error"]


# =============================================================== voice audio


@ffmpeg_only
def test_silent_voice_fires(tmp_project):
    _wav(tmp_project.imports_dir / "v_silent.wav", "silent")
    tl = Timeline(tracks=TimelineTracks(voice=[
        AudioClip(source="media/imports/v_silent.wav", start_ms=0, duration_ms=1200),
    ]))
    report = QCReport()
    _technical_voice_audio(tmp_project, report, tl, _probe_audio_stats)
    errs = [i for i in report.items if i.level == "error" and "silent" in i.message]
    assert errs and "TTS produced silence" in errs[0].message


@ffmpeg_only
def test_tone_voice_is_clean(tmp_project):
    _wav(tmp_project.imports_dir / "v_tone.wav", "tone")
    tl = Timeline(tracks=TimelineTracks(voice=[
        AudioClip(source="media/imports/v_tone.wav", start_ms=0, duration_ms=1200),
    ]))
    report = QCReport()
    _technical_voice_audio(tmp_project, report, tl, _probe_audio_stats)
    assert not [i for i in report.items if i.level in ("error", "warn")]


@ffmpeg_only
def test_clipping_voice_warns(tmp_project):
    _wav(tmp_project.imports_dir / "v_clip.wav", "clip")
    tl = Timeline(tracks=TimelineTracks(voice=[
        AudioClip(source="media/imports/v_clip.wav", start_ms=0, duration_ms=1200),
    ]))
    report = QCReport()
    _technical_voice_audio(tmp_project, report, tl, _probe_audio_stats)
    warns = [i for i in report.items if i.level == "warn" and "clipping" in i.message]
    assert warns and "dBFS" in warns[0].message
    assert not [i for i in report.items if i.level == "error"]


@ffmpeg_only
def test_voice_checks_wired_into_run_qc(tmp_project):
    """run_qc runs the voice pass (via its internal astats cache) and gates it
    on a timeline being present."""
    _wav(tmp_project.imports_dir / "v_silent.wav", "silent")
    tl = Timeline(tracks=TimelineTracks(voice=[
        AudioClip(source="media/imports/v_silent.wav", start_ms=0, duration_ms=1200),
    ]))
    report = run_qc(tmp_project, tl, extract_frames=False)
    assert [i for i in report.items if i.level == "error" and "silent" in i.message]
    assert not report.ok
    # gating: with no timeline the voice pass never runs (and never crashes)
    clean = run_qc(tmp_project, None, extract_frames=False)
    assert not [i for i in clean.items if "silent" in i.message]


# =============================================================== repair ops


@pytest.fixture
def repair_project(tmp_project):
    """tmp_project with one real 540x960/1.0s take selected on S001."""
    from manju.core.models import Dialogue, ShotSpec, ShotStatus, TakeSidecar

    src = tmp_project.runtime_dir / "src.mp4"
    _clip_mp4(src)
    take = tmp_project.register_take(
        "S001", src, TakeSidecar(provider="manual_import", spec_hash="manual"))
    shot = ShotSpec(id="S001", scene="convenience_store", characters=["linxia"],
                    duration="auto", dialogue=Dialogue(speaker="linxia", text="台词"),
                    status=ShotStatus(selected_take=take.name))
    tmp_project.save_shot(shot)
    return tmp_project, take.name


def _probe(path: Path):
    from manju.media.probe import probe
    return probe(path)


@ffmpeg_only
def test_retime_take(repair_project):
    from manju.media.repair_ops import retime_take

    project, take = repair_project
    new = retime_take(project, "S001", take, 0.9)
    assert new.name != take
    assert new.sidecar.provider == "repair"
    assert new.sidecar.params["op"] == "retime"
    assert new.sidecar.params["source_take"] == take
    # 900ms → snapped to 22 frames @24fps = 917ms
    assert abs(_probe(new.media_path).duration_ms - 917) <= 42
    # append-only: the source take file is untouched
    assert project.get_take("S001", take).media_path.exists()


@ffmpeg_only
@pytest.mark.parametrize("mode", ["freeze", "pad_black"])
def test_extend_take(repair_project, mode):
    from manju.media.repair_ops import extend_take

    project, take = repair_project
    new = extend_take(project, "S001", take, 500, mode=mode)
    assert new.sidecar.params == {"op": "extend", "source_take": take,
                                  "ms": 500, "mode": mode, "target_ms": 1500}
    assert abs(_probe(new.media_path).duration_ms - 1500) <= 42


@ffmpeg_only
def test_trim_take(repair_project):
    from manju.media.repair_ops import trim_take

    project, take = repair_project
    new = trim_take(project, "S001", take, 300)
    dur = _probe(new.media_path).duration_ms
    assert dur < 1000 and abs(dur - 708) <= 42


@ffmpeg_only
@pytest.mark.parametrize("mode", ["center_crop", "pad_blur"])
def test_crop_pad_take(repair_project, mode):
    from manju.media.repair_ops import crop_pad_take

    project, take = repair_project  # source is 540x960, project is 1080x1920
    new = crop_pad_take(project, "S001", take, mode=mode)
    info = _probe(new.media_path)
    assert (info.width, info.height) == (1080, 1920)
    assert new.sidecar.params["op"] == "croppad" and new.sidecar.params["mode"] == mode


@ffmpeg_only
def test_retime_extreme_factor_chains_atempo(repair_project):
    """factor 0.4 → tempo 2.5, outside atempo's [0.5,2.0]; the chain keeps it
    valid and the clip renders."""
    from manju.media.repair_ops import retime_take

    project, take = repair_project
    new = retime_take(project, "S001", take, 0.4)
    assert _probe(new.media_path).duration_ms < 700


# =============================================================== CLI surface


@ffmpeg_only
def test_cli_repair_op_creates_take(repair_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    project, take = repair_project
    monkeypatch.chdir(project.root)
    runner = CliRunner()

    res = runner.invoke(app, ["repair", "--op", "croppad", "--shot", "S001",
                              "--mode", "pad_blur", "--json"])
    assert res.exit_code == 0, res.output
    names = [t.name for t in project.takes("S001")]
    assert len(names) == 2  # source + repaired
    # source still present (append-only)
    assert project.get_take("S001", take).media_path.exists()


@ffmpeg_only
def test_cli_repair_op_extend_requires_ms(repair_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    project, take = repair_project
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    res = runner.invoke(app, ["repair", "--op", "extend", "--shot", "S001"])
    assert res.exit_code == 1  # ms defaults to 0 → guarded failure


def test_cli_repair_auto_path_unchanged(repair_project, monkeypatch):
    """No --op → the legacy --auto path still runs (needs repair_plan.yaml)."""
    from typer.testing import CliRunner

    from manju.cli import app

    project, take = repair_project
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    # no repair_plan.yaml yet → the old guard fires
    res = runner.invoke(app, ["repair", "--auto"])
    assert res.exit_code == 1
    assert "repair_plan.yaml" in res.output
