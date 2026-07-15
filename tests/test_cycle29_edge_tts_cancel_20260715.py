"""Cycle-29: Edge TTS accepts should_cancel; pre-start cancel raises ProviderCanceled."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from manju.gui import page as page_mod
from manju.providers.base import ProviderCanceled
from manju.providers.edge_tts import EdgeTtsProvider
from manju.providers.manifest import ProviderManifest


def _edge_manifest() -> ProviderManifest:
    return ProviderManifest.model_validate({
        "id": "edge",
        "type": "tts",
        "adapter": "manju.providers.edge_tts:EdgeTtsProvider",
        "tts": {"default_voice": "zh-CN-XiaoxiaoNeural", "audio_format": "mp3"},
        "cost": {"per_call": 0.0, "currency": "CNY"},
    })


def test_edge_synthesize_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(EdgeTtsProvider.synthesize).parameters


def test_edge_prestart_cancel_raises_provider_canceled(tmp_project, add_shot) -> None:
    shot = add_shot(
        tmp_project, "S001",
        dialogue={"speaker": "A", "text": "你好世界"},
        generation={"candidates": 1},
    )
    provider = EdgeTtsProvider(_edge_manifest())
    with pytest.raises(ProviderCanceled) as ei:
        provider.synthesize(
            tmp_project, shot, tmp_project.load_bible(),
            should_cancel=lambda: True,
        )
    assert ei.value.provider_id == "edge"
    assert "edge:S001" in ei.value.job_id


def test_jkind_css_wider_for_zh_labels() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "min-width: 4.5rem" in src
    assert "white-space: nowrap" in src
