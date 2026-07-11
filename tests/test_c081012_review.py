"""AI_IDE_08_10_12C WP4 — bound take review: five dispositions, one-variable
rule, endpoint observations. Path A: additive OPTIONAL fields on the existing
manju.qc.verdict/v2 intake (schema unchanged, 0 new public schemas).

Red-first (recorded in the completion report):
  - a v2 verdict carrying a `decision` block was accepted but the block was
    silently DROPPED from the stored record (not persisted, not validated);
  - an invalid disposition / a non-KEEP decision without a primary repair
    variable / malformed observed_states were accepted silently.
Contract: additive fields are validated at intake (payload-invalid => whole
batch rejected, ZERO writes — the existing DR02 path) and persisted verbatim
on the stored record; legacy verdicts without them stay byte-compatible.
"""

from __future__ import annotations

import pytest

from manju.core.models import TakeSidecar
from manju.qc.agent_review import (
    VerdictError,
    qc_brief,
    read_v2_records,
    record_verdicts,
)

V2 = "manju.qc.verdict/v2"


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _take_with_bytes(project, shot_id, data: bytes, *, select=True):
    tmp = project.root / f"_src_{shot_id}.mp4"
    tmp.write_bytes(data)
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    if select:
        _select(project, shot_id, take.name)
    return take


def _row(project, shot_id):
    return next(r for r in qc_brief(project, [shot_id])["shots"] if r["shot"] == shot_id)


def _v2(row, observed="present", **over):
    v = {
        "schema": V2, "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [{"expectation_id": e["id"], "observed": observed}
                         for e in row["expectations"]],
        "findings": [],
        "reviewer": {"kind": "model_visual", "name": "t"},
    }
    v.update(over)
    return v


def _shot(project, add_shot, shot_id="S001", data=b"AAAA-media"):
    add_shot(project, shot_id, quality={"must_show": ["红伞出现"]})
    _take_with_bytes(project, shot_id, data)
    return _row(project, shot_id)


ENDPOINT_OBS = {
    "dimension": "POSE_ACTION_PHASE",
    "position": "END",
    "value": "left hand remains on door handle",
    "visibility": "VISIBLE",
    "evidence_refs": ["frame:end"],
    "confidence": 0.88,
}


# ------------------------------------------------- persisted additive fields


def test_decision_and_observed_states_are_persisted(tmp_project, add_shot):
    """RED pre-fix: the decision block and observed_states were silently
    dropped from the stored record. Contract: persisted verbatim (additive)."""
    row = _shot(tmp_project, add_shot)
    v = _v2(row, decision={
        "disposition": "REROLL",
        "primary_repair_variable": "seed",
        "diagnostic_isolation": True,
        "reason": "构图正确,噪声运气差",
    }, observed_states=[ENDPOINT_OBS])
    record_verdicts(tmp_project, v)
    [rec], malformed = read_v2_records(tmp_project)
    assert malformed == 0
    assert rec["decision"]["disposition"] == "REROLL"
    assert rec["decision"]["primary_repair_variable"] == "seed"
    assert rec["decision"]["diagnostic_isolation"] is True
    assert rec["observed_states"][0]["dimension"] == "POSE_ACTION_PHASE"
    assert rec["observed_states"][0]["position"] == "END"
    assert rec["observed_states"][0]["visibility"] == "VISIBLE"


@pytest.mark.parametrize("disposition", [
    "KEEP", "FIX_IN_POST", "EDIT_DONT_REGENERATE", "REROLL", "REWRITE_SOURCE"])
def test_all_five_dispositions_expressible(tmp_project, add_shot, disposition):
    row = _shot(tmp_project, add_shot)
    decision = {"disposition": disposition}
    if disposition != "KEEP":
        decision["primary_repair_variable"] = "seed"
    record_verdicts(tmp_project, _v2(row, decision=decision))
    records, _ = read_v2_records(tmp_project)
    assert records[-1]["decision"]["disposition"] == disposition


# ------------------------------------------------- intake validation (zero-write)


