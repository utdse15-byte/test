"""Round U: media/waveform.py — waveform PNGs and RMS level buckets, cached
under the disposable .manju/frames dir with the frames.py discipline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.media.ffmpeg import MediaError

pytestmark = [pytest.mark.ffmpeg,  # F43: the fast-loop deselector
              pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required",
)]


def _gen(args: list[str], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, str(dest)],
        check=True, capture_output=True, timeout=120,
    )
    return dest


@pytest.fixture
def loud_then_silent(tmp_project) -> str:
    """2s wav: 1s of full-scale sine, then 1s of silence."""
    dest = tmp_project.root / "media" / "imports" / "voice.wav"
    _gen(
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
         "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
         "-map", "[out]"],
        dest,
    )
    return "media/imports/voice.wav"


@pytest.fixture
def video_only(tmp_project) -> str:
    dest = tmp_project.root / "media" / "imports" / "mute.mp4"
    _gen(["-f", "lavfi", "-i", "testsrc=duration=0.5:size=64x64:rate=8", "-an"], dest)
    return "media/imports/mute.mp4"


def test_waveform_png_renders_and_caches(tmp_project, loud_then_silent):
    from manju.media.waveform import waveform_png

    p1 = waveform_png(tmp_project, loud_then_silent, width=320, height=48)
    assert p1.is_file() and p1.suffix == ".png" and p1.stat().st_size > 0
    assert p1.parent == tmp_project.root / ".manju" / "frames"
    stamp = p1.stat().st_mtime_ns
    p2 = waveform_png(tmp_project, loud_then_silent, width=320, height=48)
    assert p2 == p1 and p2.stat().st_mtime_ns == stamp  # content-addressed hit
    # different geometry = different cache entry
    p3 = waveform_png(tmp_project, loud_then_silent, width=640, height=48)
    assert p3 != p1


def test_rms_levels_shape_and_energy(tmp_project, loud_then_silent):
    from manju.media.waveform import rms_levels

    levels = rms_levels(tmp_project, loud_then_silent, buckets=20)
    assert len(levels) == 20
    assert all(0.0 <= v <= 1.0 for v in levels)
    loud = sum(levels[:8]) / 8   # sine second (edges excluded)
    quiet = sum(levels[12:]) / 8  # silent second
    assert loud > quiet + 0.3, (loud, quiet)

    # cached JSON round-trip returns the same values
    again = rms_levels(tmp_project, loud_then_silent, buckets=20)
    assert again == levels


def test_no_audio_raises_plainly(tmp_project, video_only):
    from manju.media.waveform import rms_levels, waveform_png

    with pytest.raises(MediaError, match="no audio stream"):
        waveform_png(tmp_project, video_only)
    with pytest.raises(MediaError, match="no audio stream"):
        rms_levels(tmp_project, video_only)


def test_gc_frames_sweeps_waveform_cache(tmp_project, loud_then_silent):
    from manju.media.frames import gc_frames
    from manju.media.waveform import rms_levels, waveform_png

    waveform_png(tmp_project, loud_then_silent, width=128, height=32)
    rms_levels(tmp_project, loud_then_silent, buckets=10)
    freed = gc_frames(tmp_project.root)
    assert freed > 0
    cache = tmp_project.root / ".manju" / "frames"
    assert not any(cache.iterdir())
