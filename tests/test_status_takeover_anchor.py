"""The "what was I doing" line must name something somebody did.

`manju status`' first line after the header exists, by its own comment, to be
the returning-owner anchor: when was this project last touched, by whom, doing
what. On a project whose last act was a full build it said:

    上次动作  12 分钟前 · run_terminal (human)

`run_terminal` is a run-lifecycle bookkeeping record. The owner built a film;
the anchor reported engine internals. Those records dominate the tail of
events.jsonl — a single build writes run_started, several stage_attempt and
attempt_started rows, and run_terminal around the one `build` line — so the
newest event is almost never the interesting one.

Same noise class already filtered out of `manju events`' human view; it was
never applied here, and here it matters more, because a single line has no
surrounding context to read past.

Nothing is hidden: the ledger keeps every record, and `manju events` /
`manju tasks` still show them. Only the ANCHOR skips them.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from manju.cli import _BOOKKEEPING_ACTIONS, app
from manju.core.events import append_event

runner = CliRunner()


def _status(project, monkeypatch) -> str:
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0, res.output
    return res.output


def _anchor(out: str) -> str:
    line = [ln for ln in out.splitlines() if ln.startswith("上次动作")]
    return line[0] if line else ""


def _a_build(project) -> None:
    """The event shape a real build leaves: the deed, then bookkeeping."""
    append_event(project.root, "human", "build",
                 {"target": "final", "ok": True})
    append_event(project.root, "human", "stage_attempt", {"schema": "x/v1"})
    append_event(project.root, "human", "run_terminal", {"status": "completed"})


def test_the_anchor_names_the_deed_not_the_bookkeeping(
        tmp_project, monkeypatch) -> None:
    _a_build(tmp_project)
    anchor = _anchor(_status(tmp_project, monkeypatch))
    assert "build" in anchor, anchor
    assert "run_terminal" not in anchor, anchor


@pytest.mark.parametrize("noise", sorted(_BOOKKEEPING_ACTIONS))
def test_no_bookkeeping_action_can_become_the_anchor(
        tmp_project, monkeypatch, noise: str) -> None:
    append_event(tmp_project.root, "human", "select", {"shot": "S001"})
    append_event(tmp_project.root, "human", noise, {"schema": "x/v1"})
    anchor = _anchor(_status(tmp_project, monkeypatch))
    assert "select" in anchor, anchor
    assert noise not in anchor, anchor


def test_it_falls_back_rather_than_showing_nothing(
        tmp_project, monkeypatch) -> None:
    """A project with ONLY bookkeeping must still get an anchor — an empty
    line would be a worse answer than a dull one."""
    append_event(tmp_project.root, "human", "run_started", {"target": "final"})
    append_event(tmp_project.root, "human", "run_terminal", {"status": "x"})
    anchor = _anchor(_status(tmp_project, monkeypatch))
    assert anchor, "the anchor vanished when every event was bookkeeping"


def test_the_actor_and_age_survive(tmp_project, monkeypatch) -> None:
    """Who and when are half the point of the line."""
    append_event(tmp_project.root, "ai", "redo", {"shot": "S002"})
    append_event(tmp_project.root, "human", "stage_attempt", {"schema": "x/v1"})
    anchor = _anchor(_status(tmp_project, monkeypatch))
    assert "(ai)" in anchor, anchor
    assert "redo" in anchor and "S002" in anchor, anchor


def test_the_ledger_still_shows_everything(tmp_project, monkeypatch) -> None:
    """The anchor filters; the record does not. `manju events` must still
    carry the bookkeeping rows — they are what run reconstruction reads."""
    _a_build(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    out = runner.invoke(app, ["events", "-n", "50"]).output
    assert "run_terminal" in out and "stage_attempt" in out, out


def test_the_json_payload_is_untouched(tmp_project, monkeypatch) -> None:
    """Agents read recent_events in full; this is a presentation choice."""
    import json

    _a_build(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    actions = [e.get("action") for e in data.get("recent_events") or []]
    assert "run_terminal" in actions, actions


def test_bookkeeping_really_does_dominate_the_tail(tmp_project) -> None:
    """Guard the guard: if builds ever stop writing lifecycle rows last, this
    filter is protecting against nothing and should be re-derived."""
    _a_build(tmp_project)
    from manju.core.events import tail_events

    tail = tail_events(tmp_project.root, 5)
    assert tail[-1].get("action") in _BOOKKEEPING_ACTIONS, (
        f"the newest event is no longer bookkeeping: {tail[-1].get('action')}")
