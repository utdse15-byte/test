from pathlib import Path

def test_tts_provider_canceled_source():
    s = Path("src/manju/providers/tts.py").read_text(encoding="utf-8")
    assert "ProviderCanceled" in s
    assert "should_cancel" in s
