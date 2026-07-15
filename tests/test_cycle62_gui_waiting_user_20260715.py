"""Cycle-62: GUI redo/voice map WaitingUser to waiting_user result."""

from pathlib import Path

from manju.gui import page as page_mod
from manju.gui import server as server_mod


def test_gui_redo_voice_waiting_user() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert '"waiting_user": True' in src or "'waiting_user': True" in src
    assert "WaitingUser" in src


def test_job_strip_waiting_user_any_kind() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    # no longer restricted to build-only
    assert "j.result.waiting_user === true" in src
    assert 'j.kind === "build" && j.result && j.result.waiting_user' not in src
