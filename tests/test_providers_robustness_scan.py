"""providers/ robustness & spend-safety — regressions found by the hourly scan
of providers/ (adversarially reproduced before fixing).

Four defects fixed and pinned red-first (the scan also confirmed three more —
a TTS paid-double-charge, a qualification canary-cost mismatch, and a
_split_command Windows-quoting bug — deferred for dedicated treatment because
each is architecturally significant or touches a grep-pinned one-owner):

1. submission._canon dropped secret-named keys at the top level and inside
   nested DICTs but NOT inside dicts nested in a LIST/tuple — the credential
   rode the submission identity and perturbed request_digest (breaking the
   §7.5 "a secret param hashes identically to one without it" contract).
2. generic_cloud.poll caught (JsonPathError, json.JSONDecodeError) but not
   UnicodeDecodeError (a ValueError sibling, not subclass), so a 2xx poll of an
   already-billed job with a non-UTF-8 body crashed the build instead of the
   graceful ('failed', provider_error) return.
6. stock._generate parsed the search response with `(resp.json() or {}).get`,
   so a non-object JSON body (a bare array) crashed with AttributeError —
   bypassing the failures.jsonl recording contract.
7. stock._generate read the rendition URL as `chosen["link"]` with no guard, so
   a rendition entry missing "link" KeyError-crashed generate().
"""

from __future__ import annotations

import pytest

from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.providers.stock import PexelsStockProvider


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return self.script.pop(0)


# --------------------------------------------------------------------------- #
# (1) submission._canon scrubs secrets nested inside a list                     #
# --------------------------------------------------------------------------- #


def _identity(params):
    import manju.providers.submission as submission

    return submission.request_digest(submission.build_submission_identity(
        shot_id="S001", spec_hash="sp", provider_id="p", provider_profile_digest="pd",
        capability="t2v", duration_ms=1000, candidates=1, seed=7,
        params=params, compiled_prompt="hi", ref_refs=[]))


def test_canonical_params_scrubs_secret_nested_in_list():
    import manju.providers.submission as submission

    canon = submission.canonical_params({"items": [{"api_key": "SECRET", "x": 1}]})
    assert "api_key" not in canon["items"][0]  # secret dropped inside the list

    # the digest must be identical with, without, and across rotation of the
    # nested secret (the §7.5 contract the top-level case already honored).
    with_a = _identity({"loras": [{"name": "a", "access_token": "TOK-AAA"}]})
    with_b = _identity({"loras": [{"name": "a", "access_token": "TOK-BBB"}]})
    without = _identity({"loras": [{"name": "a"}]})
    assert with_a == with_b == without


# --------------------------------------------------------------------------- #
# (2) generic_cloud.poll survives a non-UTF-8 2xx body                          #
# --------------------------------------------------------------------------- #


def _generic_manifest():
    return ProviderManifest.model_validate({
        "id": "video_x", "type": "video", "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {"url": "https://api/v1/videos",
                   "body_template": {"prompt": "{prompt}"},
                   "job_id_path": "$.data.task_id"},
        "poll": {"url": "https://api/v1/videos/{job_id}",
                 "status_path": "$.data.status",
                 "status_map": {"SUCCEEDED": "succeeded", "PROCESSING": "running"},
                 "result_url_path": "$.data.video_url"},
        "cost": {"per_second": 0.08, "currency": "CNY"},
    })


def test_poll_non_utf8_body_returns_failed_not_crash(monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k-secret")
    provider = GenericCloudProvider(
        _generic_manifest(),
        transport=ScriptedTransport([HttpResponse(200, {}, b"\xff\xfe\x00 not utf-8")]),
        sleep_fn=lambda s: None,
    )
    status, info = provider.poll("job-123")  # must not raise UnicodeDecodeError
    assert status == "failed"
    assert info["failure_kind"] == FailureKind.provider_error.value


# --------------------------------------------------------------------------- #
# (6, 7) stock._generate never crashes on a malformed search/rendition          #
# --------------------------------------------------------------------------- #


def _stock_provider(script):
    manifest = ProviderManifest.model_validate({
        "id": "pexels", "type": "video",
        "adapter": "manju.providers.stock:PexelsStockProvider",
        "capabilities": ["stock_footage"],
        "auth": {"key_env": "PEXELS_API_KEY"},
        "cost": {"per_call": 0.0, "currency": "CNY"},
    })
    return PexelsStockProvider(manifest, transport=ScriptedTransport(script))


@pytest.fixture
def stock_request(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "px-key")

    def _make(**overrides):
        shot = add_shot(tmp_project, "S001", action={"main": "雨夜"}, **overrides)
        return GenerationRequest(
            project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
            spec_hash="sha256:x", duration_ms=3000, candidates=1, params={})
    return _make


def test_stock_non_object_json_body_is_recorded_failure(stock_request):
    # a 200 whose body is a bare JSON array (not an object) -> ProviderFailure,
    # not an AttributeError from `(list).get(...)`.
    provider = _stock_provider([HttpResponse(200, {}, b"[1, 2, 3]")])
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(stock_request())
    assert exc.value.kind is FailureKind.provider_error


def test_stock_unreadable_json_body_is_recorded_failure(stock_request):
    # a 200 whose body is not decodable/parseable JSON -> ProviderFailure.
    provider = _stock_provider([HttpResponse(200, {}, b"\xff\xfe not json")])
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(stock_request())
    assert exc.value.kind is FailureKind.provider_error


def test_stock_rendition_missing_link_does_not_crash(stock_request):
    # every rendition entry omits "link" — the candidate is skipped, and the
    # empty result becomes a recorded ProviderFailure, not a KeyError crash.
    search = {"videos": [{
        "id": 42, "url": "https://pexels.com/video/42",
        "video_files": [
            {"width": 1080, "height": 1920},   # no "link"
            {"width": 1920, "height": 1080},
        ],
    }]}
    import json as _json
    provider = _stock_provider([HttpResponse(200, {}, _json.dumps(search).encode())])
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(stock_request())
    assert exc.value.kind is FailureKind.provider_error
