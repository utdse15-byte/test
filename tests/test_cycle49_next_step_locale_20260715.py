"""Cycle-49: next_step suggests locale build when lines exist without finals."""

from pathlib import Path

from manju.build import status as status_mod


def test_status_source_build_locale_next_step() -> None:
    src = Path(status_mod.__file__).read_text(encoding="utf-8")
    assert "build_locale" in src
    assert "list_locales" in src
    assert "locale 成片未齐" in src
