"""DR02 WP3 — derived assurance (qc/assurance.py): the contract's 12 tests.

Red-first note: ``qc.assurance`` did not exist before WP3 (pre-implementation
run is a bare ModuleNotFoundError). These tests pin the state precedence, the
pure diff, the spec-vs-expectations stale asymmetry, the strict separation of
derived assurance from human review, the read-only repair proposal, and the
.manju-independent determinism.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess

import pytest

from manju.core.models import TakeSidecar
from manju.qc.agent_review import qc_brief, record_verdicts
from manju.qc.assurance import compute_assurance, diff, repair_proposal

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg + ffprobe required (accepted state needs a real, probe-able take)",
)

V2 = "manju.qc.verdict/v2"


# --------------------------------------------------------------- helpers


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


def _accepted_shot(project, add_shot, shot_id="S001"):
    add_shot(project, shot_id, quality={"must_show": ["红色雨伞出现"]})
    _real_take(project, shot_id)
    row = _row(project, shot_id)
    record_verdicts(project, _v2(row, ["present"]))
    return row


def _rejected_shot(project, add_shot, shot_id="S001"):
    add_shot(project, shot_id, quality={"must_show": ["红色雨伞出现"]})
    _real_take(project, shot_id)
    row = _row(project, shot_id)
    record_verdicts(project, _v2(row, ["absent"]))  # present-expectation observed absent -> FAIL
    return row


def _tree_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


# ------------------------------------------------------------- pure diff


def test_diff_truth_table():
    exps = [{"id": "p", "polarity": "present"}, {"id": "a", "polarity": "absent"}]

    def d(p_obs, a_obs):
        return diff(exps, [{"expectation_id": "p", "observed": p_obs},
                           {"expectation_id": "a", "observed": a_obs}])

    assert d("present", "absent") == {"p": "PASS", "a": "PASS"}
    assert d("absent", "present") == {"p": "FAIL", "a": "FAIL"}
    assert d("uncertain", "not_evaluated") == {"p": "UNKNOWN", "a": "UNKNOWN"}
    # a missing observation is UNKNOWN, never a silent pass
    assert diff(exps, []) == {"p": "UNKNOWN", "a": "UNKNOWN"}


# --------------------------------------------------- the 12 contract tests


@needs_ffmpeg
def test_01_execution_done_without_review_is_not_accepted(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞出现"]})
    _real_take(tmp_project, "S001")
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "unreviewed"
    assert a["assurance_state"] != "accepted"


@needs_ffmpeg
def test_02_all_pass_is_accepted(tmp_project, add_shot):
    row = _accepted_shot(tmp_project, add_shot)
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "accepted", a["reasons"]
    assert a["failed_expectation_ids"] == []
    assert a["unknown_expectation_ids"] == []
    assert a["evidence"]["packet_id"] == row["packet_id"]
    assert a["evidence"]["media_sha256"] == row["media"]["sha256"]


@needs_ffmpeg
def test_03_any_fail_is_rejected(tmp_project, add_shot):
    row = _rejected_shot(tmp_project, add_shot)
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "rejected"
    assert row["expectations"][0]["id"] in a["failed_expectation_ids"]


@needs_ffmpeg
def test_04_no_fail_but_unknown_is_unknown(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞出现"]})
    _real_take(tmp_project, "S001")
    row = _row(tmp_project, "S001")
    record_verdicts(tmp_project, _v2(row, ["uncertain"]))
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "unknown"
    assert row["expectations"][0]["id"] in a["unknown_expectation_ids"]
    assert a["failed_expectation_ids"] == []


@needs_ffmpeg
def test_05_only_legacy_is_legacy_reviewed(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞出现"]})
    _real_take(tmp_project, "S001")
    # a legacy (schema-less) verdict only — never satisfies v2 acceptance.
    record_verdicts(tmp_project, {"verdicts": [
        {"shot": "S001", "criterion": "A1", "level": "issue", "message": "legacy 结论"}]})
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "legacy_reviewed"


@needs_ffmpeg
def test_06_no_explicit_expectations(tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # no must_show / avoid / locks
    _real_take(tmp_project, "S001")
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "no_explicit_expectations"


@needs_ffmpeg
@pytest.mark.parametrize("mutation,expected_reason", [
    ("media", "media"),
    ("spec", "spec"),
    ("expectations", "expectations"),
])
def test_07_any_binding_change_is_stale(tmp_project, add_shot, mutation, expected_reason):
    add_shot(tmp_project, "S001",
             quality={"must_show": ["红色雨伞出现"]},
             continuity={"locks": ["prop:coin"]})
    _real_take(tmp_project, "S001")
    row = _row(tmp_project, "S001")
    record_verdicts(tmp_project, _v2(row))  # bound

    if mutation == "media":
        _real_take(tmp_project, "S001", color="green")  # regenerate + select a new take
    elif mutation == "spec":
        tmp_project.update_shot_raw(
            "S001", lambda d: d.setdefault("action", {}).__setitem__("main", "全新动作"))
    else:  # expectations: a locks edit moves ONLY the digest (not spec_hash)
        tmp_project.update_shot_raw(
            "S001", lambda d: d.setdefault("continuity", {}).__setitem__("locks", ["prop:watch"]))

    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "stale"
    assert expected_reason in a["stale_reasons"]


@needs_ffmpeg
def test_08_human_review_is_independent_of_assurance(tmp_project, add_shot):
    # (a) a human APPROVING a rejected shot does not make assurance accept it.
    row = _rejected_shot(tmp_project, add_shot, "S001")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("review", "approved"))
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "rejected"      # assurance is unmoved
    assert a["human_review_state"] == "approved"   # read, never written

    # (b) an ACCEPTED assurance on a needs_review shot stays accepted.
    _accepted_shot(tmp_project, add_shot, "S002")
    a2 = compute_assurance(tmp_project, "S002")
    assert a2["assurance_state"] == "accepted"
    assert a2["human_review_state"] == "needs_review"


@needs_ffmpeg
def test_09_proposal_cites_exact_expectation_ids(tmp_project, add_shot):
    row = _rejected_shot(tmp_project, add_shot)
    a = compute_assurance(tmp_project, "S001")
    prop = repair_proposal(tmp_project, "S001", a)
    assert prop is not None
    fid = row["expectations"][0]["id"]
    assert fid in prop["failed_expectation_ids"]
    assert any(fid in c["reason"] for c in prop["action_candidates"])
    assert prop["recommended_skill"] == "repair-loop"
    assert prop["do_not_execute_automatically"] is True
    assert all(c["requires_human_or_agent_patch"] for c in prop["action_candidates"])

    # an accepted shot yields no proposal.
    _accepted_shot(tmp_project, add_shot, "S002")
    assert repair_proposal(tmp_project, "S002") is None


@needs_ffmpeg
def test_10_proposal_has_no_side_effects(tmp_project, add_shot):
    _rejected_shot(tmp_project, add_shot)
    before = _tree_hashes(tmp_project.root)
    prop = repair_proposal(tmp_project, "S001")
    assert prop is not None
    after = _tree_hashes(tmp_project.root)
    assert before == after  # no source writes, no new files, no spend


@needs_ffmpeg
def test_11_assurance_survives_manju_deletion(tmp_project, add_shot):
    _rejected_shot(tmp_project, add_shot)
    a1 = compute_assurance(tmp_project, "S001")
    manju = tmp_project.root / ".manju"
    if manju.exists():
        shutil.rmtree(manju)
    a2 = compute_assurance(tmp_project, "S001")
    assert a1 == a2  # derived purely from files; .manju/state.sqlite is irrelevant


@needs_ffmpeg
def test_12_output_is_deterministic(tmp_project, add_shot):
    _accepted_shot(tmp_project, add_shot)
    assert compute_assurance(tmp_project, "S001") == compute_assurance(tmp_project, "S001")

# ------------------------------------------------- the deterministic QC gate


@needs_ffmpeg
def test_13_policy_blocking_qc_error_blocks_acceptance_per_shot(
        tmp_project, add_shot):
    """All promises PASS but the machine tier reports an error for THIS shot →
    rejected (the "no policy-blocking error" acceptance condition). An error
    attributed to a DIFFERENT shot must not leak — the gate is per-shot."""
    from manju.qc.assurance import assurance_for_all
    from manju.qc.checks import QCItem, QCReport

    _accepted_shot(tmp_project, add_shot)  # S001, all expectations PASS

    other = QCReport(items=[QCItem("error", "content", "S999", "别的镜头炸了")])
    a = compute_assurance(tmp_project, "S001", qc_report=other)
    assert a["assurance_state"] == "accepted", a["reasons"]

    mine = QCReport(items=[QCItem("error", "technical", "S001", "时长偏差超限")])
    a = compute_assurance(tmp_project, "S001", qc_report=mine)
    assert a["assurance_state"] == "rejected"
    assert any("QC" in r or "policy" in r for r in a["reasons"]), a["reasons"]

    # the bulk surface threads one shared report through every shot's gate
    states = {x["subject"]["id"]: x["assurance_state"]
              for x in assurance_for_all(tmp_project, qc_report=mine)}
    assert states["S001"] == "rejected"
