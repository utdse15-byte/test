from pathlib import Path
from manju.gui import server as server_mod

def test_handle_rebuild_accepts_shot_key():
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    i = src.find("def _act_edit_handle_rebuild")
    chunk = src[i:i+500]
    assert 'body.get("shot")' in chunk
