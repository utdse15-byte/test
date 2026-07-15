from pathlib import Path
from manju.gui import page as page_mod

def test_spend_kinds_complete():
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    for k in ("build", "redo", "voice", "redo_batch", "voice_batch", "voice_preview", "handle_rebuild"):
        assert k + ":" in src[src.find("SPEND_KINDS"):src.find("SPEND_KINDS")+500], k
