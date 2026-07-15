"""Cycle-55: cockpit hero build passes lang into plan modal."""

from pathlib import Path

from manju.gui import page as page_mod


def test_hero_build_passes_lang() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "action.lang" in src
    assert "params.lang = action.lang" in src
