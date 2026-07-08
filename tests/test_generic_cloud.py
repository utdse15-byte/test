"""§8.6 generic cloud adapter + §8.2 manifests: onboarding an API = filling
config. Every network behavior is exercised offline via a scripted transport."""

from __future__ import annotations

import json

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse, render_body
from manju.providers.jsonpath import JsonPathError, extract
from manju.providers.manifest import (
    ProviderManifest,
    estimate_cost,
    load_manifests,
)

# ------------------------------------------------------------------ jsonpath


def test_jsonpath_variants():
    data = {"data": {"task_id": "j1", "items": [{"url": "u0"}, {"url": "u1"}]}}
    assert extract(data, "$.data.task_id") == "j1"
    assert extract(data, "data.task_id") == "j1"
    assert extract(data, "$.data.items[1].url") == "u1"
    with pytest.raises(JsonPathError):
        extract(data, "$.data.nope")
    with pytest.raises(JsonPathError):
        extract(data, "$.data.items[9].url")


# ----------------------------------------------------------------- manifest


def _manifest_dict(**overrides):
    base = {
        "id": "video_x",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video", "image_to_video", "vertical"],
        "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}", "duration": "{duration_s}",
                              "seed": "{seed}", "size": "{width}x{height}"},
            "job_id_path": "$.data.task_id",
        },
        "poll": {
            "url": "https://api.example.com/v1/videos/{job_id}",
            "status_path": "$.data.status",
            "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed",
                           "PROCESSING": "running", "PENDING": "queued"},
            "result_url_path": "$.data.video_url",
        },
        "failure": {"content_rejected_when": ["contentPolicy", "risk_control"]},
        "limits": {"max_concurrent": 2, "rate_limit_per_min": 6, "max_duration_ms": 6000},
        "cost": {"per_second": 0.08, "currency": "CNY"},
    }
    base.update(overrides)
    return base


def test_manifest_loading_and_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    write_yaml(tmp_path / "video_x" / "provider.yaml", _manifest_dict())
    write_yaml(tmp_path / "broken" / "provider.yaml", {"type": "video"})  # no id
    manifests, errors = load_manifests()
    assert "video_x" in manifests
    assert len(errors) == 1 and "broken" in errors[0]


def test_manifest_generic_validation(monkeypatch):
    monkeypatch.delenv("VIDEO_X_KEY", raising=False)
    m = ProviderManifest.model_validate(_manifest_dict())
    problems = m.validate_for_generic()
    assert problems == ["auth.key_env VIDEO_X_KEY is not set in the environment"]
    bad = ProviderManifest.model_validate(
        _manifest_dict(poll={"url": "https://x/{job_id}", "status_path": "$.s",
                             "status_map": {"OK": "done"}})
    )
    assert any("status_map" in p for p in bad.validate_for_generic())


def test_estimate_cost():
    m = ProviderManifest.model_validate(_manifest_dict())
    # 4s × 0.08/s × 2 candidates
    assert estimate_cost(m, 4000, 2) == pytest.approx(0.64)


# ------------------------------------------------------------- render_body


def test_render_body_typed_and_embedded():
    values = {"prompt": "雨夜", "duration_s": 4.0, "seed": 7, "width": 1080, "height": 1920}
    out = render_body(
        {"prompt": "{prompt}", "duration": "{duration_s}", "size": "{width}x{height}",
         "nested": {"seed": "{seed}"}, "flag": True},
        values,
    )
    assert out["duration"] == 4.0  # typed, not "4.0"
    assert out["size"] == "1080x1920"  # embedded -> string
    assert out["nested"]["seed"] == 7
    assert out["flag"] is True
    with pytest.raises(ProviderFailure) as exc:
        render_body({"x": "{missing}"}, values)
    assert exc.value.kind is FailureKind.invalid


# --------------------------------------------------- provider with transport


class ScriptedTransport:
    """Replays a list of (predicate-matched) responses and records requests."""

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


@pytest.fixture
def request_for(tmp_project, add_shot):
    def _make(shot_id="S001", duration_ms=4000, **gen_overrides):
        shot = add_shot(tmp_project, shot_id,
                        generation={"candidates": 1, **gen_overrides})
        return GenerationRequest(
            project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
            spec_hash="sha256:test", duration_ms=duration_ms, candidates=1,
            params={"seed": 7},
        )
    return _make


