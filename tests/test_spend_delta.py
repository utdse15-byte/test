"""Spend delta (REPORTS/COMPETITIVE-UX-STUDY.md "spend delta"): the run ledger
records ACTUAL cost, but the *事前* estimate used to vanish after the build. The
estimate is now persisted per run so ``spend_report`` can show estimate-vs-actual
— the delta column that calibrates trust in the ask_before gate (§8.3).

Covers:
- the ledger surfaces ``estimated_total`` / ``delta`` and per-row
  ``estimated_cost`` once any row carries an estimate;
- ``estimated_total`` sums only the non-None estimates (rows predating the
  feature contribute nothing);
- an estimate-less ledger — whether via ``estimated_cost=None`` or a legacy row
  inserted with the pre-feature column list — reports all three as ``None``;
- the sidecar fallback (§3) is honestly ``None`` (sidecars record actual cost
  only);
- the ``ALTER TABLE ... ADD COLUMN estimated_cost`` upgrade is idempotent across
  reopens;
- a real ``run_build`` persists the shot's pre-flight estimate onto the ledger
  row of the take it generates (caption_card offline chain).
"""

from __future__ import annotations

import pytest

from manju.build import graph
from manju.build.graph import run_build
from manju.build.spend import spend_report
from manju.core.models import RemoteJobInfo, TakeSidecar
from manju.runtime.state import RuntimeState

# --------------------------------------------------------------- estimate present


