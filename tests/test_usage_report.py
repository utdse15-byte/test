"""Behavioral tests for the local owner-friction usage report (P2 item 11).

Local-only, derived from existing jsonl history; never network, never a build
input. We synthesize the history files and assert the computed metrics.
"""

from __future__ import annotations

import json

from manju.build.usage_report import usage_report


def _write_jobs(project, records) -> None:
    d = project.root / ".manju"
    d.mkdir(parents=True, exist_ok=True)
    (d / "jobs.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _write_failures(project, records) -> None:
    d = project.root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / "failures.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_empty_history_is_honest_zeros(tmp_project) -> None:
    r = usage_report(tmp_project)
    assert r["jobs_analyzed"] == 0
    assert r["duration_by_kind"] == {}
    assert r["cancellations"]["total"] == 0
    assert r["telemetry"].startswith("none")


def test_duration_percentiles_and_state_counts(tmp_project) -> None:
    _write_jobs(tmp_project, [
        {"id": "1", "kind": "build", "state": "done",
         "started": "2026-07-15T00:00:00+00:00", "finished": "2026-07-15T00:00:10+00:00"},
        {"id": "2", "kind": "build", "state": "done",
         "started": "2026-07-15T00:00:00+00:00", "finished": "2026-07-15T00:00:20+00:00"},
        {"id": "3", "kind": "build", "state": "failed",
         "started": "2026-07-15T00:00:00+00:00", "finished": "2026-07-15T00:00:30+00:00"},
    ])
    r = usage_report(tmp_project)
    assert r["jobs_analyzed"] == 3
    b = r["duration_by_kind"]["build"]
    assert b["count"] == 3
    assert b["p50_s"] == 20.0     # sorted [10,20,30], median = 20
    assert r["state_counts_by_kind"]["build"] == {"done": 2, "failed": 1}


def test_cancellation_and_possibly_billed_upper_bound(tmp_project) -> None:
    _write_jobs(tmp_project, [
        # a paid kind that canceled -> counts toward possibly-billed
        {"id": "1", "kind": "voice", "state": "canceled",
         "started": "2026-07-15T00:00:00+00:00", "finished": "2026-07-15T00:00:01+00:00"},
        # a non-paid kind that canceled -> NOT possibly-billed
        {"id": "2", "kind": "export", "state": "canceled",
         "started": "2026-07-15T00:00:00+00:00", "finished": "2026-07-15T00:00:01+00:00"},
    ])
    r = usage_report(tmp_project)
    assert r["cancellations"]["total"] == 2
    assert r["possibly_billed_more_than_once"] == 1  # only the paid voice cancel


def test_only_last_transition_per_job_is_counted(tmp_project) -> None:
    _write_jobs(tmp_project, [
        {"id": "1", "kind": "build", "state": "queued"},
        {"id": "1", "kind": "build", "state": "running",
         "started": "2026-07-15T00:00:00+00:00"},
        {"id": "1", "kind": "build", "state": "done",
         "started": "2026-07-15T00:00:00+00:00", "finished": "2026-07-15T00:00:05+00:00"},
    ])
    r = usage_report(tmp_project)
    assert r["jobs_analyzed"] == 1
    assert r["state_counts_by_kind"]["build"] == {"done": 1}


def test_provider_failure_categories(tmp_project) -> None:
    _write_failures(tmp_project, [
        {"cause": "provider rate limit 429"},
        {"cause": "connection timeout 超时"},
        {"cause": "content rejected 审核不通过"},
        {"cause": "something odd"},
    ])
    r = usage_report(tmp_project)
    cats = r["provider_failure_categories"]
    assert cats["rate_limit"] == 1
    assert cats["timeout"] == 1
    assert cats["content_rejected"] == 1
    assert cats["other"] == 1


def test_unavailable_metrics_are_none_not_fabricated(tmp_project) -> None:
    r = usage_report(tmp_project)
    assert r["retry_count_by_operation"] is None
    assert r["waiting_user"] is None
    assert r["cache_hit_rate"] is None
    assert r["cancellations"]["confirmed"] is None
