"""Cycle-51: handle_rebuild cancelable; should_cancel threaded."""

import inspect
from pathlib import Path

from manju.gui.edit_engine import run_handle_rebuild
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job
from manju.gui import page as page_mod
from manju.gui import server as server_mod


def test_handle_rebuild_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(run_handle_rebuild).parameters


def test_handle_rebuild_in_cancelable_kinds() -> None:
    assert "handle_rebuild" in CANCELABLE_RUNNING_KINDS
    j = Job(id="h1", kind="handle_rebuild", params={"shot": "S001"}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_gui_wires_handle_rebuild_cancel() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "run_handle_rebuild" in src
    assert "should_cancel=job.should_cancel" in src


def test_kind_zh_handle_rebuild() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "补拍手柄" in src
