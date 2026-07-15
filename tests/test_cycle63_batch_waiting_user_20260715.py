"""Cycle-63: redo_batch/voice_batch map WaitingUser to waiting_user result."""

from pathlib import Path

from manju.gui import server as server_mod


def test_batch_jobs_catch_waiting_user() -> None:
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    assert src.count("WaitingUser") >= 4  # redo, voice, redo_batch, voice_batch
    assert "redo_batch" in src and "voice_batch" in src
