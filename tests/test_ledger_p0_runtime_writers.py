"""Ledger P0 (runtime_writers): symlink/hardlink write-through on the runtime
append ledgers, the SQLite runtime state, and the QC / funnel scaffold writers.

Every case is the audit's dynamic repro: a project-internal name (``events.jsonl``,
``reports/``, ``.manju``, ``story/`` …) turned into a link and a normal operation
run against it must land NOTHING outside the project and must never write through
a hardlink into project truth. POSIX symlink/hardlink/junction repro; the Windows
reparse-point variant is tracked separately (audit FUNNEL NEEDS-TARGET), so the
whole module skips on Windows rather than error on mkfifo/os.link.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.events import (
    EvidenceWriteError,
    append_event,
    append_jsonl_line,
    tail_events,
)
from manju.core.failures import Failure, read_failures, record_failure
from manju.core.recents import touch_recent
from manju.core.safeio import SafeOutError
from manju.qc.checks import QCItem, QCReport
from manju.qc.report import write_reports
from manju.runtime.state import RuntimeState

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink/hardlink repro; Windows reparse variant tracked separately"
)


# --------------------------------------------------------------- EVENTS-P0-001

def test_events_append_refuses_symlink_write_through(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.yaml").write_text("name: p\n", encoding="utf-8")
    victim = tmp_path / "outside.log"
    victim.write_text("keep\n", encoding="utf-8")
    (root / "events.jsonl").symlink_to(victim)

    append_event(root, "human", "note", {"x": 1})  # best-effort: drops, never raises

    assert victim.read_text(encoding="utf-8") == "keep\n"  # not written through
    assert (root / "events.jsonl").is_symlink()  # leaf not replaced either


def test_events_append_refuses_hardlink_into_truth(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    truth = root / "project.yaml"
    truth.write_text("name: p\n", encoding="utf-8")
    os.link(truth, root / "events.jsonl")  # events.jsonl == project.yaml inode

    append_event(root, "human", "note", {"x": 1})

    assert truth.read_text(encoding="utf-8") == "name: p\n"  # truth uncorrupted


def test_events_required_append_fails_closed_on_symlink(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    victim = tmp_path / "outside.log"
    victim.write_text("keep\n", encoding="utf-8")
    (root / "events.jsonl").symlink_to(victim)

    with pytest.raises(EvidenceWriteError):
        append_jsonl_line(root, {"a": 1}, durable=True, required=True)
    assert victim.read_text(encoding="utf-8") == "keep\n"


def test_events_normal_append_still_works(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    append_event(root, "human", "note", {"x": 1})
    recs = tail_events(root, 5)
    assert recs and recs[-1]["action"] == "note"


# ------------------------------------------------------------- FAILURES-P0-001

def _failure() -> Failure:
    return Failure(step="render", subject="S001", cause="boom")


def test_failures_reports_dir_symlink_writes_nothing_outside(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.yaml").write_text("name: p\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "reports").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SafeOutError):
        record_failure(root, _failure())
    assert not (outside / "failures.jsonl").exists()
    assert not (outside / "failures.lock").exists()  # lock never planted outside


def test_failures_symlink_leaf_refused(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "reports").mkdir(parents=True)
    victim = tmp_path / "outside.log"
    victim.write_text("keep\n", encoding="utf-8")
    (root / "reports" / "failures.jsonl").symlink_to(victim)

    with pytest.raises(SafeOutError):
        record_failure(root, _failure())
    assert victim.read_text(encoding="utf-8") == "keep\n"


def test_failures_hardlink_into_truth_refused(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "reports").mkdir(parents=True)
    truth = root / "project.yaml"
    truth.write_text("name: p\n", encoding="utf-8")
    os.link(truth, root / "reports" / "failures.jsonl")

    with pytest.raises(SafeOutError):
        record_failure(root, _failure())
    assert truth.read_text(encoding="utf-8") == "name: p\n"


def test_failures_normal_record_still_works(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    record_failure(root, _failure())
    got = read_failures(root, 5)
    assert got and got[0]["subject"] == "S001"


# ---------------------------------------------------------------- STATE-P0-001

def test_state_manju_dir_symlink_refused(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / ".manju").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SafeOutError):
        RuntimeState(root)
    assert not (outside / "state.sqlite").exists()


def test_state_db_file_symlink_refused(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".manju").mkdir(parents=True)
    external = tmp_path / "external.sqlite"  # does not exist yet
    (root / ".manju" / "state.sqlite").symlink_to(external)

    with pytest.raises(SafeOutError):
        RuntimeState(root)
    assert not external.exists()  # never auto-initialized outside the project


def test_state_db_hardlink_refused(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".manju").mkdir(parents=True)
    external = tmp_path / "external.sqlite"
    con = sqlite3.connect(str(external))
    con.execute("CREATE TABLE secret (v TEXT)")
    con.commit()
    con.close()
    os.link(external, root / ".manju" / "state.sqlite")

    with pytest.raises(SafeOutError):
        RuntimeState(root)
    con = sqlite3.connect(str(external))
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "runs" not in tables and "secret" in tables  # external db untouched


def test_state_normal_open_still_works(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    with RuntimeState(root) as state:
        state.record_run(shot="S001", provider="p", status="succeeded")
        assert state.count_runs() == 1


# ------------------------------------------------------------------- QC-P0-001

def _report() -> QCReport:
    return QCReport(items=[QCItem("info", "technical", "S001", "fine")])


def test_qc_reports_dir_symlink_writes_nothing_outside(tmp_project: Project) -> None:
    project = tmp_project
    outside = project.root.parent / "qc_elsewhere"
    outside.mkdir()
    reports = project.reports_dir
    if reports.exists():
        import shutil

        shutil.rmtree(reports)
    reports.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SafeOutError):
        write_reports(project, _report())
    assert not (outside / "qc.json").exists()
    assert not (outside / "repair_plan.yaml").exists()


def test_qc_normal_write_produces_three_artifacts(tmp_project: Project) -> None:
    project = tmp_project
    paths = write_reports(project, _report())
    assert paths["qc_json"].exists() and paths["qc_md"].exists()
    assert paths["repair_plan"].exists()
    payload = json.loads(paths["qc_json"].read_text(encoding="utf-8"))
    assert payload["ok"] is True and "generated_at" in payload


# --------------------------------------------------------------- FUNNEL-P0-001

def test_funnel_story_dir_symlink_writes_nothing_outside(tmp_project: Project) -> None:
    from manju.build.funnel import FunnelError, scaffold_stage

    project = tmp_project
    outside = project.root.parent / "story_elsewhere"
    outside.mkdir()
    story = project.root / "story"
    if story.exists():
        import shutil

        shutil.rmtree(story)
    story.symlink_to(outside, target_is_directory=True)

    with pytest.raises(FunnelError):  # SafeOutError folded into the create envelope
        scaffold_stage(project, "brief")
    assert not (outside / "brief.md").exists()


def test_funnel_normal_scaffold_still_works(tmp_project: Project) -> None:
    from manju.build.funnel import scaffold_stage

    project = tmp_project
    story = project.root / "story"
    brief = story / "brief.md"
    if brief.exists():
        brief.unlink()
    result = scaffold_stage(project, "brief")
    assert result["created"] and brief.exists()
    assert brief.read_text(encoding="utf-8").startswith("# 立意")


# --------------------------------------------------- recents (quartet consistency)

def test_recents_symlink_store_not_written_through(tmp_project: Project, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text('{"keep": true}\n', encoding="utf-8")
    store = tmp_path / "recents.json"
    store.symlink_to(victim)
    monkeypatch.setenv("MANJU_RECENTS", str(store))

    touch_recent(tmp_project)  # best-effort: skips the unsafe write, never raises

    assert victim.read_text(encoding="utf-8") == '{"keep": true}\n'
