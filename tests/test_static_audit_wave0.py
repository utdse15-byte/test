"""Offline regressions for the 2026-08-06 provider request audit, Wave 0."""

from __future__ import annotations

import copy
import json

import pytest

from manju.providers.base import (
    FailureKind,
    GenerationRequest,
    ProviderFailure,
)
from manju.providers.comfyui import ComfyUIProvider
from manju.providers.generic_cloud import (
    GenericCloudProvider,
    HttpResponse,
    _placeholder_map,
)
from manju.providers.manifest import ProviderManifest
from manju.providers.preflight import (
    STATUS_INCOMPATIBLE,
    check_request_compatibility,
)
from manju.providers.registry import generate_with_fallback


class CaptureTransport:
    def __init__(self):
        self.requests: list[tuple[str, str, dict, bytes | None]] = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return HttpResponse(200, {}, b'{"id":"job-1"}')


def _cloud_manifest(
    provider_id: str = "audit_cloud",
    *,
    refs: dict | None = None,
    limits: dict | None = None,
    body_template: dict | None = None,
) -> ProviderManifest:
    return ProviderManifest.model_validate({
        "id": provider_id,
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": "AUDIT_PROVIDER_KEY"},
        "submit": {
            "url": "https://example.invalid/generate",
            "body_template": body_template or {
                "prompt": "{prompt}",
                "duration": "{duration_s}",
                "width": "{width}",
                "seed": "{seed}",
            },
            "job_id_path": "$.id",
        },
        "poll": {
            "url": "https://example.invalid/jobs/{job_id}",
            "status_path": "$.status",
            "status_map": {"done": "succeeded"},
        },
        **({"refs": refs} if refs is not None else {}),
        **({"limits": limits} if limits is not None else {}),
    })


def _request(project, shot, **params) -> GenerationRequest:
    return GenerationRequest(
        project=project,
        shot=shot,
        bible=project.load_bible(),
        spec_hash="sha256:audit-spec",
        duration_ms=5000,
        params=dict(params),
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("prompt", "bypass compiled prompt"),
        ("duration_s", 60),
        ("duration_ms", 60_000),
        ("width", 8),
        ("height", 8),
        ("fps", 1),
        ("shot_id", "OTHER"),
    ],
)
def test_reserved_generation_param_rejected_before_transport(
    tmp_project, add_shot, monkeypatch, key, value
):
    monkeypatch.setenv("AUDIT_PROVIDER_KEY", "offline-test-key")
    shot = add_shot(tmp_project, "S001")
    transport = CaptureTransport()
    provider = GenericCloudProvider(
        _cloud_manifest(), transport=transport, sleep_fn=lambda _s: None
    )

    with pytest.raises(ProviderFailure) as exc:
        provider.submit(_request(tmp_project, shot, **{key: value}))

    assert exc.value.kind is FailureKind.invalid
    assert exc.value.detail["code"] == "reserved_generation_param"
    assert key in exc.value.detail["keys"]
    assert transport.requests == []


