"""Cycle-12: TTS poll cancel hook exists."""

import inspect

from manju.providers.tts import GenericTtsProvider


def test_tts_poll_accepts_should_cancel() -> None:
    sig = inspect.signature(GenericTtsProvider._poll)
    assert "should_cancel" in sig.parameters


def test_tts_synthesize_accepts_should_cancel() -> None:
    sig = inspect.signature(GenericTtsProvider.synthesize)
    assert "should_cancel" in sig.parameters
