"""Cycle-40: build panel language select for locale builds."""

from pathlib import Path

from manju.gui import page as page_mod


def test_build_panel_has_lang_select() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "语言 (lang)" in src
    assert "base (母语)" in src
    assert "refreshLangOptions" in src
    assert "body.lang = lang.value" in src
    assert "lastLocaleFinals" in src
