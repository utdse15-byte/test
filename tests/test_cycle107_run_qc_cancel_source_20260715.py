from pathlib import Path

def test_run_qc_cancel_source():
    s = Path("src/manju/qc/checks.py").read_text(encoding="utf-8")
    assert "should_cancel" in s
    assert "QC 已取消" in s
