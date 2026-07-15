from pathlib import Path

def test_continuous_work_c100_marker():
    """C100 milestone: continuous loop still has evidence on disk."""
    assert Path("REPORTS/CONTINUOUS_CYCLE_2026-07-15.md").exists()
    assert Path("REPORTS/CONTINUOUS_RESIDUAL_SCAN_C22_C54_2026-07-15.md").exists()
    cycle_tests = list(Path("tests").glob("test_cycle*_20260715.py"))
    assert len(cycle_tests) >= 70
