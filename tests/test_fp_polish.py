"""Optimization-audit polish teeth (FP L6) — guard the discoverability polish
so a future change cannot silently regress it.

Audit finding 11g (UX/flat --help): manju's ~65-command surface used to render
as ONE flat wall — `rich_help_panel` was used zero times. The fix groups every
visible top-level command and every sub-app into a small set of named panels.

These teeth pin that grouping from both sides:
  (a) every visible (non-hidden) top-level command carries a non-empty
      rich_help_panel — a new command cannot silently fall back into the wall;
  (b) every sub-app group carries one too;
  (c) the whole surface stays within a SMALL set of panels — a proliferation of
      one-off panels is the same discoverability failure as the flat wall, seen
      from the other side.

Rendering-only: panels never touch command names or params, so the frozen
tests/fixtures/cli_surface.json snapshot (test_fp_cli_snapshot) is unaffected.
"""

from __future__ import annotations

from manju.cli import app
from manju.core.skills import CORE_SKILL_ID, bundled_skills_dir

# The audited grouping is 8 panels (2026-07-12). This is a CEILING, not an
# equality: a reviewed later loop may add one, but a jump signals accidental
# one-off panels (the flat-wall failure inverted).
MAX_PANELS = 8


def _panel(obj) -> str | None:
    """The command/group's rich_help_panel iff it is a non-empty string. When a
    panel was never set, typer leaves a DefaultPlaceholder here (not a str), so
    this returns None for the ungrouped fallback."""
    p = getattr(obj, "rich_help_panel", None)
    return p if isinstance(p, str) and p.strip() else None


def _command_name(c) -> str:
    return c.name or (c.callback.__name__ if c.callback else "?")


def _visible_commands():
    return [c for c in app.registered_commands if not getattr(c, "hidden", False)]


def test_every_visible_top_level_command_has_a_help_panel():
    """(a) No visible command may render in the ungrouped --help wall (11g)."""
    missing = sorted(_command_name(c) for c in _visible_commands() if _panel(c) is None)
    assert not missing, (
        "these top-level commands carry no rich_help_panel and would fall back "
        f"into the ungrouped --help wall (audit 11g): {missing}"
    )


def test_every_sub_app_group_has_a_help_panel():
    """(b) Sub-app groups (add_typer) must be grouped in --help too."""
    missing = sorted(g.name for g in app.registered_groups if _panel(g) is None)
    assert not missing, (
        f"these sub-app groups carry no rich_help_panel (audit 11g): {missing}"
    )


def test_help_surface_stays_a_small_set_of_panels():
    """(c) The grouping must stay a SMALL, bounded set — one-off panels are the
    flat wall inverted."""
    panels = {_panel(c) for c in _visible_commands() if _panel(c)}
    panels |= {_panel(g) for g in app.registered_groups if _panel(g)}
    assert panels, "no help panels are defined at all — the flat --help wall"
    assert len(panels) <= MAX_PANELS, (
        f"the --help surface grew to {len(panels)} panels (ceiling {MAX_PANELS}); "
        f"a reviewed increase must bump MAX_PANELS on purpose: {sorted(panels)}"
    )


# ---------------------------------------------------------------- SKILL.md
# Audit finding 11b (DEPS-AI/SKILL.md staleness): the always-injected core
# protocol cheat sheet (§11) omitted several shipped CLI surfaces — migrate,
# locale, `pack --bagit`, the openclap/fcpxml/edl import-plan surfaces — and
# under-advertised the export formats, so an agent reading only SKILL.md never
# learned they exist. Pin the flagged surfaces so they cannot silently drop out
# of the cheat sheet again. Tokens are stable CLI identifiers (not prose): a
# drift-catcher, not a wording lock.

# (token, what it documents)
_REQUIRED_CHEAT_SHEET_TOKENS = [
    "migrate",       # rational edit-rate migration sub-app
    "locale",        # multilingual locale overlays
    "--bagit",       # pack BagIt (RFC 8493) serialization mode
    "import-plan",   # the openclap/fcpxml/edl read-only import surfaces (x3)
    "--capcut", "--ttml", "--edl", "--fcpxml", "--pullsheet",  # export formats
]


def _core_skill_text() -> str:
    return (bundled_skills_dir() / CORE_SKILL_ID / "SKILL.md").read_text(encoding="utf-8")


def test_core_skill_cheat_sheet_documents_the_audited_surfaces():
    """The always-injected SKILL.md must keep documenting the surfaces the audit
    found missing — an agent that reads only the cheat sheet must learn they
    exist (audit 11b)."""
    body = _core_skill_text()
    missing = [tok for tok in _REQUIRED_CHEAT_SHEET_TOKENS if tok not in body]
    assert not missing, (
        "the always-injected SKILL.md cheat sheet no longer documents these "
        f"shipped CLI surfaces (audit 11b): {missing}"
    )
