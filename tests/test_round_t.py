"""Round T: TAKE IN/OUT trimming (``set_inout`` repair op) + the frame-preview
service (media/frames.py) that backs the GUI scrubber and cover picker, plus
their two CLI surfaces (`manju repair --op inout`, `manju frames`).

Media is generated with ffmpeg lavfi, mirroring test_round_q / test_packaging.
Every ffmpeg-dependent test is gated so a toolless box still collects the file.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.models import Dialogue, ShotSpec, ShotStatus, TakeSidecar
from manju.timeline.compiler import snap_to_frame_grid

ffmpeg_only = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
# one frame at 24fps plus probe-rounding slack (same tolerance the packaging
# end-to-end suite uses for "within a frame of the ask").
FRAME_TOL_MS = 1000 / 24 + 5


# ------------------------------------------------------------- media helpers


def _clip_mp4(path: Path, w: int = 540, h: int = 960, seconds: float = 2.0,
              audio: bool = True) -> Path:
    """A testsrc2 clip whose picture changes over time (so a frame pulled at
    t>0 differs from the head) with an optional sine tone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [*FF, "-f", "lavfi", "-i",
            f"testsrc2=size={w}x{h}:rate=24:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                 "-shortest", str(path)]
    else:
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path)]
    subprocess.run(args, check=True)
    return path


def _probe(path: Path):
    from manju.media.probe import probe
    return probe(path)


def _head_frame_hash(path: Path, tmp: Path, at_s: float = 0.0) -> str:
    """Independent hash of the frame at ``at_s`` — the source-head fingerprint
    the frame-accuracy test compares the cropped take against."""
    out = tmp / f"hf_{at_s}.png"
    subprocess.run([*FF, "-ss", f"{at_s:.3f}", "-i", str(path),
                    "-frames:v", "1", str(out)], check=True)
    return hashlib.sha256(out.read_bytes()).hexdigest()


@pytest.fixture
def inout_project(tmp_project):
    """tmp_project with one real 540x960 / 2.0s take selected on S001."""
    src = tmp_project.runtime_dir / "src.mp4"
    _clip_mp4(src)
    take = tmp_project.register_take(
        "S001", src, TakeSidecar(provider="manual_import", spec_hash="manual"))
    shot = ShotSpec(id="S001", scene="convenience_store", characters=["linxia"],
                    duration="auto", dialogue=Dialogue(speaker="linxia", text="台词"),
                    status=ShotStatus(selected_take=take.name))
    tmp_project.save_shot(shot)
    return tmp_project, take


# =============================================================== set_inout op


@ffmpeg_only
def test_set_inout_frame_accurate_and_lineage(inout_project, tmp_path):
    from manju.media.repair_ops import set_inout_take

    project, take = inout_project
    fps = project.load_config().fps
    # mode="reencode" bakes the region into new bytes (the legacy TB path). The
    # raw-media frame-accuracy assertions below only hold for the re-encoded take;
    # the new DEFAULT (virtual) leaves the whole file behind a sidecar window and
    # is covered by test_virtual_trim.py.
    new = set_inout_take(project, "S001", take.name, 500, 1500, mode="reencode")

    # --- lineage (append-only new take with repair provenance) ---
    assert new.name != take.name
    assert new.sidecar.provider == "repair"
    assert new.sidecar.params["op"] == "set_inout"
    assert new.sidecar.params["mode"] == "reencode"
    assert new.sidecar.params["source_take"] == take.name
    assert new.sidecar.params["in_ms"] == 500
    assert new.sidecar.params["out_ms"] == 1500

    # --- frame accuracy: probe duration == snapped(out-in) within one frame ---
    target = snap_to_frame_grid(1500 - 500, fps)
    assert new.sidecar.params["target_ms"] == target
    got = _probe(new.media_path).duration_ms
    assert abs(got - target) <= FRAME_TOL_MS, (got, target)

    # --- content differs from the source head (it is the [500,1500) region) ---
    assert _head_frame_hash(new.media_path, tmp_path) != \
        _head_frame_hash(take.media_path, tmp_path)

    # --- a re-encoded trim carries NO window (no handles) ---
    assert new.sidecar.source_in_ms == 0 and new.sidecar.source_out_ms is None

    # --- audio survives the cut (kept in sync via the re-encode) ---
    assert _probe(new.media_path).has_audio is True


