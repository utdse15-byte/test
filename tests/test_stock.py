"""Round K: the stock-footage provider (competitive-study adoption #2) —
verified offline with a scripted transport; live use is one free key away."""

from __future__ import annotations

import json

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.providers.stock import PexelsStockProvider


def _stock_manifest(**overrides):
    base = {
        "id": "pexels",
        "type": "video",
        "adapter": "manju.providers.stock:PexelsStockProvider",
        "capabilities": ["stock_footage"],
        "auth": {"key_env": "PEXELS_API_KEY"},
        "cost": {"per_call": 0.0, "currency": "CNY"},
    }
    base.update(overrides)
    return base


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return self.script.pop(0)


SEARCH_RESULT = {
    "videos": [{
        "id": 42, "url": "https://pexels.com/video/42",
        "video_files": [
            {"width": 1080, "height": 1920, "link": "https://cdn/v42_portrait.mp4"},
            {"width": 1920, "height": 1080, "link": "https://cdn/v42_landscape.mp4"},
            {"width": 540, "height": 960, "link": "https://cdn/v42_small.mp4"},
        ],
    }]
}


@pytest.fixture
def request_for(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "px-key")

    def _make(**shot_overrides):
        shot = add_shot(tmp_project, "S001",
                        action={"main": "雨夜的便利店门口"}, **shot_overrides)
        return GenerationRequest(
            project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
            spec_hash="sha256:x", duration_ms=3000, candidates=1, params={},
        )
    return _make


def test_search_download_register_with_lineage(request_for):
    provider = PexelsStockProvider(
        ProviderManifest.model_validate(_stock_manifest()),
        transport=ScriptedTransport([
            HttpResponse(200, {}, json.dumps(SEARCH_RESULT).encode()),
            HttpResponse(200, {}, b"STOCKVIDEO"),
        ]),
    )
    req = request_for()
    takes = provider.generate(req)
    assert len(takes) == 1
    sidecar = takes[0].sidecar
    assert sidecar.params["query"] == "雨夜的便利店门口"  # action line drove the search
    assert sidecar.params["source_url"] == "https://pexels.com/video/42"
    assert sidecar.params["rendition"] == "1080x1920"  # portrait match for 1080x1920

    method, url, headers, _ = provider._transport.requests[0]
    assert "orientation=portrait" in url
    assert headers["Authorization"] == "px-key"


def test_missing_key_is_actionable(request_for, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    provider = PexelsStockProvider(
        ProviderManifest.model_validate(_stock_manifest()),
        transport=ScriptedTransport([]),
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for())
    assert exc.value.kind is FailureKind.invalid
    assert "pexels.com/api" in str(exc.value)


def test_no_results_names_the_query(request_for):
    provider = PexelsStockProvider(
        ProviderManifest.model_validate(_stock_manifest()),
        transport=ScriptedTransport([HttpResponse(200, {}, b'{"videos": []}')]),
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for())
    assert "雨夜的便利店门口" in str(exc.value)


def test_stock_failure_is_recorded_in_failures_jsonl(request_for, tmp_project):
    """F2: stock is a plain Provider on the shot fallback chain (like
    comfyui/local_cmd), so its terminal failures must land in
    reports/failures.jsonl in the house shape — the registry's fallback walker
    never records. RED at HEAD: stock raised without recording, so `manju
    failures` was blind to a stock outage."""
    from manju.core.failures import read_failures

    provider = PexelsStockProvider(
        ProviderManifest.model_validate(_stock_manifest()),
        transport=ScriptedTransport([HttpResponse(500, {}, b"upstream exploded")]),
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for())
    assert exc.value.kind is FailureKind.provider_error

    recs = read_failures(tmp_project, 5)
    assert recs, "stock failure was not recorded to failures.jsonl"
    r = recs[0]
    assert r["step"] == "generate" and r["subject"] == "S001"
    assert r["detail"]["provider"] == "pexels"
    assert r["detail"]["failure_kind"] == "provider_error"
    assert "HTTP 500" in r["cause"]
    assert "upstream exploded" in r["evidence"]


def test_stock_download_rejects_html_error_page(request_for, tmp_project):
    """F11: a stock rendition that 200s with an HTML error page (an expired CDN
    link, a rate-limit interstitial, a login wall) must never be registered as a
    poisoned .mp4 take — the SAME guard comfyui/generic_cloud/tts apply. RED at
    HEAD: the HTML bytes were written and registered as a take."""
    provider = PexelsStockProvider(
        ProviderManifest.model_validate(_stock_manifest()),
        transport=ScriptedTransport([
            HttpResponse(200, {}, json.dumps(SEARCH_RESULT).encode()),
            HttpResponse(200, {"Content-Type": "text/html"},
                         b"<html><body>rate limited</body></html>"),
        ]),
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for())
    assert "html" in str(exc.value).lower()
    # no poisoned take was registered for the shot
    gen_dir = tmp_project.root / "media" / "gen" / "S001"
    assert not gen_dir.exists() or not list(gen_dir.glob("*.mp4"))


def test_fallback_chain_routes_stock_footage_step(tmp_path, monkeypatch,
                                                  tmp_project, add_shot):
    """A shot listing 'stock_footage' in its fallback resolves to the manifest
    provider via capability routing — no engine change needed (§8.6)."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    write_yaml(tmp_path / "pexels" / "provider.yaml", _stock_manifest())

    from manju.providers.registry import fallback_chain, get_provider

    shot = add_shot(tmp_project, "S010",
                    generation={"fallback": ["stock_footage", "still_frame_motion",
                                             "caption_card"]})
    chain = fallback_chain(shot)
    assert chain[0] == "pexels" and chain[-1] == "caption_card"
    assert isinstance(get_provider("pexels"), PexelsStockProvider)
