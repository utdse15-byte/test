from pathlib import Path

def test_status_build_locale_source():
    s = Path("src/manju/build/status.py").read_text(encoding="utf-8")
    assert "build_locale" in s
    assert "list_locales" in s
    assert "locale 成片未齐" in s
