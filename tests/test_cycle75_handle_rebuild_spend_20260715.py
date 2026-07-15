from pathlib import Path
from manju.gui import page as page_mod

def test_spend_kinds_handle_rebuild():
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert 'handle_rebuild: "/api/edit/handle-rebuild"' in src
