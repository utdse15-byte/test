"""CLI surface tests (§11) — every read command supports ``--json``; the new
``propose`` / ``auto`` / ``serve-mcp`` commands and the run-ledger wiring.

These drive the real Typer app through ``typer.testing.CliRunner`` against a
freshly scaffolded project (``tests/conftest.py`` fixtures), cwd-anchored via
``monkeypatch.chdir``. No ffmpeg is needed except for the single, ffmpeg-gated
``redo`` case (real generation through the fallback chain). Kept well under 15s.
"""

from __future__ import annotations

import json
import shutil

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.events import tail_events

runner = CliRunner()

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """cwd-anchor a fresh project so ``_project()`` discovers it from cwd."""
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


# --------------------------------------------------------------- --json reads


def test_status_json_has_shots_total_and_run_log(in_project):
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "shots_total" in data
    assert data["shots_total"] == 0
    # §6/§8.3: the run-ledger snapshot is always present (best-effort).
    assert "run_log" in data
    assert set(data["run_log"]) >= {"runs", "total_cost", "currency"}
    assert data["run_log"]["runs"] == 0


def test_check_json_ok(in_project):
    result = runner.invoke(app, ["check", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True


def test_select_json_selects_take_and_logs_event(in_project, add_shot, make_take):
    add_shot(in_project, "S001")
    take = make_take(in_project, "S001", "sha256:whatever")

    result = runner.invoke(app, ["select", "S001", take.name, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == {"shot": "S001", "take": take.name, "ok": True}

    # the collaboration log gained a select entry (§10)
    actions = [(e.get("actor"), e.get("action")) for e in tail_events(in_project.root, 50)]
    assert any(a == "select" for _, a in actions)


def test_lock_json_returns_hash_and_tamper_fails_check(in_project, add_shot):
    add_shot(in_project, "S001")

    result = runner.invoke(app, ["lock", "S001", "dialogue.text", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["shot"] == "S001" and data["field"] == "dialogue.text"
    assert data["hash"].startswith("sha256:")

    # a healthy locked project passes check …
    assert runner.invoke(app, ["check"]).exit_code == 0

    # … tamper the sealed field on disk → the lock no longer verifies (§5)
    in_project.update_shot_raw(
        "S001", lambda d: d["dialogue"].__setitem__("text", "被篡改的台词。")
    )
    tampered = runner.invoke(app, ["check"])
    assert tampered.exit_code != 0


def test_import_json_copies_and_never_overwrites(in_project, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-media-bytes")

    first = runner.invoke(app, ["import", str(src), "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["imported"] == ["media/imports/clip.mp4"]

    # imports are sacred: a second import of the same name gets a _2 suffix (§3)
    second = runner.invoke(app, ["import", str(src), "--json"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["imported"] == ["media/imports/clip_2.mp4"]
    assert (in_project.imports_dir / "clip.mp4").exists()
    assert (in_project.imports_dir / "clip_2.mp4").exists()


def test_gc_json_reports_freed_bytes(in_project):
    result = runner.invoke(app, ["gc", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["freed_bytes"] >= 0
    assert data["hard"] is False


def test_doctor_json_has_checks_list(in_project):
    result = runner.invoke(app, ["doctor", "--json"])
    data = json.loads(result.output)
    assert isinstance(data["checks"], list) and data["checks"]
    assert "ok" in data
    ffmpeg = next(c for c in data["checks"] if c["name"] == "ffmpeg")
    # ffmpeg is installed in this environment (see module gate)
    assert ffmpeg["ok"] is _HAS_FFMPEG


def test_rebuild_index_json_reports_runs(in_project):
    result = runner.invoke(app, ["rebuild-index", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["runs"] >= 0
    assert data["pending_jobs"] >= 0
    assert "shots" in data


# --------------------------------------------------------------------- propose


def test_propose_creates_numbered_markdown(in_project):
    first = runner.invoke(app, ["propose", "第一个提案", "--body", "想改台词"])
    assert first.exit_code == 0, first.output
    props = sorted(in_project.proposals_dir.glob("*.md"))
    assert len(props) == 1 and props[0].name.startswith("0001_")
    text = props[0].read_text(encoding="utf-8")
    assert text == "# 第一个提案\n\n想改台词\n"

    second = runner.invoke(app, ["propose", "第二个提案", "--body", "再改一次", "--json"])
    assert second.exit_code == 0, second.output
    rel = json.loads(second.output)["path"]
    assert rel.startswith("proposals/0002_")
    names = sorted(p.name for p in in_project.proposals_dir.glob("*.md"))
    assert names[0].startswith("0001_") and names[1].startswith("0002_")


def test_propose_shares_counter_with_mcp_tool(in_project):
    """The CLI and the MCP `propose` tool draw from ONE numbering counter."""
    from manju.mcp import tools as mcp_tools

    # MCP first -> 0001, then CLI -> 0002 (same _next_proposal_number scheme)
    mcp_tools.call_tool(in_project, "propose", {"title": "MCP 提案", "body": "来自 MCP"})
    cli = runner.invoke(app, ["propose", "CLI 提案", "--body", "来自 CLI"])
    assert cli.exit_code == 0, cli.output

    numbers = sorted(int(p.name[:4]) for p in in_project.proposals_dir.glob("*.md"))
    assert numbers == [1, 2]


# ------------------------------------------------------------------------ auto


def test_auto_without_claude_cli_errors(in_project, monkeypatch):
    # §10: autopilot is a thin shell over `claude -p`; with no claude on PATH it
    # must fail loudly and never spawn a subprocess.
    monkeypatch.setattr("manju.cli.shutil.which", lambda name: None)
    result = runner.invoke(app, ["auto", "把片子做出来"])
    assert result.exit_code == 1
    assert "claude CLI not found" in (result.stdout + result.stderr)


# ------------------------------------------------------------ redo (ffmpeg e2e)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="redo runs real generation via the fallback chain")
def test_redo_json_generates_local_take(in_project, add_shot):
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["redo", "S001", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["shot"] == "S001"
    assert len(data["takes"]) >= 1  # kenburns has no ref image -> caption_card card

    # the local generation was recorded in the disposable run ledger (§8.3)
    from manju.runtime.state import RuntimeState

    with RuntimeState(in_project.root) as state:
        log = state.run_log()
    assert any(r["shot"] == "S001" and r["status"] == "succeeded" for r in log)
