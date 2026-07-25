"""A plan you cannot read is not a plan you can approve.

`manju pull-sheet <file>` defaults to inspect-only precisely so a human reviews
the change before `--apply` writes it. The text surface showed only counts —
"0 create · 1 update · 11 unchanged" — so the reviewer's choices were to apply
blind or diff the CSV by hand. The per-shot detail was already computed and
already carried by `--json`; it simply never reached the human.

Pinned here: changed rows are named with their fields and new values, unchanged
rows stay a count (that is all they are), and a locked field that will be
skipped is called out rather than silently dropped.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from typer.testing import CliRunner

from manju.cli import app
from manju.core.container import Project

runner = CliRunner()


def _export_sheet(project: Project) -> Path:
    res = runner.invoke(app, ["export", "--pullsheet", "--yes"])
    assert res.exit_code == 0, res.stdout
    sheets = list((project.root / "exports" / "pullsheet").glob("*.csv"))
    assert sheets, f"no pull sheet exported: {res.stdout}"
    return sheets[0]


def _edit(sheet: Path, dest: Path, **changes: str) -> Path:
    rows = list(csv.DictReader(sheet.open(encoding="utf-8")))
    assert rows, "exported sheet has no rows"
    rows[0].update(changes)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    dest.write_text(buf.getvalue(), encoding="utf-8")
    return dest


def test_plan_names_the_shot_and_fields_it_would_change(
        tmp_project: Project, add_shot, monkeypatch, tmp_path: Path) -> None:
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    monkeypatch.chdir(tmp_project.root)
    sheet = _export_sheet(tmp_project)
    edited = _edit(sheet, tmp_path / "edited.csv",
                   shot_size="close_up", movement="dolly_in")

    res = runner.invoke(app, ["pull-sheet", str(edited)])
    assert res.exit_code == 0, res.stdout
    assert "1 update" in res.stdout
    assert "S001" in res.stdout          # WHICH shot
    assert "camera" in res.stdout        # WHICH field
    assert "close_up" in res.stdout      # and the value being approved


def test_an_unchanged_sheet_stays_quiet(
        tmp_project: Project, add_shot, monkeypatch) -> None:
    """No noise when there is nothing to review."""
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    sheet = _export_sheet(tmp_project)

    res = runner.invoke(app, ["pull-sheet", str(sheet)])
    assert res.exit_code == 0, res.stdout
    assert "0 update" in res.stdout
    assert "更新" not in res.stdout and "新建" not in res.stdout


def test_plan_still_writes_nothing(
        tmp_project: Project, add_shot, monkeypatch, tmp_path: Path) -> None:
    """The detail is printed by the INSPECT path — it must stay zero-write."""
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    sheet = _export_sheet(tmp_project)
    edited = _edit(sheet, tmp_path / "e.csv", shot_size="close_up")
    before = (tmp_project.shots_dir / "S001.yaml").read_bytes()

    runner.invoke(app, ["pull-sheet", str(edited)])
    assert (tmp_project.shots_dir / "S001.yaml").read_bytes() == before


def test_apply_still_lands_the_change(
        tmp_project: Project, add_shot, monkeypatch, tmp_path: Path) -> None:
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    sheet = _export_sheet(tmp_project)
    edited = _edit(sheet, tmp_path / "e.csv",
                   shot_size="close_up", movement="dolly_in")

    res = runner.invoke(app, ["pull-sheet", str(edited), "--apply"])
    assert res.exit_code == 0, res.stdout
    cam = Project(tmp_project.root).load_shot("S001").camera
    assert cam.shot_size == "close_up"
    assert cam.movement == "dolly_in"
