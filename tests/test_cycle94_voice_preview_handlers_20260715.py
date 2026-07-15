from pathlib import Path
from manju.gui import server as server_mod

def test_voice_preview_handlers():
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    i = src.find("def _act_voice_preview")
    chunk = src[i:i+1200]
    assert "PreviewUnavailable" in chunk
    assert "ProviderCanceled" in chunk
    assert "WaitingUser" in chunk
