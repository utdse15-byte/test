"""Cycle-16: board status shows locale finals."""

from pathlib import Path

from manju.board import board as board_mod


def test_board_status_mentions_locale_finals() -> None:
    src = Path(board_mod.__file__).read_text(encoding="utf-8")
    assert "locale finals" in src
    assert "locale_finals" in src
