from pathlib import Path

def test_local_cmd_cancel_source():
    s = Path("src/manju/providers/local_cmd.py").read_text(encoding="utf-8")
    assert "ProviderCanceled" in s
    assert "should_cancel" in s
    assert "0.5" in s or "slice" in s