@ffmpeg_only
def test_set_inout_append_only_source_untouched(inout_project):
    from manju.media.repair_ops import set_inout_take

    project, take = inout_project
    before = take.media_path.read_bytes()
    new = set_inout_take(project, "S001", take.name, 0, 1000)
    # source bytes unchanged; both takes present
    assert project.get_take("S001", take.name).media_path.read_bytes() == before
    names = {t.name for t in project.takes("S001")}
    assert names == {take.name, new.name}


@ffmpeg_only
def test_set_inout_probe_validation(inout_project):
    """out>duration and a sub-frame window are clean one-line failures."""
    from manju.media.ffmpeg import MediaError
    from manju.media.repair_ops import set_inout_take

    project, take = inout_project  # source is 2000ms
    with pytest.raises(MediaError, match="exceeds source duration"):
        set_inout_take(project, "S001", take.name, 0, 9000)
    with pytest.raises(MediaError, match="shorter than one frame"):
        set_inout_take(project, "S001", take.name, 100, 110)


def test_set_inout_arg_validation_no_ffmpeg(tmp_project, make_take):
    """The in/out ordering guards fire before any probe (no ffmpeg needed)."""
    from manju.media.ffmpeg import MediaError
    from manju.media.repair_ops import set_inout_take

    take = make_take(tmp_project, "S001", "sha256:x")
    with pytest.raises(MediaError, match="in-ms must be >= 0"):
        set_inout_take(tmp_project, "S001", take.name, -1, 100)
    with pytest.raises(MediaError, match="must be greater than in-ms"):
        set_inout_take(tmp_project, "S001", take.name, 500, 500)
    with pytest.raises(MediaError, match="must be greater than in-ms"):
        set_inout_take(tmp_project, "S001", take.name, 500, 400)


# =============================================================== frame service


@ffmpeg_only
def test_extract_frame_caches_same_path_no_reencode(inout_project):
    from manju.media.frames import extract_frame

    project, take = inout_project
    rel = project.relpath(take.media_path)
    first = extract_frame(project, rel, 800)
    assert first.exists() and first.suffix == ".jpg"
    assert first.parent == project.runtime_dir / "frames"
    mtime = first.stat().st_mtime_ns
    second = extract_frame(project, rel, 800)
    assert second == first  # content-addressed hit
    assert second.stat().st_mtime_ns == mtime  # no re-encode


@ffmpeg_only
def test_extract_frame_distinct_at_ms_distinct_files(inout_project):
    from manju.media.frames import extract_frame

    project, take = inout_project
    rel = project.relpath(take.media_path)
    a = extract_frame(project, rel, 200)
    b = extract_frame(project, rel, 1200)
    assert a != b  # keyed on at_ms → different cache entries
    assert a.read_bytes() != b.read_bytes()  # and genuinely different frames


@ffmpeg_only
def test_extract_frame_clamps_past_end(inout_project):
    from manju.media.frames import extract_frame

    project, take = inout_project  # 2000ms source
    rel = project.relpath(take.media_path)
    out = extract_frame(project, rel, 999_999)  # far past the end
    assert out.exists() and out.stat().st_size > 0  # still a real frame


@ffmpeg_only
def test_extract_frame_past_end_requests_share_one_cache_entry(inout_project):
    """round-W #36: the cache key must be derived from the CLAMPED seek time,
    not the raw requested at_ms — two different too-late requests that both
    clamp to the same last frame must mint exactly ONE cache file, not two
    content-identical ones (a GUI scrubber/cover picker dragging past the end
    is exactly the high-frequency case that used to bloat the cache)."""
    from manju.media.frames import extract_frame, frames_cache_dir

    project, take = inout_project  # 2000ms source
    rel = project.relpath(take.media_path)
    before = set(frames_cache_dir(project.root).glob("*.jpg"))

    a = extract_frame(project, rel, 5_000)   # both clamp to the same last frame
    b = extract_frame(project, rel, 999_999)

    assert a == b  # SAME cache path — one entry, not two
    after = set(frames_cache_dir(project.root).glob("*.jpg"))
    assert after - before == {a}  # exactly one new file landed in the cache


