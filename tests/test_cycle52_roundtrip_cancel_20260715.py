"""Cycle-52: roundtrip apply honors should_cancel between rows."""

import inspect
from pathlib import Path

from manju.build.roundtrip import apply_roundtrip
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job
from manju.gui import server as server_mod


def test_apply_roundtrip_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(apply_roundtrip).parameters


def test_roundtrip_in_cancelable_kinds() -> None:
    assert "roundtrip" in CANCELABLE_RUNNING_KINDS
    j = Job(id="rt1", kind="roundtrip", params={}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_gui_roundtrip_wires_cancel() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "apply_roundtrip" in src
    assert "should_cancel=job.should_cancel" in src
    assert "roundtrip 已取消" in src
