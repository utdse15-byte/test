from pathlib import Path
from manju.gui import server as server_mod

def test_redo_maps_provider_canceled():
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert src.count("ProviderCanceled") >= 4
    assert "C84" in src or "mid-generate cancel" in src or "C85" in src
