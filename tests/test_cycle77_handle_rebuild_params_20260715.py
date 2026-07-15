from pathlib import Path
from manju.gui import server as server_mod

def test_handle_rebuild_job_params_full():
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert '"assume_yes": assume_yes' in src
    assert '"transition_ms": transition_ms' in src
    assert '"handle_rebuild"' in src
