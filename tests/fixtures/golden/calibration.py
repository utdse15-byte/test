"""AI_IDE_20B — the reviewer CALIBRATION runner (contract §5).

Feeds the golden visual corpus through the REAL AI_IDE_15 pipeline with the 20A
fake reviewer + disagreeing twin, then computes the §5 report:

    per-dimension precision/recall · false-blocker rate · missed-blocker rate ·
    unknown rate · disagreement rate · cost/latency · model/profile digest

HONEST FRAMING (addendum ruling): the subjects here are the deterministic FAKE
reviewer profiles — the rates prove the HARNESS (corpus → real qc_brief packets →
real record_verdicts intake → real stored records → real reviewer_agreement /
drift surfaces → pure metric arithmetic), NOT the accuracy of any real model.
The corpus IS the annotated set; the fake is the subject. Real-model rows would
require an operator-run canary review through AI_IDE_14 qualification.

Everything is deterministic and offline: no network, no clock in any metric
(cost/latency are reported as honest zeros for the in-process fakes — the FIELD
exists so a real run can fill it). Thresholds are NEVER written back to any
production policy; runtime code never reads this module or its report (a 20B
self-test greps src/manju for it).
"""

from __future__ import annotations

import json
from pathlib import Path

from manju.core.container import Project
from manju.core.models import ShotSpec, TakeSidecar
from manju.core.yamlio import write_yaml
from manju.qc.agent_review import qc_brief, read_v2_records, record_verdicts
from manju.qc.production import drift_trend, repair_routes, reviewer_agreement

from .fake_reviewer import PROFILES, FakeVisionReviewer, load_manifest

SCHEMA = "manju.golden_corpus.calibration/20B"
CALIBRATION_DIR = "calibration"          # under <project>/reports/
UNKNOWN_ANSWERS = ("uncertain", "not_evaluated")
DEFINITE_ANSWERS = ("present", "absent")

SUBJECT_NOTE = (
    "校准对象是确定性的 fake reviewer(primary/twin)——这些比率证明 harness 本身"
    "(corpus→真实 packet→真实 intake→真实存储记录→纯函数指标)正确,不代表任何"
    "真实模型的精度;真实模型行需经 AI_IDE_14 资格化后由操作者跑真实 canary review。"
)


# ------------------------------------------------------------------ scenario


def _new_project(root: Path) -> Project:
    project = Project.create(Path(root) / "calibration_proj", git_init=False)
    write_yaml(project.root / "bible" / "scenes.yaml",
               {"convenience_store": {"name": "便利店"}})
    write_yaml(project.root / "bible" / "characters.yaml",
               {"linxia": {"name": "林夏"}})
    return project


def _add_shot(project: Project, shot_id: str, must_show: str) -> None:
    shot = ShotSpec.model_validate({
        "id": shot_id,
        "scene": "convenience_store",
        "characters": ["linxia"],
        "duration": "auto",
        "quality": {"must_show": [must_show]},
    })
    project.save_shot(shot)
    index = project.load_index()
    if shot_id not in index.order:
        index.order.append(shot_id)
        project.save_index(index)


def _register_case(project: Project, shot_id: str, case: dict, golden_dir: Path):
    take = project.register_take(
        shot_id, golden_dir / case["path"],
        TakeSidecar(provider="test", spec_hash="h"))
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    return take


def visual_cases(manifest: dict) -> list[dict]:
    """The committed visual-continuity cases in deterministic (id) order."""
    return sorted((c for c in manifest["cases"]
                   if c["partition"] == "visual_continuity"), key=lambda c: c["id"])


# ------------------------------------------------------------------ the run


