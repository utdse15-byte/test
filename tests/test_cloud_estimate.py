"""Cloud spend delta (adversarial review R22 SECONDARY): the pre-flight *事前*
estimate must ride the CLOUD ledger row too, not only local runs.

R22 fixed estimate attribution for LOCAL providers (``_record_local_runs``), but
``CloudProvider._on_success`` recorded its row WITHOUT ``estimated_cost`` — so
cloud money, the feature's whole point, never entered ``spend_report``'s
``estimated_total``/``delta``. The fix threads the plan's per-shot estimate onto
:class:`GenerationRequest` and records it in ``_on_success``.

These tests pin that a cloud generation persists ``req.estimated_cost`` onto its
one ledger row and that the report surfaces it, using the §8.6 generic_cloud
scripted-transport pattern (every network behavior exercised offline). They also
pin that the new field defaults to ``None`` so pre-feature constructors keep
working and an estimate-less cloud run reports honestly.
"""

from __future__ import annotations

import json

import pytest

from manju.build.spend import spend_report
from manju.providers.base import GenerationRequest
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest, estimate_cost
from manju.runtime.state import RuntimeState

# ------------------------------------------------------- scripted-transport rig


def _manifest_dict(**overrides):
    """A minimal generic_cloud video manifest priced per second (§8.6).

    ``poll.cost_path`` lets the scripted API report the ACTUAL per-job cost, so a
    test can drive a real estimate-vs-actual delta rather than a zero total.
    """
    base = {
        "id": "video_x",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video", "vertical"],
        "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}", "duration": "{duration_s}",
                              "seed": "{seed}"},
            "job_id_path": "$.data.task_id",
        },
        "poll": {
            "url": "https://api.example.com/v1/videos/{job_id}",
            "status_path": "$.data.status",
            "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed",
                           "PROCESSING": "running", "PENDING": "queued"},
            "result_url_path": "$.data.video_url",
            "cost_path": "$.data.cost",  # the API reports the real per-job cost
        },
        "limits": {"max_duration_ms": 6000},
        "cost": {"per_second": 0.08, "currency": "CNY"},
    }
    base.update(overrides)
    return base


class ScriptedTransport:
    """Replays a list of responses and records requests (§8.6 offline pattern)."""

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


def _cloud_run_script(actual_cost):
    """submit → poll(running) → poll(succeeded, carrying actual_cost) → download."""
    return [
        _resp(200, {"data": {"task_id": "job_1"}}),
        _resp(200, {"data": {"status": "PROCESSING"}}),
        _resp(200, {"data": {"status": "SUCCEEDED",
                             "video_url": "https://cdn.example.com/out.mp4",
                             "cost": actual_cost}}),
        HttpResponse(200, {}, b"FAKEVIDEO"),
    ]


def _provider(script, monkeypatch, **manifest_overrides):
    monkeypatch.setenv("VIDEO_X_KEY", "k-secret")
    manifest = ProviderManifest.model_validate(_manifest_dict(**manifest_overrides))
    provider = GenericCloudProvider(
        manifest, transport=ScriptedTransport(script), sleep_fn=lambda s: None
    )
    return provider, manifest


def _request(project, add_shot, manifest, *, estimated_cost, duration_ms=4000):
    """A GenerationRequest carrying the plan's pre-flight estimate (the *事前*
    figure the build loop / redo now threads through)."""
    shot = add_shot(project, "S001", generation={"candidates": 1})
    return GenerationRequest(
        project=project, shot=shot, bible=project.load_bible(),
        spec_hash="sha256:test", duration_ms=duration_ms, candidates=1,
        params={"seed": 7}, estimated_cost=estimated_cost,
    )


# --------------------------------------------------------- estimate on the row


def test_cloud_generate_persists_estimate_on_ledger_row(tmp_project, add_shot,
                                                        monkeypatch):
    provider, manifest = _provider(_cloud_run_script(actual_cost=0.5), monkeypatch)
    # the "known manifest price": 4s × 0.08/s × 1 candidate = 0.32 CNY
    estimate = estimate_cost(manifest, 4000, 1)
    assert estimate == pytest.approx(0.32)
    req = _request(tmp_project, add_shot, manifest, estimated_cost=estimate)

    takes = provider.generate(req)
    assert len(takes) == 1

    with RuntimeState(tmp_project.root) as st:
        # one row per generate() call (not one per take): the estimate lands once
        assert st.count_runs() == 1
        row = st.run_log()[0]
    assert row["provider"] == "video_x"
    assert row["status"] == "succeeded"
    assert row["cost"] == pytest.approx(0.5)            # 事后 actual, from the API
    assert row["currency"] == "CNY"
    # the fix: the pre-flight estimate now rides the CLOUD row (was dropped, R22)
    assert row["estimated_cost"] == pytest.approx(req.estimated_cost)


def test_spend_report_includes_cloud_estimate_in_total_and_delta(tmp_project,
                                                                 add_shot,
                                                                 monkeypatch):
    provider, manifest = _provider(_cloud_run_script(actual_cost=0.5), monkeypatch)
    estimate = estimate_cost(manifest, 4000, 1)  # 0.32
    req = _request(tmp_project, add_shot, manifest, estimated_cost=estimate)
    provider.generate(req)

    report = spend_report(tmp_project)
    assert report["source"] == "ledger"
    assert report["total"] == pytest.approx(0.5)
    # cloud estimate now enters estimated_total (R22 SECONDARY: it used to vanish)
    assert report["estimated_total"] == pytest.approx(req.estimated_cost)
    assert report["estimated_total"] == pytest.approx(0.32)
    assert report["delta"] == pytest.approx(0.5 - 0.32)  # actual − estimated
    assert report["recent"][0]["estimated_cost"] == pytest.approx(0.32)


# ----------------------------------------------- default None back-compatibility


def test_generation_request_default_estimate_is_none(tmp_project, add_shot):
    # A constructor predating the field omits estimated_cost entirely -> None:
    # the appended default keeps every old call site valid.
    shot = add_shot(tmp_project, "S001")
    req = GenerationRequest(
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="sha256:test", duration_ms=4000,
    )
    assert req.estimated_cost is None


def test_cloud_generate_without_estimate_records_none(tmp_project, add_shot,
                                                      monkeypatch):
    # A cloud run whose request carried no estimate records None (honest, never
    # coerced to 0), so the report shows no delta for it.
    provider, manifest = _provider(_cloud_run_script(actual_cost=0.5), monkeypatch)
    shot = add_shot(tmp_project, "S001", generation={"candidates": 1})
    req = GenerationRequest(  # built the pre-feature way — no estimated_cost
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="sha256:test", duration_ms=4000, candidates=1, params={"seed": 7},
    )
    assert req.estimated_cost is None

    provider.generate(req)
    with RuntimeState(tmp_project.root) as st:
        row = st.run_log()[0]
    assert row["cost"] == pytest.approx(0.5)   # actual still recorded
    assert row["estimated_cost"] is None       # honest — no estimate available

    report = spend_report(tmp_project)
    assert report["source"] == "ledger"
    assert report["estimated_total"] is None
    assert report["delta"] is None
