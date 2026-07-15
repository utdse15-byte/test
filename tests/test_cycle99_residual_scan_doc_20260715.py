from pathlib import Path

def test_residual_scan_doc_exists():
    p = Path("REPORTS/CONTINUOUS_RESIDUAL_SCAN_C22_C54_2026-07-15.md")
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "Cancel honesty" in text or "cancel" in text.lower()