def _provider(script, monkeypatch, **manifest_overrides):
    monkeypatch.setenv("VIDEO_X_KEY", "k-secret")
    manifest = ProviderManifest.model_validate(_manifest_dict(**manifest_overrides))
    transport = ScriptedTransport(script)
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    return provider, transport


def test_happy_path_registers_take(request_for, monkeypatch):
    provider, transport = _provider(
        [
            _resp(200, {"data": {"task_id": "job_1"}}),
            _resp(200, {"data": {"status": "PROCESSING"}}),
            _resp(200, {"data": {"status": "SUCCEEDED",
                                 "video_url": "https://cdn.example.com/out.mp4"}}),
            HttpResponse(200, {}, b"FAKEVIDEO"),
        ],
        monkeypatch,
    )
    req = request_for()
    takes = provider.generate(req)
    assert len(takes) == 1
    sidecar = takes[0].sidecar
    assert sidecar.provider == "video_x"
    assert sidecar.remote and sidecar.remote.job_id == "job_1"
    assert sidecar.remote.currency == "CNY"
    assert sidecar.compiled_prompt  # §8.5: every spend is reproducible
    assert takes[0].media_path.read_bytes() == b"FAKEVIDEO"

    # submit body: typed duration, auth header, seed from params
    method, url, headers, body = transport.requests[0]
    sent = json.loads(body)
    assert sent["duration"] == 4.0 and sent["seed"] == 7
    assert headers["Authorization"] == "Bearer k-secret"


def test_fallback_cost_uses_per_second_and_duration_not_per_call_alone(request_for, monkeypatch):
    """Goal 53: when the response carries no cost_path, the fallback cost
    prices per_call + per_second × duration (the SAME shape estimate_cost()
    uses) — not per_call alone, which under-reports a per-second-priced
    provider."""
    provider, transport = _provider(
        [
            _resp(200, {"data": {"task_id": "job_1"}}),
            _resp(200, {"data": {"status": "SUCCEEDED",
                                 "video_url": "https://cdn.example.com/out.mp4"}}),
            HttpResponse(200, {}, b"FAKEVIDEO"),
        ],
        monkeypatch,
    )
    req = request_for(duration_ms=5000)  # 5s × 0.08/s = 0.4 (per_call defaults to 0)
    takes = provider.generate(req)
    assert takes[0].sidecar.remote.cost == pytest.approx(0.4)


