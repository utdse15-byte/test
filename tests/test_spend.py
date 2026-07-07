"""`manju spend` — accumulated-spend visibility (§8.3 事后逐笔记账).

Covers ``manju.build.spend.spend_report``:

- an empty project -> ``source "empty"``, zero total;
- the run ledger (``RuntimeState``) is authoritative once it holds rows —
  totals, by-provider / by-shot groupings, cost-descending ordering, and the
  newest-first recent list;
- the ledger wins over on-disk sidecars whenever it has rows;
- when the ledger has no rows the report degrades to the take sidecars on disk
  (§3 rebuild source), deriving spend from ``sidecar.remote.cost``;
- ``budget_limit`` mirrors project.yaml.
"""

from __future__ import annotations

import pytest

from manju.build.spend import spend_report
from manju.core.models import RemoteJobInfo, TakeSidecar
from manju.runtime.state import RuntimeState


def _register_paid_take(project, tmp_path, add_shot, shot_id, *, provider, cost, n,
                        currency="CNY"):
    """Add a discoverable shot and register a take whose sidecar carries a
    ``remote.cost`` — the shape ``_from_sidecars`` reads (RemoteJobInfo, §4.2)."""
    add_shot(project, shot_id)
    media = tmp_path / f"paid_{n}.mp4"
    media.write_bytes(b"fakevideo-" + str(n).encode("ascii"))
    project.register_take(
        shot_id,
        media,
        TakeSidecar(
            provider=provider,
            spec_hash="manual",
            remote=RemoteJobInfo(job_id=f"job-{n}", cost=cost, currency=currency),
        ),
    )


# --------------------------------------------------------------------- empty


def test_empty_project_reports_empty(tmp_project):
    report = spend_report(tmp_project)
    assert report["source"] == "empty"
    assert report["total"] == 0
    assert report["currency"] is None
    assert report["by_provider"] == []
    assert report["by_shot"] == []
    assert report["recent"] == []
    assert report["budget_limit"] is None  # default BudgetConfig.limit


# -------------------------------------------------------------------- ledger


def test_ledger_totals_groupings_and_ordering(tmp_project):
    with RuntimeState(tmp_project.root) as state:
        state.record_run(shot="S001", provider="cloud_a", status="succeeded",
                         cost=5.0, currency="CNY")
        state.record_run(shot="S002", provider="cloud_a", status="succeeded",
                         cost=3.0, currency="CNY")
        state.record_run(shot="S001", provider="cloud_b", status="succeeded",
                         cost=1.5, currency="USD")

    report = spend_report(tmp_project)

    assert report["source"] == "ledger"
    assert report["total"] == pytest.approx(9.5)
    # two currencies in play -> the single-currency contract yields None (§8.3)
    assert report["currency"] is None

    # by_provider: cloud_a (5.0 + 3.0) outranks cloud_b (1.5); sorted cost desc
    assert report["by_provider"] == [
        {"provider": "cloud_a", "runs": 2, "cost": pytest.approx(8.0)},
        {"provider": "cloud_b", "runs": 1, "cost": pytest.approx(1.5)},
    ]
    # by_shot: S001 (5.0 + 1.5) outranks S002 (3.0)
    assert report["by_shot"] == [
        {"shot": "S001", "runs": 2, "cost": pytest.approx(6.5)},
        {"shot": "S002", "runs": 1, "cost": pytest.approx(3.0)},
    ]


def test_ledger_recent_is_newest_first_and_full_shape(tmp_project):
    with RuntimeState(tmp_project.root) as state:
        state.record_run(shot="S001", provider="cloud_a", status="succeeded",
                         cost=5.0, currency="CNY")
        state.record_run(shot="S002", provider="cloud_a", status="succeeded",
                         cost=3.0, currency="CNY")
        state.record_run(shot="S001", provider="cloud_b", status="failed",
                         failure_kind="timeout", currency="USD")

    recent = spend_report(tmp_project)["recent"]

    assert len(recent) == 3
    # run_log is id DESC, so the most recently recorded (failed cloud_b) leads
    assert recent[0]["shot"] == "S001"
    assert recent[0]["provider"] == "cloud_b"
    assert recent[0]["status"] == "failed"
    assert recent[0]["cost"] == 0.0
    assert recent[0]["currency"] == "USD"
    # every recent row carries the full documented shape (estimated_cost added
    # by the spend-delta feature; None here since no estimate was recorded)
    assert set(recent[0]) == {"ts", "shot", "provider", "take", "cost",
                              "currency", "status", "estimated_cost"}
    assert recent[0]["estimated_cost"] is None


def test_ledger_is_authoritative_over_sidecars(tmp_project, add_shot, tmp_path):
    # A paid take sits on disk (7.25) AND the ledger has one unrelated row (1.0).
    _register_paid_take(tmp_project, tmp_path, add_shot, "S001",
                        provider="cloud_a", cost=7.25, n=1)
    with RuntimeState(tmp_project.root) as state:
        state.record_run(shot="S001", provider="ledger_only", status="succeeded",
                         cost=1.0, currency="CNY")

    report = spend_report(tmp_project)
    assert report["source"] == "ledger"
    # the ledger's 1.0 wins; the un-recorded 7.25 sidecar is ignored
    assert report["total"] == pytest.approx(1.0)
    assert [e["provider"] for e in report["by_provider"]] == ["ledger_only"]


# ------------------------------------------------------------ sidecar fallback


def test_sidecar_fallback_when_ledger_has_no_rows(tmp_project, add_shot, tmp_path):
    # Nothing recorded in the ledger -> derive spend from take sidecars on disk.
    _register_paid_take(tmp_project, tmp_path, add_shot, "S001",
                        provider="cloud_a", cost=7.25, n=1)
    _register_paid_take(tmp_project, tmp_path, add_shot, "S002",
                        provider="cloud_b", cost=2.75, n=2)

    report = spend_report(tmp_project)

    assert report["source"] == "sidecars"
    assert report["total"] == pytest.approx(10.0)
    assert report["currency"] == "CNY"  # single currency across takes
    assert report["by_provider"] == [
        {"provider": "cloud_a", "runs": 1, "cost": pytest.approx(7.25)},
        {"provider": "cloud_b", "runs": 1, "cost": pytest.approx(2.75)},
    ]
    assert {e["shot"] for e in report["by_shot"]} == {"S001", "S002"}
    assert len(report["recent"]) == 2
    assert all(r["status"] == "succeeded" for r in report["recent"])


# -------------------------------------------------------------------- budget


def test_budget_limit_reflects_config(tmp_project):
    config = tmp_project.load_config()
    config.budget.limit = 88.0
    tmp_project.save_config(config)

    report = spend_report(tmp_project)
    assert report["budget_limit"] == 88.0
