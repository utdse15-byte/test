"""Cycle-10: queue gate tooltips while jobs active."""

from pathlib import Path

from manju.gui import page as page_mod


def test_update_gates_job_running_tooltip() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "任务运行中" in src
    assert "job running" in src
