"""ComfyUI adapter (round-q) — verified OFFLINE with a scripted transport
exactly like the stock provider: fake /prompt, /history, /view responses. The
ComfyUI HTTP shape (POST /prompt -> prompt_id, GET /history/{id} outputs, GET
/view download) is exercised end to end without a running ComfyUI."""

from __future__ import annotations

import json

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
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


def _manifest(project=None, **comfy_overrides):
    comfy = {
        "base_url": "http://127.0.0.1:9999",
        "workflow_file": "comfyui/wf.json",
        "input_map": INPUT_MAP,
        "poll_interval_s": 0.01,
    }
    comfy.update(comfy_overrides)
    data = {
        "id": "comfyui",
        "type": "video",
        "adapter": COMFYUI_ADAPTER,
        "capabilities": ["image_to_video", "first_last_frame"],
        "cost": {"per_call": 0.0, "currency": "CNY"},
        "comfyui": comfy,
    }
    if project is not None:
        wf = project.root / "comfyui" / "wf.json"
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_text(json.dumps(WORKFLOW), encoding="utf-8")  # API-format = JSON
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


class RefusingTransport:
    def __call__(self, method, url, headers, body):
        raise ConnectionRefusedError("[Errno 111] Connection refused")


def _resp(status, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(status, {}, body)


def _history_ok(prompt_id, outputs):
    return _resp(200, {prompt_id: {"outputs": outputs,
                                   "status": {"status_str": "success", "completed": True,
                                              "messages": []}}})


@pytest.fixture
def request_for(tmp_project, add_shot):
    def _make(shot_id="S001", duration_ms=4000, **gen):
        shot = add_shot(tmp_project, shot_id, generation={"candidates": 1, **gen})
        return GenerationRequest(
            project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
            spec_hash="sha256:test", duration_ms=duration_ms, candidates=1,
            params={"seed": 7},
        )
    return _make


def test_input_mapping_poll_to_success_and_lineage(request_for, tmp_project):
    provider = ComfyUIProvider(
        _manifest(tmp_project),
        transport=ScriptedTransport([
            _resp(200, {"prompt_id": "p1", "number": 1, "node_errors": {}}),
            _resp(200, {}),  # /history empty -> still running (poll waits)
            _history_ok("p1", {"9": {"gifs": [
                {"filename": "final.mp4", "subfolder": "vids", "type": "output"}]}}),
            HttpResponse(200, {}, b"VIDEOBYTES"),
        ]),
        sleep_fn=lambda s: None,
    )
    req = request_for()
    takes = provider.generate(req)
    assert len(takes) == 1
    sc = takes[0].sidecar

    # input_map wrote typed/string values into the RIGHT node inputs
    method, url, _, body = provider._transport.requests[0]
    assert method == "POST" and url.endswith("/prompt")
    sent = json.loads(body)
    graph = sent["prompt"]
    assert "client_id" in sent
    assert graph["6"]["inputs"]["text"] == sc.compiled_prompt  # {prompt} -> compiled
    assert graph["3"]["inputs"]["seed"] == 7                    # {seed} -> typed int
    assert graph["50"]["inputs"]["width"] == 1080               # from project config

    # polling waited for the empty /history before the success entry
    assert provider._transport.requests[1][1].endswith("/history/p1")
    assert provider._transport.requests[2][1].endswith("/history/p1")

    # download went to /view with filename/subfolder/type and produced the take
    assert "filename=final.mp4" in provider._transport.requests[3][1]
    assert takes[0].media_path.read_bytes() == b"VIDEOBYTES"

    # lineage (§4.2): workflow hash, mapped inputs, prompt_id, output, duration
    assert sc.params["workflow_file"] == "comfyui/wf.json"
    assert sc.params["workflow_sha256"].startswith("sha256:")
    assert sc.params["prompt_id"] == "p1"
    assert sc.params["mapped_inputs"]["3.seed"] == 7
    assert sc.params["output"]["filename"] == "final.mp4"
    assert sc.params["output"]["output_key"] == "gifs"
    assert "duration_s" in sc.params
    assert sc.remote and sc.remote.job_id == "p1" and sc.remote.cost == 0.0


def test_node_error_surfaces_node_id(request_for, tmp_project):
    provider = ComfyUIProvider(
        _manifest(tmp_project),
        transport=ScriptedTransport([
            _resp(200, {"prompt_id": "p2"}),
            _resp(200, {"p2": {"outputs": {}, "status": {
                "status_str": "error", "completed": False, "messages": [
                    ["execution_start", {}],
                    ["execution_error", {"node_id": "3", "node_type": "KSampler",
                                         "exception_message": "CUDA out of memory",
                                         "exception_type": "RuntimeError"}],
                ]}}}),
        ]),
        sleep_fn=lambda s: None,
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S002"))
    assert exc.value.kind is FailureKind.provider_error
    assert "node 3" in str(exc.value) and "CUDA out of memory" in str(exc.value)
    assert exc.value.detail["node_id"] == "3"


def test_workflow_validation_error_is_surfaced(request_for, tmp_project):
    provider = ComfyUIProvider(
        _manifest(tmp_project),
        transport=ScriptedTransport([
            _resp(400, {"error": {"type": "prompt_outputs_failed_validation"},
                        "node_errors": {"3": {"errors": ["seed out of range"]}}}),
        ]),
        sleep_fn=lambda s: None,
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S003"))
    assert exc.value.kind is FailureKind.provider_error
    assert "400" in str(exc.value)
    assert "node_errors" in str(exc.value.detail)


def test_connection_refused_names_base_url(request_for, tmp_project):
    provider = ComfyUIProvider(_manifest(tmp_project), transport=RefusingTransport())
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S004"))
    assert exc.value.kind is FailureKind.provider_error
    assert "127.0.0.1:9999" in str(exc.value)
    assert "is ComfyUI running?" in str(exc.value)


def test_missing_workflow_file_is_invalid(request_for, tmp_project):
    # manifest points at a workflow that was never exported
    provider = ComfyUIProvider(_manifest(), transport=ScriptedTransport([]))
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S005"))
    assert exc.value.kind is FailureKind.invalid
    assert "workflow file not found" in str(exc.value)


# ------------------------------------------------- output selection order

def _select(outputs, **overrides):
    prov = ComfyUIProvider(_manifest(**overrides))
    return prov._select_output(outputs)


def test_output_selection_prefers_video_then_gif_then_image():
    img = {"filename": "a.png", "subfolder": "", "type": "output"}
    vid = {"filename": "b.mp4", "subfolder": "", "type": "output"}
    gif = {"filename": "c.gif", "subfolder": "", "type": "output"}

    # a video anywhere beats an image on another node
    chosen = _select({"9": {"images": [img]}, "10": {"videos": [vid]}})
    assert chosen["output_key"] == "videos" and chosen["filename"] == "b.mp4"

    # no video -> gif beats image
    chosen = _select({"9": {"gifs": [gif]}, "10": {"images": [img]}})
    assert chosen["output_key"] == "gifs" and chosen["node_id"] == "9"

    # only images -> the first (numerically-sorted) image node wins
    chosen = _select({"10": {"images": [img]}, "9": {"images": [
        {"filename": "z.png", "subfolder": "", "type": "output"}]}})
    assert chosen["node_id"] == "9"  # "9" sorts before "10" numerically

    assert _select({}) is None


def test_output_node_pins_the_pick():
    a = {"filename": "a.png", "subfolder": "", "type": "output"}
    b = {"filename": "b.png", "subfolder": "", "type": "output"}
    chosen = _select({"9": {"images": [a]}, "10": {"images": [b]}}, output_node="10")
    assert chosen["node_id"] == "10" and chosen["filename"] == "b.png"


# --------------------------------------------------- registry integration

def test_fallback_chain_routes_image_to_video(tmp_path, monkeypatch, tmp_project,
                                               add_shot):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    write_yaml(tmp_path / "comfyui" / "provider.yaml", {
        "id": "comfyui", "type": "video", "adapter": COMFYUI_ADAPTER,
        "capabilities": ["image_to_video", "first_last_frame"],
        "cost": {"per_call": 0.0},
        "comfyui": {"workflow_file": "comfyui/wf.json"},
    })
    from manju.providers.registry import fallback_chain, get_provider, manifest_errors

    assert manifest_errors() == []
    shot = add_shot(tmp_project, "S010",
                    generation={"fallback": ["image_to_video", "still_frame_motion",
                                             "caption_card"]})
    chain = fallback_chain(shot)
    assert chain[0] == "comfyui" and chain[-1] == "caption_card"
    assert isinstance(get_provider("comfyui"), ComfyUIProvider)
