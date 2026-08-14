"""Product-polish trust tests for external finishing interchange.

A successful carrier export and a safely recoverable round trip are different
facts. These tests pin the product contract introduced in R1 wave 3:

* sidecar failure keeps the carrier, but is never silent;
* a missing T0 baseline can be inspected but never applied;
* carrier kinds never borrow another format's same-stem sidecar;
* the export center exposes finishing readiness as derived status.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from manju.build.exportstatus import deliverables_data
from manju.build.roundtrip import (
    BASELINE_MISSING,
    RoundtripBaselineError,
    RoundtripBaselineWarning,
    apply_roundtrip,
    inspect_roundtrip_baseline,
    plan_roundtrip,
)
from manju.core.models import (
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml
from manju.exporters.fcpxml import export_fcpxml
from manju.exporters.otio import export_otio


def _timeline(project) -> Timeline:
    """One contained video clip; no ffmpeg or probing is required."""
    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    source = project.root / "media" / "gen" / "S001" / "take_01.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fake-video")
    timeline = Timeline(
        meta=TimelineMeta(compiled_from="proof-fingerprint", mode="manual"),
        fps=24,
        width=1080,
        height=1920,
        duration_ms=2000,
        tracks=TimelineTracks(
            video=[
                VideoClip(
                    shot="S001",
                    take="take_01",
                    source=project.relpath(source),
                    start_ms=0,
                    duration_ms=2000,
                )
            ]
        ),
    )
    project.save_timeline(timeline)
    return timeline


def _make_baseline_write_fail(monkeypatch) -> None:
    import manju.build.roundtrip as roundtrip

    def refuse(*_args, **_kwargs):
        raise OSError("simulated read-only baseline directory")

    monkeypatch.setattr(roundtrip, "write_baseline", refuse)


def test_fcpxml_carrier_survives_baseline_failure_and_reports_warning(
    tmp_project, monkeypatch
):
    _make_baseline_write_fail(monkeypatch)
    warnings: list[str] = []

    carrier = export_fcpxml(
        tmp_project,
        _timeline(tmp_project),
        baseline_warnings=warnings,
    )

    assert carrier.exists() and carrier.stat().st_size > 0
    assert warnings and "回程基线创建失败" in warnings[0]
    status = inspect_roundtrip_baseline(tmp_project, carrier, kind="fcpxml")
    assert status["state"] == "baseline_missing"
    assert status["ready"] is False


def test_direct_exporter_call_emits_runtime_warning_on_baseline_failure(
    tmp_project, monkeypatch
):
    _make_baseline_write_fail(monkeypatch)

    with pytest.warns(RoundtripBaselineWarning, match="不能安全回收"):
        carrier = export_otio(tmp_project, _timeline(tmp_project))

    assert carrier.exists()


def test_missing_baseline_is_unverified_and_core_apply_refuses(
    tmp_project, tmp_path
):
    carrier = export_otio(tmp_project, _timeline(tmp_project))
    stray = tmp_path / "return-from-editor.otio"
    shutil.copy2(carrier, stray)

    plan = plan_roundtrip(tmp_project, stray)

    assert plan["baseline"] is None
    assert plan["baseline_state"] == "missing"
    assert plan["appliable"] is False
    assert plan["rows_reliable"] is False
    assert any(row["class"] == "baseline_missing" for row in plan["rows"])
    assert not any(row["class"] == "no_changes" for row in plan["rows"])
    with pytest.raises(RoundtripBaselineError) as exc:
        apply_roundtrip(tmp_project, plan, actor="human")
    assert exc.value.reason == BASELINE_MISSING


def test_fcpxml_never_borrows_same_stem_otio_baseline(tmp_project):
    timeline = _timeline(tmp_project)
    otio = export_otio(tmp_project, timeline)
    fcpxml = export_fcpxml(tmp_project, timeline)
    assert otio.stem == fcpxml.stem

    fcpxml_baseline = (
        tmp_project.exports_dir
        / "fcpxml"
        / ".baseline"
        / f"{fcpxml.stem}.json"
    )
    fcpxml_baseline.unlink()

    status = inspect_roundtrip_baseline(tmp_project, fcpxml, kind="fcpxml")
    assert status["state"] == "baseline_missing"
    assert status["ready"] is False


def test_export_status_exposes_finishing_readiness(tmp_project):
    timeline = _timeline(tmp_project)
    export_otio(tmp_project, timeline)
    fcpxml = export_fcpxml(tmp_project, timeline)
    (
        tmp_project.exports_dir
        / "fcpxml"
        / ".baseline"
        / f"{fcpxml.stem}.json"
    ).unlink()

    data = deliverables_data(tmp_project)
    carriers = {row["kind"]: row for row in data["finishing"]["carriers"]}

    assert data["finishing"]["total"] == 2
    assert data["finishing"]["ready"] == 1
    assert carriers["otio"]["state"] == "ready"
    assert carriers["otio"]["roundtrip_ready"] is True
    assert carriers["fcpxml"]["state"] == "baseline_missing"
    assert carriers["fcpxml"]["roundtrip_ready"] is False


def test_cli_json_surfaces_baseline_warning(tmp_project, monkeypatch):
    import json

    from typer.testing import CliRunner

    from manju.cli import app

    _timeline(tmp_project)
    _make_baseline_write_fail(monkeypatch)
    monkeypatch.chdir(tmp_project.root)

    result = CliRunner().invoke(app, ["export", "--fcpxml", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["outputs"]["fcpxml"].endswith(".fcpxml")
    assert payload["notes"]
    assert "不能安全回收" in payload["notes"][0]
