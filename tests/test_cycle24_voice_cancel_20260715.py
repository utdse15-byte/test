"""Cycle-24: single-shot voice job is cancelable + wires should_cancel to TTS."""

from __future__ import annotations

from pathlib import Path

from manju.gui import server as server_mod
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job


def test_voice_in_cancelable_running_kinds() -> None:
    assert "voice" in CANCELABLE_RUNNING_KINDS
    j = Job(id="v1", kind="voice", params={"shot": "S001"}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True


def test_act_voice_source_threads_should_cancel() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert "should_cancel" in src
    # Both the primary voice action and the retry rebuild path must wire it.
    assert 'synth_kwargs["should_cancel"] = job.should_cancel' in src
    assert src.count('synth_kwargs["should_cancel"] = job.should_cancel') >= 2
