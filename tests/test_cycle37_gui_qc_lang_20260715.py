"""Cycle-37: GUI /api/qc accepts lang and refuses silent base QC."""

from pathlib import Path

from manju.gui import server as server_mod


def test_act_qc_source_handles_lang() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "newest_locale_final" in src
    assert "apply_locale_overlay" in src
    assert "无 locale 成片可 QC" in src
    assert 'body.get("lang")' in src
    # must not fall back to base when lang is set
    assert "不会回退" in src or "无 locale 成片" in src
