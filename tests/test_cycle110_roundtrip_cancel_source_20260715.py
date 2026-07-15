from pathlib import Path

def test_roundtrip_cancel_source():
    s = Path("src/manju/build/roundtrip.py").read_text(encoding="utf-8")
    assert "should_cancel" in s
    assert "canceled" in s
    assert "已取消" in s
