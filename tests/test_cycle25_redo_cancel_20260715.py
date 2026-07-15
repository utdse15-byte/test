"""Cycle-25: single-shot redo is cancelable; should_cancel reaches GenerationRequest."""

from __future__ import annotations

import inspect
from pathlib import Path

from manju.build.graph import redo_shot, _run_redo
from manju.gui import server as server_mod
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job


def test_redo_shot_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(redo_shot).parameters
    assert "should_cancel" in inspect.signature(_run_redo).parameters


def test_redo_in_cancelable_running_kinds() -> None:
    assert "redo" in CANCELABLE_RUNNING_KINDS
    j = Job(id="r1", kind="redo", params={"shot": "S001"}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_gui_redo_wires_should_cancel() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert 'kwargs["should_cancel"] = job.should_cancel' in src
    # primary _act_redo + retry rebuild path
    assert src.count('kwargs["should_cancel"] = job.should_cancel') >= 2
