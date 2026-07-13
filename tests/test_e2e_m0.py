"""M0 acceptance (§13): the 12-shot vertical sample, end to end.

12 local clips, 12 Chinese captions, 1 BGM, 1080x1920 → proxy + final +
captions.srt; reopening the project keeps full state; deleting a media file
is caught by QC/check; swapping one shot only re-renders the affected
segment; Chinese paths throughout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required for the M0 end-to-end sample",
)


@pytest.fixture(scope="module")
def sample_project(tmp_path_factory):
    from tests.fixtures.make_sample import make_sample_project

    dest = tmp_path_factory.mktemp("m0") / "雨夜便利店"
    root = make_sample_project(dest, shots=12, clip_seconds=1.0, with_bgm=True)
    from manju.core.container import Project

    return Project(root)


def _probe_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(float(out) * 1000)


def test_m0_full_build(sample_project):
    from manju.build.graph import run_build

    result = run_build(sample_project, target="final")
    assert result.ok, f"errors={result.errors} warnings={result.warnings}"
    assert result.render_path, "no final render produced"

    final = sample_project.resolve(result.render_path)
    assert final.exists() and final.stat().st_size > 0
    assert (sample_project.captions_dir / "captions.srt").exists()
    assert sample_project.timeline_path.exists()

    timeline = sample_project.load_timeline()
    assert len(timeline.tracks.video) == 12
    assert len(timeline.tracks.captions) >= 12
    assert timeline.tracks.music, "BGM track missing"
    assert timeline.width == 1080 and timeline.height == 1920

    # rendered duration matches the timeline within tolerance
    assert abs(_probe_ms(final) - timeline.duration_ms) < 1000

    # resolution of the final file matches the project (§13 acceptance)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(final)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "1080,1920"


def test_m0_reopen_state_intact(sample_project):
    """Reopen the project fresh: all state must come from text + media (§3)."""
    from manju.build.status import project_status
    from manju.core.container import Project

    reopened = Project(sample_project.root)
    info = project_status(reopened)
    assert info["shots_total"] == 12
    assert set(info["shots_by_state"].keys()) == {"manual"}
    assert info["timeline"]["exists"]


def test_m0_runtime_dir_disposable(sample_project):
    """Delete .manju entirely; everything still works (§3 discipline 3)."""
    shutil.rmtree(sample_project.runtime_dir, ignore_errors=True)
    from manju.core.check import run_check

    assert run_check(sample_project).ok


def test_m0_incremental_rerender(sample_project):
    """Swap one shot's take → only that segment is rebuilt (§7)."""
    import time

    from manju.build.graph import run_build
    from manju.providers.manual import register_manual_take

    # xdist-safe (the #34 L5 program): under --dist load this test can land on
    # a different worker from test_m0_full_build, with a FRESH module fixture
    # whose segment cache is empty. The subject here is the SECOND, incremental
    # build — so populate the cache ourselves when the full-build test didn't
    # run on this worker, instead of asserting another test's side effect.
    if not any(sample_project.segments_dir.glob("*.mp4")):
        first = run_build(sample_project, target="final")
        assert first.ok, f"priming build failed: errors={first.errors}"
    segments_before = {p.name: p.stat().st_mtime for p in sample_project.segments_dir.glob("*.mp4")}
    assert segments_before, "first build should have populated the segment cache"

    # a replacement clip, visually distinct
    tmp = sample_project.runtime_dir / "swap.mp4"
    tmp.parent.mkdir(exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(tmp)],
        check=True,
    )
    take = register_manual_take(sample_project, "S006", tmp)
    sample_project.update_shot_raw(
        "S006", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )

    time.sleep(0.05)
    result = run_build(sample_project, target="final")
    assert result.ok, result.errors

    segments_after = {p.name: p.stat().st_mtime for p in sample_project.segments_dir.glob("*.mp4")}
    reused = [n for n in segments_before if n in segments_after
              and segments_after[n] == segments_before[n]]
    # every previously cached segment must be reused untouched; the swap adds new ones
    assert len(reused) == len(segments_before), "segment cache was invalidated by an unrelated change"
    assert len(segments_after) > len(segments_before), "no new segment for the swapped take"

    # append-only finals: v1 and v2 both exist
    finals = sorted(sample_project.final_dir.glob("final_v*.mp4"))
    assert len(finals) >= 2


def test_m0_missing_media_detected(sample_project):
    """Deleting a selected take's media must be caught (§13 acceptance)."""
    from manju.core.check import run_check

    victim = sample_project.get_take("S003", sample_project.load_shot("S003").status.selected_take)
    assert victim and victim.media_path
    backup = victim.media_path.read_bytes()
    victim.media_path.unlink()
    try:
        report = run_check(sample_project)
        assert not report.ok
        assert any("S003" in e for e in report.errors)
    finally:
        victim.media_path.write_bytes(backup)
    assert run_check(sample_project).ok


def test_m0_pack_roundtrip(sample_project, tmp_path):
    """pack → unpack must produce an openable project (§3)."""
    import zipfile

    archive = tmp_path / "样片.manjupkg"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(sample_project.root.rglob("*")):
            if path.is_file():
                rel = path.relative_to(sample_project.root).as_posix()
                if rel.startswith((".manju/", ".git/")):
                    continue
                zf.write(path, rel)
    dest = tmp_path / "样片.manju"
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)

    from manju.core.check import run_check
    from manju.core.container import Project

    restored = Project(dest)
    assert run_check(restored).ok
    assert len(restored.shot_ids()) == 12
