"""Tests for manju.runtime.state and the CloudProvider bookkeeping it drives.

SQLite holds only rebuildable state (§1-⑥, §3): the per-call cost ledger
(``runs``) and in-flight remote jobs (``jobs``). Resume-polling never
resubmits/double-charges (§8.1); rebuild re-derives the ledger from take
sidecars on disk (§3).
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from manju.core.models import RemoteJobInfo, TakeSidecar
from manju.providers.base import CloudProvider, FailureKind, GenerationRequest, ProviderFailure
from manju.runtime.state import RuntimeState


def _incrementing_clock(start: datetime | None = None):
    """Zero-arg clock returning strictly increasing UTC datetimes, so ordering
    by timestamp is deterministic under a stubbed clock."""
    state = {"t": start or datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)}

    def now() -> datetime:
        t = state["t"]
        state["t"] = t + timedelta(seconds=1)
        return t

    return now


# --------------------------------------------------------------- runs / ledger


def test_record_run_returns_ids_and_run_log_is_newest_first(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        id1 = st.record_run(shot="S001", provider="cloud_x", status="succeeded",
                            cost=1.0, currency="CNY", remote_job_id="j1", take="take_01")
        id2 = st.record_run(shot="S002", provider="cloud_x", status="failed",
                            failure_kind="content_rejected", error="审核拒绝")
        assert (id1, id2) == (1, 2)

        log = st.run_log()
        assert [r["id"] for r in log] == [2, 1]  # newest first
        assert log[0]["status"] == "failed"
        assert log[0]["failure_kind"] == "content_rejected"
        assert log[0]["cost"] == 0.0  # NOT NULL DEFAULT 0
        assert log[1]["remote_job_id"] == "j1"
        assert log[1]["take"] == "take_01"


def test_total_cost_single_currency(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        st.record_run(shot="S001", provider="p", status="succeeded", cost=0.32, currency="CNY")
        st.record_run(shot="S002", provider="p", status="succeeded", cost=0.08, currency="CNY")
        total, currency = st.total_cost()
        assert total == pytest.approx(0.40)
        assert currency == "CNY"


def test_total_cost_mixed_currency_is_none(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        st.record_run(shot="S001", provider="p", status="succeeded", cost=1.0, currency="CNY")
        st.record_run(shot="S002", provider="p", status="succeeded", cost=2.0, currency="USD")
        total, currency = st.total_cost()
        assert total == pytest.approx(3.0)
        assert currency is None


def test_total_cost_empty_ledger(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        assert st.total_cost() == (0.0, None)


# ------------------------------------------------------------------- jobs table


def test_pending_jobs_filtering_and_close(tmp_project):
    with RuntimeState(tmp_project.root, now_fn=_incrementing_clock()) as st:
        st.open_job("j1", provider="cloud_a", shot="S001")
        st.open_job("j2", provider="cloud_b", shot="S001")
        st.open_job("j3", provider="cloud_a", shot="S002")

        assert [j["remote_job_id"] for j in st.pending_jobs()] == ["j3", "j2", "j1"]  # newest first
        assert {j["remote_job_id"] for j in st.pending_jobs(shot="S001")} == {"j1", "j2"}
        assert {j["remote_job_id"] for j in st.pending_jobs(provider="cloud_a")} == {"j1", "j3"}
        got = st.pending_jobs(shot="S001", provider="cloud_a")
        assert len(got) == 1 and got[0]["remote_job_id"] == "j1"
        assert got[0]["status"] == "pending"

        st.close_job("j1", "succeeded")
        remaining = {j["remote_job_id"] for j in st.pending_jobs()}
        assert remaining == {"j2", "j3"}


def test_open_job_insert_or_replace_is_idempotent(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        st.open_job("dup", provider="c", shot="S001")
        st.open_job("dup", provider="c", shot="S001")
        assert len(st.pending_jobs()) == 1


# ----------------------------------------------------------- delete + rebuild


def test_runtime_state_rebuilds_from_scratch_after_manju_wiped(tmp_project):
    # §3: deleting the whole .manju/ runtime dir and reopening must just work.
    shutil.rmtree(tmp_project.root / ".manju")
    assert not (tmp_project.root / ".manju").exists()
    with RuntimeState(tmp_project.root) as st:
        rid = st.record_run(shot="S001", provider="p", status="succeeded", cost=1.0, currency="CNY")
        assert rid == 1
    assert (tmp_project.root / ".manju" / "state.sqlite").exists()


def test_rebuild_rederives_succeeded_runs_from_take_sidecars(
    tmp_project, add_shot, make_take, tmp_path
):
    project = tmp_project
    for sid in ("S001", "S002", "S003"):
        add_shot(project, sid)
    # Two plain generated takes (no remote cost) ...
    make_take(project, "S001", "sha256:a")
    make_take(project, "S002", "sha256:b")
    # ... and one take carrying a RemoteJobInfo of 0.32 CNY.
    cloud_media = tmp_path / "cloud_take.mp4"
    cloud_media.write_bytes(b"cloud-bytes")
    project.register_take(
        "S003",
        cloud_media,
        TakeSidecar(
            provider="cloud_video_x",
            spec_hash="sha256:c",
            remote=RemoteJobInfo(job_id="job_8f2c91", cost=0.32, currency="CNY"),
        ),
    )

    with RuntimeState(project.root) as st:
        # seed some junk so we can prove rebuild wipes runs first.
        st.record_run(shot="ZZZ", provider="junk", status="failed", cost=99.0, currency="USD")

        result = st.rebuild(project)
        assert result["runs"] == 3

        log = st.run_log()
        assert len(log) == 3
        assert all(r["status"] == "succeeded" for r in log)  # junk wiped
        assert {r["shot"] for r in log} == {"S001", "S002", "S003"}

        s003 = next(r for r in log if r["shot"] == "S003")
        assert s003["remote_job_id"] == "job_8f2c91"
        assert s003["cost"] == pytest.approx(0.32)
        assert s003["provider"] == "cloud_video_x"

        # ledger recomputed purely from disk: 0.32 CNY, plain takes contribute 0.
        assert st.total_cost() == (pytest.approx(0.32), "CNY")


def test_rebuild_prunes_stale_pending_jobs_only(tmp_project):
    project = tmp_project
    # An old pending job (submitted > 7 days ago) ...
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with RuntimeState(project.root, now_fn=lambda: old) as st:
        st.open_job("old_job", provider="c", shot="S001")
    # ... and a fresh one, then rebuild with a present-time clock.
    with RuntimeState(project.root, now_fn=lambda: datetime.now(timezone.utc)) as st:
        st.open_job("fresh_job", provider="c", shot="S002")
        result = st.rebuild(project)
        pending = {j["remote_job_id"] for j in st.pending_jobs()}
        assert "old_job" not in pending  # stale -> pruned (§3)
        assert "fresh_job" in pending
        assert result["pending_jobs"] == 1


# -------------------------------------------- CloudProvider resume semantics


class _ScriptedCloud(CloudProvider):
    """A cloud provider whose remote steps are scripted, to exercise the
    submit/poll/download orchestration and the RuntimeState bookkeeping."""

    id = "cloud_test"

    def __init__(self, *, fail: ProviderFailure | None = None, **kw):
        kw.setdefault("sleep_fn", lambda _s: None)
        super().__init__(**kw)
        self._fail = fail
        self.submit_calls = 0
        self.polled_ids: list[str] = []

    def submit(self, req: GenerationRequest) -> str:
        self.submit_calls += 1
        return "job_fresh_1"

    def poll(self, job_id: str):
        self.polled_ids.append(job_id)
        if self._fail is not None:
            return "failed", {"failure_kind": self._fail.kind.value,
                              "reason": self._fail.message}
        return "succeeded", {"cost": 0.5, "currency": "CNY"}

    def download(self, job_id: str, dest_dir: Path):
        out = Path(dest_dir) / "out.mp4"
        out.write_bytes(b"fake-cloud-video-" + job_id.encode("ascii"))
        return [out]


def _req(project, shot, **params) -> GenerationRequest:
    return GenerationRequest(
        project=project,
        shot=shot,
        bible=project.load_bible(),
        spec_hash="sha256:test",
        duration_ms=3000,
        candidates=1,
        params=dict(params),
    )


def test_generate_resumes_pending_job_without_resubmitting(tmp_project, add_shot):
    project = tmp_project
    shot = add_shot(project, "S001")
    # A job was already submitted for this shot+provider before a crash.
    with RuntimeState(project.root) as st:
        st.open_job("job_resume_1", provider="cloud_test", shot="S001")

    provider = _ScriptedCloud()
    takes = provider.generate(_req(project, shot))

    # §8.1: submit was NOT called; polling resumed on the persisted job id.
    assert provider.submit_calls == 0
    assert provider.polled_ids == ["job_resume_1"]
    assert len(takes) == 1

    with RuntimeState(project.root) as st:
        assert st.pending_jobs() == []  # job closed
        log = st.run_log()
        assert log[0]["status"] == "succeeded"
        assert log[0]["remote_job_id"] == "job_resume_1"
        assert log[0]["provider"] == "cloud_test"


def test_generate_fresh_submits_once_and_records_closed_job_and_cost(tmp_project, add_shot):
    project = tmp_project
    shot = add_shot(project, "S001")

    provider = _ScriptedCloud()
    takes = provider.generate(_req(project, shot))

    assert provider.submit_calls == 1
    assert provider.polled_ids == ["job_fresh_1"]
    assert len(takes) == 1

    with RuntimeState(project.root) as st:
        assert st.pending_jobs() == []  # opened on submit, closed on success
        total, currency = st.total_cost()
        assert total == pytest.approx(0.5)
        assert currency == "CNY"
        log = st.run_log()
        assert log[0]["status"] == "succeeded"
        assert log[0]["remote_job_id"] == "job_fresh_1"
        assert log[0]["take"] == takes[0].name


def test_generate_records_failure_kind_and_closes_job(tmp_project, add_shot):
    project = tmp_project
    shot = add_shot(project, "S001")
    provider = _ScriptedCloud(
        fail=ProviderFailure(FailureKind.content_rejected, "审核拒绝:悬疑题材")
    )

    with pytest.raises(ProviderFailure) as excinfo:
        provider.generate(_req(project, shot))
    assert excinfo.value.kind is FailureKind.content_rejected
    assert provider.submit_calls == 1  # content_rejected is never retried

    with RuntimeState(project.root) as st:
        assert st.pending_jobs() == []  # job closed as failed
        log = st.run_log()
        assert log[0]["status"] == "failed"
        assert log[0]["failure_kind"] == "content_rejected"
        assert log[0]["remote_job_id"] == "job_fresh_1"


def test_generate_survives_broken_runtime_state(tmp_project, add_shot):
    # §3: a broken DB must never block generation. Make .manju/ a FILE so
    # RuntimeState cannot create its db there; generation must still succeed.
    project = tmp_project
    shot = add_shot(project, "S001")
    shutil.rmtree(project.root / ".manju")
    (project.root / ".manju").write_text("not a directory", encoding="utf-8")

    provider = _ScriptedCloud()
    takes = provider.generate(_req(project, shot))
    assert len(takes) == 1  # generation unaffected by the unusable DB path
