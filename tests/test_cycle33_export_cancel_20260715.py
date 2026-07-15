"""Cycle-33: export jobs cancelable; packaging under cancel_scope."""

from __future__ import annotations

from pathlib import Path

from manju.gui import server as server_mod
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job


def test_export_in_cancelable_running_kinds() -> None:
    assert "export" in CANCELABLE_RUNNING_KINDS
    j = Job(id="e1", kind="export", params={"kind": "cover"}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_export_fn_uses_cancel_scope() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "cancel_scope(job.should_cancel)" in src
    assert "MediaCanceled" in src
    assert "已取消导出" in src
