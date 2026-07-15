"""Cycle-46: run_qc accepts should_cancel; GUI qc is cancelable."""

import inspect
from pathlib import Path

from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job
from manju.gui import server as server_mod
from manju.qc.checks import run_qc


def test_run_qc_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(run_qc).parameters


def test_qc_in_cancelable_kinds() -> None:
    assert "qc" in CANCELABLE_RUNNING_KINDS
    j = Job(id="q1", kind="qc", params={}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_gui_qc_wires_should_cancel() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "should_cancel=job.should_cancel" in src
    assert "QC 已取消" in src
