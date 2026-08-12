"""Offline closeout gates for the v5 product surface.

These tests exercise the current files + CLI + GUI boundary and deliberately
avoid provider transport, media generation, and paid operations.
"""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

from typer.testing import CliRunner

from manju.build.status import project_status
from manju.cli import app
from manju.gui.jobs import JobRunner
from manju.gui.state import build_state

runner = CliRunner()


def _current_surface_files(root: Path) -> list[Path]:
    # This document is the single explanatory record for the removed protocol.
    # The three zero-cost integration documents may name the external
    # OpenChatCut protocol, but remain documentation-only and do not restore a
    # Manju runtime surface. All other current product files stay token-free.
    excluded = {".git", "REPORTS", "docs/archive", "docs/ARCHITECTURE_BOUNDARIES.md"}
    external_integration_docs = {
        "docs/plans/MANJU_THREE_PROJECTS_ZERO_COST_EXECUTION_PLAN.md",
        "docs/plans/manju_zero_cost_execution_tasks.yaml",
        "docs/runbooks/MANJU_ZERO_COST_LOCAL_RUNBOOK.md",
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        text_suffixes = {"", ".md", ".txt", ".toml", ".yaml", ".yml", ".json",
                         ".py", ".js", ".css", ".html"}
        if ("__pycache__" in path.parts or any(part.endswith(".egg-info") for part in path.parts)
                or path.suffix.lower() not in text_suffixes):
            continue
        if (rel in external_integration_docs
                or any(rel == item or rel.startswith(item + "/") for item in excluded)):
            continue
        if rel.startswith(("DECISIONS.md", "PROGRESS.md")):
            continue
        if rel.startswith(("src/", "tests/", "skills/", "docs/", ".github/")) or path.name in {
            "README.md", "CLAUDE.md", "STATE.md", "pyproject.toml"
        }:
            files.append(path)
    return files


def test_current_surface_has_no_removed_protocol_tokens() -> None:
    root = Path(__file__).resolve().parents[1]
    protocol = "m" + "cp"
    media_extra = protocol + "-" + "video"
    old_command = "serve-" + protocol
    needles = (protocol, media_extra, old_command, "Tool" + "Policy",
               "Agent" + "Profile")
    offenders = []
    for path in _current_surface_files(root):
        text = path.read_text(encoding="utf-8").lower()
        if any(needle.lower() in text for needle in needles):
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == []
    assert importlib.util.find_spec("manju." + protocol) is None


def test_architecture_boundary_is_the_only_explanatory_exception() -> None:
    root = Path(__file__).resolve().parents[1]
    boundary = root / "docs" / "ARCHITECTURE_BOUNDARIES.md"
    text = boundary.read_text(encoding="utf-8").lower()
    assert "project files" in text and "json-capable cli" in text
    assert "local gui" in text and "removed" in text


def test_cli_and_gui_project_status_share_one_core(tmp_project) -> None:
    core = project_status(tmp_project)
    gui_runner = JobRunner(tmp_project.runtime_dir, project_id=str(tmp_project.root))
    try:
        view = build_state(tmp_project, gui_runner)
    finally:
        gui_runner.shutdown(timeout=2.0)
    assert view["project"]["name"] == core["project"]
    assert view["shots_by_state"] == core["shots_by_state"]
    assert view["next_step"] == core["next_step"]


def test_offline_gate_rejects_external_socket_attempts(tmp_project, monkeypatch) -> None:
    monkeypatch.chdir(tmp_project.root)
    real_connect = socket.socket.connect

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError(f"unexpected external network: {host}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    result = runner.invoke(app, ["build", "--dry-run", "--json"])
    assert result.exit_code in (0, 1), result.output
    assert json.loads(result.output)["action"] == "build"
