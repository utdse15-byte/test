"""AI_IDE_16 §6 — the deterministic animatic (build --target animatic).

Pinned: the animatic is a DERIVED artifact assembled from adopted/candidate
keyframes via ffmpeg_kenburns pan/hold over the EXISTING timeline audio; it is
NEVER a video take, NEVER selected, and is deletable + rebuildable (content
keyed). No paid video is planned/submitted.
"""

from __future__ import annotations

import shutil

import pytest

from manju.build import graph
from manju.build.graph import _animatic_shot_still, run_build

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _png(path, color="blue"):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)
    return path


def _image_take(project, shot_id, color, spec_hash):
    from manju.core.models import TakeSidecar

    tmp = project.root / f"_kf_{shot_id}_{spec_hash[-4:]}.png"
    _png(tmp, color)
    return project.register_take(
        shot_id, tmp, TakeSidecar(provider="test", spec_hash=spec_hash))


# ------------------------------------------------------------ still selection


def test_still_selection_prefers_adopted_keyframe(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    t1 = _image_take(tmp_project, "S001", "red", "sha256:a")
    _image_take(tmp_project, "S001", "green", "sha256:b")
    # no adoption yet → first candidate
    first = _animatic_shot_still(tmp_project, "S001")
    assert first is not None and first.suffix == ".png"
    # adopt the SECOND take → still selection follows the adoption
    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = t1.name
    tmp_project.save_shot(shot)
    assert _animatic_shot_still(tmp_project, "S001") == t1.media_path


def test_still_selection_none_without_keyframes(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _animatic_shot_still(tmp_project, "S001") is None


# ------------------------------------------------------------ target validation


def test_animatic_is_a_valid_target_and_plans_no_paid_video(
        tmp_project, add_shot, monkeypatch):
    """The animatic plan is voice-only — it never plans or prices paid video."""
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (99.0, "CNY"))
    add_shot(tmp_project, "S001")
    _image_take(tmp_project, "S001", "blue", "sha256:k")
    result = run_build(tmp_project, target="animatic", dry_run=True, actor="ai")
    assert result.ok is True
    # voice-only plan → no video estimate leaks in
    assert all(p.get("kind") == "voice" for p in result.plan)


# ------------------------------------------------------------ real render


@ffmpeg
def test_animatic_render_is_derived_never_a_take_or_selected(
        tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _image_take(tmp_project, "S001", "blue", "sha256:k")
    result = run_build(tmp_project, target="animatic", actor="ai", assume_yes=True)
    assert result.ok is True, result.errors
    # the output lives under renders/animatic/, NOT renders/final or a take dir
    assert result.render_path and "renders/animatic" in result.render_path
    out = tmp_project.root / result.render_path
    assert out.exists()
    # NEVER a video take: takes() still shows only the image keyframe
    from manju.qc import production as prod
    assert prod.video_takes(tmp_project, "S001") == []
    # NEVER selected
    assert tmp_project.load_shot("S001").status.selected_take is None
    # timeline.json was NOT written (in-memory only)
    assert not tmp_project.timeline_path.exists()


@ffmpeg
def test_animatic_is_deletable_and_rebuildable(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _image_take(tmp_project, "S001", "blue", "sha256:k")
    r1 = run_build(tmp_project, target="animatic", actor="ai", assume_yes=True)
    out1 = tmp_project.root / r1.render_path
    assert out1.exists()
    # content-key idempotent: a second run with no change reuses the artifact
    r2 = run_build(tmp_project, target="animatic", actor="ai", assume_yes=True)
    assert r2.render_path == r1.render_path
    # delete + rebuild reproduces an animatic from the same keyframes
    out1.unlink()
    out1.with_suffix(".key.json").unlink(missing_ok=True)
    r3 = run_build(tmp_project, target="animatic", actor="ai", assume_yes=True)
    assert (tmp_project.root / r3.render_path).exists()
