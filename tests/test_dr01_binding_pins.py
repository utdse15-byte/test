"""WP2/WP3 binding pins — regression pins for already-covered invariants.

pin1 byte-sensitivity: the final content key follows the take's BYTES (via the
ordered segment cache keys), so overwriting a selected take's media with
different bytes (same filename + duration) re-keys the final.
pin2 selection-sensitivity: flipping ``status.selected_take`` recompiles a
timeline that points at a different take → a different final key.
pin3 single-resolution: OTIO + JianYing exports both point every video clip at
exactly ``project.resolve(clip.source)`` — no exporter re-derives a different
file.

These EXPECT GREEN today; they exist so a later change to the key/exporter
pipeline can never silently break byte/selection sensitivity or single
resolution. Real ffmpeg media (small lavfi clips), so the whole file is skipped
when ffmpeg/ffprobe are absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.models import ProbeInfo, TakeSidecar
from manju.media.probe import probe_duration_ms
from manju.media.render import final_content_key
from manju.timeline.compiler import build_timeline
from tests.fixtures.make_sample import make_sample_project

pytestmark = [pytest.mark.ffmpeg,  # F43: the fast-loop deselector
              pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)]

CLIP_SECONDS = 1.0


def _ffmpeg_clip(dest: Path, *, freq: int, seconds: float = CLIP_SECONDS) -> None:
    """A real, valid, fixed-duration clip whose bytes differ per ``freq``/pattern."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=540x960:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(dest)],
        check=True, capture_output=True,
    )


def _sample(tmp_path_factory, tag: str, shots: int = 2) -> Project:
    root = make_sample_project(
        tmp_path_factory.mktemp(tag) / "样片", shots=shots, clip_seconds=CLIP_SECONDS,
    )
    return Project(root)


def _final_key(project: Project) -> str:
    timeline, _tl_path, _ = build_timeline(project, probe_duration_ms)
    return final_content_key(project, timeline, ass_file=None, target="final")


# --------------------------------------------------------------------- pin1


def test_dr01_pin1_final_key_is_byte_sensitive(tmp_path_factory):
    """Overwriting a selected take's media with different bytes (same name,
    same duration) changes the final content key — the key vouches for the
    real footage, not just its path/duration."""
    project = _sample(tmp_path_factory, "pin1")
    key_before = _final_key(project)

    timeline, _tl_path, _ = build_timeline(project, probe_duration_ms)
    src = project.resolve(timeline.tracks.video[0].source)
    assert src.exists()
    dur_before = probe_duration_ms(src)
    _ffmpeg_clip(src, freq=1777)  # different bytes, same 1.0s duration
    assert probe_duration_ms(src) == dur_before  # duration unchanged

    key_after = _final_key(project)
    assert key_before != key_after, "final key ignored a change in the take's bytes"


# --------------------------------------------------------------------- pin2


def test_dr01_pin2_final_key_is_selection_sensitive(tmp_path_factory):
    """Two takes on a shot; flipping the selection recompiles a timeline that
    references the other take → the final key differs."""
    project = _sample(tmp_path_factory, "pin2")
    key_take1 = _final_key(project)

    # register a SECOND, byte-different take for S001 and select it
    second = project.runtime_dir / "second_take.mp4"
    _ffmpeg_clip(second, freq=1999)
    info2 = project.register_take(
        "S001", second,
        TakeSidecar(
            provider="manual_import", spec_hash="manual",
            probe=ProbeInfo(duration_ms=int(CLIP_SECONDS * 1000), width=540,
                            height=960, fps=24, has_audio=True),
        ),
    )
    project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", info2.name)
    )

    key_take2 = _final_key(project)
    assert key_take1 != key_take2, "final key ignored the selected-take change"


# --------------------------------------------------------------------- pin3


def test_dr01_pin3_single_source_resolution(tmp_path_factory):
    """OTIO and JianYing skeleton exports embed, for every video clip, exactly
    the file ``project.resolve(clip.source)`` yields — neither exporter
    re-derives a different file."""
    from manju.exporters.jianying import export_jianying
    from manju.exporters.otio import export_otio

    project = _sample(tmp_path_factory, "pin3")
    timeline, _tl_path, _ = build_timeline(project, probe_duration_ms)

    otio_doc = json.loads(export_otio(project, timeline).read_text(encoding="utf-8"))
    draft = json.loads(export_jianying(project, timeline).read_text(encoding="utf-8"))

    video_track = next(t for t in otio_doc["tracks"]["children"] if t["kind"] == "Video")
    otio_url_by_shot = {
        c["metadata"]["manju"]["shot"]: c["media_reference"]["target_url"]
        for c in video_track["children"]
    }
    jy_path_by_name = {
        m["material_name"]: m["path"] for m in draft["materials"]["videos"]
    }

    expected_files = set()
    for clip in timeline.tracks.video:
        resolved = project.resolve(clip.source)
        expected_files.add(resolved)

        # OTIO keeps the project-relative source; it must resolve to the same file
        otio_url = otio_url_by_shot[clip.shot]
        assert project.resolve(otio_url) == resolved, (
            f"OTIO re-derived a different source for {clip.shot}: {otio_url!r}"
        )
        # JianYing embeds the resolved absolute path
        jy_path = jy_path_by_name[f"{clip.shot}/{clip.take}"]
        assert jy_path == resolved.as_posix(), (
            f"JianYing re-derived a different source for {clip.shot}: {jy_path!r}"
        )

    # neither export invents a video file outside the compiled clip sources
    assert {project.resolve(u) for u in otio_url_by_shot.values()} <= expected_files
    assert {Path(p) for p in jy_path_by_name.values()} <= expected_files
