"""Round AA (goal item 8): honest skill/workflow evaluation (core/evaluate.py).

Pins: a fresh/empty project degrades to an all-zeros report (never crashes);
seeded events.jsonl + reports/qc_agent.jsonl records roll up into per-skill
usage counts, never-used skills, redo/repair rework hotspots (with small-N
flagging), funnel scaffold coverage, and QC verdict tallies; the ``honesty``
section is always present with its required fields; the CLI `manju evaluate
--json` round-trips the same shape and is deterministic across repeated
calls; and the CLI `manju skills show` path actually appends the
``skill_used`` event evaluate() reads.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manju.cli import app
from manju.core.events import append_event
from manju.core.evaluate import SMALL_N_THRESHOLD, evaluate

runner = CliRunner()


def _write_qc_line(project, rec: dict) -> None:
    path = project.reports_dir / "qc_agent.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------- empty


def test_empty_project_is_all_zeros(tmp_project):
    report = evaluate(tmp_project)

    assert report["events_total"] == 0
    assert report["project"] == tmp_project.load_config().name

    sk = report["skills"]
    assert sk["used_total"] == 0
    assert sk["installed_total"] > 0  # the bundled library ships skills
    assert sk["never_used"]  # every installed skill is unused
    assert all(row["count"] == 0 for row in sk["usage"])

    wf = report["workflow"]
    assert wf["redo"] == {"total": 0, "hotspots": []}
    assert wf["repair"] == {"total": 0, "hotspots": []}
    assert wf["select"] == {"manual": 0, "auto": 0, "total": 0}
    assert wf["build"] == {"total": 0, "ok": 0, "failed": 0, "canceled": 0}
    assert wf["ingest"] == {"runs": 0, "files_landed": 0}
    assert wf["funnel"]["scaffold_events"] == {}
    assert wf["funnel"]["never_scaffolded"]  # brief/synopsis/beats, none scaffolded

    qc = report["qc"]
    assert qc["verdicts_total"] == 0
    assert qc["by_level"] == {"blocker": 0, "issue": 0, "fyi": 0}
    assert qc["malformed_lines"] == 0

    assert report["actors"] == {}

    honesty = report["honesty"]
    assert honesty["summary"]
    assert honesty["cannot_claim"]
    assert honesty["small_n_threshold"] == SMALL_N_THRESHOLD


# ----------------------------------------------------------------- seeded


def test_seeded_events_roll_up_correctly(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    root = tmp_project.root

    # skill usage: creation-funnel used 3x (2 cli + 1 gui) — not low_n (>= threshold)
    append_event(root, "human", "skill_used", {"skill": "creation-funnel", "via": "cli"})
    append_event(root, "human", "skill_used", {"skill": "creation-funnel", "via": "cli"})
    append_event(root, "ai", "skill_used", {"skill": "creation-funnel", "via": "gui"})
    # narrative-pacing used once — low_n
    append_event(root, "ai", "skill_used", {"skill": "narrative-pacing", "via": "auto"})

    # redo hotspots: S001 x3, S002 x1
    for _ in range(3):
        append_event(root, "ai", "redo", {"shot": "S001", "takes": ["t1"]})
    append_event(root, "human", "redo", {"shot": "S002", "takes": ["t1"]})

    # repair: S001 x2
    for _ in range(2):
        append_event(root, "human", "repair", {"shot": "S001", "op": "trim"})

    # select / auto_select
    append_event(root, "human", "select", {"shot": "S001", "take": "t1", "via": "cli"})
    append_event(root, "engine", "auto_select", {"shot": "S002", "take": "t1", "via": "build_auto_select"})

    # build: one ok, one failed
    append_event(root, "human", "build", {"target": "final", "ok": True})
    append_event(root, "human", "build", {"target": "final", "ok": False})

    # ingest
    append_event(root, "human", "ingest", {"rows": 5, "attempted": 5, "landed": 4})

    # funnel scaffold — brief only
    append_event(root, "human", "funnel_scaffold", {"stage": "brief", "path": "story/brief.md", "force": False})

    report = evaluate(tmp_project)

    # ---- skills
    sk = report["skills"]
    by_id = {row["id"]: row for row in sk["usage"]}
    assert by_id["creation-funnel"]["count"] == 3
    assert by_id["creation-funnel"]["low_n"] is False
    assert by_id["creation-funnel"]["by_via"] == {"cli": 2, "gui": 1}
    assert by_id["narrative-pacing"]["count"] == 1
    assert by_id["narrative-pacing"]["low_n"] is True
    assert "manju" in sk["never_used"]  # the core skill was never served here
    assert "creation-funnel" not in sk["never_used"]
    # usage sorted by count desc then id asc
    assert sk["usage"][0]["id"] == "creation-funnel"

    # ---- workflow: redo/repair hotspots
    wf = report["workflow"]
    assert wf["redo"]["total"] == 4
    redo_by_shot = {h["shot"]: h for h in wf["redo"]["hotspots"]}
    assert redo_by_shot["S001"]["count"] == 3
    assert redo_by_shot["S001"]["low_n"] is False
    assert redo_by_shot["S002"]["count"] == 1
    assert redo_by_shot["S002"]["low_n"] is True
    assert wf["redo"]["hotspots"][0]["shot"] == "S001"  # sorted desc by count

    assert wf["repair"]["total"] == 2
    assert wf["repair"]["hotspots"][0] == {"shot": "S001", "count": 2, "last_used": wf["repair"]["hotspots"][0]["last_used"], "low_n": True}

    assert wf["select"] == {"manual": 1, "auto": 1, "total": 2}
    assert wf["build"] == {"total": 2, "ok": 1, "failed": 1, "canceled": 0}
    assert wf["ingest"] == {"runs": 1, "files_landed": 4}

    fn = wf["funnel"]
    assert fn["scaffold_events"]["brief"]["count"] == 1
    assert "brief" not in fn["never_scaffolded"]
    assert "synopsis" in fn["never_scaffolded"] and "beats" in fn["never_scaffolded"]

    # ---- actors
    assert report["actors"]["human"] > 0
    assert report["actors"]["ai"] > 0
    assert report["actors"]["engine"] == 1

    # ---- honesty always present, regardless of data
    assert report["honesty"]["cannot_claim"]


def test_qc_verdicts_and_malformed_lines(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _write_qc_line(tmp_project, {
        "ts": "2026-07-08T00:00:00+00:00", "actor": "ai", "shot": "S001",
        "take": "t1", "take_hash": "h1", "criterion": "A1", "level": "blocker",
        "message": "身份不一致", "evidence": "",
    })
    _write_qc_line(tmp_project, {
        "ts": "2026-07-08T00:00:01+00:00", "actor": "ai", "shot": "S001",
        "take": "t1", "take_hash": "h1", "criterion": "C1", "level": "issue",
        "message": "背景穿帮", "evidence": "",
    })
    _write_qc_line(tmp_project, {
        "ts": "2026-07-08T00:00:02+00:00", "actor": "human", "shot": "S001",
        "take": "t1", "take_hash": "h1", "criterion": "D1", "level": "fyi",
        "message": "留意", "evidence": "",
    })
    # a torn/invalid line must not crash the reader
    path = tmp_project.reports_dir / "qc_agent.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")

    report = evaluate(tmp_project)
    qc = report["qc"]
    assert qc["verdicts_total"] == 3
    assert qc["by_level"] == {"blocker": 1, "issue": 1, "fyi": 1}
    assert qc["by_actor"] == {"ai": 2, "human": 1}
    assert qc["malformed_lines"] == 1


# ----------------------------------------------------------------- degrade


def test_malformed_events_line_is_skipped_not_fatal(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    append_event(tmp_project.root, "human", "redo", {"shot": "S001", "takes": ["t1"]})
    events_path = tmp_project.root / "events.jsonl"
    with open(events_path, "a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write("\n")  # blank line too

    report = evaluate(tmp_project)  # must not raise
    assert report["workflow"]["redo"]["total"] == 1
    assert report["events_total"] == 1  # the torn line + blank line don't count


# --------------------------------------------------------------------- CLI


def test_cli_evaluate_json_roundtrip_and_deterministic(tmp_project, monkeypatch, add_shot):
    add_shot(tmp_project, "S001")
    append_event(tmp_project.root, "human", "redo", {"shot": "S001", "takes": ["t1"]})
    append_event(tmp_project.root, "human", "skill_used", {"skill": "creation-funnel", "via": "cli"})
    monkeypatch.chdir(tmp_project.root)

    res1 = runner.invoke(app, ["evaluate", "--json"])
    assert res1.exit_code == 0, res1.output
    data1 = json.loads(res1.output)

    res2 = runner.invoke(app, ["evaluate", "--json"])
    assert res2.exit_code == 0
    data2 = json.loads(res2.output)

    assert data1 == data2  # deterministic across repeated calls
    assert data1["workflow"]["redo"]["total"] == 1
    assert data1["skills"]["used_total"] == 1
    assert "honesty" in data1 and data1["honesty"]["cannot_claim"]

    # non-JSON render includes the honesty block (中文) and doesn't crash
    res_human = runner.invoke(app, ["evaluate"])
    assert res_human.exit_code == 0
    assert "honesty" in res_human.output or "提示" in res_human.output
    assert "评估报告" in res_human.output


def test_cli_skills_show_lands_skill_used_event(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)

    res = runner.invoke(app, ["skills", "show", "manju"])
    assert res.exit_code == 0, res.output

    report = evaluate(tmp_project)
    row = next(r for r in report["skills"]["usage"] if r["id"] == "manju")
    assert row["count"] == 1
    assert row["by_via"] == {"cli": 1}
    assert row["last_used"]

    res_json = runner.invoke(app, ["evaluate", "--json"])
    data = json.loads(res_json.output)
    row2 = next(r for r in data["skills"]["usage"] if r["id"] == "manju")
    assert row2["count"] == 1
