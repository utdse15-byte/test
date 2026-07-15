from pathlib import Path

def test_cockpit_build_locale_source():
    s = Path("src/manju/gui/cockpit.py").read_text(encoding="utf-8")
    assert "build_locale" in s
    assert "lang_hint" in s or 'action["lang"]' in s
