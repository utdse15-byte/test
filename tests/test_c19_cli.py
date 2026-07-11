"""AI_IDE_19 — the CLI surface (thin wrappers over the derived modules)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()

FIXTURE = {
    "analyzer": "fixture",
    "shot_boundaries": [{"start_ms": 0, "end_ms": 1000}, {"start_ms": 1000, "end_ms": 2000}],
    "scene_boundaries": [{"start_ms": 0, "end_ms": 2000, "scene": "store"}],
    "roi_tracks": [{"subject_id": "x", "keyframes": [{"t_ms": 0, "cx": 0.5, "cy": 0.5}]}],
    "ocr": {"value": [{"t_ms": 0, "text": "hi"}], "confidence": 0.05},
}


def _proj(tmp_path):
    from manju.core.container import Project
    return Project.create(tmp_path / "p.manju", git_init=False)


def test_analyze_fixture_and_provider_gate(tmp_path, monkeypatch):
    proj = _proj(tmp_path)
    monkeypatch.chdir(proj.root)
    media = proj.root / "clip.mp4"
    media.write_bytes(b"bytes-A")
    fx = proj.root / "fx.json"
    fx.write_text(json.dumps(FIXTURE), encoding="utf-8")

    r = runner.invoke(app, ["analyze", str(media), "--fixture", str(fx), "--json", "--write"])
    assert r.exit_code == 0, r.output
    ev = json.loads(r.output)
    assert ev["schema"] == "manju.media-analysis/v1"
    assert "ocr" in ev["unknown_axes"]
    # the report materialised (deletable projection)
    assert list((proj.root / "reports" / "analysis").glob("*.json"))

    # the cloud slot refuses when unqualified
    g = runner.invoke(app, ["analyze", str(media), "--provider", "nope", "--json"])
    assert g.exit_code == 0
    assert json.loads(g.output)["refusal"] == "ANALYZER_NOT_QUALIFIED"


def test_reframe_and_tool_and_roughcut(tmp_path, monkeypatch):
    proj = _proj(tmp_path)
    monkeypatch.chdir(proj.root)
    report = proj.root / "an.json"
    report.write_text(json.dumps({"schema": "manju.media-analysis/v1",
                                  "evidence": {"roi_tracks": FIXTURE["roi_tracks"]}}),
                      encoding="utf-8")
    r = runner.invoke(app, ["reframe", str(report), "--source", "1920x1080",
                            "--target", "1080x1920", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["status"] == "ok"

    t = runner.invoke(app, ["tool", "crop", "--json"])
    assert t.exit_code == 0 and json.loads(t.output)["priced"] == 0.0
    bad = runner.invoke(app, ["tool", "nonsense", "--json"])
    assert bad.exit_code == 1

    align = proj.root / "a.align.json"
    align.write_text(json.dumps({"header": {"source_media_hash": "sha256:a"},
                                 "cues": [{"start_ms": 0, "end_ms": 300, "text": "呃"}]}),
                     encoding="utf-8")
    rc = runner.invoke(app, ["rough-cut", str(align), "--json"])
    assert rc.exit_code == 0
    assert json.loads(rc.output)["default_action"] == "annotate"
