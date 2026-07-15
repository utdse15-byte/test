"""Cycle-14: GUI state + SPA surface locale_finals."""

from pathlib import Path

from manju.gui import page as page_mod
from manju.gui import state as state_mod


def test_page_js_renders_locale_finals() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "locale_finals" in src
    assert "locale 成片" in src


def test_build_state_includes_locale_finals_key() -> None:
    src = Path(state_mod.__file__).read_text(encoding="utf-8")
    assert '"locale_finals"' in src or "'locale_finals'" in src