@ffmpeg_only
def test_extract_frame_width_resize(inout_project):
    from manju.media.frames import extract_frame

    project, take = inout_project
    rel = project.relpath(take.media_path)
    out = extract_frame(project, rel, 400, width=120)
    assert _probe(out).width == 120  # exact requested width, aspect kept


def test_extract_frame_rejects_missing_source(tmp_project):
    from manju.media.frames import extract_frame
    from manju.media.ffmpeg import MediaError

    with pytest.raises(MediaError, match="frame source not found"):
        extract_frame(tmp_project, "media/gen/S001/nope.mp4", 0)


@ffmpeg_only
def test_frame_strip_count_spacing_and_width(inout_project):
    from manju.media.frames import frame_strip

    project, take = inout_project
    rel = project.relpath(take.media_path)
    strip = frame_strip(project, rel, count=8, width=160)
    assert len(strip) == 8
    assert all(p.exists() and p.stat().st_size > 0 for p in strip)
    assert all(_probe(p).width == 160 for p in strip)
    # evenly spaced across the clip → the ends are different frames
    assert strip[0].read_bytes() != strip[-1].read_bytes()


@ffmpeg_only
def test_frame_strip_is_cached(inout_project):
    from manju.media.frames import frame_strip

    project, take = inout_project
    rel = project.relpath(take.media_path)
    first = frame_strip(project, rel, count=6, width=160)
    mtimes = [p.stat().st_mtime_ns for p in first]
    second = frame_strip(project, rel, count=6, width=160)
    assert second == first
    assert [p.stat().st_mtime_ns for p in second] == mtimes  # no re-encode


# =============================================================== CLI surfaces


@ffmpeg_only
def test_cli_repair_op_inout(inout_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    project, take = inout_project
    monkeypatch.chdir(project.root)
    res = CliRunner().invoke(
        app, ["repair", "--op", "inout", "--shot", "S001",
               "--in-ms", "500", "--out-ms", "1500", "--json"])
    assert res.exit_code == 0, res.output
    names = [t.name for t in project.takes("S001")]
    assert len(names) == 2  # source + cropped
    assert project.get_take("S001", take.name).media_path.exists()  # append-only


def test_cli_repair_op_inout_requires_bounds(tmp_project, make_take, monkeypatch):
    """The missing-bounds guard fires before ffmpeg, so no real media needed."""
    from typer.testing import CliRunner

    from manju.cli import app

    take = make_take(tmp_project, "S001", "sha256:x")
    shot = ShotSpec(id="S001", scene="convenience_store", characters=["linxia"],
                    duration="auto", dialogue=Dialogue(speaker="linxia", text="台词"),
                    status=ShotStatus(selected_take=take.name))
    tmp_project.save_shot(shot)
    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(
        app, ["repair", "--op", "inout", "--shot", "S001", "--in-ms", "500"])
    assert res.exit_code == 1  # missing --out-ms → guarded failure
    assert "--in-ms and --out-ms" in res.output


@ffmpeg_only
def test_cli_frames_at_and_strip(inout_project, monkeypatch):
    import json

    from typer.testing import CliRunner

    from manju.cli import app

    project, take = inout_project
    monkeypatch.chdir(project.root)
    rel = project.relpath(take.media_path)
    runner = CliRunner()

    res = runner.invoke(app, ["frames", rel, "--at", "700", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["at_ms"] == 700
    assert project.resolve(data["frame"]).exists()

    res = runner.invoke(app, ["frames", rel, "--strip", "5", "--width", "160", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["count"] == 5 and len(data["strip"]) == 5
    assert all(project.resolve(p).exists() for p in data["strip"])


def test_cli_frames_missing_source_fails(tmp_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, ["frames", "media/gen/S001/nope.mp4", "--at", "0"])
    assert res.exit_code == 1
    assert "frames failed" in res.output
