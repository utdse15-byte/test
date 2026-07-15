from pathlib import Path

def test_asr_cancel_source():
    s = Path("src/manju/providers/asr.py").read_text(encoding="utf-8")
    assert "ProviderCanceled" in s
    assert "should_cancel" in s
