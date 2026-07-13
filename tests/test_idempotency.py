"""FIX-A (build idempotency via final content key) and FIX-B (frame-rate
integrity): red-first acceptance tests for review defects A and B.

Empirical pre-fix facts these tests were written against: a 1200ms clip at
24fps is 28.8 frames — segments came out 29 frames / 1216ms, and the concat
gave the final r_frame_rate=143/6 instead of 24/1; every `manju build`
rendered a brand-new final_vN for identical inputs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.ffmpeg,  # F43: the fast-loop deselector
              pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)]


@pytest.fixture(scope="module")
def built_project(tmp_path_factory):
    """A 6-shot sample (min_shot_ms=1200 → deliberately NOT frame-aligned at
    24fps pre-fix) built to final once."""
    from manju.build.graph import run_build
    from manju.core.container import Project
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path_factory.mktemp("idem") / "样片", shots=6)
    project = Project(root)
    result = run_build(project, target="final")
    assert result.ok, result.errors
    return project


def _finals(project) -> list[Path]:
    return sorted(project.final_dir.glob("final_v*.mp4"))


# ------------------------------------------------------------------- FIX-B


def test_final_frame_rate_and_duration_qc(built_project):
    """The two new technical QC assertions must hold on a fresh final:
    r_frame_rate == project fps, |final - timeline| ≤ 1 frame (pre-fix this
    was RED: r_frame_rate=143/6)."""
    from manju.qc.checks import run_qc

    report = run_qc(built_project, built_project.load_timeline(), extract_frames=False)
    final_errors = [i for i in report.items if i.subject == "final" and i.level == "error"]
    assert not final_errors, [i.message for i in final_errors]


def test_final_r_frame_rate_is_project_fps(built_project):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1",
         str(_finals(built_project)[-1])],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "r_frame_rate=24/1", out


def test_clip_durations_snap_to_frame_grid(built_project):
    """FIX-B rounding rule: every clip duration is a whole number of frames
    (nearest frame count, min 1), re-expressed as nearest integer ms."""
    timeline = built_project.load_timeline()
    fps = timeline.fps
    for clip in timeline.tracks.video:
        frames = clip.duration_ms * fps / 1000.0
        # a frame-aligned duration re-rounds onto itself
        assert clip.duration_ms == round(round(frames) * 1000 / fps), (
            f"{clip.shot}: {clip.duration_ms}ms is {frames:.3f} frames at {fps}fps"
        )


# ------------------------------------------------------------------- FIX-A


def test_build_twice_is_idempotent(built_project):
    """Same inputs → same final content key → second build must NOT add a
    final (pre-fix this was RED: every build appended final_vN+1)."""
    from manju.build.graph import run_build

    before = _finals(built_project)
    result = run_build(built_project, target="final")
    assert result.ok, result.errors
    after = _finals(built_project)
    assert len(after) == len(before), (
        f"idempotency broken: {len(before)} finals -> {len(after)}"
    )
    # and the build still reports the (existing) final it stands behind
    assert result.render_path


def test_final_key_sidecar_written(built_project):
    final = _finals(built_project)[-1]
    sidecar = final.with_suffix(".key.json")
    assert sidecar.exists(), "each final_vN must carry a content-key sidecar"
    import json

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["final_key"].startswith("sha256:")
    assert data["target"] == "final"


def test_force_rerenders(built_project):
    from manju.build.graph import run_build

    before = len(_finals(built_project))
    result = run_build(built_project, target="final", force=True)
    assert result.ok, result.errors
    assert len(_finals(built_project)) == before + 1


def test_redo_selected_then_build_adds_exactly_one(built_project):
    """A genuinely different selected take changes the key → exactly one new
    final; a further unchanged build adds none."""
    from manju.build.graph import run_build
    from manju.providers.manual import register_manual_take

    swap = built_project.runtime_dir / "swap.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=990:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(swap)],
        check=True,
    )
    take = register_manual_take(built_project, "S002", swap)
    built_project.update_shot_raw(
        "S002", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    before = len(_finals(built_project))
    assert run_build(built_project, target="final").ok
    assert len(_finals(built_project)) == before + 1
    assert run_build(built_project, target="final").ok
    assert len(_finals(built_project)) == before + 1
