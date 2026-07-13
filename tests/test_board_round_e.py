"""Round E: the review board catches up with the engine (voice chips, render
verdicts, QC posters) and QC surfaces voice staleness."""

from __future__ import annotations

import shutil
import subprocess

import pytest

WAV = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
       b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


@pytest.fixture
def project_with_voice(tmp_project, add_shot):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required")
    from manju.providers.manual import register_manual_take

    clip = tmp_project.runtime_dir / "c.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词。"})
    take = register_manual_take(tmp_project, "S001", clip)
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    # a hand-dropped voice take (manual voice)
    tdir = tmp_project.takes_dir("S001")
    (tdir / "voice_take_01.wav").write_bytes(WAV)
    return tmp_project


@needs_ffmpeg
def test_board_shows_voice_chip_and_verdicts(project_with_voice):
    from manju.board.board import generate_board
    from manju.build.graph import run_build

    assert run_build(project_with_voice, target="final").ok
    html = generate_board(project_with_voice).read_text(encoding="utf-8")
    # UX audit F18 evolution: the chip now renders the GUI's Chinese state
    # vocabulary with the enum on the title attribute (one state, one name
    # across both review surfaces); the pin's intent — the voice chip appears
    # and carries the state — is unchanged.
    assert "配音 手动置入" in html
    assert 'title="manual"' in html
    assert "final: skip (content key matches)" in html


@needs_ffmpeg
def test_board_uses_qc_frame_poster(project_with_voice):
    from manju.board.board import generate_board
    from manju.build.graph import run_build

    assert run_build(project_with_voice, target="qc").ok  # extracts reports/frames
    assert (project_with_voice.reports_dir / "frames" / "S001.jpg").exists()
    html = generate_board(project_with_voice).read_text(encoding="utf-8")
    assert 'poster="reports/frames/S001.jpg"' in html


def test_qc_surfaces_voice_staleness(tmp_project, add_shot):
    from manju.core.models import VoiceTakeSidecar
    from manju.qc.checks import run_qc

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "旧台词"})
    # synthesize-equivalent: register a voice take with the CURRENT hash...
    from manju.core.spec import compute_voice_hash

    src = tmp_project.runtime_dir / "v.wav"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(WAV)
    tmp_project.register_voice_take(
        "S001", src,
        VoiceTakeSidecar(provider="tts_x",
                         voice_hash=compute_voice_hash(
                             tmp_project.load_shot("S001"), tmp_project.load_bible())),
    )
    report = run_qc(tmp_project, None, extract_frames=False)
    assert not [i for i in report.items if "stale" in i.message and "voice" in i.message]

    # change the line -> voice goes stale -> QC info with the redo suggestion
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "新台词")
    )
    report = run_qc(tmp_project, None, extract_frames=False)
    hits = [i for i in report.items if "voice take predates" in i.message]
    assert hits and "manju voice S001" in hits[0].suggestion


def test_qc_notes_missing_voice(tmp_project, add_shot):
    from manju.qc.checks import run_qc

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    report = run_qc(tmp_project, None, extract_frames=False)
    assert any("no voice take yet" in i.message for i in report.items)
