"""`manju events` has to stay readable once the project has a history.

The human view printed ``json.dumps(detail)`` in full. Ordinary evidence
records — ``stage_attempt`` carries a schema id, a run id, an attempt id, a
spec hash per output, an output list and a semantic digest — render at 700-900
columns each, so the command whose help says "who did what, when" became a wall
of hashes exactly when there was finally something to read.

The digest keeps the first few meaningful fields, truncates long values, and
SAYS how many it elided plus where the whole record lives. Nothing is lost:
``--json`` already emitted complete records and is untouched — the MCP `events`
tool and every JSON consumer read that path.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manju.cli import _event_detail_brief, app
from manju.core.events import append_event

runner = CliRunner()

HEAVY = {
    "schema": "manju.stage-attempt-evidence/v1",
    "run_id": "run_20260725_042140_41b323",
    "attempt_id": "att_fb727dc0018a",
    "sequence": 4,
    "stage": "generate",
    "unit": {"kind": "shot", "shot": "S005"},
    "action": "cache_hit",
    "state": "SKIPPED_CACHE_HIT",
    "duration_ms": 0,
    "outputs": [{"role": "take", "take": "take_01",
                 "spec_hash": "sha256:" + "8f" * 32,
                 "path": "media/gen/S005/take_01.mp4"}],
    "semantic_digest": "sha256:" + "c0" * 32,
}


def _events(project, monkeypatch, *args) -> str:
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["events", *args])
    assert res.exit_code == 0, res.output
    return res.output


# ------------------------------------------------------------- the digest


def test_a_heavy_record_becomes_a_scannable_line() -> None:
    brief = _event_detail_brief(HEAVY)
    assert len(brief) < 200, f"{len(brief)} columns: {brief}"
    assert "run_id=run_20260725_042140_41b323" in brief


def test_the_digest_says_what_it_left_out() -> None:
    """Silent truncation would read as "that is the whole record"."""
    brief = _event_detail_brief(HEAVY)
    assert "--json" in brief
    assert "+" in brief and "项" in brief


def test_structural_noise_is_not_what_gets_shown() -> None:
    """schema/semantic_digest are true but never answer "who did what"."""
    brief = _event_detail_brief(HEAVY)
    assert "manju.stage-attempt-evidence/v1" not in brief
    assert "semantic_digest" not in brief


def test_a_long_value_is_truncated_visibly() -> None:
    brief = _event_detail_brief({"hash": "sha256:" + "ab" * 32})
    assert "…" in brief
    assert len(brief) < 80


def test_a_short_detail_is_shown_whole_with_no_noise() -> None:
    brief = _event_detail_brief({"shot": "S003", "take": "take_02", "via": "cli"})
    assert brief == "shot=S003, take=take_02, via=cli"


def test_an_empty_detail_adds_nothing() -> None:
    assert _event_detail_brief({}) == ""
    assert _event_detail_brief(None) == ""


def test_an_all_noise_detail_still_shows_something() -> None:
    """Filtering must never leave a bare action with no context at all."""
    brief = _event_detail_brief({"schema": "manju.x/v1"})
    assert "manju.x/v1" in brief


# --------------------------------------------------------------- end to end


def test_the_human_view_is_bounded(tmp_project, monkeypatch) -> None:
    for _ in range(3):
        append_event(tmp_project.root, "human", "stage_attempt", dict(HEAVY))
    for line in _events(tmp_project, monkeypatch).splitlines():
        assert len(line) < 260, f"{len(line)} columns: {line[:120]}…"


def test_the_human_view_still_says_who_did_what_when(
        tmp_project, monkeypatch) -> None:
    append_event(tmp_project.root, "ai", "select", {"shot": "S003", "take": "take_02"})
    out = _events(tmp_project, monkeypatch)
    assert "[ai]" in out
    assert "select" in out
    assert "shot=S003" in out
    assert "20" in out                      # a timestamp is still on the line


def test_json_still_carries_the_complete_record(tmp_project, monkeypatch) -> None:
    """The elided fields must remain reachable, verbatim."""
    append_event(tmp_project.root, "human", "stage_attempt", dict(HEAVY))
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["events", "--json"])
    assert res.exit_code == 0, res.output
    entry = [e for e in json.loads(res.output) if e["action"] == "stage_attempt"][-1]
    assert entry["detail"] == HEAVY
