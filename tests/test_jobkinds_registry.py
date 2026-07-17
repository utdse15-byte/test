"""Consistency tests for the single authoritative job-kind registry (P1 item 4).

These are BEHAVIORAL: they exercise the registry API and the live
``GET /api/meta/job-kinds`` endpoint, and tie registry declarations to runtime
Job flags. None of them scan source text or report files.
"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from manju.core import jobkinds
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, RETRYABLE_KINDS, Job
from manju.gui.server import create_server


# --------------------------------------------------------------------------- #
# Registry internal consistency
# --------------------------------------------------------------------------- #

def test_every_kind_has_a_user_facing_label() -> None:
    for kind, s in jobkinds.JOB_KINDS.items():
        assert s.display_name_zh, f"{kind} has no 中文 label"


def test_every_paid_operation_declares_billing_metadata() -> None:
    for kind in jobkinds.paid_kinds():
        s = jobkinds.JOB_KINDS[kind]
        assert s.spend_endpoint, f"{kind} is paid but declares no spend_endpoint"
        assert s.paid is True


def test_paid_iff_spend_endpoint() -> None:
    for s in jobkinds.JOB_KINDS.values():
        assert s.paid == (s.spend_endpoint is not None)


def test_every_retryable_kind_is_labeled_and_paid() -> None:
    # Retry only ever rebuilds the spend-gated generation kinds; each must be
    # labeled (the UI shows a retry chip) and declare its billing bucket.
    for kind in jobkinds.retryable_kinds():
        s = jobkinds.JOB_KINDS[kind]
        assert s.display_name_zh
        assert s.spend_endpoint, f"retryable {kind} has no billing metadata"


def test_locale_aware_kinds_are_registered_and_include_qc_build_voice() -> None:
    la = jobkinds.locale_aware_kinds()
    assert la <= jobkinds.all_kinds()
    for k in ("build", "voice", "qc"):
        assert k in la, f"{k} must be locale-aware for multi-locale work"


def test_destructive_kinds_are_opt_in_and_currently_empty() -> None:
    # Append-only architecture: no kind irreversibly overwrites source truth.
    assert jobkinds.destructive_kinds() == frozenset()


# --------------------------------------------------------------------------- #
# The derived structures ARE views onto the registry (no duplication)
# --------------------------------------------------------------------------- #

def test_gui_frozensets_are_registry_views() -> None:
    assert RETRYABLE_KINDS == jobkinds.retryable_kinds()
    assert CANCELABLE_RUNNING_KINDS == jobkinds.cancelable_running_kinds()


def test_registry_matches_the_historical_pinned_contents() -> None:
    # The exact contents other tests pin, proven here to be registry-derived.
    assert jobkinds.retryable_kinds() == frozenset(
        {"build", "redo", "voice", "redo_batch", "voice_batch"})
    assert jobkinds.cancelable_running_kinds() == frozenset({
        "build", "redo_batch", "voice_batch", "voice", "redo", "ingest_plan",
        "ingest", "series_sync_bible", "edit_preview_batch", "voice_preview",
        "export", "repair", "qc", "handle_rebuild", "roundtrip"})


# --------------------------------------------------------------------------- #
# Registry declarations drive runtime Job flags (cancelable / retryable)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("kind", sorted(jobkinds.all_kinds()))
def test_running_job_cancelable_flag_matches_registry(kind: str) -> None:
    j = Job(id="t", kind=kind, params={}, project_id="p")
    j.state = "running"
    expected = kind in jobkinds.cancelable_running_kinds()
    assert j.to_dict()["cancelable"] is expected


@pytest.mark.parametrize("kind", sorted(jobkinds.all_kinds()))
def test_failed_job_retryable_flag_matches_registry(kind: str) -> None:
    # A failed job of a retryable kind is offered retry (routes to the GUI's
    # retry factory); non-retryable kinds are not. This is the runtime proof
    # that "every retryable job has a retry factory path".
    j = Job(id="t", kind=kind, params={}, project_id="p")
    j.state = "failed"
    expected = kind in jobkinds.retryable_kinds()
    assert j.to_dict()["retryable"] is expected


# --------------------------------------------------------------------------- #
# The read-only metadata endpoint the frontend consumes
# --------------------------------------------------------------------------- #

@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.close()


def _get_json(server, path):
    url = f"http://127.0.0.1:{server.port}{path}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, method="GET")
    with opener.open(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_meta_endpoint_serves_the_registry(gui) -> None:
    status, data = _get_json(gui, "/api/meta/job-kinds")
    assert status == 200
    rows = data["job_kinds"]
    served = {r["kind"]: r for r in rows}
    # Endpoint covers exactly the registry — no more, no unknown kinds.
    assert set(served) == set(jobkinds.all_kinds())
    for kind, spec in jobkinds.JOB_KINDS.items():
        r = served[kind]
        assert r["display_name_zh"] == spec.display_name_zh
        assert r["cancelable_while_running"] == spec.cancelable_while_running
        assert r["retryable"] == spec.retryable
        assert r["paid"] == spec.paid
        assert r["spend_endpoint"] == spec.spend_endpoint
        assert r["locale_aware"] == spec.locale_aware


def test_meta_endpoint_only_references_known_kinds(gui) -> None:
    # The frontend builds its label map solely from this payload, so if the
    # payload only carries registered kinds, the frontend cannot reference an
    # unknown one.
    _status, data = _get_json(gui, "/api/meta/job-kinds")
    assert jobkinds.known(r["kind"] for r in data["job_kinds"])
