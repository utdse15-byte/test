"""Cycle-26: cancelable kind names match submit(); job strip KIND_ZH complete."""

from __future__ import annotations

from manju.core import jobkinds
from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job


def test_cancelable_kinds_match_real_submit_names() -> None:
    # Real kinds from gui/server.py JobRunner.submit(...) calls that wire cancel.
    assert "ingest" in CANCELABLE_RUNNING_KINDS
    assert "ingest_apply" not in CANCELABLE_RUNNING_KINDS  # never submitted
    assert "series_sync_bible" in CANCELABLE_RUNNING_KINDS
    assert "series_sync" not in CANCELABLE_RUNNING_KINDS  # wrong old alias
    assert "ingest_plan" in CANCELABLE_RUNNING_KINDS
    assert "edit_preview_batch" in CANCELABLE_RUNNING_KINDS
    assert "edit_preview" not in CANCELABLE_RUNNING_KINDS  # wrong old alias
    # series_new_episode does not sample should_cancel — cancelable would be a lie.
    assert "series_new_episode" not in CANCELABLE_RUNNING_KINDS


def test_ingest_running_is_cancelable() -> None:
    j = Job(id="i1", kind="ingest", params={}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True
    j2 = Job(id="s1", kind="series_sync_bible", params={}, project_id="p")
    j2.state = "running"
    assert j2.to_dict()["cancelable"] is True
    j3 = Job(id="e1", kind="edit_preview_batch", params={}, project_id="p")
    j3.state = "running"
    assert j3.to_dict()["cancelable"] is True


def test_kind_zh_covers_ingest_and_series() -> None:
    # Behavioral (P1 item 2/4): the labels resolve from the authoritative
    # registry, not a hard-coded frontend map.
    labels = jobkinds.display_labels()
    assert labels["ingest_plan"] == "导入计划"
    assert labels["ingest"] == "导入应用"
    assert labels["series_sync_bible"] == "同步设定"
