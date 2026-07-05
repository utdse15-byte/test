"""Round C: `manju explain` — the build system justifies itself, read-only."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from manju.cli import app

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


@pytest.fixture
def built(tmp_project, add_shot):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required")
    from manju.build.graph import run_build
    from manju.providers.manual import register_manual_take

    clip = tmp_project.runtime_dir / "c.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词。"})
    take = register_manual_take(tmp_project, "S001", clip)
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    assert run_build(tmp_project, target="final").ok
    return tmp_project


@needs_ffmpeg
def test_explain_fresh_project_says_skip(built):
    from manju.build.explain import explain

    info = explain(built)
    [shot] = info["shots"]
    assert shot["video"]["state"] == "manual"
    assert info["timeline"]["verdict"] == "unchanged (fingerprint match)"
    assert info["renders"]["final"]["verdict"] == "skip (content key matches)"


@needs_ffmpeg
def test_explain_names_the_change(built):
    from manju.build.explain import explain

    # a picture-side edit: timeline unchanged (dialogue drives voice, action
    # is not a compile input) but the shot goes stale? action changes
    # spec_hash -> video STALE; take stays selected -> timeline unchanged.
    built.update_shot_raw(
        "S001", lambda d: d.setdefault("action", {}).__setitem__("main", "改了动作")
    )
    info = explain(built)
    [shot] = info["shots"]
    assert shot["video"]["state"] == "manual"  # manual takes never go stale (§4.3)

    # a caption-relevant edit: dialogue text changes the compiled captions ->
    # timeline fingerprint differs -> recompile verdict + final re-render
    built.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "新台词。")
    )
    info = explain(built)
    assert info["timeline"]["verdict"] == "recompile (inputs changed since last compile)"
    [shot] = info["shots"]
    assert shot["voice"]["state"] == "missing"  # new line has no voice take yet


@needs_ffmpeg
def test_explain_is_read_only(built):
    from manju.build.explain import explain

    finals_before = sorted(built.final_dir.glob("*"))
    timeline_before = built.timeline_path.read_bytes()
    explain(built)
    assert sorted(built.final_dir.glob("*")) == finals_before
    assert built.timeline_path.read_bytes() == timeline_before


@needs_ffmpeg
def test_explain_cli_human_and_json(built, monkeypatch):
    monkeypatch.chdir(built.root)
    result = CliRunner().invoke(app, ["explain"])
    assert result.exit_code == 0, result.output
    assert "时间线" in result.output and "final" in result.output

    result = CliRunner().invoke(app, ["explain", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert {"shots", "timeline", "renders"} <= set(payload)


def test_explain_empty_project_degrades(tmp_project, monkeypatch):
    """No shots: explain must still answer, not crash."""
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["explain", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["shots"] == []
    assert "cannot compile yet" in payload["timeline"]["verdict"]


def test_mcp_exposes_explain():
    from manju.mcp.tools import TOOL_DEFS

    names = [t["name"] for t in TOOL_DEFS]
    assert "explain" in names
    tool = next(t for t in TOOL_DEFS if t["name"] == "explain")
    assert "Read-only" in tool["description"]
