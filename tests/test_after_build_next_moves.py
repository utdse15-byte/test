"""A video tool that finishes a film should offer a way to look at it.

`manju build` ended at a path:

    时间线: timeline/timeline.json
    渲染: renders/final/final_v3.mp4
    QC: 通过
    build ok

Every other step of the funnel hands the owner a next move. The one moment
there is an actual artifact — the thing the whole system exists to produce —
the output stopped. Nothing said how to see it, compare it, or ship it.

This is presentation, not a feature: every command offered already exists. What
changed is WHEN they are offered.

Two rules, both learned the hard way earlier in this session, and both pinned
below: only offer a route that will work right now (`compare` needs a previous
final to exist), and say when a route will ask for confirmation rather than
either implying it just runs or teaching the owner to paste `--yes` past their
own approval gate.
"""

from __future__ import annotations


import pytest

from manju.cli import _echo_after_build



@pytest.fixture
def project(tmp_project):
    """A REAL project: `_echo_after_build` reads the config through the same
    loader the export gate uses, and a stub with only `.root` silently took the
    unreadable-config fallback — so the first version of this file asserted the
    gate branch while never reaching it."""
    (tmp_project.root / "renders" / "final").mkdir(parents=True, exist_ok=True)
    return tmp_project


def _set_ask_before(project, *tokens: str) -> None:
    from manju.core.yamlio import read_yaml, write_yaml

    cfg_path = project.root / "project.yaml"
    cfg = read_yaml(cfg_path)
    cfg["ask_before"] = list(tokens)
    write_yaml(cfg_path, cfg)


def _finals(project, *versions: int) -> None:
    for v in versions:
        (project.root / "renders" / "final" / f"final_v{v}.mp4").write_bytes(b"x")


def _out(project, rel: str, capsys) -> str:
    _echo_after_build(project, rel)
    return capsys.readouterr().out


# ------------------------------------------------------------- the offer


def test_it_offers_a_way_to_see_the_film(project, capsys) -> None:
    _finals(project, 1)
    out = _out(project, "renders/final/final_v1.mp4", capsys)
    assert "manju gui" in out
    assert "manju frames" in out


def test_the_frames_command_names_the_file_just_rendered(project, capsys) -> None:
    """A generic `manju frames <media>` would make the owner go find the path."""
    _finals(project, 2)
    out = _out(project, "renders/final/final_v2.mp4", capsys)
    assert "manju frames renders/final/final_v2.mp4" in out


def test_it_offers_the_ways_to_ship(project, capsys) -> None:
    _finals(project, 1)
    out = _out(project, "renders/final/final_v1.mp4", capsys)
    for cmd in ("manju package", "manju export", "manju exports"):
        assert cmd in out, cmd


# ------------------------------------- never offer a route that would refuse


def test_compare_is_offered_only_when_there_is_a_previous_version(
        project, capsys) -> None:
    _finals(project, 1, 2)
    out = _out(project, "renders/final/final_v2.mp4", capsys)
    assert "manju compare v1 v2" in out


def test_compare_is_absent_on_a_first_build(project, capsys) -> None:
    """v1 has nothing to diff against; `manju compare` would just refuse."""
    _finals(project, 1)
    out = _out(project, "renders/final/final_v1.mp4", capsys)
    assert "manju compare" not in out, out


def test_compare_is_absent_when_the_previous_file_is_gone(
        project, capsys) -> None:
    """Version numbers are not proof the file is still on disk."""
    _finals(project, 3)          # v2 deliberately missing
    out = _out(project, "renders/final/final_v3.mp4", capsys)
    assert "manju compare" not in out, out


# ------------------------------------------- be honest about the approval gate


def test_the_confirmation_gate_is_announced(project, capsys) -> None:
    """package/export answer `waiting_user: … 确认后重试` while final_export is
    in ask_before. Saying so up front makes the gate expected, not a surprise."""
    _set_ask_before(project, "expensive_generation", "final_export", "lock_change")
    _finals(project, 1)
    out = _out(project, "renders/final/final_v1.mp4", capsys)
    assert "会先要确认" in out


def test_no_yes_flag_is_taught(project, capsys) -> None:
    """Never train the owner to paste --yes past their own approval step."""
    _set_ask_before(project, "final_export")
    _finals(project, 1)
    out = _out(project, "renders/final/final_v1.mp4", capsys)
    assert "--yes" not in out, out


def test_the_gate_note_disappears_when_the_gate_is_off(project, capsys) -> None:
    _set_ask_before(project, "expensive_generation")
    _finals(project, 1)
    out = _out(project, "renders/final/final_v1.mp4", capsys)
    assert "会先要确认" not in out, out


def test_an_unreadable_config_warns_rather_than_promises(project, capsys) -> None:
    """Fail toward the caveat: claiming it will just run is the worse error."""
    (project.root / "project.yaml").write_text("{{ not yaml", encoding="utf-8")
    _finals(project, 1)
    out = _out(project, "renders/final/final_v1.mp4", capsys)
    assert "会先要确认" in out
