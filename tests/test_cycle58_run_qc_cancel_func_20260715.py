from manju.qc.checks import run_qc

def test_run_qc_cancel_early(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "A", "text": "x"}, generation={"candidates": 1})
    n = {"i": 0}
    def cancel_soon():
        n["i"] += 1
        return n["i"] >= 1  # trip immediately at first checkpoint
    report = run_qc(tmp_project, None, extract_frames=False, should_cancel=cancel_soon)
    msgs = [getattr(i, "message", "") for i in report.items]
    assert any(m.startswith("QC 已取消") for m in msgs), msgs
