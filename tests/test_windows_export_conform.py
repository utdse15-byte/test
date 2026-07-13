"""W3 §5.1 — every `manju export` ships its conform-loss document.

Before this wave the conform-loss machinery was library+tests only
(conform.py:43-44 said so explicitly): `manju export` wrote NLE artifacts and
told the user NOTHING about what the target format dropped. The plan's rule is
禁止静默丢失 — so the CLI now derives one content-addressed
``manju.conform-loss/v1`` doc per produced target into ``reports/conform/``
(derived advisory output, never a build input; a derivation fault degrades to
a visible ⚠ note and never fails an export whose artifact already landed).

ffmpeg-free: the manual-mode timeline fabricator from test_export_center.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manju.cli import app
from manju.core.models import (
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml

runner = CliRunner()


def _manual_timeline(project) -> Timeline:
    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="take_01",
                      source="media/gen/S001/take_01.mp4", start_ms=0, duration_ms=2000)]),
    )
    project.save_timeline(tl)
    src = project.root / "media" / "gen" / "S001" / "take_01.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fakevideo")
    return tl


def test_export_emits_conform_loss_docs(tmp_project, monkeypatch):
    _manual_timeline(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["export", "--otio", "--srt", "--json", "--yes"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "conform" in payload
    assert set(payload["conform"]) >= {"otio", "srt"}

    for key, expected_target in (("otio", "otio"), ("srt", "srt_ass")):
        doc_path = tmp_project.root / payload["conform"][key]
        assert doc_path.exists(), payload["conform"]
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        assert doc["schema"] == "manju.conform-loss/v1"
        assert doc["target"] == expected_target
        for category in ("preserved", "approximated", "dropped", "unsupported"):
            assert isinstance(doc[category], list)


def test_export_human_output_names_the_conform_docs(tmp_project, monkeypatch):
    _manual_timeline(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["export", "--otio", "--yes"])
    assert result.exit_code == 0, result.output
    assert "conform[otio]:" in result.output


def test_conform_docs_are_derived_only_never_read_back(tmp_project, monkeypatch):
    """The docs land under reports/ (derived output). Deleting them must
    change NOTHING about a re-export — grep-pin style behavioral check."""
    import shutil

    _manual_timeline(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    first = runner.invoke(app, ["export", "--otio", "--json", "--yes"])
    assert first.exit_code == 0
    shutil.rmtree(tmp_project.root / "reports" / "conform")
    second = runner.invoke(app, ["export", "--otio", "--json", "--yes"])
    assert second.exit_code == 0
    p1 = json.loads(first.output)
    p2 = json.loads(second.output)
    assert p1["outputs"] == p2["outputs"]
    assert p2["conform"], "the doc is re-derived, not remembered"