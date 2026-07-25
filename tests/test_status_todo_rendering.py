"""`manju status`' to-do list, read the way the owner actually reads it.

Two defects, both only visible once a project has real depth:

1. The list was a flat `todo[:6]`. A dozen shots typically need the SAME thing
   (twelve 配音 lines), so the one genuinely different item — a newtake
   decision, a broken shot — could be pushed below the cut by six identical
   neighbours. The rarest line is the one worth reading, and it was the one
   most likely to disappear.

2. The tail said "manju status --json 看全部": a HUMAN, reading their own
   to-do list, was told to go parse JSON to see the rest of it.

Now every distinct `key` is shown before any kind repeats, and the tail names
what is left by kind and by shot. `--json` is unchanged — agents read `todo`
in full there, and that path is what the machine contract lives on.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()


def _status(project, monkeypatch) -> str:
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0, res.output
    return res.output


def _todo_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("待办")]


def _many_voice_shots(project, add_shot, n: int = 12) -> None:
    """n shots that all need the same thing — the ordinary shape of a project."""
    for i in range(1, n + 1):
        sid = f"S{i:03d}"
        add_shot(project, sid, status={"selected_take": "take_01"})
        d = project.root / "media" / "gen" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "take_01.mp4").write_bytes(b"v")
        (d / "take_01.yaml").write_text(
            "take: take_01\nspec_hash: manual\n", encoding="utf-8")


# -------------------------------------------------------------- no dead ends


def test_the_tail_does_not_send_a_human_to_json(
        tmp_project, add_shot, monkeypatch) -> None:
    _many_voice_shots(tmp_project, add_shot)
    out = _status(tmp_project, monkeypatch)
    assert "--json" not in out, "the human view still points at JSON"


def test_the_tail_names_what_is_left(tmp_project, add_shot, monkeypatch) -> None:
    """"另有 6 项" alone is still a dead end; the kinds and shots must show."""
    _many_voice_shots(tmp_project, add_shot)
    lines = _todo_lines(_status(tmp_project, monkeypatch))
    assert len(lines) > 1, lines
    tail = lines[-1]
    if "另有" in tail:
        assert "×" in tail, tail                      # by kind
        assert "S0" in tail, tail                     # ...and by shot


def test_no_shot_is_hidden(tmp_project, add_shot, monkeypatch) -> None:
    """Every shot with pending work appears somewhere in the human output."""
    _many_voice_shots(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)
    out = runner.invoke(app, ["status"]).output
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    for item in data["todo"]:
        assert item["shot"] in out, f"{item['shot']} is not visible anywhere"


# ------------------------------------------------- the rare item stays visible


def test_a_lone_distinct_item_is_not_crowded_out(
        tmp_project, add_shot, monkeypatch) -> None:
    """The defect this rewrite exists for: one odd item among many identical
    ones must still be on screen, wherever it sorts."""
    _many_voice_shots(tmp_project, add_shot, n=11)
    # The odd one out, added LAST so it sorts at the very bottom — the worst
    # case for a flat head-slice: a shot with no take at all, whose todo kind
    # differs from the eleven above it.
    add_shot(tmp_project, "S099")

    monkeypatch.chdir(tmp_project.root)
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    keys = {i["key"] for i in data["todo"]}
    if len(keys) < 2:                       # premise not met — say so, don't pass
        raise AssertionError(f"expected two todo kinds, got {keys}")
    rare = min(keys, key=lambda k: sum(i["key"] == k for i in data["todo"]))
    rare_shot = next(i["shot"] for i in data["todo"] if i["key"] == rare)

    out = runner.invoke(app, ["status"]).output
    shown = [ln for ln in _todo_lines(out) if "另有" not in ln]
    assert any(rare_shot in ln for ln in shown), (
        f"the rare {rare!r} item ({rare_shot}) is not among the shown lines")


# ------------------------------------------------------------ shape unchanged


def test_a_short_list_renders_one_line_each(
        tmp_project, add_shot, monkeypatch) -> None:
    """Small projects must look exactly as before — no folding, no tail."""
    _many_voice_shots(tmp_project, add_shot, n=3)
    lines = _todo_lines(_status(tmp_project, monkeypatch))
    assert all("另有" not in ln for ln in lines), lines
    assert len(lines) == 3, lines


def test_the_json_todo_is_untouched(tmp_project, add_shot, monkeypatch) -> None:
    """Agents branch on `key` here; the rewrite is presentation only."""
    _many_voice_shots(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert len(data["todo"]) == 12
    assert all({"shot", "key", "action"} <= set(i) for i in data["todo"])
