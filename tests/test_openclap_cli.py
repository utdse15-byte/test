"""OpenClap adapter — CLI sub-app (inspect / export / import-plan).

Verifies the three commands wire up, honor the repo's ``--json`` envelope
convention, and that a hard parse error exits non-zero with a structured error
object (WP acceptance: the three commands work; ``--json`` conventions match).
"""

from __future__ import annotations

import gzip
import json

import yaml
from typer.testing import CliRunner

from manju.cli import app
from manju.core.models import Timeline, TimelineTracks, VideoClip

runner = CliRunner()


def _write_clap(path, items):
    path.write_bytes(gzip.compress(
        yaml.safe_dump(items, allow_unicode=True, sort_keys=False).encode("utf-8"),
        mtime=0))
    return path


def _minimal_items():
    return [
        {"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": 0,
         "numberOfScenes": 0, "numberOfSegments": 1},
        {"id": "m", "title": "T", "width": 1080, "height": 1920, "durationInMs": 10},
        {"id": "v1", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 10,
         "assetUrl": "media/gen/S001/take_01.mp4"},
    ]


def test_cli_inspect_json(tmp_path):
    path = _write_clap(tmp_path / "ok.clap", _minimal_items())
    result = runner.invoke(app, ["openclap", "inspect", str(path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["header"]["format"] == "clap-0"
    assert data["actual_counts"]["segments"] == 1
    assert data["segment_categories"] == {"VIDEO": 1}


def test_cli_inspect_corrupt_emits_structured_error(tmp_path):
    items = _minimal_items()
    items[0]["numberOfSegments"] = 99  # mismatch -> fail closed
    path = _write_clap(tmp_path / "bad.clap", items)
    result = runner.invoke(app, ["openclap", "inspect", str(path), "--json"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["code"] == "segment_count_mismatch"
    assert data["diagnostics"] and any(d["severity"] == "error" for d in data["diagnostics"])


def test_cli_export_json(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    t = make_take(tmp_project, "S001", "sha256:a")
    tl = Timeline(duration_ms=1000, tracks=TimelineTracks(video=[
        VideoClip(shot="S001", take=t.name, source=tmp_project.relpath(t.media_path),
                  start_ms=0, duration_ms=1000)]))
    tmp_project.save_timeline(tl)
    monkeypatch.chdir(tmp_project.root)

    result = runner.invoke(app, ["openclap", "export", "--yes", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["output"] == f"exports/openclap/{tmp_project.load_config().name}.clap"
    assert data["counts"]["segments"] == 1
    assert (tmp_project.root / data["output"]).exists()


def test_cli_export_honors_final_export_gate(tmp_project, monkeypatch):
    """`.clap` is an outward-facing artifact: the default ask_before=final_export
    gate applies exactly like `manju export` / `manju package` (WP5)."""
    monkeypatch.chdir(tmp_project.root)
    assert "final_export" in (tmp_project.load_config().ask_before or [])
    result = runner.invoke(app, ["openclap", "export", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "waiting_user"


def test_cli_export_without_timeline_fails(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["openclap", "export", "--yes", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]


def test_cli_import_plan_json(tmp_path):
    path = _write_clap(tmp_path / "ok.clap", _minimal_items())
    result = runner.invoke(app, ["openclap", "import-plan", str(path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["schema"] == "manju.openclap-import-plan/v1"
    assert data["source_sha256"].startswith("sha256:")
    assert data["target_project"] is None
    assert any(op["op"] == "create_shot" for op in data["operations"])
