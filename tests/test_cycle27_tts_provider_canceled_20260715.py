"""Cycle-27: TTS poll cancel raises ProviderCanceled (not ProviderFailure)."""

from __future__ import annotations

import json

import pytest

from manju.providers.base import ProviderCanceled, ProviderFailure
from manju.providers.generic_cloud import HttpResponse
from manju.providers.manifest import GENERIC_TTS_ADAPTER, ProviderManifest
from manju.providers.tts import GenericTtsProvider


def _manifest() -> ProviderManifest:
    return ProviderManifest.model_validate({
        "id": "tts_test",
        "type": "tts",
        "adapter": GENERIC_TTS_ADAPTER,
        "cost": {"per_call": 0.0, "currency": "CNY"},
        "submit": {
            "method": "POST",
            "url": "https://example.test/v1/tts",
            "body_template": {"text": "{text}"},
            "job_id_path": "$.job_id",
        },
        "poll": {
            "url": "https://example.test/v1/tts/{job_id}",
            "status_path": "$.status",
            "status_map": {
                "queued": "queued",
                "running": "running",
                "done": "succeeded",
                "error": "failed",
            },
        },
        "tts": {
            "audio_url_path": "$.audio_url",
            "audio_format": "mp3",
        },
    })


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)

    def __call__(self, method, url, headers, body):
        if not self.script:
            raise AssertionError(f"unexpected {method} {url}")
        return self.script.pop(0)


def _resp(payload):
    return HttpResponse(200, {}, json.dumps(payload).encode())


def test_tts_poll_cancel_raises_provider_canceled(tmp_project, add_shot) -> None:
    shot = add_shot(
        tmp_project, "S001",
        dialogue={"speaker": "A", "text": "你好"},
        generation={"candidates": 1},
    )
    transport = ScriptedTransport([
        _resp({"job_id": "tj1"}),
        _resp({"status": "running"}),
    ])
    provider = GenericTtsProvider(
        _manifest(),
        transport=transport,
        sleep_fn=lambda _s: None,
    )
    n = {"i": 0}

    def cancel_after_first_check() -> bool:
        n["i"] += 1
        return n["i"] >= 2

    with pytest.raises(ProviderCanceled) as ei:
        provider.synthesize(
            tmp_project, shot, tmp_project.load_bible(),
            should_cancel=cancel_after_first_check,
        )
    assert ei.value.job_id == "tj1"
    assert ei.value.provider_id == "tts_test"
    assert not isinstance(ei.value, ProviderFailure)


def test_locale_build_maps_provider_canceled(tmp_project, add_shot, monkeypatch) -> None:
    from manju.build.graph import BuildCanceled
    from manju.build import locale_build as lb
    from manju.providers.base import ProviderCanceled
    import manju.providers.tts as tts_mod

    add_shot(
        tmp_project, "S001",
        dialogue={"speaker": "A", "text": "你好"},
        generation={"candidates": 1},
    )

    class FakeTts:
        id = "fake"

        def synthesize(self, project, shot, bible, **kwargs):
            raise ProviderCanceled("fake", "job-x")

    monkeypatch.setattr(tts_mod, "get_tts_provider", lambda _name=None: FakeTts())
    with pytest.raises(BuildCanceled) as ei:
        lb.synthesize_locale_voices(
            tmp_project,
            [{"shot": "S001", "provider": "fake"}],
            lang="en",
            hold_lock=False,
            should_cancel=lambda: False,
        )
    assert "locale voice:en" in str(ei.value)