def test_unknown_disposition_rejected_zero_write(tmp_project, add_shot):
    """RED pre-fix: an invalid disposition was accepted silently."""
    row = _shot(tmp_project, add_shot)
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, _v2(row, decision={"disposition": "MAYBE_OK"}))
    assert "disposition" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_non_keep_requires_primary_repair_variable(tmp_project, add_shot):
    """Contract §9.3: every non-KEEP decision names exactly one primary
    variable. RED pre-fix: accepted without one."""
    row = _shot(tmp_project, add_shot)
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, _v2(row, decision={"disposition": "REROLL"}))
    assert "primary_repair_variable" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_unknown_repair_variable_rejected(tmp_project, add_shot):
    row = _shot(tmp_project, add_shot)
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, _v2(row, decision={
            "disposition": "REROLL", "primary_repair_variable": "vibes"}))
    assert "primary_repair_variable" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_multi_variable_marks_isolation_false(tmp_project, add_shot):
    """§9.3: a deliberate multi-variable try is allowed but must carry
    diagnostic_isolation=false; it is stored, never inferred away."""
    row = _shot(tmp_project, add_shot)
    record_verdicts(tmp_project, _v2(row, decision={
        "disposition": "REROLL", "primary_repair_variable": "seed",
        "diagnostic_isolation": False}))
    [rec], _ = read_v2_records(tmp_project)
    assert rec["decision"]["diagnostic_isolation"] is False


def test_bad_observed_state_rejected_zero_write(tmp_project, add_shot):
    """RED pre-fix: malformed observed_states were accepted silently."""
    row = _shot(tmp_project, add_shot)
    bad = dict(ENDPOINT_OBS, visibility="KINDA")
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, _v2(row, observed_states=[bad]))
    assert "visibility" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []

    bad2 = dict(ENDPOINT_OBS, position="MIDDLE-ISH")
    with pytest.raises(VerdictError):
        record_verdicts(tmp_project, _v2(row, observed_states=[bad2]))
    assert read_v2_records(tmp_project)[0] == []


def test_unsafe_observed_state_evidence_ref_rejected(tmp_project, add_shot):
    row = _shot(tmp_project, add_shot)
    bad = dict(ENDPOINT_OBS, evidence_refs=["../../etc/passwd"])
    with pytest.raises(VerdictError):
        record_verdicts(tmp_project, _v2(row, observed_states=[bad]))
    assert read_v2_records(tmp_project)[0] == []


# ------------------------------------------------- separation of powers


def test_keep_does_not_auto_select(tmp_project, add_shot):
    """§8.4: KEEP is a review conclusion, never a selection. selected_take is
    untouched by intake."""
    add_shot(tmp_project, "S001", quality={"must_show": ["红伞出现"]})
    take = _take_with_bytes(tmp_project, "S001", b"k-bytes", select=False)
    _select(tmp_project, "S001", take.name)  # explicit human select of take_01
    row = _row(tmp_project, "S001")
    before = tmp_project.load_shot("S001").status.selected_take
    record_verdicts(tmp_project, _v2(row, decision={"disposition": "KEEP"}))
    assert tmp_project.load_shot("S001").status.selected_take == before


def test_decision_never_writes_assurance_acceptance(tmp_project, add_shot):
    """Ruling 5: the reviewer cannot write assurance. A KEEP decision on a
    FAILED expectation still derives `rejected` — acceptance stays the 02 pure
    function over expectations/QC, decision fields are view-only."""
    from manju.qc.assurance import compute_assurance

    row = _shot(tmp_project, add_shot)
    record_verdicts(tmp_project, _v2(row, observed="absent",
                                     decision={"disposition": "KEEP"}))
    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] == "rejected"


def test_media_replacement_stales_decision_record(tmp_project, add_shot):
    """§9 review binding: same-name byte replacement after the brief -> the
    stored decision record is binding-stale (bound to bytes A, never restamped)."""
    add_shot(tmp_project, "S001", quality={"must_show": ["红伞出现"]})
    take = _take_with_bytes(tmp_project, "S001", b"AAAA-original")
    row = _row(tmp_project, "S001")
    take.media_path.write_bytes(b"BBBB-swapped-in-longer!!")
    record_verdicts(tmp_project, _v2(row, decision={
        "disposition": "KEEP"}))
    [rec], _ = read_v2_records(tmp_project)
    assert rec["binding"] == "stale"
    assert rec["decision"]["disposition"] == "KEEP"  # the work is kept as history


def test_legacy_v2_verdict_without_decision_still_intakes(tmp_project, add_shot):
    """Compatibility: a pre-08_10_12C v2 verdict (no decision/observed_states)
    intakes exactly as before — additive means absent is legal."""
    row = _shot(tmp_project, add_shot)
    result = record_verdicts(tmp_project, _v2(row))
    assert result["written"] == 1
    [rec], _ = read_v2_records(tmp_project)
    assert "decision" not in rec
    assert "observed_states" not in rec