def run_calibration(root: Path, *, manifest: dict | None = None,
                    profiles=PROFILES, write_report: bool = True) -> dict:
    """Drive every committed visual case through the REAL 15 pipeline once per
    profile and derive the §5 calibration report. Deterministic: same corpus +
    same profiles → an identical report dict (the report file's ``generated_by``
    carries no clock)."""
    from tests.fixtures.golden import GOLDEN_DIR

    manifest = manifest or load_manifest()
    cases = visual_cases(manifest)
    project = _new_project(Path(root))

    # one shot per case, S001..S0NN in case order — the ONE scenario both
    # profiles review (reviewer_agreement needs both opinions on one shot).
    shot_of: dict[str, str] = {}
    for i, case in enumerate(cases, start=1):
        sid = f"S{i:03d}"
        shot_of[case["id"]] = sid
        _add_shot(project, sid, f"基准要求:{case['dimension']} 符合金标注")
        _register_case(project, sid, case, GOLDEN_DIR)

    rows = {r["shot"]: r for r in qc_brief(project)["shots"]}
    assert set(rows) == set(shot_of.values()), "every case must brief"

    reviewers = {p: FakeVisionReviewer(p, manifest) for p in profiles}
    for profile, reviewer in reviewers.items():
        batch = {"schema": "manju.qc.verdict/v2", "verdicts": [
            reviewer.build_verdict(rows[shot_of[c["id"]]], include_dimensions=True)
            for c in cases]}
        result = record_verdicts(project, batch)
        assert result["written"] == len(cases)

    # read back what the REAL intake stored — metrics derive from the stored
    # records, never from the stance table (that is the point of the harness).
    records, malformed = read_v2_records(project)
    assert malformed == 0
    by_profile: dict[str, dict[str, dict]] = {p: {} for p in profiles}
    name_to_profile = {reviewers[p].manifest_id: p for p in profiles}
    for rec in records:
        profile = name_to_profile.get((rec.get("reviewer") or {}).get("name"))
        if profile is None:
            continue
        by_profile[profile][rec["subject"]["id"]] = rec

    # per-shot agreement through the REAL 15 surface.
    agreement = {sid: reviewer_agreement(project, sid) for sid in shot_of.values()}
    trend = drift_trend(project)
    routes = repair_routes(project)

    # ---------------- metric derivation (pure arithmetic, explicit denominators)
    answers: dict[str, dict[str, dict]] = {p: {} for p in profiles}
    for case in cases:
        sid = shot_of[case["id"]]
        for p in profiles:
            rec = by_profile[p][sid]
            answers[p][case["id"]] = {
                "observed": rec["observations"][0]["observed"],
                "blocker_finding": any(f.get("level") == "blocker"
                                       for f in rec.get("findings") or []),
                "binding": rec["binding"],
            }

    report_profiles = {}
    for p in profiles:
        report_profiles[p] = _profile_metrics(cases, answers[p], reviewers[p])

    if len(profiles) >= 2:
        a, b = profiles[0], profiles[1]
        disagreements = [c["id"] for c in cases
                         if answers[a][c["id"]]["observed"] != answers[b][c["id"]]["observed"]]
        disagreement = {
            "between": [reviewers[a].manifest_id, reviewers[b].manifest_id],
            "disagreements": len(disagreements),
            "denominator": len(cases),
            "rate": round(len(disagreements) / len(cases), 6),
            "case_ids": disagreements,
            "blocker_disagreement_shots": sorted(
                sid for sid, view in agreement.items()
                if view["state"] == "UNKNOWN_REVIEWER_DISAGREEMENT"),
        }
    else:
        disagreement = None

    report = {
        "schema": SCHEMA,
        "subject_note": SUBJECT_NOTE,
        "corpus": {
            "manifest_schema": manifest["schema"],
            "manifest_version": manifest["version"],
            "rubric_version": manifest["rubric_version"],
            "visual_cases": len(cases),
        },
        "profiles": report_profiles,
        "disagreement": disagreement,
        "real_pipeline_surfaces": {
            "packets_issued": len(cases),
            "records_stored": len(records),
            "records_bound": sum(1 for recs in by_profile.values()
                                 for r in recs.values() if r["binding"] == "bound"),
            "agreement_states": sorted({v["state"] for v in agreement.values()}),
            "drift_dimensions": sorted(trend["dimensions"].keys()),
            "route_vocabulary_size": len(routes["route_vocabulary"]),
            "routes_proposed": len(routes["routes"]),
        },
        "policy_writeback": "NEVER — 阈值不自动写回 production policy(契约 §5);"
                            "本报告是派生视图,运行时代码不读取它。",
    }
    if write_report:
        dest = project.reports_dir / CALIBRATION_DIR
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "calibration.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        report["report_path"] = str(Path("reports") / CALIBRATION_DIR / "calibration.json")
    return report


