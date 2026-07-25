"""The command list is for the owner, and the owner reads Chinese.

56 of the 82 top-level rows were English-only, so `manju --help` was mostly
unreadable to the one person it exists for. Each row now renders its Chinese
lead; the English stays in the docstring and therefore in
`manju <cmd> --help`, one keystroke away and unchanged.

Two shapes were tried and rejected on measurement, both recorded here as
assertions so they are not re-tried by accident:

* "中文 / English" on every row — correct-looking, but each row wrapped to two
  or three lines and the help page grew from 175 lines to well over 200. A wall
  made taller is not a wall made readable.
* Taking `help.split("\\n\\n")[0]` as the blurb — several docstrings open with a
  six-line paragraph (`explain`), which dumped the whole thing into the list
  and buried its neighbours. First LINE, not first paragraph.

The last test is the anti-drift lever: a NEW English-only top-level command
fails the suite until it is given a lead, so the table cannot fall behind the
surface it describes.
"""

from __future__ import annotations

import re

import click
import pytest
import typer
from typer.testing import CliRunner

from manju.cli import _ZH_LEAD, app

runner = CliRunner()
CJK = re.compile(r"[一-鿿]")


def _group() -> tuple[click.Command, click.Context]:
    cmd = typer.main.get_command(app)
    return cmd, click.Context(cmd)


def _rows() -> dict[str, str]:
    cmd, ctx = _group()
    out = {}
    for name in cmd.list_commands(ctx):
        sub = cmd.get_command(ctx, name)
        out[name] = (sub.short_help or sub.help or "").strip()
    return out


# ------------------------------------------------------------- readability


def test_every_row_is_readable_in_chinese() -> None:
    missing = sorted(n for n, blurb in _rows().items() if not CJK.search(blurb))
    assert missing == [], f"top-level rows still English-only: {missing}"


def test_every_row_is_a_single_line() -> None:
    """The wall is only worth reading if a row is one row."""
    for name, blurb in _rows().items():
        assert "\n" not in blurb, f"{name}: blurb spans lines — {blurb[:80]!r}"


def test_no_row_dumps_a_paragraph() -> None:
    """`explain`'s docstring opens with six lines; the list must take one."""
    for name, blurb in _rows().items():
        assert len(blurb) < 200, f"{name}: {len(blurb)} chars in the list row"


def test_the_help_page_did_not_get_taller() -> None:
    """The bilingual draft grew the page past 200 lines. Readability that
    costs a screen and a half of scrolling is not readability."""
    out = runner.invoke(app, ["--help"], env={"COLUMNS": "100"}).output
    assert len(out.splitlines()) <= 180, len(out.splitlines())


# --------------------------------------------------- the English is not lost


def test_the_english_survives_in_per_command_help() -> None:
    for name, probe in (("status", "Takeover entry point"),
                        ("build", "One-command build"),
                        ("doctor", "Environment health")):
        out = runner.invoke(app, [name, "--help"], env={"COLUMNS": "100"}).output
        assert probe in out, f"{name}: English summary gone from --help"


def test_an_already_chinese_row_is_not_double_glossed() -> None:
    """Rows that were already bilingual keep their authored wording."""
    assert "创作漏斗" in _rows()["create"]
    assert _rows()["create"].count("创作漏斗") == 1


# ------------------------------------------------------------- anti-drift


def test_every_lead_names_a_real_command() -> None:
    cmd, ctx = _group()
    known = set(cmd.list_commands(ctx))
    stale = sorted(set(_ZH_LEAD) - known)
    assert stale == [], f"leads for commands that no longer exist: {stale}"


def test_a_new_english_only_command_must_get_a_lead() -> None:
    """The lever that keeps this from rotting: the table is derived from the
    surface, so adding an English-only command fails here until it is glossed."""
    cmd, ctx = _group()
    ungloss = []
    for name in cmd.list_commands(ctx):
        sub = cmd.get_command(ctx, name)
        source = (sub.help or "")
        if source and not CJK.search(source.split("\n")[0]) and name not in _ZH_LEAD:
            ungloss.append(name)
    assert ungloss == [], (
        f"English-only top-level commands with no Chinese lead: {ungloss} — "
        "add them to _ZH_LEAD in cli.py")


def test_the_guard_has_something_to_guard() -> None:
    """Guard the guard: if every docstring were Chinese the check above would
    pass while asserting nothing."""
    assert len(_ZH_LEAD) >= 40, f"only {len(_ZH_LEAD)} leads — has the surface moved?"
