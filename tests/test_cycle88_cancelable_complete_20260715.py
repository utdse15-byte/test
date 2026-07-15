from manju.build.graph import BatchResult
from manju.gui.jobs import Job

def test_jobrunner_pattern_canceled_dict():
    # simulate what JobRunner does
    result = BatchResult(canceled=True, errors=["已取消"]).to_dict()
    assert result.get("canceled") is True

def test_cancelable_kinds_complete():
    from manju.gui.jobs import CANCELABLE_RUNNING_KINDS
    for k in ("build", "redo", "voice", "qc", "export", "repair", "handle_rebuild", "roundtrip", "voice_preview"):
        assert k in CANCELABLE_RUNNING_KINDS, k
