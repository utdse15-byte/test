"""战役④ field find: a SHORT dialogueless film cannot build (2026-07-31).

Matrix measured in the field (same machine, byte-equal shot specs):

- 2.0s × 1 silent  → ffmpeg exit 234: loudnorm 吃纯静音 → NaN → aac 拒收
- 1.4s × 2 silent  → same NaN failure (2.8s total)
- 1.4s × 2 audible → builds green
- 2.0s × 3 silent  → builds green (6s: loudnorm's integrated window works)
- 4.0s × 1 silent  → builds green

So the failing class is exactly SHORT + DIGITALLY SILENT — the owner's
very first hello-world film. Fix: at the final compose, when there are no
overlay audio tracks at all, the film is short, and the concatenated
audio измеримо digital silence, bypass loudnorm (normalizing silence is
the identity — the masters surface already speaks 该总线静音 for this
state). Probe doubt keeps loudnorm — UNKNOWN never guessed into a bypass.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


# ------------------------------------------------------ graph-level bypass


def _empty_timeline(duration_ms=2000):
    from manju.core.models import Timeline

    return Timeline.model_validate({
        "fps": 24, "width": 1080, "height": 1920,
        "duration_ms": duration_ms,
        "clips": [], "tracks": {},
    })


def test_final_graph_normally_carries_loudnorm(tmp_project):
    from manju.media.render import _build_audio_graph

    _, stmts, aout = _build_audio_graph(
        _empty_timeline(), tmp_project, target="final", total_s=2.0)
    assert any("loudnorm" in s for s in stmts)
    assert aout == "[aout]"


def test_final_graph_bypasses_loudnorm_for_silent_base(tmp_project):
    from manju.media.render import _build_audio_graph

    _, stmts, aout = _build_audio_graph(
        _empty_timeline(), tmp_project, target="final", total_s=2.0,
        silent_base=True)
    assert not any("loudnorm" in s for s in stmts)
    assert aout == "[base]"  # the pass-through label, like the proxy path


def test_proxy_graph_is_untouched_by_the_flag(tmp_project):
    from manju.media.render import _build_audio_graph

    _, stmts_a, aout_a = _build_audio_graph(
        _empty_timeline(), tmp_project, target="proxy", total_s=2.0)
    _, stmts_b, aout_b = _build_audio_graph(
        _empty_timeline(), tmp_project, target="proxy", total_s=2.0,
        silent_base=True)
    assert (stmts_a, aout_a) == (stmts_b, aout_b)


# ------------------------------------------------------ the field repro pin


@pytest.mark.ffmpeg
def test_a_two_second_silent_film_builds(tmp_path):
    from manju.core.container import Project
    from manju.core.yamlio import write_yaml

    project = Project.create(tmp_path / "hello", git_init=False)
    write_yaml(project.root / "bible" / "scenes.yaml", {"s": {"name": "场"}})
    write_yaml(project.shots_dir / "S001.yaml", {
        "id": "S001", "scene": "s", "duration": 2.0,
        "action": {"main": "店主的第一部片。", "emotion": "静"},
    })
    write_yaml(project.shots_dir / "index.yaml",
               {"order": ["S001"], "defaults": {}})
    proc = subprocess.run(
        [sys.executable, "-m", "manju.cli", "build"],
        cwd=project.root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert proc.returncode == 1
    select = subprocess.run(
        [sys.executable, "-m", "manju.cli", "select", "S001", "1"],
        cwd=project.root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert select.returncode == 0, select.stdout + select.stderr
    proc = subprocess.run(
        [sys.executable, "-m", "manju.cli", "build", "--gen", "off"],
        cwd=project.root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    finals = list((project.root / "renders" / "final").glob("final_v*.mp4"))
    assert finals, "no final produced"
