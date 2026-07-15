from pathlib import Path

def test_continuous_ledger_exists():
    p = Path("REPORTS/CONTINUOUS_CYCLE_2026-07-15.md")
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "Cycle" in text or "cycle" in text or "Continuous" in text
    assert len(text) > 500
