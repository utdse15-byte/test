"""The last line of `manju qc` was a machine token with no meaning attached.

Walking the QC loop on a real film, a clean run ended with:

    验收 assurance: no_explicit_expectations 4

A bare snake_case token in an otherwise Chinese CLI, with nothing saying what
it means or whether it needs action — on the command the owner runs most often
after a build. Worse, it reads like an outstanding debt: QC said "0 errors, 0
warnings, qc ok" and then appeared to list four unresolved things.

It is in fact the ordinary state of a shot nobody wrote promises for. Each
state now carries a Chinese gloss taken from its own documented reason in
qc/assurance.py, and the all-clean case says plainly that it is not a problem.

The token stays on the line: an agent reading the same output branches on it,
and `--json` is untouched.
"""

from __future__ import annotations

import pytest

from manju.cli import _ASSURANCE_ZH, _echo_assurance_summary
from manju.qc.assurance import STATES


def _rows(*states: str) -> list[dict]:
    return [{"subject": {"kind": "shot", "id": f"S{i:03d}"},
             "assurance_state": s, "reasons": [f"reason for {s}"]}
            for i, s in enumerate(states, 1)]


def _out(capsys, *states: str) -> str:
    _echo_assurance_summary(_rows(*states))
    return capsys.readouterr().out


# ------------------------------------------------------------- the glossary


@pytest.mark.parametrize("state", STATES)
def test_every_real_state_has_a_gloss(state: str) -> None:
    """Derived from the engine's own STATES tuple, so a new state cannot be
    added without this failing — the table cannot fall behind."""
    assert state in _ASSURANCE_ZH, f"no Chinese gloss for {state!r}"


def test_no_gloss_invents_a_state() -> None:
    assert set(_ASSURANCE_ZH) <= set(STATES), set(_ASSURANCE_ZH) - set(STATES)


def test_the_machine_token_survives_next_to_the_gloss(capsys) -> None:
    """An agent reads this line too; the gloss is additive."""
    out = _out(capsys, "accepted", "accepted")
    assert "accepted 2" in out
    assert _ASSURANCE_ZH["accepted"] in out


# ------------------------------------------------- clean is stated as clean


def test_an_all_clean_run_says_it_is_not_a_problem(capsys) -> None:
    out = _out(capsys, *(["no_explicit_expectations"] * 4))
    assert "no_explicit_expectations 4" in out
    assert "这不是问题" in out
    assert "must_show" in out, "does not say how to give QC something to watch"


def test_a_fully_accepted_run_does_not_nag(capsys) -> None:
    """Nothing to reassure about when every shot passed review."""
    out = _out(capsys, "accepted", "accepted")
    assert "这不是问题" not in out


@pytest.mark.parametrize("bad", ["rejected", "unknown", "stale"])
def test_a_run_with_findings_is_never_called_fine(capsys, bad: str) -> None:
    """The reassurance must not appear next to a real finding — that would be
    the engine telling the owner to ignore its own verdict."""
    out = _out(capsys, "no_explicit_expectations", bad)
    assert "这不是问题" not in out, out
    assert bad in out


def test_findings_still_list_their_shot_and_reason(capsys) -> None:
    """The per-shot detail lines are unchanged."""
    out = _out(capsys, "rejected")
    assert "S001" in out and "rejected" in out
    assert "reason for rejected" in out


def test_an_empty_assurance_list_prints_nothing(capsys) -> None:
    _echo_assurance_summary([])
    assert capsys.readouterr().out == ""
    _echo_assurance_summary(None)
    assert capsys.readouterr().out == ""
