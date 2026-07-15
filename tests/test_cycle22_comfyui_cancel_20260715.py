"""Cycle-22: ComfyUI /history poll honors should_cancel via ProviderCanceled."""

from __future__ import annotations

import inspect
import json

import pytest

from manju.providers.base import GenerationRequest, ProviderCanceled
from manju.providers.comfyui import ComfyUIProvider
from manju.providers.generic_cloud import HttpResponse
from manju.providers.manifest import COMFYUI_ADAPTER, ProviderManifest

WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "__placeholder__"}},
    "50": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
    "9": {"class_type": "VHS_VideoCombine", "inputs": {}},
}

INPUT_MAP = {
    "6.text": "{prompt}",
    "3.seed": "{seed}",
    "50.width": "{width}",
    "50.height": "{height}",
}


def _manifest(project):
    wf = project.root / "comfyui" / "wf.json"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(json.dumps(WORKFLOW), encoding="utf-8")
    data = {
        "id": "comfyui",
        "type": "video",
        "adapter": COMFYUI_ADAPTER,
        "capabilities": ["image_to_video", "first_last_frame"],
        "cost": {"per_call": 0.0, "currency": "CNY"},
        "comfyui": {
            "base_url": "http://127.0.0.1:9999",
            "workflow_file": "comfyui/wf.json",
            "input_map": INPUT_MAP,
            "poll_interval_s": 0.01,
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


def test_comfyui_poll_signature_accepts_should_cancel() -> None:
    assert "should_cancel" in inspect.signature(ComfyUIProvider._poll).parameters


def test_comfyui_poll_cancel_raises_provider_canceled(tmp_project, add_shot) -> None:
    """should_cancel tripping mid-poll must raise ProviderCanceled (not failure)."""
    shot = add_shot(tmp_project, "S001", generation={"candidates": 1})
    # Submit succeeds; first history is empty (still running); cancel trips before
    # any further wait so we never need a success body.
    transport = ScriptedTransport([
        _resp(200, {"prompt_id": "p-cancel", "number": 1, "node_errors": {}}),
        _resp(200, {}),  # empty history — still running
    ])
    provider = ComfyUIProvider(
        _manifest(tmp_project),
        transport=transport,
        sleep_fn=lambda _s: None,
    )
    trips = {"n": 0}

    def cancel_after_first_history_round() -> bool:
        # First check is before first history GET (n=0 → False), then after empty
        # history + sleep budget check path we re-enter loop (n=1 → True).
        trips["n"] += 1
        return trips["n"] >= 2

    req = GenerationRequest(
        project=tmp_project,
        shot=shot,
        bible=tmp_project.load_bible(),
        spec_hash="sha256:test",
        duration_ms=4000,
        candidates=1,
        params={"seed": 7},
        should_cancel=cancel_after_first_history_round,
    )
    with pytest.raises(ProviderCanceled) as ei:
        provider.generate(req)
    assert ei.value.job_id == "p-cancel"
    assert ei.value.provider_id == "comfyui"
    # Must NOT have been recorded as a provider failure.
    failures = tmp_project.root / "reports" / "failures.jsonl"
    if failures.exists():
        text = failures.read_text(encoding="utf-8")
        assert "p-cancel" not in text


def test_comfyui_poll_no_cancel_still_succeeds(tmp_project, add_shot) -> None:
    """Default should_cancel=None path remains success-compatible."""
    shot = add_shot(tmp_project, "S001", generation={"candidates": 1})
    outputs = {
        "9": {"videos": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]},
    }
    transport = ScriptedTransport([
        _resp(200, {"prompt_id": "p-ok", "number": 1, "node_errors": {}}),
        _resp(200, {"p-ok": {
            "outputs": outputs,
            "status": {"status_str": "success", "completed": True, "messages": []},
        }}),
        _resp(200, b"\x00\x00fake-mp4-bytes"),
    ])
    provider = ComfyUIProvider(
        _manifest(tmp_project),
        transport=transport,
        sleep_fn=lambda _s: None,
    )
    req = GenerationRequest(
        project=tmp_project,
        shot=shot,
        bible=tmp_project.load_bible(),
        spec_hash="sha256:test",
        duration_ms=4000,
        candidates=1,
        params={"seed": 7},
    )
    takes = provider.generate(req)
    assert len(takes) == 1
