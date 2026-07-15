"""Cycle-23: ASR async poll honors should_cancel between rounds."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from manju.providers.asr import GenericAsrProvider
from manju.providers.base import FailureKind, ProviderFailure
from manju.providers.generic_cloud import HttpResponse
from manju.providers.manifest import GENERIC_ASR_ADAPTER, ProviderManifest


def _manifest(*, async_poll: bool = True) -> ProviderManifest:
    data = {
        "id": "asr_test",
        "type": "asr",
        "adapter": GENERIC_ASR_ADAPTER,
        "cost": {"per_call": 0.0, "currency": "CNY"},
        "submit": {
            "method": "POST",
            "url": "https://example.test/v1/asr",
            "body_template": {"audio": "{audio_b64}"},
            "job_id_path": "$.job_id",
        },
        "asr": {
            "segments_path": "$.segments",
            "start_key": "start",
            "end_key": "end",
            "text_key": "text",
            "time_unit": "ms",
            "language": "zh",
        },
    }
    if async_poll:
        data["poll"] = {
            "url": "https://example.test/v1/asr/{job_id}",
            "status_path": "$.status",
            "status_map": {
                "queued": "queued",
                "running": "running",
                "done": "succeeded",
                "error": "failed",
            },
        }
    return ProviderManifest.model_validate(data)


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if not self.script:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.script.pop(0)


def _resp(status, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(status, {}, body)


def test_asr_transcribe_signature_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(GenericAsrProvider.transcribe).parameters
    assert "should_cancel" in inspect.signature(GenericAsrProvider._poll_segments).parameters


def test_asr_async_poll_cancel(tmp_path: Path) -> None:
    media = tmp_path / "a.wav"
    media.write_bytes(b"RIFF....WAVE")
    transport = ScriptedTransport([
        _resp(200, {"job_id": "j1"}),
        _resp(200, {"status": "running"}),
    ])
    provider = GenericAsrProvider(
        _manifest(async_poll=True),
        transport=transport,
        sleep_fn=lambda _s: None,
    )
    n = {"i": 0}

    def cancel_after_first_poll_check() -> bool:
        n["i"] += 1
        return n["i"] >= 2  # allow first poll GET, cancel on re-entry

    with pytest.raises(ProviderFailure) as ei:
        provider.transcribe(media, should_cancel=cancel_after_first_poll_check)
    assert ei.value.kind == FailureKind.provider_error
    assert ei.value.detail.get("canceled") is True
    assert "j1" in str(ei.value)


def test_asr_async_poll_succeeds_without_cancel(tmp_path: Path) -> None:
    media = tmp_path / "a.wav"
    media.write_bytes(b"RIFF....WAVE")
    transport = ScriptedTransport([
        _resp(200, {"job_id": "j2"}),
        _resp(200, {
            "status": "done",
            "segments": [
                {"start": 0, "end": 1000, "text": "你好"},
            ],
        }),
    ])
    provider = GenericAsrProvider(
        _manifest(async_poll=True),
        transport=transport,
        sleep_fn=lambda _s: None,
    )
    segs = provider.transcribe(media)
    assert len(segs) == 1
    assert segs[0].text == "你好"
