"""AI_IDE_16 §9 — Storyboard Pull Sheet round-trip (CSV + Markdown).

Pinned: EXPORT is a pure derivation (shot id, duration, frame refs, camera,
action, dialogue, transition, refs, quality, status). IMPORT is inspect (ZERO
writes) → a shot-package-style proposal → CAS apply mapped onto the DR03A
machinery: a NEW row creates a shot, an EDITED row is a checked-write CAS
proposal, a STALE source fails the CAS, and selected_take / media / locks are
NEVER overwritten.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from manju.build import pullsheet as ps


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts and ".manju" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------ export


def test_export_csv_has_all_columns_and_rows(tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "推门而入"},
             camera={"shot_size": "close_up"})
    csv_text = ps.compile_pull_sheet_csv(tmp_project)
    header = csv_text.splitlines()[0].split(",")
    assert header == list(ps.COLUMNS)
    assert "S001" in csv_text and "close_up" in csv_text


def test_export_md_is_a_table(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    md = ps.compile_pull_sheet_md(tmp_project)
    assert "| shot_id |" in md and "| --- |" in md.replace(" ", " ")
    assert "S001" in md


def test_export_writes_csv_and_md(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    out = ps.export_pull_sheet(tmp_project)
    assert out["csv"].exists() and out["md"].exists()
    assert "pullsheet" in out["csv"].as_posix()


def test_csv_md_round_trip_parses_back(tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "a|b line"})  # pipe stress for md
    add_shot(tmp_project, "S002")
    csv_rows = ps.parse_sheet(ps.compile_pull_sheet_csv(tmp_project), fmt="csv")
    md_rows = ps.parse_sheet(ps.compile_pull_sheet_md(tmp_project), fmt="md")
    assert [r["shot_id"] for r in csv_rows] == ["S001", "S002"]
    assert [r["shot_id"] for r in md_rows] == ["S001", "S002"]


# ------------------------------------------------------------ import: inspect


def test_import_inspect_is_zero_write(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(ps.compile_pull_sheet_csv(tmp_project), encoding="utf-8")
    before = _tree_hash(tmp_project.root)
    plan = ps.plan_pull_sheet_import(tmp_project, sheet)
    assert plan["proposal_only"] is True
    assert plan["do_not_execute_automatically"] is True
    assert _tree_hash(tmp_project.root) == before      # nothing written


def test_unchanged_sheet_is_a_noop_plan(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(ps.compile_pull_sheet_csv(tmp_project), encoding="utf-8")
    plan = ps.plan_pull_sheet_import(tmp_project, sheet)
    assert plan["summary"] == {"create": 0, "update": 0, "unchanged": 1}


# ------------------------------------------------------------ import: edit existing


def _edit_csv(text: str, shot_id: str, col: str, value: str) -> str:
    import csv as _csv
    import io

    rows = list(_csv.DictReader(io.StringIO(text)))
    for r in rows:
        if r["shot_id"] == shot_id:
            r[col] = value
    buf = io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=list(ps.COLUMNS), lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def test_edit_existing_shot_is_a_cas_update_applied(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001", action={"main": "old action"})
    sheet = tmp_path / "sheet.csv"
    edited = _edit_csv(ps.compile_pull_sheet_csv(tmp_project), "S001", "action", "NEW action")
    sheet.write_text(edited, encoding="utf-8")
    plan = ps.plan_pull_sheet_import(tmp_project, sheet)
    assert plan["summary"]["update"] == 1
    res = ps.apply_pull_sheet_import(tmp_project, plan, actor="human")
    assert res["ok"] and res["applied"] == ["S001"]
    assert tmp_project.load_shot("S001").action.main == "NEW action"


def test_edit_never_overwrites_selected_take_or_media(tmp_project, add_shot, make_take, tmp_path):
    add_shot(tmp_project, "S001", action={"main": "old"})
    t = make_take(tmp_project, "S001", "sha256:v", suffix=".mp4")
    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = t.name
    tmp_project.save_shot(shot)
    sheet = tmp_path / "s.csv"
    sheet.write_text(_edit_csv(ps.compile_pull_sheet_csv(tmp_project), "S001", "action", "new"),
                     encoding="utf-8")
    plan = ps.plan_pull_sheet_import(tmp_project, sheet)
    ps.apply_pull_sheet_import(tmp_project, plan, actor="human")
    after = tmp_project.load_shot("S001")
    assert after.action.main == "new"
    assert after.status.selected_take == t.name       # selected_take untouched
    assert t.media_path.exists()                      # media untouched


def test_stale_source_fails_cas(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001", action={"main": "old"})
    sheet = tmp_path / "s.csv"
    sheet.write_text(_edit_csv(ps.compile_pull_sheet_csv(tmp_project), "S001", "action", "sheet-edit"),
                     encoding="utf-8")
    plan = ps.plan_pull_sheet_import(tmp_project, sheet)   # CAS token captured here
    # someone else edits the shot AFTER inspect → the CAS token is now stale
    other = tmp_project.load_shot("S001")
    other.action.main = "concurrent-edit"
    tmp_project.save_shot(other)
    res = ps.apply_pull_sheet_import(tmp_project, plan, actor="human")
    assert res["ok"] is False
    assert res["refused"] and res["applied"] == []
    # the concurrent edit stands — the stale sheet import never clobbered it
    assert tmp_project.load_shot("S001").action.main == "concurrent-edit"


def test_locked_field_blocks_the_write(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001", action={"main": "old"},
             locked={"action.main": ""})  # a lock on the field the sheet edits
    sheet = tmp_path / "s.csv"
    sheet.write_text(_edit_csv(ps.compile_pull_sheet_csv(tmp_project), "S001", "action", "hijack"),
                     encoding="utf-8")
    plan = ps.plan_pull_sheet_import(tmp_project, sheet)
    res = ps.apply_pull_sheet_import(tmp_project, plan, actor="human")
    assert res["ok"] is False and res["refused"]
    assert tmp_project.load_shot("S001").action.main == "old"   # lock held


# ------------------------------------------------------------ import: new shot


def test_new_shot_row_creates_via_shotpackage(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    text = ps.compile_pull_sheet_csv(tmp_project)
    # append a brand-new shot row S050
    new_row = ("S050,convenience_store,linxia,2500,,medium,static,eye_level,"
               "新镜头动作,linxia,新台词,cut,,,,needs_review")
    text = text.rstrip("\n") + "\n" + new_row + "\n"
    sheet = tmp_path / "s.csv"
    sheet.write_text(text, encoding="utf-8")
    plan = ps.plan_pull_sheet_import(tmp_project, sheet)
    assert plan["summary"]["create"] == 1
    assert plan["create_plan"] is not None
    res = ps.apply_pull_sheet_import(tmp_project, plan, actor="human")
    assert "S050" in res["created"]
    created = tmp_project.load_shot("S050")
    assert created.action.main == "新镜头动作"
    assert created.status.selected_take is None       # a create carries no take
