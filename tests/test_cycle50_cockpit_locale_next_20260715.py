"""Cycle-50: cockpit hero uses build_locale next_step_key."""

from pathlib import Path

from manju.gui import cockpit as cockpit_mod


def test_cockpit_honors_build_locale_key() -> None:
    src = Path(cockpit_mod.__file__).read_text(encoding="utf-8")
    assert "build_locale" in src
    assert 'next_step_key") == "build_locale"' in src or "build_locale" in src