def test_report_surfaces_estimate_total_delta_and_per_row(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        st.record_run(shot="S001", provider="cloud_a", status="succeeded",
                      cost=5.0, currency="CNY", estimated_cost=4.0)
        st.record_run(shot="S002", provider="cloud_a", status="succeeded",
                      cost=3.0, currency="CNY", estimated_cost=3.5)

    report = spend_report(tmp_project)

    assert report["source"] == "ledger"
    assert report["total"] == pytest.approx(8.0)
    # estimate-vs-actual: sum of estimates 7.5, delta = actual - estimated = 0.5
    assert report["estimated_total"] == pytest.approx(7.5)
    assert report["delta"] == pytest.approx(0.5)
    assert report["delta_coverage"] == "覆盖 2/2 条记录"  # fully covered here

    # per-row estimate rides each recent entry (newest first: S002 leads)
    recent = report["recent"]
    assert [r["shot"] for r in recent] == ["S002", "S001"]
    assert recent[0]["estimated_cost"] == pytest.approx(3.5)
    assert recent[1]["estimated_cost"] == pytest.approx(4.0)
    assert "estimated_cost" in recent[0]


def test_estimated_total_sums_only_non_none_estimates(tmp_project):
    # One priced run and one estimate-less run: estimated_total counts only the
    # priced one. Goal 67: the delta must compare "actual for the SAME covered
    # rows" against that partial estimate — NOT the grand total (8.0) against a
    # partial estimate, which would misrepresent the delta as if it covered
    # everything. `total` itself stays the true all-rows figure.
    with RuntimeState(tmp_project.root) as st:
        st.record_run(shot="S001", provider="cloud_a", status="succeeded",
                      cost=5.0, currency="CNY", estimated_cost=4.0)
        st.record_run(shot="S002", provider="cloud_a", status="succeeded",
                      cost=3.0, currency="CNY")  # no estimate

    report = spend_report(tmp_project)

    assert report["total"] == pytest.approx(8.0)              # true all-rows total
    assert report["estimated_total"] == pytest.approx(4.0)    # only S001's 4.0
    assert report["delta"] == pytest.approx(1.0)               # S001: 5.0 - 4.0 only
    assert report["delta_coverage"] == "覆盖 1/2 条记录"
    by_shot = {r["shot"]: r for r in report["recent"]}
    assert by_shot["S001"]["estimated_cost"] == pytest.approx(4.0)
    assert by_shot["S002"]["estimated_cost"] is None


# --------------------------------------------------------------- no estimate


def test_estimateless_ledger_reports_none_fields(tmp_project):
    # record_run with the default estimated_cost=None -> no delta to show.
    with RuntimeState(tmp_project.root) as st:
        st.record_run(shot="S001", provider="cloud_a", status="succeeded",
                      cost=5.0, currency="CNY")
        st.record_run(shot="S002", provider="cloud_b", status="failed",
                      failure_kind="timeout")

    report = spend_report(tmp_project)

    assert report["source"] == "ledger"
    assert report["estimated_total"] is None
    assert report["delta"] is None
    assert all(r["estimated_cost"] is None for r in report["recent"])


def test_legacy_rows_without_estimate_column_report_none(tmp_project):
    # A row written by code predating the feature: insert with the OLD column
    # list, leaving the new nullable column NULL. The report degrades honestly.
    with RuntimeState(tmp_project.root) as st:
        with st._conn:
            st._conn.execute(
                "INSERT INTO runs (ts, shot, provider, status, cost, currency) "
                "VALUES (?, 'S001', 'cloud_a', 'succeeded', 5.0, 'CNY')",
                (st._ts(),),
            )

    report = spend_report(tmp_project)
    assert report["source"] == "ledger"
    assert report["estimated_total"] is None
    assert report["delta"] is None
    assert report["recent"][0]["estimated_cost"] is None


def test_sidecar_fallback_has_none_estimate_fields(tmp_project, add_shot, tmp_path):
    # No ledger rows -> derive from take sidecars, which carry ACTUAL cost only.
    add_shot(tmp_project, "S001")
    media = tmp_path / "paid.mp4"
    media.write_bytes(b"fakevideo")
    tmp_project.register_take(
        "S001",
        media,
        TakeSidecar(
            provider="cloud_a",
            spec_hash="manual",
            remote=RemoteJobInfo(job_id="job-1", cost=7.25, currency="CNY"),
        ),
    )

    report = spend_report(tmp_project)
    assert report["source"] == "sidecars"
    assert report["total"] == pytest.approx(7.25)
    assert report["estimated_total"] is None
    assert report["delta"] is None
    assert report["recent"][0]["estimated_cost"] is None


# --------------------------------------------------------------- ALTER idempotence


def test_alter_add_column_is_idempotent_across_opens(tmp_project):
    # Opening RuntimeState re-runs the ALTER on every connect; the duplicate
    # column must be swallowed (idempotent upgrade), never raised.
    with RuntimeState(tmp_project.root) as st:
        st.record_run(shot="S001", provider="p", status="succeeded",
                      cost=1.0, currency="CNY", estimated_cost=0.9)
    # Second open on the same DB must not raise and must see the persisted data.
    with RuntimeState(tmp_project.root) as st:
        log = st.run_log()
        assert log[0]["estimated_cost"] == pytest.approx(0.9)


# --------------------------------------------------------------- real build


def test_real_build_persists_estimate_on_generated_take(tmp_project, add_shot,
                                                        monkeypatch):
    # Every planned shot prices at 2.0 CNY (as a manifest would). A missing shot
    # is generated through the offline caption_card fallback chain (ffmpeg),
    # and the ledger row for the take it produces must carry that 2.0 estimate.
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (2.0, "CNY"))
    add_shot(tmp_project, "S001")  # MISSING -> enters the generation plan

    result = run_build(tmp_project, target="qc", actor="ai", assume_yes=True)
    assert result.generated  # offline chain produced a take

    with RuntimeState(tmp_project.root) as st:
        log = st.run_log()
    take_rows = [r for r in log if r["shot"] == "S001" and r["take"] is not None]
    assert take_rows, "the generated take should have a ledger row"
    assert take_rows[0]["estimated_cost"] == pytest.approx(2.0)
    assert take_rows[0]["status"] == "succeeded"


def test_multi_take_run_counts_estimate_once(tmp_project, add_shot, tmp_path):
    """Review R22 finding: one ledger row per take must NOT multiply the
    per-shot plan estimate (candidates=2 inflated estimated_total 2x)."""
    from manju.build.graph import _record_local_runs
    from manju.build.spend import spend_report
    from manju.core.models import TakeSidecar

    add_shot(tmp_project, "S001")
    takes = []
    for i in range(2):  # two takes of one shot, provider classified local
        media = tmp_path / f"m{i}.mp4"
        media.write_bytes(b"x" * (i + 1))
        takes.append(tmp_project.register_take(
            "S001", media, TakeSidecar(provider="caption_card", spec_hash="h")))
    _record_local_runs(tmp_project, takes, {}, estimates={"S001": 6.0})
    report = spend_report(tmp_project)
    assert report["source"] == "ledger"
    assert report["estimated_total"] == 6.0  # once per shot, not per take
