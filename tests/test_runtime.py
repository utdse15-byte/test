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

        st.close_job("j1", "succeeded", provider="cloud_a")
        remaining = {j["remote_job_id"] for j in st.pending_jobs()}
        assert remaining == {"j2", "j3"}


def test_open_job_insert_or_replace_is_idempotent(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        st.open_job("dup", provider="c", shot="S001")
        st.open_job("dup", provider="c", shot="S001")
        assert len(st.pending_jobs()) == 1


def test_jobs_pk_is_provider_plus_remote_job_id(tmp_project):
    """#47: the jobs table used to key on remote_job_id ALONE — two DIFFERENT
    providers returning the SAME job id string would silently replace each
    other's pending row. The compound (provider, remote_job_id) PK must let
    both survive, and close_job must require provider so it closes the RIGHT
    one instead of guessing."""
    with RuntimeState(tmp_project.root) as st:
        st.open_job("job_123", provider="cloud_a", shot="S001")
        st.open_job("job_123", provider="cloud_b", shot="S002")  # same id, other provider

        pending = st.pending_jobs()
        assert len(pending) == 2, "cross-provider same-id collision lost a row"
        by_provider = {j["provider"]: j for j in pending}
        assert by_provider["cloud_a"]["shot"] == "S001"
        assert by_provider["cloud_b"]["shot"] == "S002"

        # closing cloud_a's job must not touch cloud_b's same-id job
        st.close_job("job_123", "succeeded", provider="cloud_a")
        remaining = st.pending_jobs()
        assert len(remaining) == 1
        assert remaining[0]["provider"] == "cloud_b"
        assert remaining[0]["status"] == "pending"


def test_legacy_single_column_jobs_table_migrates_cleanly(tmp_project):
    """#47: a DB created by pre-round-W code has ``jobs(remote_job_id TEXT
    PRIMARY KEY, ...)`` — opening it must not raise, must not silently keep
    the collision-prone shape, and the §3 rebuild path must still work
    afterward (rebuild-index's ledger rebuild touches this same table)."""
    import sqlite3

    db_path = tmp_project.root / ".manju" / "state.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(str(db_path))
    try:
        legacy.executescript(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, shot TEXT,
                provider TEXT, params TEXT, status TEXT NOT NULL, failure_kind TEXT,
                cost REAL NOT NULL DEFAULT 0, currency TEXT, remote_job_id TEXT,
                take TEXT, error TEXT
            );
            CREATE TABLE jobs (
                remote_job_id TEXT PRIMARY KEY,
                provider      TEXT NOT NULL,
                shot          TEXT NOT NULL,
                params        TEXT,
                status        TEXT NOT NULL,
                submitted_at  TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );
            """
        )
        legacy.execute(
            "INSERT INTO jobs VALUES ('legacy_job', 'cloud_old', 'S001', NULL, "
            "'pending', '2020-01-01T00:00:00', '2020-01-01T00:00:00')"
        )
        legacy.commit()
    finally:
        legacy.close()

    # opening RuntimeState must migrate, not raise
    with RuntimeState(tmp_project.root) as st:
        cols = st._conn.execute("PRAGMA table_info(jobs)").fetchall()
        pk_cols = {c["name"] for c in cols if c["pk"]}
        assert pk_cols == {"provider", "remote_job_id"}
        # the fresh table is empty (legacy pending jobs are the accepted
        # worst case, same as any .manju/ loss — never silently mis-keyed)
        assert st.pending_jobs() == []
        # the old data is preserved, just renamed aside, not destroyed
        legacy_tables = [
            r[0] for r in st._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'jobs_legacy_%'"
            ).fetchall()
        ]
        assert len(legacy_tables) == 1

        # new jobs work normally post-migration
        st.open_job("new_job", provider="cloud_new", shot="S002")
        assert len(st.pending_jobs()) == 1

        # §3 rebuild path (rebuild-index) must still work against the migrated DB
        result = st.rebuild(tmp_project)
        assert isinstance(result["pending_jobs"], int)

    # reopening again must be a no-op migration (idempotent, no re-rename)
    with RuntimeState(tmp_project.root) as st2:
        legacy_tables2 = [
            r[0] for r in st2._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'jobs_legacy_%'"
            ).fetchall()
        ]
        assert len(legacy_tables2) == 1  # unchanged — already migrated


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


def test_generate_resolves_intent_on_success(tmp_project, add_shot):
    """Goal 27: the pre-submit intent is written BEFORE submit() and resolved
    to the confirmed job id right after — never left dangling on a clean run."""
    project = tmp_project
    shot = add_shot(project, "S001")
    provider = _ScriptedCloud()
    provider.generate(_req(project, shot))

    with RuntimeState(project.root) as st:
        assert st.dangling_intents() == []  # nothing left open
        row = st._conn.execute("SELECT * FROM intents").fetchone()
        assert row is not None
        assert row["status"] == "resolved" and row["remote_job_id"] == "job_fresh_1"
        assert row["provider"] == "cloud_test" and row["shot"] == "S001"


def test_generate_resolves_intent_locally_on_submit_failure(tmp_project, add_shot):
    """Goal 27: when submit() itself raises, the intent is resolved (status
    'error') IN THIS PROCESS — it must never be mistaken for a dangling
    (crashed, possibly-billed) intent on the next attempt."""
    project = tmp_project
    shot = add_shot(project, "S001")

    class _FailingSubmit(_ScriptedCloud):
        def submit(self, req):
            self.submit_calls += 1
            raise ProviderFailure(FailureKind.invalid, "bad request")

    provider = _FailingSubmit()
    with pytest.raises(ProviderFailure):
        provider.generate(_req(project, shot))

    with RuntimeState(project.root) as st:
        assert st.dangling_intents() == []  # resolved locally, not left open
        row = st._conn.execute("SELECT * FROM intents").fetchone()
        assert row is not None and row["status"] == "error"
        assert row["remote_job_id"] is None


def test_dangling_intent_from_earlier_crash_is_flagged_not_silent(tmp_project, add_shot):
    """Goal 27: an intent left OPEN by an earlier attempt (simulating a crash
    between opening the intent and getting the job id back) is flagged as a
    LOUD structured Failure on the next generate() call — never silently
    ignored — but does NOT block the new attempt."""
    from manju.core.failures import read_failures

    project = tmp_project
    shot = add_shot(project, "S001")
    with RuntimeState(project.root) as st:
        stale_id = st.open_intent(provider="cloud_test", shot="S001")
        # never resolved — simulates the process dying right here

    provider = _ScriptedCloud()
    takes = provider.generate(_req(project, shot))
    assert len(takes) == 1  # the new attempt proceeds; not blocked

    failures = read_failures(project, n=20)
    assert any(
        "未确认的提交意图" in f.get("cause", "") and stale_id in (f.get("evidence") or "")
        for f in failures
    )
    with RuntimeState(project.root) as st:
        # the OLD intent stays open (still needs human review); the NEW
        # submit's own intent resolved cleanly.
        still_dangling = {d["id"] for d in st.dangling_intents()}
        assert stale_id in still_dangling
        resolved = st._conn.execute(
            "SELECT * FROM intents WHERE status = 'resolved'").fetchone()
        assert resolved is not None and resolved["remote_job_id"] == "job_fresh_1"


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
