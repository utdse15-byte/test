from pathlib import Path

def test_edge_tts_cancel_source():
    s = Path("src/manju/providers/edge_tts.py").read_text(encoding="utf-8")
    assert "ProviderCanceled" in s
    assert "should_cancel" in s
