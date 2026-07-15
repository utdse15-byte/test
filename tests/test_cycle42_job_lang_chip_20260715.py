"""Cycle-42: job strip shows locale tag when params/result carry lang."""

from pathlib import Path

from manju.gui import page as page_mod


def test_job_row_lang_chip() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "jobLang" in src
    assert "j.params.lang" in src
    assert "jlang" in src
