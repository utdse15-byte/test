"""Cycle-39: GUI /api/build accepts lang for locale builds."""

from pathlib import Path

from manju.gui import server as server_mod


def test_act_build_source_handles_lang() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert 'body.get("lang")' in src
    assert 'kwargs["lang"] = lang' in src or 'kwargs_dry["lang"] = lang' in src
    # dry_run and real job both carry lang
    assert "kwargs_dry" in src
    assert '"lang": lang' in src or "'lang': lang" in src