def test_seed_has_one_explicit_recipe_owner(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")
    values = _placeholder_map(_request(tmp_project, shot, seed=73))
    assert values["seed"] == 73


def test_preflight_and_submit_share_reserved_key_rule(
    tmp_project, add_shot, monkeypatch
):
    manifest = _cloud_manifest()
    facts = {
        "provider_id": manifest.id,
        "source_kind": "manifest",
        "capabilities": ["text_to_video"],
        "limits": {},
        "refs": {},
    }
    verdict = check_request_compatibility(
        facts,
        capability="text_to_video",
        duration_ms=5000,
        params={"duration_s": 60},
        body_placeholders=manifest.submit.body_template,
    )
    assert verdict["status"] == STATUS_INCOMPATIBLE
    assert any(r["code"] == "RESERVED_GENERATION_PARAM" for r in verdict["reasons"])

    monkeypatch.setenv("AUDIT_PROVIDER_KEY", "offline-test-key")
    shot = add_shot(tmp_project, "S001")
    transport = CaptureTransport()
    provider = GenericCloudProvider(manifest, transport=transport)
    with pytest.raises(ProviderFailure):
        provider.submit(_request(tmp_project, shot, duration_s=60))
    assert transport.requests == []


def test_comfyui_reserved_values_cannot_be_overridden(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")
    manifest = ProviderManifest.model_validate({
        "id": "audit_comfy",
        "type": "video",
        "adapter": "manju.providers.comfyui:ComfyUIProvider",
        "capabilities": ["text_to_video"],
        "comfyui": {
            "workflow_file": "workflow.json",
            "input_map": {"1.width": "{width}"},
        },
    })
    provider = ComfyUIProvider(manifest, transport=CaptureTransport())
    with pytest.raises(ProviderFailure) as exc:
        provider._values(_request(tmp_project, shot, width=8))
    assert exc.value.detail["code"] == "reserved_generation_param"


def _owned_ref(project, name: str, role: str | None, subject: str | None = None) -> dict:
    path = project.root / "media" / "refs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"offline-ref:" + name.encode("ascii"))
    binding: dict = {"ref": project.relpath(path)}
    if role:
        binding["controls"] = [role]
    if subject:
        binding["subject_ref"] = subject
    return binding


def test_mode_none_owner_blocks_before_transport(
    tmp_project, add_shot, monkeypatch
):
    binding = _owned_ref(
        tmp_project, "identity.png", "character_identity", "character:linxia"
    )
    shot = add_shot(
        tmp_project, "S001", generation={"params": {"refs": [binding]}}
    )
    req = _request(tmp_project, shot, refs=[binding])
    transport = CaptureTransport()
    monkeypatch.setenv("AUDIT_PROVIDER_KEY", "offline-test-key")
    provider = GenericCloudProvider(_cloud_manifest(), transport=transport)

    with pytest.raises(ProviderFailure) as exc:
        provider.submit(req)

    assert exc.value.detail["code"] == "required_reference_omitted"
    assert transport.requests == []


def test_budget_omitted_owner_blocks_before_transport(
    tmp_project, add_shot, monkeypatch
):
    identity = _owned_ref(
        tmp_project, "identity.png", "character_identity", "character:linxia"
    )
    location = _owned_ref(tmp_project, "location.png", "location")
    bindings = [identity, location]
    shot = add_shot(
        tmp_project, "S001", generation={"params": {"refs": bindings}}
    )
    req = _request(tmp_project, shot, refs=bindings)
    transport = CaptureTransport()
    monkeypatch.setenv("AUDIT_PROVIDER_KEY", "offline-test-key")
    provider = GenericCloudProvider(
        _cloud_manifest(
            refs={"image_mode": "base64_field", "field": "$.image", "max_images": 1},
            limits={"max_ref_images": 1},
        ),
        transport=transport,
    )

    with pytest.raises(ProviderFailure) as exc:
        provider.submit(req)

    assert exc.value.detail["code"] == "required_reference_omitted"
    assert location["ref"] in str(exc.value)
    assert transport.requests == []


def test_omitted_nonowner_remains_advisory(
    tmp_project, add_shot, monkeypatch
):
    first = _owned_ref(tmp_project, "a.png", None)
    second = _owned_ref(tmp_project, "b.png", None)
    bindings = [first, second]
    shot = add_shot(
        tmp_project, "S001", generation={"params": {"refs": bindings}}
    )
    req = _request(tmp_project, shot, refs=bindings)
    transport = CaptureTransport()
    monkeypatch.setenv("AUDIT_PROVIDER_KEY", "offline-test-key")
    provider = GenericCloudProvider(
        _cloud_manifest(
            refs={"image_mode": "base64_field", "field": "$.image", "max_images": 1},
            limits={"max_ref_images": 1},
        ),
        transport=transport,
    )

    provider.submit(req)

    assert len(transport.requests) == 1
    assert req.params["ref_delivery"]["budget"]["omitted"][0]["ref"] == second["ref"]


def test_request_identity_uses_only_provider_selected_bindings(
    tmp_project, add_shot, monkeypatch
):
    first = _owned_ref(tmp_project, "selected.png", None)
    omitted = _owned_ref(tmp_project, "omitted.png", None)
    bindings = [first, omitted]
    shot = add_shot(
        tmp_project, "S001", generation={"params": {"refs": bindings}}
    )
    monkeypatch.setenv("AUDIT_PROVIDER_KEY", "offline-test-key")
    provider = GenericCloudProvider(
        _cloud_manifest(
            refs={"image_mode": "base64_field", "field": "$.image", "max_images": 1},
            limits={"max_ref_images": 1},
        )
    )

    req_a = _request(tmp_project, shot, refs=bindings)
    provider._ensure_provider_request(req_a)
    identity_a, digest_a = provider._build_identity(req_a)

    (tmp_project.root / omitted["ref"]).write_bytes(b"changed omitted bytes")
    req_b = _request(tmp_project, shot, refs=copy.deepcopy(bindings))
    provider._ensure_provider_request(req_b)
    identity_b, digest_b = provider._build_identity(req_b)

    assert [r["logical_id"] for r in identity_a["refs"]] == [first["ref"]]
    assert identity_a["refs"] == identity_b["refs"]
    assert digest_a == digest_b


def test_request_identity_binds_final_rendered_body(
    tmp_project, add_shot, monkeypatch
):
    monkeypatch.setenv("AUDIT_PROVIDER_KEY", "offline-test-key")
    shot = add_shot(tmp_project, "S001")
    provider = GenericCloudProvider(_cloud_manifest())
    req = _request(tmp_project, shot, seed=9)
    provider._ensure_provider_request(req)
    identity, _digest = provider._build_identity(req)

    assert identity["rendered_request_digest"].startswith("sha256:")
    facts = provider._rendered_request_facts(req)
    assert identity["rendered_request_digest"] == facts["digest"]
    assert facts["body"]["duration"] == 5.0
    assert facts["body"]["seed"] == 9


class _MutatingFailureProvider:
    id = "attempt_a"

    def generate(self, req):
        req.params["ref_delivery"] = {"provider": self.id}
        req.params["remote_job_id"] = "old-job"
        req._dr06_identity_cache = {self.id: ("polluted", "polluted")}
        raise ProviderFailure(FailureKind.provider_error, "offline failure")


class _CaptureProvider:
    id = "attempt_b"

    def __init__(self):
        self.request = None

    def generate(self, req):
        self.request = req
        return [object()]


def test_fallback_attempts_do_not_share_mutable_request_state(
    tmp_project, add_shot, monkeypatch
):
    shot = add_shot(tmp_project, "S001")
    base = _request(tmp_project, shot, seed=17)
    original = copy.deepcopy(base.params)
    second = _CaptureProvider()
    providers = {"attempt_a": _MutatingFailureProvider(), "attempt_b": second}

    monkeypatch.setattr(
        "manju.providers.registry.get_provider", lambda provider_id: providers[provider_id]
    )
    out = generate_with_fallback(base, ["attempt_a", "attempt_b"])

    assert len(out) == 1
    assert second.request is not base
    assert second.request.params == original
    assert not hasattr(second.request, "_dr06_identity_cache")
    assert base.params == original
    assert not hasattr(base, "_dr06_identity_cache")
