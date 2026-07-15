from pathlib import Path
from manju.gui import page as page_mod
from manju.gui import server as server_mod

def test_voice_preview_spend_path():
    page = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert 'voice_preview: "/api/voice/preview"' in page
    srv = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "WaitingUser" in srv
    assert "voice_preview" in srv
