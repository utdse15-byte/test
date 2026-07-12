"""FP loop J — task-oriented workflow navigation (roadmap §8.3, navigation only).

The WORKFLOWS table (src/manju/cli_workflows.py) is cli-side CODE, not a new
fact source — and this file is its honesty gate:

  1. Every command a workflow step (or see_also row) names must resolve in the
     LIVE typer registry — the SAME resolver mechanism test_fp_docs.py uses for
     the README command table, imported from there (not duplicated). An
     aspirational command fails RED.
  2. The `manju help-workflow` surface: list mode (title + when), detail mode
     (steps with whys), --json machine-readable forms, and a structured
     unknown-name error listing the valid workflows.

No schema id: the command's --json output is plain CLI JSON, covered by the
cli-json-surface document row in CONTRACTS.yaml — deliberately NOT a new
manju.*/vN registration.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manju.cli import app
from manju.cli_workflows import WORKFLOWS, detail_payload, iter_commands, list_payload

from tests.test_fp_docs import (
    _clean_tokens,
    _registry_view,
    _resolve_segment,
    _strip_placeholders,
)

runner = CliRunner()


# ------------------------------------------ table honesty (registry-enforced)


def test_every_workflow_command_resolves_in_live_registry():
    """The doc-validation mechanism applied to the WORKFLOWS table: every
    command string it names (steps AND see_also) exists in the typer app."""
    top_leaf, groups = _registry_view()
    failures: list[str] = []
    for wf_name, command in iter_commands():
        if not command.startswith("manju "):
            failures.append(f"[{wf_name}] {command!r} does not start with 'manju '")
            continue
        body = _strip_placeholders(command[len("manju "):])
        tokens = _clean_tokens(body)
        if not tokens:
            failures.append(f"[{wf_name}] {command!r} left no resolvable tokens")
            continue
        ok, _group, reason = _resolve_segment(tokens, None, top_leaf, groups)
        if not ok:
            failures.append(f"[{wf_name}] `{command}` -> {reason}")
    assert not failures, (
        "workflow step(s) name commands that do not exist in the live registry "
        "(fix the table, never invent a command):\n  " + "\n  ".join(failures)
    )


def test_workflow_floor_and_entry_shape():
    """At least 8 workflows; every entry carries a title, a one-line when, and
    >= 2 steps whose whys are non-empty."""
    assert len(WORKFLOWS) >= 8
    for name, wf in WORKFLOWS.items():
        assert wf["title"].strip(), name
        assert wf["when"].strip(), name
        assert len(wf["steps"]) >= 2, f"{name} needs at least 2 steps"
        for command, why in wf["steps"]:
            assert command.strip().startswith("manju "), (name, command)
            assert why.strip(), f"empty why in {name}: {command}"
        assert isinstance(wf["see_also"], list), name


def test_next_pointers_stay_inside_the_table():
    for name, wf in WORKFLOWS.items():
        nxt = wf["next"]
        assert nxt is None or nxt in WORKFLOWS, (name, nxt)
        assert nxt != name, f"{name} must not point at itself"


# ---------------------------------------------------------------- CLI surface


def test_list_mode_names_every_workflow(tmp_path, monkeypatch):
    """`manju help-workflow` (no name) lists every workflow with its when line
    — and needs NO project (navigation is global)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["help-workflow"])
    assert result.exit_code == 0, result.output
    for name in WORKFLOWS:
        assert name in result.output
    assert "何时 when" in result.output


def test_detail_mode_prints_steps_with_whys():
    result = runner.invoke(app, ["help-workflow", "qc-repair"])
    assert result.exit_code == 0, result.output
    assert "manju qc" in result.output
    assert "manju repair --auto" in result.output
    assert "append-only" in result.output          # a why actually renders
    assert "preview-final" in result.output        # next pointer
    assert "manju failures" in result.output       # see_also renders


def test_list_json_is_machine_readable():
    result = runner.invoke(app, ["help-workflow", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [w["name"] for w in data["workflows"]] == list(WORKFLOWS)
    for w in data["workflows"]:
        assert w["title"] and w["when"] and w["step_count"] >= 2
    assert data == list_payload()


def test_detail_json_is_machine_readable():
    result = runner.invoke(app, ["help-workflow", "recover", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == detail_payload("recover")
    assert data["name"] == "recover"
    assert [s["command"] for s in data["steps"]] == [
        c for c, _ in WORKFLOWS["recover"]["steps"]]
    assert all(s["why"].strip() for s in data["steps"])


def test_unknown_workflow_is_a_structured_error_listing_valid_names():
    result = runner.invoke(app, ["help-workflow", "no-such-flow"])
    assert result.exit_code != 0
    for name in WORKFLOWS:
        assert name in result.output  # the human error names every valid flow

    result = runner.invoke(app, ["help-workflow", "no-such-flow", "--json"])
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["code"] == "unknown_workflow"
    assert data["valid_workflows"] == sorted(WORKFLOWS)
    assert "no-such-flow" in data["error"]
