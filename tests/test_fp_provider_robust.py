"""FP provider-robustness — generic_cloud poll transient-5xx (F6), the
per-job bookkeeping-dict eviction (F3), and the shared status→kind helper (F4).

Every network behavior is exercised OFFLINE via a scripted transport, exactly
like test_generic_cloud. The paid submit path, idempotency and DR06 admission
are UNTOUCHED — these pins only cover the idempotent poll/status read and the
in-memory bookkeeping.
"""

from __future__ import annotations

import json

import pytest

from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest


def _manifest_dict(**overrides):
    base = {
        "id": "video_x",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
            "job_id_path": "$.data.task_id",
        },
        "poll": {
            "url": "https://api.example.com/v1/videos/{job_id}",
            "status_path": "$.data.status",
            "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed",
                           "PROCESSING": "running", "PENDING": "queued"},
            "result_url_path": "$.data.video_url",
        },
        "limits": {"max_duration_ms": 60000},
        "cost": {"per_second": 0.08, "currency": "CNY"},
    }
    base.update(overrides)
    return base


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


def _provider(script, monkeypatch, **kwargs):
    monkeypatch.setenv("VIDEO_X_KEY", "k-secret")
    kwargs.setdefault("sleep_fn", lambda s: None)
    manifest = ProviderManifest.model_validate(_manifest_dict())
    transport = ScriptedTransport(script)
    return GenericCloudProvider(manifest, transport=transport, **kwargs), transport


@pytest.fixture
def request_for(tmp_project, add_shot):
    def _make(shot_id="S001", duration_ms=4000, **gen_overrides):
        shot = add_shot(tmp_project, shot_id,
                        generation={"candidates": 1, **gen_overrides})
        return GenerationRequest(
            project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
            spec_hash="sha256:test", duration_ms=duration_ms, candidates=1,
            params={"seed": 7})
    return _make


def _submits(transport):
    return [r for r in transport.requests if r[0] == "POST"]


# ============================================================ F6: poll 5xx


def test_poll_5xx_transient_completes_with_exactly_one_submit(request_for, monkeypatch):
    """F6: a transient 5xx (503) during ONE poll GET is NOT terminal — polling
    continues within the budget and the PAID job completes, with EXACTLY ONE
    submit (never a resubmit → no double-charge). RED at HEAD: a 503 poll was a
    terminal provider_error that killed the in-flight job."""
    provider, transport = _provider([
        _resp(200, {"data": {"task_id": "job_1"}}),          # submit
        _resp(503, b"upstream hiccup"),                       # transient poll blip
        _resp(200, {"data": {"status": "PROCESSING"}}),       # still running
        _resp(200, {"data": {"status": "SUCCEEDED",
                             "video_url": "https://cdn.example.com/out.mp4"}}),
        HttpResponse(200, {}, b"FAKEVIDEO"),                  # download
    ], monkeypatch)
    takes = provider.generate(request_for())
    assert len(takes) == 1
    assert takes[0].media_path.read_bytes() == b"FAKEVIDEO"
    assert len(_submits(transport)) == 1  # the 5xx never resubmitted


def test_poll_4xx_stays_terminal(request_for, monkeypatch):
    """F6 boundary: a 4xx (404) during poll is a definite terminal failure — only
    5xx is treated as transient. The chain is not kept alive on a client error."""
    provider, transport = _provider([
        _resp(200, {"data": {"task_id": "job_1"}}),
        _resp(404, b"no such job"),
    ], monkeypatch)
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for())
    assert exc.value.kind is FailureKind.provider_error
    assert len(_submits(transport)) == 1


def test_poll_5xx_forever_hits_poll_timeout_not_infinite_loop(request_for, monkeypatch):
    """F6: a 5xx that NEVER clears degrades to the EXISTING poll-timeout budget
    (FailureKind.timeout), never an infinite loop. A tiny timeout_s + a no-op
    sleep_fn (the budget is measured against accumulated INTENDED sleep) make it
    deterministic. Still exactly one submit."""
    script = [_resp(200, {"data": {"task_id": "job_1"}})] + [_resp(503, b"down")] * 60
    provider, transport = _provider(script, monkeypatch, timeout_s=2.0, max_retries=1)
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for())
    assert exc.value.kind is FailureKind.timeout
    assert len(_submits(transport)) == 1  # re-polls the SAME job, never resubmits


# ============================================================ F3: dict eviction


def test_download_evicts_job_dicts(request_for, monkeypatch):
    """F3: after a successful generate+download the per-job bookkeeping dicts do
    NOT retain the job_id — a registry-cached provider instance reused across a
    build of hundreds of shots must not accumulate them monotonically. RED at
    HEAD: _results/_submitted grew unbounded (no del/pop anywhere)."""
    provider, _ = _provider([
        _resp(200, {"data": {"task_id": "job_1"}}),
        _resp(200, {"data": {"status": "SUCCEEDED",
                             "video_url": "https://cdn.example.com/out.mp4"}}),
        HttpResponse(200, {}, b"FAKEVIDEO"),
    ], monkeypatch)
    takes = provider.generate(request_for())
    assert len(takes) == 1
    assert provider._results == {}    # popped at the terminal download read
    assert provider._submitted == {}


def test_two_successive_jobs_do_not_accumulate(request_for, monkeypatch):
    """F3: two generate() calls on ONE cached instance leave both dicts empty —
    each job's entry is evicted at its own terminal download."""
    script = []
    for job in ("a", "b"):
        script += [
            _resp(200, {"data": {"task_id": job}}),
            _resp(200, {"data": {"status": "SUCCEEDED",
                                 "video_url": f"https://cdn.example.com/{job}.mp4"}}),
            HttpResponse(200, {}, b"V"),
        ]
    provider, _ = _provider(script, monkeypatch)
    provider.generate(request_for("S001"))
    provider.generate(request_for("S002"))
    assert provider._results == {} and provider._submitted == {}


# ============================================================ F4: status_to_kind


def test_status_to_kind_maps_only_429_to_rate_limited():
    """F4: the shared status→kind — 429 is the ONLY safely-retryable status
    (rate_limited); every other error status (5xx outage, other 4xx) is
    provider_error. Content rejection is body-driven, deliberately out of scope."""
    from manju.providers.base import status_to_kind

    assert status_to_kind(429) is FailureKind.rate_limited
    for s in (400, 401, 403, 404, 408, 500, 502, 503, 504):
        assert status_to_kind(s) is FailureKind.provider_error
