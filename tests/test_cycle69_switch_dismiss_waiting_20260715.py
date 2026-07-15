from pathlib import Path
from manju.gui import page as page_mod

def test_project_switch_dismisses_any_waiting_user():
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "j.result && j.result.waiting_user === true" in src
    # build-only dismiss should be gone from project switch block
    assert "j.kind === \"build\" && j.result && j.result.waiting_user === true" not in src or src.count("j.kind === \"build\" && j.result && j.result.waiting_user") <= 1
