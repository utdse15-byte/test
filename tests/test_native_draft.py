"""Dual-path native draft exporters (§13 M1, decision 8) — structural
verification against the real pyJianYingDraft / pycapcut libraries; opening
in the pinned desktop apps is the part only a human can do (§14)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.models import Timeline
from manju.exporters.native_draft import (
    ExporterUnavailable,
    capcut_cli_available,
    capcut_cli_lint,
    export_capcut_native,
    export_jianying_native,
)

has_ffmpeg = shutil.which("ffmpeg") is not None
has_pyjyd = importlib.util.find_spec("pyJianYingDraft") is not None
has_pycapcut = importlib.util.find_spec("pycapcut") is not None


@pytest.fixture
def draft_project(tmp_project, add_shot):
    """Two real clips on a timeline with voice-free captions and music."""
    if not has_ffmpeg:
        pytest.skip("ffmpeg required")
    clips = []
    for i in range(2):
        clip = tmp_project.runtime_dir / f"c{i}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-shortest", "-pix_fmt", "yuv420p", str(clip)],
            check=True,
        )
        clips.append(clip)
    bgm = tmp_project.imports_dir / "bgm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=110:duration=3", str(bgm)],
        check=True,
    )
    from manju.providers.manual import register_manual_take

    for i, clip in enumerate(clips, start=1):
        sid = f"S00{i}"
        add_shot(tmp_project, sid, dialogue={"speaker": "linxia", "text": f"台词{i}:2036。"})
        take = register_manual_take(tmp_project, sid, clip)
        tmp_project.update_shot_raw(
            sid, lambda d, t=take.name: d.setdefault("status", {}).__setitem__("selected_take", t)
        )
    rules = tmp_project.load_rules()
    rules.music.source = "media/imports/bgm.wav"
    tmp_project.save_rules(rules)

    from manju.media.probe import probe_duration_ms
    from manju.timeline.compiler import build_timeline

    timeline, _, _ = build_timeline(tmp_project, probe_duration_ms)
    return tmp_project, timeline


@pytest.mark.skipif(not has_pyjyd, reason="pyJianYingDraft not installed")
def test_jianying_native_draft(draft_project):
    project, timeline = draft_project
    draft = export_jianying_native(project, timeline)
    assert draft.name == "draft_content.json" and draft.exists()
    data = json.loads(draft.read_text(encoding="utf-8"))
    assert data["duration"] == timeline.duration_ms * 1000  # µs
    track_types = [t.get("type") for t in data["tracks"]]
    assert "video" in track_types and "text" in track_types and "audio" in track_types
    video_track = next(t for t in data["tracks"] if t.get("type") == "video")
    assert len(video_track["segments"]) == 2
    # µs timeranges, contiguous
    seg0 = video_track["segments"][0]["target_timerange"]
    assert seg0["start"] == 0 and seg0["duration"] == timeline.tracks.video[0].duration_ms * 1000
    # caption text present
    assert "2036" in draft.read_text(encoding="utf-8")


@pytest.mark.skipif(not has_pycapcut, reason="pycapcut not installed")
def test_capcut_native_draft(draft_project):
    project, timeline = draft_project
    draft = export_capcut_native(project, timeline)
    assert draft.exists()
    data = json.loads(draft.read_text(encoding="utf-8"))
    assert data["duration"] == timeline.duration_ms * 1000
    assert any(t.get("type") == "video" for t in data["tracks"])


def test_unavailable_lib_is_actionable(monkeypatch, tmp_project):
    """Adapter wall: a missing library degrades with instructions, not a crash."""
    import sys

    import manju.exporters.native_draft as nd

    # None in sys.modules makes any import of the name raise ImportError —
    # the same mechanism importlib.import_module consults.
    monkeypatch.setitem(sys.modules, "pyJianYingDraft", None)
    with pytest.raises(ExporterUnavailable) as exc:
        nd.export_jianying_native(tmp_project, Timeline())
    assert "pip install" in str(exc.value)


def test_capcut_cli_wall():
    """capcut-cli is absent in this environment: lint returns None (fallback
    to our own lint), never raises. If someone installs it, list-of-str."""
    result = capcut_cli_lint(Path("/nonexistent"))
    if capcut_cli_available():
        assert isinstance(result, list)
    else:
        assert result is None


@pytest.mark.skipif(not has_pyjyd, reason="pyJianYingDraft not installed")
def test_build_graph_exports_native(draft_project):
    """`manju build --target exports` carries the native draft when the
    project's export profiles ask for jianying."""
    project, _ = draft_project
    config = project.load_config()
    config.export_profiles = ["srt", "otio", "jianying", "capcut"]
    project.save_config(config)

    from manju.build.graph import run_build

    result = run_build(project, target="exports")
    assert result.ok, result.errors
    assert "jianying_native" in result.exports
    if has_pycapcut:
        assert "capcut" in result.exports
