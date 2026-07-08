"""Round W (goal item 78): OTIO / JianYing exporters must refuse a clip whose
source escapes the project root — the SAME containment guard render.py's
``project.resolve(clip.source)`` already enforces for the rendered film.

Before this fix: ``jianying._abs_path`` caught the ``project.resolve``
failure and silently fell back to the RAW (possibly absolute/outside-project)
source string; ``otio._external_reference`` never validated ``clip.source``
at all. Either way, a manual or corrupted ``timeline.json`` with an absolute
or ``../``-escaping source could get written straight into an exported draft
or .otio document — an export that is supposed to be self-contained ends up
referencing a file outside the project.
"""

from __future__ import annotations

import os

import pytest

from manju.core.container import ProjectError
from manju.core.models import AudioClip, Timeline, TimelineTracks, VideoClip
from manju.exporters.jianying import export_jianying
from manju.exporters.otio import export_otio

EXPORTERS = [export_otio, export_jianying]


def _video_timeline(source: str) -> Timeline:
    return Timeline(
        duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="take_01", source=source,
                             start_ms=0, duration_ms=2000)],
        ),
    )


def _audio_timeline(source: str) -> Timeline:
    return Timeline(
        duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="take_01",
                             source="media/gen/S001/take_01.mp4",
                             start_ms=0, duration_ms=2000)],
            music=[AudioClip(source=source, start_ms=0, duration_ms=2000)],
        ),
    )


@pytest.mark.parametrize("export_fn", EXPORTERS)
def test_export_refuses_absolute_video_source(tmp_project, tmp_path, export_fn):
    outside = tmp_path / "outside_video.mp4"
    outside.write_bytes(b"fake-mp4-bytes")
    with pytest.raises(ProjectError, match="S001/take_01"):
        export_fn(tmp_project, _video_timeline(str(outside)))


@pytest.mark.parametrize("export_fn", EXPORTERS)
def test_export_refuses_escaping_relative_video_source(tmp_project, tmp_path, export_fn):
    outside = tmp_path / "outside_video2.mp4"
    outside.write_bytes(b"fake-mp4-bytes")
    # a relative path that resolves outside the project root via ../
    escaping = os.path.relpath(str(outside), start=str(tmp_project.root))
    assert escaping.startswith("..")  # sanity: genuinely escapes
    with pytest.raises(ProjectError, match="S001/take_01"):
        export_fn(tmp_project, _video_timeline(escaping))


@pytest.mark.parametrize("export_fn", EXPORTERS)
def test_export_refuses_absolute_audio_source(tmp_project, tmp_path, export_fn):
    outside = tmp_path / "outside_bgm.wav"
    outside.write_bytes(b"fake-wav-bytes")
    with pytest.raises(ProjectError):
        export_fn(tmp_project, _audio_timeline(str(outside)))


def test_export_still_works_for_contained_sources(tmp_project):
    """No regression: a normal project-relative source keeps exporting fine
    on both exporters — the byte-identity guard for existing/healthy
    projects (nothing changes for a source that never escapes the root)."""
    (tmp_project.gen_dir / "S001").mkdir(parents=True, exist_ok=True)
    (tmp_project.gen_dir / "S001" / "take_01.mp4").write_bytes(b"fake")
    tl = _video_timeline("media/gen/S001/take_01.mp4")
    assert export_otio(tmp_project, tl).exists()
    assert export_jianying(tmp_project, tl).exists()
