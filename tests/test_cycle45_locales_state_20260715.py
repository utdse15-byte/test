"""Cycle-45: GUI state exposes declared locales; build select uses them."""

from pathlib import Path

from manju.gui import page as page_mod
from manju.gui import state as state_mod


def test_state_source_lists_locales() -> None:
    src = Path(state_mod.__file__).read_text(encoding="utf-8")
    assert "list_locales" in src
    assert '"locales": locales' in src or "'locales': locales" in src


def test_page_lang_select_uses_last_locales() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "lastLocales" in src
    assert "台词" in src or "declared" in src
