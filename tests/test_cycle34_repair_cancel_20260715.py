"""Cycle-34: repair jobs cancelable via cancel_scope."""

from pathlib import Path

from manju.gui import server as server_mod
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job


def test_repair_in_cancelable_running_kinds() -> None:
    assert "repair" in CANCELABLE_RUNNING_KINDS
    j = Job(id="r1", kind="repair", params={"shot": "S001", "op": "trim"}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_repair_fn_uses_cancel_scope() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "已取消修复" in src
    assert "cancel_scope(job.should_cancel)" in src
