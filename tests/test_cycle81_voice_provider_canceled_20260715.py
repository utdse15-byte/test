from pathlib import Path
from manju.gui import server as server_mod

def test_voice_maps_provider_canceled():
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "ProviderCanceled" in src
    assert '"canceled": True' in src or "'canceled': True" in src
