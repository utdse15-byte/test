"""Plan-document references belong in the source, not in the command list.

`manju --help` carried 40 `§`-references plus `(goal item 2)`, `(P3)`, `(WP3)`,
`(AI_IDE_18 WP7)` and friends. They trace a command back to the plan documents
it was built from, which the next maintainer needs — but CLAUDE.md states the
plan document is NOT in the repo, so for the owner reading the command list
they are pointers to something nobody can open.

Stripped at render time rather than edited out of 65 docstrings: one owner,
reversible, and `manju <cmd> --help` still shows the reference where a
maintainer would look for it.

The transform is deliberately conservative, and the tests below encode why: a
greedier first draft turned "(the decision is one line of text — §3)" into
"(the decision is one line of text —)" and "(§4, §5). The safety net" into
". The safety net". Removing noise is not worth mangling sentences, so a
marker woven into prose is left alone.
"""

from __future__ import annotations

import re

import click
import pytest
import typer
from typer.testing import CliRunner

from manju.cli import _strip_provenance, app

runner = CliRunner()


def _group() -> tuple[click.Command, click.Context]:
    cmd = typer.main.get_command(app)
    return cmd, click.Context(cmd)


# ------------------------------------------------------------ the transform


@pytest.mark.parametrize("raw, want", [
    # whole parenthetical is provenance → goes
    ("Create a <name>.manju project directory (§3).",
     "Create a <name>.manju project directory."),
    ("List the preset kits (P3).", "List the preset kits."),
    ("创作漏斗 / creation funnel (goal item 2).", "创作漏斗 / creation funnel."),
    ("Schema + references (§4, §5). The safety net.",
     "Schema + references. The safety net."),
    ("列出批次(round AA,goal item 2)。", "列出批次。"),
    # provenance TRAILING real content → only the marker goes
    ("Tail the collaboration log (who did what, when — §10).",
     "Tail the collaboration log (who did what, when)."),
    ("Labeled git checkpoint (git is the patch engine, §3).",
     "Labeled git checkpoint (git is the patch engine)."),
    # nothing to do
    ("Force new takes (append-only; existing selection stands).",
     "Force new takes (append-only; existing selection stands)."),
    ("批量入库(目录或文件列表)。", "批量入库(目录或文件列表)。"),
])
def test_the_transform_is_exact(raw: str, want: str) -> None:
    assert _strip_provenance(raw) == want


@pytest.mark.parametrize("raw", [
    "AI_IDE_16 §9 — round-trip an edited storyboard pull sheet.",
    "Local web workbench (§1-⑦ revisited) — a client of the SAME engine core.",
    "Recreate the runtime dir (§3: SQLite may be deleted).",
])
def test_a_marker_woven_into_prose_is_left_alone(raw: str) -> None:
    """Better a visible reference than a mangled sentence."""
    assert _strip_provenance(raw) == raw


def test_no_dangling_separator_is_ever_produced() -> None:
    """The two failure shapes of the greedy draft, stated directly."""
    for raw in ("Pick a take (the decision is one line of text — §3).",
                "Schema + references + locks (§4, §5). The safety net."):
        out = _strip_provenance(raw)
        assert "—)" not in out and "()" not in out, out
        assert " ." not in out and " ," not in out, out


# ---------------------------------------------------------------- end to end


def test_the_command_list_is_much_quieter() -> None:
    """A large reduction, not zero — the transform is conservative by design,
    so a marker inside a sentence survives on purpose (see the prose tests).
    What must be gone is the STANDALONE parenthetical, which is the shape that
    carried almost all of the noise."""
    out = runner.invoke(app, ["--help"], env={"COLUMNS": "100"}).output
    assert out.count("§") < 12, f"{out.count('§')} §-refs left in the command list"
    for marker in ("(goal item 2)", "(P3)", "(WP3)", "(§3)", "(§10)", "(§11)"):
        assert marker not in out, f"standalone provenance survived: {marker}"


def test_the_detector_had_something_to_do() -> None:
    """Guard the guard: if the docstrings ever lose their markers on their own,
    the count assertion above would pass while testing nothing."""
    cmd, ctx = _group()
    raw = " ".join((cmd.get_command(ctx, n).help or "")
                   for n in cmd.list_commands(ctx))
    assert len(re.findall(r"§", raw)) >= 20, "source no longer carries §-refs"


def test_per_command_help_keeps_the_reference() -> None:
    """The maintainer's copy survives — `help` is untouched, only `short_help`
    is rewritten, so `manju status --help` still cites its section."""
    out = runner.invoke(app, ["status", "--help"], env={"COLUMNS": "100"}).output
    assert "§10" in out, out[:400]


def test_every_row_still_says_something() -> None:
    """A row emptied by over-stripping would be worse than a noisy one."""
    cmd, ctx = _group()
    for name in cmd.list_commands(ctx):
        sub = cmd.get_command(ctx, name)
        blurb = (sub.short_help or sub.help or "").strip()
        assert len(blurb) >= 4, f"{name}: blurb collapsed to {blurb!r}"
