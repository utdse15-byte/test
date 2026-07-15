"""Cycle-41: plan modal receives lang for locale builds."""

from pathlib import Path

from manju.gui import page as page_mod


def test_plan_modal_passes_lang() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "planParams.lang = body.lang" in src
    assert "构建前计划 · locale" in src
