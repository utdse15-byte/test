from pathlib import Path
from manju.gui import page as page_mod

def test_qc_multi_locale_toast():
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "另有" in src
    assert "manju qc --lang" in src
    assert "langs.length > 1" in src