def test_content_rejected_never_retried(request_for, monkeypatch):
    provider, transport = _provider(
        [
            _resp(200, {"data": {"task_id": "job_2"}}),
            _resp(200, {"data": {"status": "FAILED",
                                 "message": "blocked by contentPolicy: 悬疑内容"}}),
        ],
        monkeypatch,
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S002"))
    assert exc.value.kind is FailureKind.content_rejected
    # §8.1: rejection reason preserved for the agent, and no retry happened
    assert "contentPolicy" in str(exc.value.detail)
    assert len(transport.requests) == 2


def test_rate_limited_submit_retries(request_for, monkeypatch):
    provider, transport = _provider(
        [
            _resp(429, {"error": "slow down"}),
            _resp(200, {"data": {"task_id": "job_3"}}),
            _resp(200, {"data": {"status": "SUCCEEDED",
                                 "video_url": "https://cdn.example.com/o.mp4"}}),
            HttpResponse(200, {}, b"V"),
        ],
        monkeypatch,
    )
    takes = provider.generate(request_for("S003"))
    assert len(takes) == 1  # retried after 429 and succeeded


def test_unmapped_status_is_provider_error(request_for, monkeypatch):
    provider, _ = _provider(
        [
            _resp(200, {"data": {"task_id": "job_4"}}),
            _resp(200, {"data": {"status": "WEIRD_STATE"}}),
        ],
        monkeypatch,
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S004"))
    assert exc.value.kind is FailureKind.provider_error
    assert "status_map" in str(exc.value)


def test_missing_key_env_fails_clean(request_for, monkeypatch):
    monkeypatch.delenv("VIDEO_X_KEY", raising=False)
    manifest = ProviderManifest.model_validate(_manifest_dict())
    provider = GenericCloudProvider(
        manifest, transport=ScriptedTransport([]), sleep_fn=lambda s: None
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S005"))
    assert exc.value.kind is FailureKind.invalid
    assert "VIDEO_X_KEY" in str(exc.value)


def test_duration_over_limit_rejected(request_for, monkeypatch):
    provider, _ = _provider([], monkeypatch)  # limit 6000ms in manifest
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S006", duration_ms=8000))
    assert exc.value.kind is FailureKind.invalid


def test_engine_side_throttle(request_for, monkeypatch):
    sleeps: list[float] = []
    clock = iter([0.0, 0.0, 0.1, 0.1, 0.1]).__next__  # 2nd submit 0.1s after 1st
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    manifest = ProviderManifest.model_validate(
        _manifest_dict(limits={"rate_limit_per_min": 60})  # min interval 1s
    )
    script = []
    for job in ("a", "b"):
        script += [
            _resp(200, {"data": {"task_id": job}}),
            _resp(200, {"data": {"status": "SUCCEEDED",
                                 "video_url": f"https://c/{job}.mp4"}}),
            HttpResponse(200, {}, b"V"),
        ]
    provider = GenericCloudProvider(
        manifest, transport=ScriptedTransport(script),
        sleep_fn=sleeps.append, clock=clock,
    )
    provider.generate(request_for("S007"))
    provider.generate(request_for("S008"))
    assert any(0.8 <= s <= 1.0 for s in sleeps), f"no throttle sleep in {sleeps}"


# ----------------------------------------------------- registry integration


def test_registry_and_fallback_resolution(tmp_path, monkeypatch, tmp_project, add_shot):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    write_yaml(tmp_path / "video_x" / "provider.yaml", _manifest_dict())

    from manju.providers.registry import (
        available_providers,
        fallback_chain,
        get_provider,
        manifest_errors,
    )

    assert manifest_errors() == []
    providers = available_providers()
    assert "video_x" in providers and providers["video_x"].kind == "cloud"
    assert get_provider("video_x").id == "video_x"

    # image_to_video has no local mapping -> resolves via manifest capability
    shot = add_shot(tmp_project, "S010")
    chain = fallback_chain(shot)
    assert chain[0] == "video_x" and chain[-1] == "caption_card"


def test_dry_run_prices_from_manifest(tmp_path, monkeypatch, tmp_project, add_shot):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    write_yaml(tmp_path / "video_x" / "provider.yaml", _manifest_dict())

    add_shot(tmp_project, "S001", duration=4.0,
             generation={"provider": "video_x", "candidates": 2})
    from manju.build.graph import run_build

    result = run_build(tmp_project, dry_run=True)
    assert result.ok
    assert len(result.plan) == 1
    # goal 29: generic_cloud's submit/poll/download path always returns exactly
    # ONE result per job — candidates is clamped to 1 for pricing (4s × 0.08 ×
    # 1), not 2, so the estimate matches what will actually be produced/billed.
    assert result.plan[0]["estimated_cost"] == pytest.approx(0.32)
    assert result.estimated_cost == pytest.approx(0.32)
    assert "candidates_note" in result.plan[0]
    assert "已按 1 计价" in result.plan[0]["candidates_note"]


def test_budget_breaker_on_estimate(tmp_path, monkeypatch, tmp_project, add_shot):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    write_yaml(tmp_path / "video_x" / "provider.yaml", _manifest_dict())

    add_shot(tmp_project, "S001", duration=4.0,
             generation={"provider": "video_x", "candidates": 2})
    config = tmp_project.load_config()
    # goal 29: candidates is clamped to 1 for generic_cloud pricing, so the
    # honest estimate is 0.32 (4s × 0.08 × 1), not 0.64 — set the limit below
    # THAT so the breaker still trips.
    config.budget.limit = 0.25
    tmp_project.save_config(config)

    from manju.build.graph import run_build

    result = run_build(tmp_project, dry_run=False)
    assert not result.ok
    assert any("budget" in e for e in result.errors)  # §8.3 熔断 -> waiting_user


def test_dry_run_prices_the_routed_provider_not_the_static_head(
        tmp_path, monkeypatch, tmp_project, add_shot):
    """Goal 7: when a routing.yaml sends the shot to a DIFFERENT provider than
    the static §8.4 fallback-chain head, the dry-run estimate must price the
    provider that will ACTUALLY run — not the untouched fallback head."""
    import manju.providers.registry as registry_mod

    # "aaa_cheap" sorts first alphabetically -> the static fallback chain picks
    # it with no explicit shot.generation.provider and no routing.
    write_yaml(tmp_path / "aaa_cheap" / "provider.yaml",
              {**_manifest_dict(), "id": "aaa_cheap",
               "auth": {"key_env": "AAA_CHEAP_KEY"},
               "cost": {"per_second": 0.01, "currency": "CNY"}})
    write_yaml(tmp_path / "zzz_pricey" / "provider.yaml",
              {**_manifest_dict(), "id": "zzz_pricey",
               "auth": {"key_env": "ZZZ_PRICEY_KEY"},
               "cost": {"per_second": 0.20, "currency": "CNY"}})
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    monkeypatch.setenv("AAA_CHEAP_KEY", "k")
    monkeypatch.setenv("ZZZ_PRICEY_KEY", "k")
    registry_mod._manifest_cache = None

    # no explicit shot.generation.provider — the static chain would pick
    # aaa_cheap (alphabetically-first capable manifest).
    add_shot(tmp_project, "S001", duration=4.0, generation={"candidates": 1})
    from manju.providers.registry import fallback_chain

    shot = tmp_project.load_shot("S001")
    assert fallback_chain(shot)[0] == "aaa_cheap"  # confirms the static head

    # routing sends this shot to zzz_pricey instead (a literal else selector).
    write_yaml(tmp_project.root / "timeline" / "routing.yaml",
              {"strategy": "default",
               "strategies": {"default": {"rules": [], "else": "zzz_pricey"}}})
    registry_mod._manifest_cache = None

    from manju.build.graph import run_build

    result = run_build(tmp_project, dry_run=True)
    assert result.ok
    assert len(result.plan) == 1
    # 4s × 0.20/s (zzz_pricey) — NOT 4s × 0.01/s (aaa_cheap, the static head)
    assert result.plan[0]["estimated_cost"] == pytest.approx(0.80)


# ============================================== network hygiene (goal 43/44/54)


def test_default_transport_refuses_non_http_scheme():
    """Goal 43: a non-http(s) URL (file://, ftp://, data://…) is refused
    before urlopen ever touches it — never a surprise local-file read or
    non-HTTP protocol dial from a manifest/response-supplied URL."""
    from manju.providers.generic_cloud import default_transport

    with pytest.raises(ProviderFailure) as exc:
        default_transport("GET", "file:///etc/passwd", {}, None)
    assert "scheme" in str(exc.value).lower()


def test_default_transport_caps_response_size(monkeypatch):
    """Goal 43: an oversized response is refused mid-stream (never buffered
    unbounded into memory) — configurable via MANJU_MAX_DOWNLOAD_BYTES."""
    import io

    from manju.providers import generic_cloud as gc_mod

    monkeypatch.setenv("MANJU_MAX_DOWNLOAD_BYTES", "10")

    class _FakeResp:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return io.BytesIO(b"x" * 1000).read(n if n and n > 0 else 1000)

    monkeypatch.setattr(gc_mod.urllib.request, "urlopen", lambda *a, **k: _FakeResp())
    with pytest.raises(ProviderFailure) as exc:
        gc_mod.default_transport("GET", "https://example.com/huge", {}, None)
    assert "cap" in str(exc.value).lower() or "10-byte" in str(exc.value)


def test_download_rejects_html_error_page(request_for, monkeypatch):
    """Goal 43/44: a 200 response that is actually an HTML error page (an
    expired signed URL, a CDN error…) must never be written to disk as the
    result media."""
    provider, transport = _provider(
        [
            _resp(200, {"data": {"task_id": "job_1"}}),
            _resp(200, {"data": {"status": "SUCCEEDED",
                                 "video_url": "https://cdn.example.com/out.mp4"}}),
            HttpResponse(200, {"Content-Type": "text/html"},
                        b"<html><body>Link expired</body></html>"),
        ],
        monkeypatch,
    )
    req = request_for()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert "html" in str(exc.value).lower()
