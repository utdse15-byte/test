"""Cycle-38: workbench QC button prefers locale final when present."""

from pathlib import Path

from manju.gui import page as page_mod


def test_qc_button_uses_locale_finals() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "lastLocaleFinals" in src
    assert "body.lang" in src
    assert "locale " in src  # toast mentions locale
    assert 'post(btn, "/api/qc", body' in src or 'post(btn, "/api/qc", body,' in src
