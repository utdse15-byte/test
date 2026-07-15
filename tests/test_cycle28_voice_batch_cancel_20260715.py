"""Cycle-28: voice_batch threads should_cancel into synthesize; ProviderCanceled."""

from __future__ import annotations

import inspect
from pathlib import Path

from manju.build import graph as graph_mod
from manju.build.graph import voice_batch
from manju.providers.base import ProviderCanceled


def test_voice_batch_source_threads_should_cancel() -> None:
    src = Path(graph_mod.__file__).read_text(encoding="utf-8")
    assert "synth_kwargs" in src
    assert 'synth_kwargs["should_cancel"] = should_cancel' in src
    assert "ProviderCanceled" in src
    assert "已取消等待" in src


def test_voice_batch_signature_still_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(voice_batch).parameters


def test_voice_batch_mid_poll_cancel_is_canceled_not_failed(
    tmp_project, add_shot, monkeypatch,
) -> None:
    """ProviderCanceled during synthesize must set BatchResult.canceled."""
    import manju.providers.tts as tts_mod

    add_shot(
        tmp_project, "S001",
        dialogue={"speaker": "A", "text": "你好"},
        generation={"candidates": 1},
    )
    add_shot(
        tmp_project, "S002",
        dialogue={"speaker": "A", "text": "再见"},
        generation={"candidates": 1},
    )

    class FakeManifest:
        cost = type("C", (), {"per_call": 0.0, "currency": "CNY"})()

    class FakeTts:
        id = "fake_tts"

        def synthesize(self, project, shot, bible, **kwargs):
            raise ProviderCanceled("fake_tts", "job-v")

    monkeypatch.setattr(tts_mod, "tts_providers", lambda: {"fake_tts": FakeManifest()})
    monkeypatch.setattr(tts_mod, "get_tts_provider", lambda name=None: FakeTts())

    result = voice_batch(
        tmp_project, shots=["S001", "S002"], assume_yes=True, actor="test",
    )
    assert result.canceled is True
    assert not result.failed  # cancel must not look like per-shot failure
    assert any("已取消等待" in e for e in result.errors)
