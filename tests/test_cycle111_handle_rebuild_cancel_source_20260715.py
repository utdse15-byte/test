from pathlib import Path

def test_handle_rebuild_cancel_source():
    s = Path("src/manju/gui/edit_engine.py").read_text(encoding="utf-8")
    assert "should_cancel" in s
    assert "ProviderCanceled" in s or "已取消补拍手柄" in s
