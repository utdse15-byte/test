"""DR02 WP4 — surface wiring (CLI / reports / director / GUI).

The DR02 core (expectations/assurance/v2 packet+verdict pipe) is already merged;
these tests pin how its DERIVED, READ-ONLY outputs surface through the existing
commands: the `manju qc` report + --json envelope, reports/qc.json + qc.md, the
director's suggest_next advisories and the GUI review surface.
`director_suggest` tools. Every assurance surface is additive: it never changes
the qc exit code, never writes a source, and an old qc.json (no assurance block)
keeps working.

Red-first: these were authored before the surfaces were wired.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.models import TakeSidecar
from manju.qc.agent_review import qc_brief, read_v2_records, record_verdicts
from manju.qc.assurance import assurance_for_all
from manju.qc.checks import run_qc
from manju.qc.report import write_reports

runner = CliRunner()

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg + ffprobe required (real, probe-able take)",
)

V2 = "manju.qc.verdict/v2"


# --------------------------------------------------------------- helpers
# (mirror tests/test_dr02_assurance.py's fixture patterns — a real, probe-able
# take + a v2 verdict echoing the brief's packet.)


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _real_take(project, shot_id, *, color="red", select=True):
    tmp = project.root / f"_src_{shot_id}_{color}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=160x120:d=1:r=24", "-pix_fmt", "yuv420p", str(tmp)],
        check=True, capture_output=True,
    )
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    if select:
        _select(project, shot_id, take.name)
    return take


def _row(project, shot_id):
    return next(r for r in qc_brief(project, [shot_id])["shots"] if r["shot"] == shot_id)


def _v2(row, observed=None, *, findings=None):
    exps = row["expectations"]
    if observed is None:
        observed = ["present"] * len(exps)
    return {
        "schema": V2, "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [{"expectation_id": e["id"], "observed": o}
                         for e, o in zip(exps, observed)],
        "findings": findings or [],
        "reviewer": {"kind": "model_visual", "name": "t"},
    }


def _rejected_shot(project, add_shot, shot_id="S001"):
    add_shot(project, shot_id, quality={"must_show": ["红色雨伞出现"]})
    _real_take(project, shot_id)
    row = _row(project, shot_id)
    record_verdicts(project, _v2(row, ["absent"]))  # present-expectation, absent -> FAIL
    return row


def _accepted_shot(project, add_shot, shot_id="S001"):
    add_shot(project, shot_id, quality={"must_show": ["红色雨伞出现"]})
    _real_take(project, shot_id)
    row = _row(project, shot_id)
    record_verdicts(project, _v2(row, ["present"]))
    return row


def _write_qc_with_assurance(project):
    """Run QC + write reports WITH the assurance block, the way a surface does."""
    report = run_qc(project, project.load_timeline(), extract_frames=False)
    assurance = assurance_for_all(project, qc_report=report)
    write_reports(project, report, assurance=assurance)
    return report, assurance


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


# ------------------------------------------------- 1. CLI --json envelope


@needs_ffmpeg
def test_qc_json_envelope_carries_assurance_states(in_project, add_shot):
    row = _rejected_shot(in_project, add_shot)
    result = runner.invoke(app, ["qc", "--json"])
    assert result.exit_code == 0, result.output  # rejected assurance + ok QC -> exit 0
    data = json.loads(result.output)
    block = data["assurance"]
    states = {s["subject"]["id"]: s["assurance_state"] for s in block["shots"]}
    assert states["S001"] == "rejected"
    # a repair proposal for the rejected shot rides along, citing the failed id
    fid = row["expectations"][0]["id"]
    props = {p["subject"]: p for p in block["proposals"]}
    assert "S001" in props
    assert fid in props["S001"]["failed_expectation_ids"]
    assert props["S001"]["do_not_execute_automatically"] is True


@needs_ffmpeg
def test_qc_exit_code_unaffected_by_assurance(in_project, add_shot):
    """A rejected-assurance shot whose deterministic QC has no error still exits 0
    — assurance is a SEPARATE axis from the qc gate."""
    _rejected_shot(in_project, add_shot)
    assert runner.invoke(app, ["qc"]).exit_code == 0


# ------------------------------------------------- 2. on-disk artifacts


@needs_ffmpeg
def test_qc_json_and_md_on_disk_carry_assurance(in_project, add_shot):
    _rejected_shot(in_project, add_shot)
    human = runner.invoke(app, ["qc"])
    # the human summary prints counts + a per-shot rejected line with its reason
    assert "验收 assurance:" in human.output
    assert "S001 · rejected" in human.output
    data = json.loads((in_project.reports_dir / "qc.json").read_text(encoding="utf-8"))
    assert "assurance" in data
    states = {s["subject"]["id"]: s["assurance_state"] for s in data["assurance"]["shots"]}
    assert states["S001"] == "rejected"
    md = (in_project.reports_dir / "qc.md").read_text(encoding="utf-8")
    assert "验收" in md and "assurance" in md
    assert "rejected" in md and "S001" in md


@needs_ffmpeg
def test_repair_plan_yaml_unchanged_by_assurance(in_project, add_shot):
    """repair_plan.yaml stays the machine-tier plan; assurance lives in qc.json.
    Compared on CONTENT (ok + actions) since ``generated_at`` is a fresh
    timestamp on every write."""
    import yaml

    _rejected_shot(in_project, add_shot)
    report = run_qc(in_project, in_project.load_timeline(), extract_frames=False)

    def _plan():
        d = yaml.safe_load((in_project.reports_dir / "repair_plan.yaml").read_text("utf-8"))
        return {"ok": d.get("ok"), "actions": d.get("actions")}

    write_reports(in_project, report)
    plan_a = _plan()
    write_reports(in_project, report, assurance=assurance_for_all(in_project, qc_report=report))
    plan_b = _plan()
    assert plan_a == plan_b


# ------------------------------------------------- 3. director suggest_next


@needs_ffmpeg
def test_rejected_shot_yields_advisory_repair_suggestion(in_project, add_shot):
    from manju.build.director import suggest_next

    row = _rejected_shot(in_project, add_shot)
    _write_qc_with_assurance(in_project)
    fid = row["expectations"][0]["id"]

    sugg = suggest_next(in_project)
    hits = [s for s in sugg if s.kind == "repair" and s.shot == "S001" and fid in s.text]
    assert hits, [s.to_dict() for s in sugg]
    assert all(s.action is None for s in hits)  # advisory only, no spend


@needs_ffmpeg
def test_unknown_shot_points_at_brief_re_review(in_project, add_shot):
    from manju.build.director import suggest_next

    add_shot(in_project, "S001", quality={"must_show": ["红色雨伞出现"]})
    _real_take(in_project, "S001")
    row = _row(in_project, "S001")
    record_verdicts(in_project, _v2(row, ["uncertain"]))  # -> unknown
    _write_qc_with_assurance(in_project)

    sugg = suggest_next(in_project)
    hits = [s for s in sugg if s.kind == "repair" and s.shot == "S001"
            and "brief" in s.text and s.action is None]
    assert hits, [s.to_dict() for s in sugg]


def test_old_qc_json_without_assurance_block_suggest_works(project_no_ff):
    """An old qc.json with no assurance block -> suggest_next runs, zero new
    assurance suggestions, no crash."""
    from manju.build import director as d

    project = project_no_ff
    qc = {"ok": True, "items": []}
    project.reports_dir.mkdir(parents=True, exist_ok=True)
    (project.reports_dir / "qc.json").write_text(json.dumps(qc), encoding="utf-8")
    # must not raise, and the assurance sub-helper contributes nothing
    d.suggest_next(project)
    assert d._assurance_suggestions(project) == []


# ------------------------------------------------- 7. additive-only writer


def test_write_reports_without_assurance_is_additive(project_no_ff, add_shot):
    """qc.json's existing keys are byte-identical with/without the assurance arg;
    the block is added, never mutating ok/items."""
    project = project_no_ff
    add_shot(project, "S001")
    report = run_qc(project, project.load_timeline(), extract_frames=False)

    write_reports(project, report)
    base = json.loads((project.reports_dir / "qc.json").read_text(encoding="utf-8"))
    assert "assurance" not in base
    assert set(base) == {"ok", "generated_at", "items"}

    write_reports(project, report, assurance=assurance_for_all(project, qc_report=report))
    withblk = json.loads((project.reports_dir / "qc.json").read_text(encoding="utf-8"))
    assert "assurance" in withblk
    # existing keys unchanged (generated_at aside, which is a timestamp)
    assert withblk["ok"] == base["ok"]
    assert withblk["items"] == base["items"]


# --------------------------------------------------------------- fixtures


@pytest.fixture
def project_no_ff(tmp_project):
    """A plain project (no ffmpeg needed) for the writer/director-shape tests."""
    return tmp_project
