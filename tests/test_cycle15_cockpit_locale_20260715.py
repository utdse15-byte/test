"""Cycle-15: cockpit phase honest with locale-only finals."""

from manju.gui.cockpit import _final_summary, _phase


def test_phase_not_render_when_locale_finals() -> None:
    status = {
        "shots_by_state": {},
        "shots_total": 1,
        "timeline": {"exists": True},
        "latest_final": None,
        "locale_finals": {"en": "renders/final/locales/en/final_v1.mp4"},
        "qc": {},
    }
    ph = _phase(status)
    assert ph[0] != "render"


def test_final_summary_falls_back_to_locale() -> None:
    status = {
        "latest_final": None,
        "locale_finals": {"ja": "renders/final/locales/ja/final_v2.mp4"},
        "latest_final_note": "无 base",
    }
    s = _final_summary(status, None)
    assert s is not None
    assert "ja" in s["path"]
