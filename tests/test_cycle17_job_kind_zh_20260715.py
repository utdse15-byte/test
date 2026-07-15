"""Cycle-17: job strip shows Chinese kind labels."""

from pathlib import Path

from manju.gui import page as page_mod


def test_job_kind_zh_map() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "KIND_ZH" in src
    assert "批量重做" in src
