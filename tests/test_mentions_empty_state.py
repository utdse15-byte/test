"""A bare header over nothing reads as "broken", not as "clean".

`manju mentions` on a project with no @mentions printed exactly one line — the
title — and stopped. The user cannot tell from that whether the scan found
nothing, scanned nothing, or failed silently; and nothing on screen says what
an @mention even looks like, so there is no way forward either.

The empty state now names the scope it scanned and shows the syntax. The
non-empty output and the JSON are untouched.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manju.cli import app
from manju.core.yamlio import write_yaml

runner = CliRunner()

CHARS = {"linxia": {"name": "林夏", "appearance": "短发"}}


def _run(project, monkeypatch, *args):
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["mentions", *args])
    assert res.exit_code == 0, res.stdout
    return res.stdout


def test_no_mentions_says_so_instead_of_going_quiet(
        tmp_project, add_shot, monkeypatch) -> None:
    write_yaml(tmp_project.root / "bible" / "characters.yaml", CHARS)
    for sid in ("S001", "S002", "S003"):
        add_shot(tmp_project, sid)
    out = _run(tmp_project, monkeypatch)
    assert len(out.strip().splitlines()) > 1, f"still a bare header: {out!r}"
    assert "没有发现" in out
    assert "3 个镜头" in out                    # the scope it actually looked at
    assert "story" in out                       # ...and the other place it looks
    assert "不是错误" in out                    # this is a clean result


def test_the_empty_state_shows_the_syntax(tmp_project, add_shot, monkeypatch) -> None:
    """The dead end needs a way out: what to type, and what to run after."""
    write_yaml(tmp_project.root / "bible" / "characters.yaml", CHARS)
    add_shot(tmp_project, "S001")
    out = _run(tmp_project, monkeypatch)
    assert "@" in out
    assert "--apply" in out


def test_a_single_shot_scope_is_named_as_such(
        tmp_project, add_shot, monkeypatch) -> None:
    write_yaml(tmp_project.root / "bible" / "characters.yaml", CHARS)
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    out = _run(tmp_project, monkeypatch, "S001")
    assert "镜头 S001" in out
    assert "2 个镜头" not in out, "scoped run must not claim it scanned the project"


def test_the_empty_state_is_gone_once_there_is_a_hit(
        tmp_project, add_shot, monkeypatch) -> None:
    write_yaml(tmp_project.root / "bible" / "characters.yaml", CHARS)
    add_shot(tmp_project, "S001", action={"main": "@林夏 抬头"})
    out = _run(tmp_project, monkeypatch)
    assert "没有发现" not in out
    assert "@林夏" in out


def test_an_unresolved_mention_is_not_an_empty_state(
        tmp_project, add_shot, monkeypatch) -> None:
    """Unresolved is a FINDING — the report must show it, not the how-to."""
    write_yaml(tmp_project.root / "bible" / "characters.yaml", CHARS)
    add_shot(tmp_project, "S001", action={"main": "@查无此人 出现"})
    out = _run(tmp_project, monkeypatch)
    assert "没有发现" not in out
    assert "未解析" in out


def test_the_json_shape_is_unchanged(tmp_project, add_shot, monkeypatch) -> None:
    """Agents read this; the empty state is presentation only."""
    write_yaml(tmp_project.root / "bible" / "characters.yaml", CHARS)
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["mentions", "--json"])
    assert res.exit_code == 0, res.stdout
    rep = json.loads(res.stdout)
    assert rep["shots"] == [{"shot": "S001", "resolved": [], "unresolved": []}]
    assert rep["story"] == []
