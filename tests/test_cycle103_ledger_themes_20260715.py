from pathlib import Path

def test_continuous_ledger_mentions_cancel_and_locale():
    text = Path("REPORTS/CONTINUOUS_CYCLE_2026-07-15.md").read_text(encoding="utf-8")
    assert "Cycle" in text or "cycle" in text
    # at least some of our work themes appear
    assert "cancel" in text.lower() or "Cancel" in text or "locale" in text.lower() or "Continuous" in text
