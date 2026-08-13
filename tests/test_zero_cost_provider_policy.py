"""Strict-zero-cost provider egress policy, exercised entirely offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.local_cmd import LocalCommandProvider
from manju.providers.manifest import LOCAL_CMD_ADAPTER, ProviderManifest
from manju.providers.zero_cost import (
    STRICT_ZERO_COST,
    credential_presence,
    execution_policy_snapshot,
    require_manifest_allowed,
    require_transport_allowed,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[tuple] = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return HttpResponse(200, {}, json.dumps({"id": "local-1"}).encode())


def _manifest(url: str, *, key_env: str | None = None) -> ProviderManifest:
    return ProviderManifest.model_validate({
        "id": "local_fake",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": key_env},
        "submit": {
            "url": url,
            "body_template": {"prompt": "{prompt}", "duration": "{duration_s}"},
            "job_id_path": "$.id",
        },
        "poll": {
            "url": url.rstrip("/") + "/{job_id}",
            "status_path": "$.status",
            "status_map": {"done": "succeeded", "running": "running"},
            "result_url_path": "$.result_url",
        },
        "limits": {"max_duration_ms": 6000, "max_resolution": "720p"},
        "cost": {"per_call": 0.0, "per_second": 0.0, "currency": "CNY"},
    })


def _request(tmp_project, add_shot) -> GenerationRequest:
    shot = add_shot(tmp_project, "S001", generation={"candidates": 1})
    return GenerationRequest(
        project=tmp_project,
        shot=shot,
        bible=tmp_project.load_bible(),
        spec_hash="sha256:strict-zero-cost",
        duration_ms=4000,
        candidates=1,
        params={},
    )


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1/generate",
    "http://10.0.0.8:9000/generate",
    "http://127.0.0.2:5198/generate",
    "http://localhost.example.com/generate",
])
def test_strict_mode_rejects_non_loopback_before_custom_transport(
    monkeypatch, tmp_project, add_shot, url
) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    monkeypatch.setenv("SHOULD_NOT_BE_READ", "secret-sentinel")
    transport = RecordingTransport()
    provider = GenericCloudProvider(
        _manifest(url, key_env="SHOULD_NOT_BE_READ"),
        transport=transport,
        sleep_fn=lambda _: None,
    )

    with pytest.raises(ProviderFailure) as caught:
        provider.submit(_request(tmp_project, add_shot))

    assert caught.value.kind is FailureKind.invalid
    assert caught.value.detail["reason_code"] == "STRICT_ZERO_COST_EGRESS_BLOCKED"
    assert caught.value.detail["transport_count"] == 0
    assert "secret-sentinel" not in str(caught.value)
    assert transport.requests == []


def test_strict_mode_allows_credential_free_loopback_fake(
    monkeypatch, tmp_project, add_shot
) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    transport = RecordingTransport()
    provider = GenericCloudProvider(
        _manifest("http://127.0.0.1:5198/v1/generate"),
        transport=transport,
        sleep_fn=lambda _: None,
    )

    assert provider.submit(_request(tmp_project, add_shot)) == "local-1"
    assert len(transport.requests) == 1
    assert "Authorization" not in transport.requests[0][2]


def test_strict_mode_rejects_loopback_credential_reference_without_reading_it(
    monkeypatch, tmp_project, add_shot
) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    monkeypatch.setenv("SHOULD_NOT_BE_READ", "secret-sentinel")
    transport = RecordingTransport()
    provider = GenericCloudProvider(
        _manifest("http://localhost:5198/v1/generate", key_env="SHOULD_NOT_BE_READ"),
        transport=transport,
        sleep_fn=lambda _: None,
    )

    with pytest.raises(ProviderFailure) as caught:
        provider.submit(_request(tmp_project, add_shot))

    assert caught.value.detail["reason_code"] == "STRICT_ZERO_COST_CREDENTIAL_FORBIDDEN"
    assert "secret-sentinel" not in str(caught.value)
    assert transport.requests == []


def test_policy_snapshot_and_digest_are_stable_and_secret_free(monkeypatch) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    monkeypatch.setenv("UNRELATED_PROVIDER_KEY", "never-record-this")

    first = execution_policy_snapshot()
    second = execution_policy_snapshot()

    assert first == second
    assert first["schema"] == "manju.provider-execution-policy/v1"
    assert first["mode"] == STRICT_ZERO_COST
    assert first["credential_resolution"] == "forbidden"
    assert first["arbitrary_subprocess"] == "forbidden"
    assert LOCAL_CMD_ADAPTER not in first["allowed_provider_adapters"]
    assert "never-record-this" not in json.dumps(first)
    assert first["digest"].startswith("sha256:")
    assert credential_presence("UNRELATED_PROVIDER_KEY") is None


def test_unconfigured_mode_preserves_legacy_endpoint_behavior(monkeypatch) -> None:
    monkeypatch.delenv("MANJU_EXECUTION_MODE", raising=False)
    require_transport_allowed("https://api.example.com/v1/generate")


def test_strict_mode_rejects_unaudited_custom_adapter(monkeypatch) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    manifest = _manifest("http://127.0.0.1:5198/v1/generate").model_copy(
        update={"adapter": "third_party.provider:CloudProvider"}
    )

    with pytest.raises(ProviderFailure) as caught:
        require_manifest_allowed(manifest)

    assert caught.value.detail["reason_code"] == "STRICT_ZERO_COST_ADAPTER_FORBIDDEN"
    assert caught.value.detail["transport_count"] == 0


def test_strict_mode_rejects_arbitrary_local_command_adapter(monkeypatch) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    manifest = _manifest("http://127.0.0.1:5198/v1/generate").model_copy(
        update={"adapter": LOCAL_CMD_ADAPTER}
    )

    with pytest.raises(ProviderFailure) as caught:
        require_manifest_allowed(manifest)

    assert caught.value.detail["reason_code"] == "STRICT_ZERO_COST_ADAPTER_FORBIDDEN"
    assert caught.value.detail["transport_count"] == 0
    assert caught.value.detail["adapter"] == LOCAL_CMD_ADAPTER


def test_direct_local_command_provider_is_blocked_before_popen(
    monkeypatch, tmp_project, add_shot
) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    manifest = ProviderManifest.model_validate({
        "id": "local_process",
        "type": "video",
        "adapter": LOCAL_CMD_ADAPTER,
        "capabilities": ["text_to_video"],
        "local_cmd": {"command": "untrusted-tool --out {out}"},
        "cost": {"per_call": 0.0, "currency": "CNY"},
    })
    provider = LocalCommandProvider(manifest)

    def unexpected_popen(*args, **kwargs):
        raise AssertionError("strict mode must reject before Popen")

    monkeypatch.setattr("manju.providers.local_cmd.subprocess.Popen", unexpected_popen)
    with pytest.raises(ProviderFailure) as caught:
        provider.generate(_request(tmp_project, add_shot))

    assert caught.value.detail["reason_code"] == "STRICT_ZERO_COST_ADAPTER_FORBIDDEN"
    assert caught.value.detail["transport_count"] == 0


def test_local_proof_document_is_stable_compatible_and_zero_transport(monkeypatch) -> None:
    from scripts.dogfood.local_proof_preflight import proof_document

    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    profile = Path("scripts/dogfood/providers/localhost_fake_video/provider.yaml")
    first = proof_document(profile)
    second = proof_document(profile)

    assert first == second
    assert first["preflight"]["status"] == "COMPATIBLE"
    assert first["request"]["simulated_cost"] == 0.0
    assert first["provider"]["credential_ref"] is None
    assert first["transport_count"] == first["network_calls"] == 0

def test_strict_mode_rechecks_redirect_target_before_follow_up(monkeypatch) -> None:
    """A 30x from a loopback fake must not become the egress escape hatch:
    the redirect handler re-runs the loopback gate on the NEW url before
    urllib builds the follow-up request. Off-loopback -> refused; a loopback
    target keeps the legacy credential-scrubbing behavior."""
    import urllib.request

    from manju.providers import generic_cloud as gc

    monkeypatch.setenv("MANJU_EXECUTION_MODE", STRICT_ZERO_COST)
    h = gc.CredentialSafeRedirectHandler()
    req = urllib.request.Request("http://127.0.0.1:5198/v1/jobs", method="GET")

    with pytest.raises(ProviderFailure) as caught:
        h.redirect_request(req, None, 302, "Found",
                           {"location": "https://api.example.com/x"},
                           "https://api.example.com/x")
    assert caught.value.detail["reason_code"] == "STRICT_ZERO_COST_EGRESS_BLOCKED"

    new = h.redirect_request(req, None, 302, "Found",
                             {"location": "http://127.0.0.1:5198/v2/jobs"},
                             "http://127.0.0.1:5198/v2/jobs")
    assert new is not None