def _profile_metrics(cases: list[dict], answer: dict[str, dict],
                     reviewer: FakeVisionReviewer) -> dict:
    """The §5 metric block for one profile. Positive = 'the annotated defect is
    present' (expected observed == absent); a reviewer 'positive' = it reports
    absent. Ambiguous cases (expected uncertain/not_evaluated) are excluded from
    precision/recall denominators and measured by their own honest-unknown /
    overconfidence rates instead."""
    dims = sorted({c["dimension"] for c in cases})
    per_dimension = {}
    for dim in dims:
        dim_cases = [c for c in cases if c["dimension"] == dim]
        defect = [c for c in dim_cases
                  if c["expected_observations"][0]["observed"] == "absent"]
        clean = [c for c in dim_cases
                 if c["expected_observations"][0]["observed"] == "present"]
        tp = sum(1 for c in defect if answer[c["id"]]["observed"] == "absent")
        fn = len(defect) - tp
        fp = sum(1 for c in clean if answer[c["id"]]["observed"] == "absent")
        per_dimension[dim] = {
            "defect_cases": len(defect),
            "clean_cases": len(clean),
            "ambiguous_cases": len(dim_cases) - len(defect) - len(clean),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 6) if (tp + fp) else None,
            "recall": round(tp / (tp + fn), 6) if (tp + fn) else None,
        }

    expected_blocker = [c for c in cases
                        if c["expected_observations"][0]["severity"] == "blocker"]
    not_blocker = [c for c in cases
                   if c["expected_observations"][0]["severity"] != "blocker"]
    missed = sum(1 for c in expected_blocker
                 if not answer[c["id"]]["blocker_finding"])
    false_b = sum(1 for c in not_blocker if answer[c["id"]]["blocker_finding"])
    unknowns = [c["id"] for c in cases
                if answer[c["id"]]["observed"] in UNKNOWN_ANSWERS]
    ambiguous = [c for c in cases
                 if c["expected_observations"][0]["observed"] in UNKNOWN_ANSWERS]
    overconfident = [c["id"] for c in ambiguous
                     if answer[c["id"]]["observed"] in DEFINITE_ANSWERS]

    return {
        "reviewer": reviewer.manifest_id,
        "profile_digest": reviewer.profile_digest,
        "per_dimension": per_dimension,
        "false_blocker": {"count": false_b, "denominator": len(not_blocker),
                          "rate": round(false_b / len(not_blocker), 6)
                                  if not_blocker else None},
        "missed_blocker": {"count": missed, "denominator": len(expected_blocker),
                           "rate": round(missed / len(expected_blocker), 6)
                                   if expected_blocker else None},
        "unknown": {"count": len(unknowns), "denominator": len(cases),
                    "rate": round(len(unknowns) / len(cases), 6),
                    "case_ids": sorted(unknowns)},
        "overconfident_on_ambiguous": {
            "count": len(overconfident), "denominator": len(ambiguous),
            "rate": round(len(overconfident) / len(ambiguous), 6)
                    if ambiguous else None,
            "case_ids": sorted(overconfident)},
        # honest zeros: the fake is in-process and free; the FIELD exists so a
        # real qualified-reviewer run can fill real numbers (contract §5).
        "cost_latency": {"cost_total": 0.0, "currency": "CNY", "latency_ms": 0,
                         "note": "fake reviewer double — 零网络、进程内、确定性;"
                                 "真实模型的成本/时延须来自真实 canary run"},
    }
