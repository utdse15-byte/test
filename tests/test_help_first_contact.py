"""First contact with `manju` must not be a wall with no door in it.

Typing `manju` with no arguments prints the full help: ~60 commands across
eight panels. The one-line description named a single command — `build` — which
is the one thing a newcomer cannot do yet, because there is nothing to build.
And `manju help-workflow`, the task-oriented index that exists precisely for
"I want to do X, which commands and in what order", could only be found by
already knowing it was there.

The epilog is the fix's home on purpose: after the wall scrolls past, the
epilog is what is still ON SCREEN. Three doors — where am I, start something,
I want to do X.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()

DOORS = ("manju status", "manju create", "manju help-workflow")


def _help() -> str:
    res = runner.invoke(app, ["--help"], env={"COLUMNS": "100"})
    assert res.exit_code == 0, res.output
    return res.output


def _bare() -> str:
    """`manju` with no arguments — how a newcomer actually arrives."""
    return runner.invoke(app, [], env={"COLUMNS": "100"}).output


# The one character class the size check may not touch. Kept as escapes so the
# guard below can look for it without this line being its own false positive —
# a token in a comment tripping a grep-pin is a mistake this repo has recorded
# four times, and writing the guard is exactly when it happens again.
_BOX_DRAWING = "╭─│╰"


def _rendered_command_names(out: str) -> list[str]:
    """The app's own command names that actually appear in the rendered help.

    Matched as standalone tokens, because each row is drawn inside a panel and
    therefore carries a border prefix — which is precisely the part that
    differs between platforms.
    """
    from typer.main import get_command

    names = sorted(get_command(app).commands)  # type: ignore[attr-defined]
    return [n for n in names
            if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", out)]


def test_the_wall_is_real() -> None:
    """Guard the guard: the doors only matter while the surface is big.

    Measured by the COMMANDS on the page, not by how the terminal draws panel
    borders. The first version counted border corners and went red on the
    Windows hard gate — the FIRST platform — with "only 0 panels": that console
    gets the ASCII fallback, so the assertion was measuring Rich's terminal
    detection rather than the size of the surface. The wall is the command list
    either way.
    """
    out = _help()
    shown = _rendered_command_names(out)
    assert len(shown) >= 40, f"only {len(shown)} commands — has the surface shrunk?"


def test_the_wall_measurement_is_not_platform_dependent() -> None:
    """Pin the lesson: nothing in the size check may depend on box drawing."""
    from pathlib import Path

    body = Path(__file__).read_text(encoding="utf-8")
    i = body.index("def _rendered_command_names")
    j = body.index("def test_the_wall_measurement_is_not_platform_dependent")
    region = body[i:j]
    for ch in _BOX_DRAWING:
        assert ch not in region, "the box-drawing measurement is back"


def test_every_door_is_named(*, _=None) -> None:
    out = _help()
    for door in DOORS:
        assert door in out, f"{door} is not reachable from the top-level help"


def test_the_doors_are_on_screen_after_the_wall() -> None:
    """An epilog survives the scroll; a description at the top does not."""
    out = _help()
    tail = out[-700:]
    for door in DOORS:
        assert door in tail, f"{door} scrolled off with the wall"


def test_bare_manju_shows_them_too() -> None:
    """`no_args_is_help=True`, so this is the same page — asserted anyway
    because it is the invocation a newcomer types first."""
    out = _bare()
    for door in DOORS:
        assert door in out, door


def test_the_description_no_longer_points_at_build() -> None:
    """`build` is the one command with nothing to act on in a fresh project."""
    head = _help()[:400]
    assert "manju status" in _help(), "the first-move pointer is gone"
    assert "manju build" not in head, (
        "the top line still sends a newcomer to build")


def test_the_doors_render_as_separate_lines() -> None:
    """Rich reflows a help string as prose and collapses single newlines; the
    first draft ran all three doors into one unreadable paragraph."""
    out = _help()
    lines = [ln for ln in out.splitlines() if any(d in ln for d in DOORS)]
    assert len(lines) >= 3, f"doors are not on their own lines: {lines}"


def test_no_rich_markup_leaks_into_the_output() -> None:
    """`[b]…[/b]` must be rendered, not printed."""
    out = _help()
    assert "[b]" not in out and "[/b]" not in out, out[-400:]


def test_the_named_doors_are_real_commands() -> None:
    """A door that 404s is worse than no door."""
    for door in DOORS:
        cmd = door.split()[1]
        res = runner.invoke(app, [cmd, "--help"], env={"COLUMNS": "100"})
        assert res.exit_code == 0, f"{door} is not a working command: {res.output}"
