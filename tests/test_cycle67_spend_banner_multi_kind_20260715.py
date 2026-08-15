"""Cycle-67: spend banner covers redo/voice waiting_user, not only build."""

from pathlib import Path

from manju.gui import page as page_mod


def test_spend_kinds_map() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "SPEND_KINDS" in src
    assert 'redo: "/api/redo"' in src
    assert 'voice: "/api/voice"' in src
    assert "确认花费并继续" in src
    assert "确认花费并构建" in src
