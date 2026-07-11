"""DR06 WP0 characterization — pins CURRENT-HEAD behavior BEFORE any change.

These are the §6.5 characterization program: they encode exactly what the
paid-submission path does TODAY, so the DR06 fail-earlier/fail-closed changes
are provable deltas (each pinned-then-flipped test names one documented change).
Everything here is GREEN on the untouched tree.

The five load-bearing characterizations the contract calls out:
  1. current dangling-intent behavior — advisory (flag) NOT blocking;
  2. current pending-lookup match fields — shot+provider only, NOT digest;
  3. current submit-timeout retry count — a submit-phase timeout is RETRIED
     (max_retries times) — the ambiguity DR06 fail-closes;
  4. current params-hash completeness — intent.params_hash is a hash of the
     bare params dict, NOT the full remote-result-affecting semantics;
  5. current state-write-failure behavior — a broken/absent runtime DB does
     NOT block a cloud submit (§3 disposability; DR06 reverses this for PREPARED).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.core.hashing import hash_text
from manju.providers.base import (
    CloudProvider,
    FailureKind,
    GenerationRequest,
    ProviderFailure,
)
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.runtime.state import RuntimeState


# --------------------------------------------------------- scripted plumbing


class ScriptedTransport:
    """Replays scripted responses; a scripted entry that is an Exception is
    RAISED (models a transport-level failure like default_transport's timeout)."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if not self.script:
            raise AssertionError(f"unexpected request: {method} {url}")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _resp(status, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(status, {}, body)


def _manifest_dict(**overrides):
    base = {
        "id": "video_x",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video", "image_to_video"],
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
        "failure": {"content_rejected_when": ["contentPolicy"]},
        "limits": {"max_concurrent": 2, "rate_limit_per_min": 6},
        "cost": {"per_second": 0.08, "currency": "CNY"},
    }
    base.update(overrides)
    return base


def _provider(script, monkeypatch, **overrides):
    monkeypatch.setenv("VIDEO_X_KEY", "k-secret")
    manifest = ProviderManifest.model_validate(_manifest_dict(**overrides))
    transport = ScriptedTransport(script)
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    return provider, transport


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


class _ScriptedCloud(CloudProvider):
    id = "cloud_test"

    def __init__(self, *, submit_exc=None, **kw):
        kw.setdefault("sleep_fn", lambda _s: None)
        super().__init__(**kw)
        self._submit_exc = submit_exc
        self.submit_calls = 0

    def submit(self, req):
        self.submit_calls += 1
        if self._submit_exc is not None:
            raise self._submit_exc
        return "job_fresh_1"

    def poll(self, job_id):
        return "succeeded", {"cost": 0.5, "currency": "CNY"}

    def download(self, job_id, dest_dir):
        out = Path(dest_dir) / "out.mp4"
        out.write_bytes(b"fake-" + job_id.encode())
        return [out]


def _req(project, shot, **params):
    return GenerationRequest(
        project=project, shot=shot, bible=project.load_bible(),
        spec_hash="sha256:test", duration_ms=3000, candidates=1, params=dict(params))


# ============================================================ (1) dangling = advisory


def test_char_dangling_intent_is_advisory_not_blocking(tmp_project, add_shot):
    """HEAD: an OPEN intent from an earlier crash is flagged as a Failure but
    NEVER blocks the next generate() — it proceeds and succeeds."""
    from manju.core.failures import read_failures

    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        stale = st.open_intent(provider="cloud_test", shot="S001")

    takes = _ScriptedCloud().generate(_req(tmp_project, shot))
    assert len(takes) == 1  # proceeded despite the dangling intent

    failures = read_failures(tmp_project, n=20)
    assert any("未确认的提交意图" in f.get("cause", "") for f in failures)
    with RuntimeState(tmp_project.root) as st:
        assert stale in {d["id"] for d in st.dangling_intents()}  # still open


# ======================================================= (2) pending match fields


def test_char_pending_lookup_matches_shot_and_provider_only(tmp_project, add_shot):
    """HEAD: resume finds a pending job by shot+provider ALONE — the params /
    request semantics are never consulted, so ANY pending job for the pair is
    resumed (this is what DR06 tightens to a request_digest match)."""
    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        # a pending job whose params bear no relation to this request
        st.open_job("job_resume_1", provider="cloud_test", shot="S001",
                    params={"totally": "different"})

    provider = _ScriptedCloud()
    takes = provider.generate(_req(tmp_project, shot, seed=999))
    assert provider.submit_calls == 0  # resumed, never resubmitted
    assert len(takes) == 1
    with RuntimeState(tmp_project.root) as st:
        assert st.run_log()[0]["remote_job_id"] == "job_resume_1"


# ==================================================== (3) submit-timeout retried


def test_char_submit_timeout_is_now_outcome_unknown_no_retry(request_for, monkeypatch):
    """P0 WP2 flip (was test_char_submit_timeout_is_retried_then_raises): a
    submit-phase timeout whose disposition was never classified now DEFAULTS to
    OUTCOME_UNKNOWN at the base choke point — exactly ONE transport attempt (no
    retry: a timed-out submit may have been received remotely; retrying risks a
    double-charge). HEAD retried it max_retries times (4 attempts)."""
    from manju.providers.submission import OUTCOME_UNKNOWN_DISPOSITION

    timeout = ProviderFailure(FailureKind.timeout, "network error calling submit")
    provider, transport = _provider([timeout], monkeypatch)
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S001"))
    assert exc.value.kind is FailureKind.timeout  # kind stays honest
    assert exc.value.disposition == OUTCOME_UNKNOWN_DISPOSITION  # safety bit set
    assert exc.value.detail.get("disposition_defaulted") == "submit_phase"
    submit_posts = [r for r in transport.requests
                    if r[1].endswith("/v1/videos")]
    assert len(submit_posts) == 1, "P0 WP2: no retry on an ambiguous submit outcome"


def test_char_unparseable_submit_ok_is_now_outcome_unknown(request_for, monkeypatch):
    """FLIPPED delta: HEAD made a 2xx-but-unparseable-job-id submit a plain
    provider_error (non-retryable, fallback-ELIGIBLE). DR06 reclassifies this
    ambiguous receipt (the server accepted it; a job MAY exist) as
    OUTCOME_UNKNOWN — the disposition is now set, and the registry stops the
    fallback rather than spending again on an unresolved outcome."""
    provider, transport = _provider([_resp(200, {"data": {"no_task_id": "x"}})], monkeypatch)
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S001"))
    assert exc.value.kind is FailureKind.provider_error
    from manju.providers.submission import OUTCOME_UNKNOWN_DISPOSITION

    assert exc.value.disposition == OUTCOME_UNKNOWN_DISPOSITION  # was None on HEAD


# ================================================== (4) params-hash completeness


def test_char_intent_params_hash_is_bare_params_not_full_semantics(tmp_project, add_shot):
    """HEAD: the pre-submit intent's params_hash is hash_text over the bare
    params dict ONLY — it does NOT bind spec_hash, provider profile, refs or
    the compiled prompt. DR06's request_digest binds the full remote-result-
    affecting semantics instead."""
    shot = add_shot(tmp_project, "S001")
    provider = _ScriptedCloud()
    provider.generate(_req(tmp_project, shot, seed=7))

    with RuntimeState(tmp_project.root) as st:
        row = st._conn.execute("SELECT * FROM intents").fetchone()
    expected = hash_text(str(sorted({"seed": 7}.items())))
    assert row["params_hash"] == expected  # bare params only


def test_char_intents_table_gains_submission_columns_additively(tmp_project):
    """FLIPPED delta: HEAD's intents table had id/provider/shot/params_hash/ts/
    remote_job_id/status only. DR06 adds submission_id (UNIQUE) / request_digest
    / state / updated_ts ADDITIVELY (the #47 migration precedent) — the legacy
    columns are untouched so goal-27 rows still read exactly as before."""
    with RuntimeState(tmp_project.root) as st:
        cols = {c["name"] for c in st._conn.execute("PRAGMA table_info(intents)").fetchall()}
        idx = {r["name"] for r in st._conn.execute(
            "PRAGMA index_list(intents)").fetchall()}
    assert {"status", "params_hash", "remote_job_id"} <= cols  # legacy kept
    assert {"submission_id", "request_digest", "state", "updated_ts"} <= cols  # added
    assert "idx_intents_submission" in idx  # the UNIQUE index behind the CAS


# ================================================ (5) state-write-failure proceeds


def test_char_broken_runtime_state_now_fails_closed_on_cloud_submit(tmp_project, add_shot):
    """FLIPPED delta (the headline fail-closed change): HEAD (§3 disposability)
    swallowed a broken/absent runtime DB and a cloud generate() still SUCCEEDED.
    DR06 reverses this for the PAID path — if the PREPARED intent / DISPATCHING
    claim cannot be persisted, the submit must NOT start. A direct provider call
    raises; through the registry it degrades to the local safety net."""
    import shutil

    shot = add_shot(tmp_project, "S001")
    shutil.rmtree(tmp_project.root / ".manju")
    (tmp_project.root / ".manju").write_text("not a directory", encoding="utf-8")

    provider = _ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0  # the paid submit never started
    assert "fail-closed" in str(exc.value).lower()


# ==================================================== (6) cache-hit short-circuit


def test_char_fresh_cache_hit_never_reaches_the_cloud_provider(
    tmp_project, add_shot, make_take, monkeypatch
):
    """HEAD: a FRESH shot (selected take already usable) skips generation in
    graph.py — the cloud provider's generate() is never entered, so nothing is
    prepared/submitted. DR06 must keep this byte-identical (no identity, no
    claim on a cache hit)."""
    from manju.build.graph import run_build

    shot_id = "S001"
    add_shot(tmp_project, shot_id)
    take = make_take(tmp_project, shot_id, "h")
    tmp_project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))

    calls = {"submit": 0}
    real_submit = GenericCloudProvider.submit

    def spy(self, req):
        calls["submit"] += 1
        return real_submit(self, req)

    monkeypatch.setattr(GenericCloudProvider, "submit", spy)
    result = run_build(tmp_project, target="qc", gen="missing", actor="ai")
    assert result.ok
    assert calls["submit"] == 0  # a cache hit never submits


# ==================================================== clean-success byte anchor


def test_char_clean_cloud_success_records_one_succeeded_run(tmp_project, add_shot):
    """HEAD anchor: a clean cloud submit → poll → download records exactly one
    succeeded run with the cost, and closes the job. DR06 must leave this
    output byte-identical (only additive submission evidence)."""
    shot = add_shot(tmp_project, "S001")
    provider = _ScriptedCloud()
    takes = provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 1 and len(takes) == 1

    with RuntimeState(tmp_project.root) as st:
        assert st.pending_jobs() == []
        log = st.run_log()
        assert log[0]["status"] == "succeeded"
        assert log[0]["remote_job_id"] == "job_fresh_1"
        assert st.total_cost() == (pytest.approx(0.5), "CNY")
