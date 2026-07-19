"""media/masters integrity — a regression found by the media/ scan.

The OPTIONAL loudnorm sum master runs its ffmpeg inside `with atomic_output(ln)`
but only recorded the outcome in an `ok` flag (`ok = returncode == 0 and ...`)
instead of RAISING on failure. atomic_output finalizes (fsync + replace) on any
NORMAL block exit — its cleanup is exception-driven — and a non-zero ffmpeg exit
is not a Python exception, so a FAILED loudnorm still published a truncated
`raw_stem_sum.loudnorm.wav` into the masters dir, defeating atomic_output's
"a killed/failed ffmpeg can never leave a truncated file at a trusted path"
contract. (The sibling _render_bus / _mix_files correctly RAISE MastersError.)
"""

from __future__ import annotations

import shutil
import subprocess
import types
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.models import AudioClip, Timeline, TimelineRules, TimelineTracks

FFMPEG = shutil.which("ffmpeg")
pytestmark = [pytest.mark.ffmpeg,  # fast-loop deselector
              pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required for masters")]


def _sine(path: Path, freq: int, dur: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", "48000",
         str(path)], check=True)


def _audio_project(tmp_path: Path):
    project = Project.create(tmp_path / "P", git_init=False)
    _sine(project.root / "media" / "voice.wav", 300)
    _sine(project.root / "media" / "music.wav", 800)
    tl = Timeline(duration_ms=1000, tracks=TimelineTracks(
        voice=[AudioClip(source="media/voice.wav", start_ms=0, duration_ms=1000)],
        music=[AudioClip(source="media/music.wav", start_ms=0, duration_ms=1000,
                         gain_db=-6.0)],
    ))
    project.save_rules(TimelineRules(mode="manual"))
    project.save_timeline(tl)
    return project, tl


def test_failed_loudnorm_master_publishes_no_partial_file(tmp_path, monkeypatch):
    from manju.media import masters as M

    project, tl = _audio_project(tmp_path)
    real_run = subprocess.run

    def _run(cmd, *a, **k):
        # Fail ONLY the loudnorm ENCODE (its -af carries "loudnorm=I="); the bus
        # renders, the mix, and the loudness MEASUREMENT ("loudnorm=print_format")
        # all run for real. Mimic ffmpeg -y leaving a truncated output on failure.
        if any("loudnorm=I=" in str(x) for x in cmd):
            Path(cmd[-1]).write_bytes(b"RIFFpartial-truncated-loudnorm")
            return types.SimpleNamespace(returncode=1, stdout="", stderr="loudnorm boom")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(M.subprocess, "run", _run)

    index = M.render_masters(project, tl, loudness_target_lufs=-14.0)

    # the optional loudnorm master failed -> omitted from the index (unchanged)
    assert index["loudnorm_master"] is None
    # ...and NO truncated file is left behind (atomic_output's contract)
    ln = M.masters_dir(project) / "raw_stem_sum.loudnorm.wav"
    assert not ln.exists()
    # the rest of the masters still rendered normally
    assert index["artifacts"]
