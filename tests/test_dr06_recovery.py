"""DR06 — recovery: pending correlation, rebuild-from-events, chain-corruption
scoping, no-TTL, abandon/attach subcommands, declared idempotency, legacy rows
(contract rulings 4/8/9/10/11).

Scripted in-process cloud providers + the CLI (typer CliRunner) drive the real
recovery surfaces; the evidence stream is the source of truth throughout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.build import attempts as A
from manju.cli import app
from manju.providers import submission as S
from manju.providers.base import (
    CloudProvider,
    FailureKind,
    GenerationRequest,
    ProviderFailure,
)
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.runtime.state import RuntimeState


class ScriptedCloud(CloudProvider):
    id = "cloud_test"

    def __init__(self, *, submit_exc=None, job_id="job_fresh_1", **kw):
        kw.setdefault("sleep_fn", lambda _s: None)
        super().__init__(**kw)
        self._submit_exc = submit_exc
        self._job_id = job_id
        self.submit_calls = 0

    def submit(self, req):
        self.submit_calls += 1
        if self._submit_exc is not None:
            raise self._submit_exc
        return self._job_id

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


def _events(project, sid=None):
    recs, _ = A.read_submission_events(project, sid)
    return recs


def _cli(project, monkeypatch, *args):
    monkeypatch.chdir(project.root)
    return CliRunner().invoke(app, list(args))


# =========================================================== pending correlation


def test_admitted_resume_requires_digest_match(tmp_project, add_shot):
    """ruling 8: a fresh generate() for a shot whose ADMITTED submission has a
    MATCHING request_digest resumes polling under the SAME submission_id — never
    a resubmit."""
    shot = add_shot(tmp_project, "S001")
    # first submit -> ADMITTED, but poll never terminal (leave it pending)

    class _NoDownloadYet(ScriptedCloud):
        def poll(self, job_id):
            return "running", {}

    # drive it to ADMITTED then stop (cancel-style): use a should_cancel
    import threading
    cancel = threading.Event()
    cancel.set()
    req = GenerationRequest(project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
                            spec_hash="sha256:test", duration_ms=3000, candidates=1,
                            params={"seed": 7}, should_cancel=cancel.is_set)
    from manju.providers.base import ProviderCanceled
    with pytest.raises(ProviderCanceled):
        _NoDownloadYet().generate(req)
    with RuntimeState(tmp_project.root) as st:
        rows = st.submissions(shot="S001", provider="cloud_test")
    assert rows and rows[0]["state"] == S.ADMITTED
    sid = rows[0]["submission_id"]

    # a fresh, IDENTICAL request resumes the same submission (poll, no resubmit)
    provider = ScriptedCloud(job_id="job_fresh_1")
    takes = provider.generate(_req(tmp_project, shot, seed=7))
    assert provider.submit_calls == 0  # resumed, never resubmitted
    assert takes
    with RuntimeState(tmp_project.root) as st:
        row = st.get_submission(sid)
    assert row["state"] == S.TERMINAL_SUCCESS  # the SAME submission finished


def test_dispatching_unknown_same_shot_provider_fails_closed(tmp_project, add_shot):
    """ruling 8: a shot+provider with an unresolved OUTCOME_UNKNOWN submission
    (ANY digest) fails closed with the structured diagnostic — never auto-
    resubmits even for a DIFFERENT request."""
    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_x",
                       request_digest="sha256:other", state=S.OUTCOME_UNKNOWN)

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot, seed=999))  # different digest
    assert provider.submit_calls == 0
    d = exc.value.detail
    assert d["code"] == "submission_outcome_unknown"
    assert d["automatic_resubmit"] is False
    assert d["possible_remote_side_effect"] is True
    assert set(d["actions"]) == {"attach_remote_job", "abandon_with_duplicate_risk"}


def test_digest_mismatched_admitted_is_a_conflict_not_a_silent_poll(tmp_project, add_shot):
    """ruling 8 (test 45/46): an ADMITTED submission whose spec changed under it
    (digest mismatch) is a CONFLICT diagnostic — never a silent poll of the old
    task."""
    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_adm",
                       request_digest="sha256:STALE", state=S.ADMITTED)
        st.set_submission_state("sub_adm", S.ADMITTED, remote_job_id="job_old")

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot, seed=7))  # a different digest
    assert provider.submit_calls == 0
    assert exc.value.detail["code"] == "submission_spec_conflict"


def test_no_ttl_dispatching_never_expires(tmp_project, add_shot):
    """ruling 9 (test 43): there is NO TTL for DISPATCHING/OUTCOME_UNKNOWN/
    ADMITTED — an ancient unresolved submission still fail-closes; it is never
    swept to 'assume not-happened'."""
    shot = add_shot(tmp_project, "S001")
    from datetime import datetime, timezone
    old = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with RuntimeState(tmp_project.root, now_fn=lambda: old) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_ancient",
                       request_digest="sha256:d", state=S.DISPATCHING)

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail["code"] == "submission_outcome_unknown"


# =========================================================== rebuild from events


def test_rebuild_restores_unresolved_and_ignores_terminal(tmp_project, add_shot):
    """ruling 4: rebuild() scans submission_state events and restores UNRESOLVED
    submissions (here an OUTCOME_UNKNOWN), while a TERMINAL_SUCCESS one is not
    restored (it needs no resolution)."""
    shot = add_shot(tmp_project, "S001")
    # a clean success (TERMINAL_SUCCESS)
    ScriptedCloud().generate(_req(tmp_project, shot, seed=1))
    # an OUTCOME_UNKNOWN
    exc = ProviderFailure(FailureKind.provider_error, "ambiguous",
                          disposition=S.OUTCOME_UNKNOWN_DISPOSITION)
    with pytest.raises(ProviderFailure):
        ScriptedCloud(submit_exc=exc).generate(_req(tmp_project, add_shot(tmp_project, "S002"), seed=2))

    import shutil
    shutil.rmtree(tmp_project.root / ".manju")
    with RuntimeState(tmp_project.root) as st:
        stats = st.rebuild(tmp_project)
        unresolved = st.unresolved_submissions()
    assert stats["submissions_restored"] == 1
    assert len(unresolved) == 1 and unresolved[0]["state"] == S.OUTCOME_UNKNOWN
    assert unresolved[0]["shot"] == "S002"


# =========================================================== chain corruption


def test_corrupt_chain_fails_closed_only_that_shot_provider(tmp_project, add_shot):
    """ruling 4: a broken per-submission event chain fail-closes ONLY that
    shot+provider (RECOVERY_EVIDENCE_CORRUPT) — a DIFFERENT shot is unaffected."""
    shot1 = add_shot(tmp_project, "S001")
    shot2 = add_shot(tmp_project, "S002")
    # leave an unresolved DISPATCHING submission for S001 with a corrupt chain.
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_bad",
                       request_digest="sha256:d", state=S.DISPATCHING)
    # write two events whose chain link is broken (second's prev is wrong)
    A.append_submission_event(tmp_project, submission_id="sub_bad", request_digest="sha256:d",
                              from_state=None, to_state=S.PREPARED, provider_id="cloud_test",
                              shot="S001", prev_event_digest=None)
    A.append_submission_event(tmp_project, submission_id="sub_bad", request_digest="sha256:d",
                              from_state=S.PREPARED, to_state=S.DISPATCHING,
                              provider_id="cloud_test", shot="S001",
                              prev_event_digest="sha256:WRONG")  # broken link

    # S001 fails closed on the corruption...
    with pytest.raises(ProviderFailure) as exc:
        ScriptedCloud().generate(_req(tmp_project, shot1))
    assert exc.value.detail["code"] == "RECOVERY_EVIDENCE_CORRUPT"
    assert exc.value.detail["shot"] == "S001"
    # ...but S002 (unrelated) generates fine
    takes = ScriptedCloud().generate(_req(tmp_project, shot2))
    assert takes


# =========================================================== abandon / attach CLI


def _seed_unknown(project, sid="sub_unknown", shot="S001", provider="cloud_test"):
    with RuntimeState(project.root) as st:
        st.open_intent(provider=provider, shot=shot, submission_id=sid,
                       request_digest="sha256:d", state=S.PREPARED)
        st.set_submission_state(sid, S.DISPATCHING, expected_state=S.PREPARED)
        st.set_submission_state(sid, S.OUTCOME_UNKNOWN, expected_state=S.DISPATCHING)
    # seed a matching (valid) chain so recovery transitions link cleanly
    prev = None
    for frm, to in ((None, S.PREPARED), (S.PREPARED, S.DISPATCHING),
                    (S.DISPATCHING, S.OUTCOME_UNKNOWN)):
        rec = A.append_submission_event(project, submission_id=sid, request_digest="sha256:d",
                                        from_state=frm, to_state=to, provider_id=provider,
                                        shot=shot, prev_event_digest=prev)
        prev = S.submission_event_digest(rec)


def test_tasks_json_lists_unresolved_submissions(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    _seed_unknown(tmp_project)
    out = _cli(tmp_project, monkeypatch, "tasks", "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    us = data["unresolved_submissions"]
    assert len(us) == 1
    row = us[0]
    assert row["submission_id"] == "sub_unknown"
    assert row["state"] == S.OUTCOME_UNKNOWN
    assert row["automatic_resubmit"] is False
    assert row["possible_remote_side_effect"] is True
    assert row["evidence_chain_ok"] is True
    assert set(row["actions"]) == {"attach_remote_job", "abandon_with_duplicate_risk"}


def test_abandon_subcommand_records_terminal_and_preserves_history(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    _seed_unknown(tmp_project)
    out = _cli(tmp_project, monkeypatch, "tasks", "abandon", "sub_unknown",
               "--reason", "confirmed not created in console", "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["state"] == S.ABANDONED_BY_USER
    assert data["duplicate_risk_accepted"] is True
    # the projection is now terminal; the history (all prior events) is preserved
    with RuntimeState(tmp_project.root) as st:
        assert st.get_submission("sub_unknown")["state"] == S.ABANDONED_BY_USER
        assert st.unresolved_submissions() == []
    states = [e["to"] for e in _events(tmp_project, "sub_unknown")]
    assert states == [S.PREPARED, S.DISPATCHING, S.OUTCOME_UNKNOWN, S.ABANDONED_BY_USER]
    abandon_ev = _events(tmp_project, "sub_unknown")[-1]
    assert abandon_ev["detail"]["duplicate_risk_accepted"] is True
    assert "confirmed not created" in abandon_ev["detail"]["reason"]


def test_attach_remote_job_admits_and_is_poll_only(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    _seed_unknown(tmp_project)
    out = _cli(tmp_project, monkeypatch, "tasks", "attach-remote-job",
               "sub_unknown", "remote_job_42", "--json")
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["state"] == S.ADMITTED and data["remote_job_id"] == "remote_job_42"
    with RuntimeState(tmp_project.root) as st:
        row = st.get_submission("sub_unknown")
    assert row["state"] == S.ADMITTED and row["remote_job_id"] == "remote_job_42"
    attach_ev = _events(tmp_project, "sub_unknown")[-1]
    assert attach_ev["to"] == S.ADMITTED
    assert attach_ev["detail"]["claimed_verified_ownership"] is False  # never claims ownership
    # the chain is still intact after the recovery transition
    ok, _ = S.verify_chain(_events(tmp_project, "sub_unknown"))
    assert ok


def test_attach_rejects_wrong_expected_state(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    _seed_unknown(tmp_project)
    out = _cli(tmp_project, monkeypatch, "tasks", "attach-remote-job",
               "sub_unknown", "j", "--expected-state", "DISPATCHING")
    assert out.exit_code != 0  # it is OUTCOME_UNKNOWN, not DISPATCHING


def test_no_retry_unknown_subcommand_exists(tmp_project, monkeypatch):
    """There is NO 'retry unknown' anywhere (ruling 11) — the tasks app exposes
    only attach-remote-job / abandon for recovery (plus cancel/retry/manifest)."""
    out = _cli(tmp_project, monkeypatch, "tasks", "--help")
    assert "attach-remote-job" in out.output
    assert "abandon" in out.output
    assert "retry-unknown" not in out.output


# =========================================================== declared idempotency


def _idem_manifest(**over):
    base = {
        "id": "idem_x", "type": "video", "adapter": "generic_cloud",
        "capabilities": ["text_to_video"], "auth": {"key_env": "IDEM_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/videos",
                   "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
                   "job_id_path": "$.data.task_id"},
        "poll": {"url": "https://api.example.com/v1/videos/{job_id}",
                 "status_path": "$.data.status",
                 "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed",
                                "PROCESSING": "running"},
                 "result_url_path": "$.data.video_url"},
        "limits": {"max_concurrent": 2, "rate_limit_per_min": 6},
        "cost": {"per_second": 0.08, "currency": "CNY"},
        "submission": {"idempotency": {"mode": "header", "field": "Idempotency-Key"}},
    }
    base.update(over)
    return base


class RecordingTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, dict(headers), body))
        return self.script.pop(0)


def _resp(status, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(status, {}, body)


def _idem_req(project, add_shot, shot_id="S001", **params):
    shot = add_shot(project, shot_id, generation={"candidates": 1})
    return GenerationRequest(project=project, shot=shot, bible=project.load_bible(),
                             spec_hash="sha256:test", duration_ms=4000, candidates=1,
                             params={"seed": 7, **params})


def test_declared_idempotency_injects_stable_header(tmp_project, add_shot, monkeypatch):
    """ruling 10 (tests 48/49): a DECLARED idempotent provider injects the derived
    Idempotency-Key header; an undeclared one injects nothing (byte-identical)."""
    monkeypatch.setenv("IDEM_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_idem_manifest())
    transport = RecordingTransport([
        _resp(200, {"data": {"task_id": "job_1"}}),
        _resp(200, {"data": {"status": "SUCCEEDED", "video_url": "https://c/o.mp4"}}),
        HttpResponse(200, {}, b"V"),
    ])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    provider.id = manifest.id
    req = _idem_req(tmp_project, add_shot)
    provider.generate(req)
    submit_headers = transport.requests[0][2]
    assert "Idempotency-Key" in submit_headers
    key = submit_headers["Idempotency-Key"]
    # it is exactly the derived key for this submission (stable, secret-free)
    with RuntimeState(tmp_project.root) as st:
        sid = st.submissions(shot="S001", provider="idem_x")[0]["submission_id"]
    assert key == S.idempotency_key("idem_x", sid)
    assert not key.startswith("sha256:")


def test_undeclared_provider_injects_no_idempotency_header(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("IDEM_X_KEY", "k")
    manifest = ProviderManifest.model_validate(
        _idem_manifest(submission={"idempotency": {"mode": "none"}}))
    transport = RecordingTransport([
        _resp(200, {"data": {"task_id": "job_1"}}),
        _resp(200, {"data": {"status": "SUCCEEDED", "video_url": "https://c/o.mp4"}}),
        HttpResponse(200, {}, b"V"),
    ])
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    provider.id = manifest.id
    provider.generate(_idem_req(tmp_project, add_shot))
    assert "Idempotency-Key" not in transport.requests[0][2]


def test_idempotent_recovery_redispatches_same_submission_and_key(tmp_project, add_shot, monkeypatch):
    """ruling 10: with a DECLARED idempotent provider, a DISPATCHING/UNKNOWN
    recovery MAY re-dispatch the SAME submission_id, recording a new dispatch
    attempt AND re-using the identical idempotency key (the remote dedupes — no
    second side effect)."""
    monkeypatch.setenv("IDEM_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_idem_manifest())
    # first attempt: a submit-phase OUTCOME_UNKNOWN (leaves the submission UNKNOWN)
    transport1 = RecordingTransport([_resp(503, b"unavailable")])  # 5xx -> UNKNOWN
    p1 = GenericCloudProvider(manifest, transport=transport1, sleep_fn=lambda s: None)
    p1.id = manifest.id
    req1 = _idem_req(tmp_project, add_shot)
    with pytest.raises(ProviderFailure):
        p1.generate(req1)
    with RuntimeState(tmp_project.root) as st:
        row = st.submissions(shot="S001", provider="idem_x")[0]
    sid = row["submission_id"]
    assert row["state"] == S.OUTCOME_UNKNOWN
    first_key = S.idempotency_key("idem_x", sid)

    # recovery: an IDENTICAL request re-dispatches the SAME submission (idempotent)
    transport2 = RecordingTransport([
        _resp(200, {"data": {"task_id": "job_ok"}}),
        _resp(200, {"data": {"status": "SUCCEEDED", "video_url": "https://c/o.mp4"}}),
        HttpResponse(200, {}, b"V"),
    ])
    p2 = GenericCloudProvider(manifest, transport=transport2, sleep_fn=lambda s: None)
    p2.id = manifest.id
    # same shot/seed => same digest => idempotent redispatch of sid
    shot = tmp_project.load_shot("S001")
    req2 = GenerationRequest(project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
                             spec_hash="sha256:test", duration_ms=4000, candidates=1,
                             params={"seed": 7})
    takes = p2.generate(req2)
    assert takes
    # the SAME submission_id was reused, with the SAME idempotency key
    assert transport2.requests[0][2]["Idempotency-Key"] == first_key
    with RuntimeState(tmp_project.root) as st:
        rows = st.submissions(shot="S001", provider="idem_x")
    assert len(rows) == 1 and rows[0]["submission_id"] == sid  # NOT a new submission
    assert rows[0]["state"] == S.TERMINAL_SUCCESS


# =========================================================== legacy rows


def test_legacy_intent_row_reads_as_unknown_legacy(tmp_project):
    """A pre-DR06 intent row (no state) reads as UNKNOWN_LEGACY — never invented
    into a real state, and never surfaced as an unresolved submission."""
    with RuntimeState(tmp_project.root) as st:
        # a legacy goal-27 row: open_intent WITHOUT submission fields
        st.open_intent(provider="cloud_old", shot="S001")
        assert st.unresolved_submissions() == []  # legacy rows are not submissions
        row = st._conn.execute("SELECT * FROM intents").fetchone()
    assert S.normalize_state(row["state"]) == S.UNKNOWN_LEGACY
