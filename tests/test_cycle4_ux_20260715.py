"""Cycle-4 UX honesty pins."""

from pathlib import Path

from manju.gui import director_page as dir_mod
from manju.gui import exports_page as exp_mod
from manju.gui import page as page_mod


def test_director_run_has_catch() -> None:
    src = Path(dir_mod.__file__).read_text(encoding="utf-8")
    assert "执行中…" in src
    assert src.count(".catch(function") >= 1


def test_job_row_shows_canceling_hint() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "取消中" in src


def test_exports_accepts_job_envelope() -> None:
    src = Path(exp_mod.__file__).read_text(encoding="utf-8")
    assert "res.status === 202 || res.status === 200" in src
