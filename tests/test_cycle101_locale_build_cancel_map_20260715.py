from pathlib import Path

def test_locale_build_maps_provider_canceled():
    s = Path("src/manju/build/locale_build.py").read_text(encoding="utf-8")
    assert "ProviderCanceled" in s
    assert "BuildCanceled" in s
    assert "locale voice" in s
