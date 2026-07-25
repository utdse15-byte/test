"""No baseline must not read as "checked, nothing moved".

`truth_moved` compares the export-time baseline's `compiled_from` against the
current timeline's. With no baseline sidecar it never gets a chance to run and
stays at its default `False` — and every row stays `state="ok"`, byte-identical
to a plan that WAS verified against the export point.

That is backwards for the case roundtrip exists to serve. A draft coming back
from JianYing/Resolve arrives wherever the editor saved it, without the sidecar
that lives in `exports/<kind>/.baseline/`, so the plan most in need of a caveat
was the one that displayed none.

The row data and `state` vocabulary are unchanged (agents branch on them); only
the human-facing header now distinguishes "unknown" from "False".
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.container import Project
from manju.core.models import (
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml

runner = CliRunner()


@pytest.fixture
def exported(tmp_project: Project, monkeypatch) -> Path:
    """A project with a real OTIO export (and therefore a real baseline).

    ffmpeg-free: the manual-mode timeline fabricator from
    test_windows_export_conform, so this runs in the fast loop too."""
    write_yaml(tmp_project.rules_path, TimelineRules(mode="manual").model_dump())
    clips = []
    for i, sid in enumerate(("S001", "S002")):
        clips.append(VideoClip(shot=sid, take="take_01",
                               source=f"media/gen/{sid}/take_01.mp4",
                               start_ms=i * 2000, duration_ms=2000))
        src = tmp_project.root / "media" / "gen" / sid / "take_01.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"fakevideo")
    tmp_project.save_timeline(Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(video=clips)))

    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["export", "--otio", "--yes"])
    assert res.exit_code == 0, res.stdout
    otio = list((tmp_project.root / "exports" / "otio").glob("*.otio"))
    assert otio, f"no OTIO exported: {res.stdout}"
    return otio[0]


def test_a_carrier_with_its_baseline_reports_a_real_verdict(exported: Path) -> None:
    res = runner.invoke(app, ["roundtrip", str(exported), "--json"])
    assert res.exit_code == 0, res.stdout
    plan = json.loads(res.stdout)
    assert plan["baseline"], "the exported carrier should have found its sidecar"
    assert plan["truth_moved"] in (True, False)


def test_a_carrier_without_its_baseline_says_unknown(
        exported: Path, tmp_path: Path) -> None:
    """Copying the draft elsewhere — what an editor round-trip actually does."""
    stray = tmp_path / "back-from-the-editor.otio"
    shutil.copy(exported, stray)

    res = runner.invoke(app, ["roundtrip", str(stray)])
    assert res.exit_code == 0, res.stdout
    assert "truth_moved=未知" in res.stdout or "unknown" in res.stdout
    assert "baseline" in res.stdout          # names what is missing
    assert "--apply" in res.stdout           # and what to do about it


def test_the_json_still_carries_the_raw_fields(
        exported: Path, tmp_path: Path) -> None:
    """Agents branch on these; the caveat is presentation, not a data change."""
    stray = tmp_path / "stray.otio"
    shutil.copy(exported, stray)
    res = runner.invoke(app, ["roundtrip", str(stray), "--json"])
    plan = json.loads(res.stdout)
    assert plan["baseline"] is None
    assert "truth_moved" in plan
    assert "rows" in plan


def test_the_warning_is_absent_when_the_baseline_is_there(exported: Path) -> None:
    """No nagging on the ordinary in-place round trip."""
    res = runner.invoke(app, ["roundtrip", str(exported)])
    assert res.exit_code == 0, res.stdout
    assert "没找到导出时的 baseline" not in res.stdout
