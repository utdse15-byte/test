"""Cycle-31: voice_preview cancelable; preview_voice accepts should_cancel."""

from __future__ import annotations

import inspect
from pathlib import Path

from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job
from manju.gui import page as page_mod
from manju.gui import server as server_mod
from manju.media.ttspreview import preview_voice


def test_voice_preview_in_cancelable_kinds() -> None:
    assert "voice_preview" in CANCELABLE_RUNNING_KINDS
    j = Job(id="vp1", kind="voice_preview", params={}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_preview_voice_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(preview_voice).parameters


def test_gui_preview_wires_should_cancel() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "should_cancel=job.should_cancel" in src
    assert "voice_preview" in src


def test_kind_zh_voice_preview() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "voice_preview" in src
    assert "试听" in src
