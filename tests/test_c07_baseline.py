"""AI_IDE_07C — approved release baseline + composed release assessment.

Red-first (contract §4.3 pins + §9 test contract, 30 items). Everything is
ffmpeg-free: finals are fabricated (fake mp4 bytes + hand-written
``final_vN.key.json`` / ``.timeline.json`` sidecars) and a manual-mode
``timeline.json`` makes the content-key recompute deterministic, exactly like
tests/test_export_center.py and tests/test_compare.py.

Coverage map (§9):
  baseline evidence .... 1-10  → the approve/current_baseline core
  comparison ........... 11-16 → compare --against-baseline (same engine)
  assessment ........... 17-29 → release_assessment blockers/readiness/actions
  full suite ........... 30    → run separately
"""

from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from manju.build import attempts as A
from manju.build import baseline as BL
from manju.build.compare import compare_finals
from manju.build.exportstatus import deliverables, deliverables_data
from manju.cli import app
from manju.core.hashing import hash_file
from manju.core.models import (
    AudioClip,
    CaptionLine,
    TakeSidecar,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml
from manju.providers import submission as S
from manju.runtime.state import RuntimeState

runner = CliRunner()


# ------------------------------------------------------------- fabricators


def _manual_timeline(project, video=None) -> Timeline:
    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(video=video or [
            VideoClip(shot="S001", take="take_01",
                      source="media/gen/S001/take_01.mp4", start_ms=0, duration_ms=2000)]),
    )
    project.save_timeline(tl)
    return tl


def _final_key(project, tl: Timeline) -> str:
    from manju.media.render import final_content_key

    ass = project.captions_dir / "captions.ass"
    return final_content_key(project, tl, ass_file=ass if ass.exists() else None, target="final")


def _fab_final(project, key, *, version=1, data=b"final-bytes", run_id=None,
               output_sha256=None, snapshot=None):
    project.final_dir.mkdir(parents=True, exist_ok=True)
    p = project.final_dir / f"final_v{version}.mp4"
    p.write_bytes(data)
    sc = {"final_key": key, "target": "final", "created_at": "2026-07-06T10:00:00+00:00"}
    if run_id:
        sc["run_id"] = run_id
    if output_sha256 == "auto":
        sc["output_sha256"] = hash_file(p)
    elif output_sha256:
        sc["output_sha256"] = output_sha256
    (project.final_dir / f"final_v{version}.key.json").write_text(
        json.dumps(sc), encoding="utf-8")
    if snapshot is not None:
        (project.final_dir / f"final_v{version}.timeline.json").write_text(
            json.dumps(snapshot.model_dump(), ensure_ascii=False), encoding="utf-8")
    return p


def _clean_current_final(project):
    """A deterministic up-to-date current final (manual timeline + matching key)."""
    tl = _manual_timeline(project)
    return _fab_final(project, _final_key(project, tl), output_sha256="auto"), tl


def _snap(video, *, captions=None, music=None):
    return Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="compiled"),
        fps=24, width=1080, height=1920,
        duration_ms=sum(c.duration_ms for c in video),
        tracks=TimelineTracks(video=video, captions=captions or [], music=music or []),
    )


def _vclip(shot, take, src, start, dur):
    return VideoClip(shot=shot, take=take, source=src, start_ms=start, duration_ms=dur)


def _register(project, shot, provider):
    tmp = project.root / f"_src_{shot}_{provider}.mp4"
    tmp.write_bytes(b"take-" + f"{shot}{provider}".encode())
    info = project.register_take(shot, tmp, TakeSidecar(provider=provider, spec_hash="manual"))
    return info.name, project.relpath(info.media_path)


# =====================================================================
# §9.1  baseline evidence (1-10)
# =====================================================================


def test_1_approval_binds_exact_path_hash_bytes_and_final_key(tmp_project):
    p, tl = _clean_current_final(tmp_project)
    res = BL.approve_baseline(tmp_project, None, reason="当前个人发布基线")
    ev = res["event"]
    assert ev["kind"] == "release_baseline_approved" and ev["target"] == "final"
    art = ev["artifact"]
    assert art["path"] == tmp_project.relpath(p)
    assert art["sha256"] == hash_file(p)
    assert art["bytes"] == p.stat().st_size
    assert art["final_key"] == _final_key(tmp_project, tl)
    assert ev["actor"]["kind"] == "human" and ev["reason"] == "当前个人发布基线"
    cur = BL.current_baseline(tmp_project)
    assert cur["status"] == "VALID" and cur["artifact_sha256"] == art["sha256"]


def test_2_modifying_same_name_final_bytes_makes_baseline_damaged(tmp_project):
    p, _ = _clean_current_final(tmp_project)
    BL.approve_baseline(tmp_project, None, reason="r")
    assert BL.current_baseline(tmp_project)["status"] == "VALID"
    p.write_bytes(b"different-bytes-entirely")  # same name, new bytes
    cur = BL.current_baseline(tmp_project)
    assert cur["status"] == "DAMAGED"


def test_3_deleting_baseline_artifact_does_not_repoint_to_a_new_final(tmp_project):
    p, tl = _clean_current_final(tmp_project)
    BL.approve_baseline(tmp_project, None, reason="r")
    p.unlink()                                   # baseline artifact gone
    _fab_final(tmp_project, _final_key(tmp_project, tl), version=2)  # a newer final appears
    cur = BL.current_baseline(tmp_project)
    assert cur["status"] == "DAMAGED"            # never silently inherits v2
    assert cur["artifact"]["path"].endswith("final_v1.mp4")


def test_4_multiple_approvals_form_append_only_supersession_chain(tmp_project):
    _clean_current_final(tmp_project)
    first = BL.approve_baseline(tmp_project, None, reason="one")["event"]
    second = BL.approve_baseline(tmp_project, None, reason="two")["event"]
    assert second["supersedes_event_id"] == first["event_id"]
    assert first["event_id"] != second["event_id"]
    # append-only: both events survive in the log
    text = (tmp_project.reports_dir / "verifications.jsonl").read_text(encoding="utf-8")
    assert first["event_id"] in text and second["event_id"] in text
    assert BL.current_baseline(tmp_project)["event_id"] == second["event_id"]


def test_5_malformed_event_does_not_swallow_valid_events(tmp_project):
    _clean_current_final(tmp_project)
    BL.approve_baseline(tmp_project, None, reason="r")
    # a torn/garbage line appended after a valid baseline event
    with open(tmp_project.reports_dir / "verifications.jsonl", "a", encoding="utf-8") as f:
        f.write("{ this is not valid json \n")
    cur = BL.current_baseline(tmp_project)
    assert cur["status"] == "VALID"              # the valid event still resolves


def test_6_fsync_failure_is_not_reported_as_approval_success(tmp_project, monkeypatch):
    _clean_current_final(tmp_project)

    def boom(fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(BL.BaselineError):
        BL.approve_baseline(tmp_project, None, reason="r")
    # the durable write rolled back — no phantom baseline
    assert BL.current_baseline(tmp_project)["status"] == "NO_BASELINE"


def test_7_unattended_cannot_approve_and_no_mcp_approval_tool(tmp_project):
    _clean_current_final(tmp_project)
    with pytest.raises(BL.BaselineError):
        BL.approve_baseline(tmp_project, None, reason="r", unattended=True)
    assert BL.current_baseline(tmp_project)["status"] == "NO_BASELINE"
    # the MCP surface exposes NO approval tool under either profile
    from manju.mcp import policy as P
    from manju.mcp.tools import TOOL_DEFS

    for profile in (P.COLLABORATIVE, P.UNATTENDED):
        names = P.resolve_agent_surface(TOOL_DEFS, profile).listed_names()
        assert not any("baseline" in n or "approve" in n for n in names)


def test_8_project_source_cannot_self_declare_approval(tmp_project):
    _clean_current_final(tmp_project)
    # plant an "approved" flag inside project source truth — it must be inert
    write_yaml(tmp_project.root / "bible" / "release.yaml",
               {"release_baseline_approved": True, "baseline": "final_v1"})
    assert BL.current_baseline(tmp_project)["status"] == "NO_BASELINE"


def test_9_old_project_without_baseline_is_no_baseline(tmp_project):
    _clean_current_final(tmp_project)
    assert BL.current_baseline(tmp_project)["status"] == "NO_BASELINE"
    a = BL.release_assessment(tmp_project)
    assert a["baseline"]["status"] == "NO_BASELINE"


def test_10_risk_acceptance_records_blockers_and_human_reason(tmp_project):
    # a STALE final (sidecar key will not match the manual-timeline recompute)
    _manual_timeline(tmp_project)
    _fab_final(tmp_project, "sha256:stale-key-does-not-match", output_sha256="auto")
    # default: refuse
    with pytest.raises(BL.BaselineError):
        BL.approve_baseline(tmp_project, None, reason="r")
    # explicit human risk acceptance records the blockers + the reason
    ev = BL.approve_baseline(tmp_project, None, reason="known stale, shipping anyway",
                             accept_known_risk=True)["event"]
    assert ev.get("risk_accepted") is True
    codes = {b["code"] for b in ev.get("known_blockers") or []}
    assert "CURRENT_FINAL_STALE" in codes
    assert ev.get("risk_reason") == "known stale, shipping anyway"


# =====================================================================
# §9.2  comparison (11-16)
# =====================================================================


def _two_finals_with_snapshots(project):
    """final_v1 (baseline) and final_v2 (candidate) with a take change + snapshots."""
    t1, s1 = _register(project, "S001", "kenburns")
    t2a, s2a = _register(project, "S002", "kenburns")
    t2b, s2b = _register(project, "S002", "comfyui")
    base = [_vclip("S001", t1, s1, 0, 1200)]
    tl_a = _snap(base + [_vclip("S002", t2a, s2a, 1200, 1200)])
    tl_b = _snap(base + [_vclip("S002", t2b, s2b, 1200, 1200)])
    _fab_final(project, "sha256:aaa", version=1, data=b"v1", output_sha256="auto", snapshot=tl_a)
    _fab_final(project, "sha256:bbb", version=2, data=b"v2", output_sha256="auto", snapshot=tl_b)
    return t2a, t2b


def test_11_against_baseline_uses_same_diff_as_explicit_compare(tmp_project):
    _two_finals_with_snapshots(tmp_project)
    BL.approve_baseline(tmp_project, "final_v1", reason="baseline", accept_known_risk=True)
    diff = BL.compare_against_baseline(tmp_project)      # candidate = newest (v2)
    direct = compare_finals(tmp_project, "final_v1", "final_v2")
    assert diff["changes"] == direct["changes"]
    assert diff["comparison_mode"] == "APPROVED_BASELINE"
    assert diff["baseline"]["status"] == "VALID"
    assert diff["review_status"] == "CHANGED_REQUIRES_REVIEW"


def test_12_baseline_current_order_is_not_reversed(tmp_project):
    _two_finals_with_snapshots(tmp_project)
    BL.approve_baseline(tmp_project, "final_v1", reason="b", accept_known_risk=True)
    diff = BL.compare_against_baseline(tmp_project)
    # baseline is always the 'a' side, candidate the 'b' side
    assert diff["a"]["name"] == "final_v1" and diff["b"]["name"] == "final_v2"
    assert diff["candidate"]["name"] == "final_v2"


def test_13_take_change_is_visible_against_baseline(tmp_project):
    _t2a, t2b = _two_finals_with_snapshots(tmp_project)
    BL.approve_baseline(tmp_project, "final_v1", reason="b", accept_known_risk=True)
    diff = BL.compare_against_baseline(tmp_project)
    s2 = next(c for c in diff["changes"] if c["shot"] == "S002")
    assert s2["change"] == "take_changed" and s2["b"]["take"] == t2b


def test_14_legacy_missing_snapshot_is_unknown_not_unchanged(tmp_project):
    # baseline final has NO timeline snapshot (pre-S) → honest degradation
    _register(tmp_project, "S001", "kenburns")
    tl_b = _snap([_vclip("S001", "take_01", "media/gen/S001/take_01.mp4", 0, 1200)])
    _fab_final(tmp_project, "sha256:aaa", version=1, data=b"v1", output_sha256="auto")  # no snapshot
    _fab_final(tmp_project, "sha256:bbb", version=2, data=b"v2", output_sha256="auto", snapshot=tl_b)
    BL.approve_baseline(tmp_project, "final_v1", reason="b", accept_known_risk=True)
    diff = BL.compare_against_baseline(tmp_project)
    assert diff["degraded"] is True                       # not faked as identical
    assert diff["review_status"] == "CHANGED_REQUIRES_REVIEW"


def test_15_compare_against_baseline_writes_nothing(tmp_project):
    _two_finals_with_snapshots(tmp_project)
    BL.approve_baseline(tmp_project, "final_v1", reason="b", accept_known_risk=True)
    before = {str(p) for p in tmp_project.root.rglob("*")}
    BL.compare_against_baseline(tmp_project)
    after = {str(p) for p in tmp_project.root.rglob("*")}
    assert before == after                                # no writes, no generation


def test_16_baseline_event_timestamp_does_not_affect_media_diff(tmp_project):
    _two_finals_with_snapshots(tmp_project)
    BL.approve_baseline(tmp_project, "final_v1", reason="b", accept_known_risk=True)
    d1 = BL.compare_against_baseline(tmp_project)["changes"]
    # a superseding approval of the SAME bytes at a later time must not move the diff
    BL.approve_baseline(tmp_project, "final_v1", reason="again", accept_known_risk=True)
    d2 = BL.compare_against_baseline(tmp_project)["changes"]
    assert d1 == d2


# =====================================================================
# §9.3  assessment (17-29)
# =====================================================================


def _codes(assessment):
    return {b["code"] for b in assessment["blockers"]}


def test_17_current_final_missing_stale_hash_mismatch_block(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # (a) missing
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False and "CURRENT_FINAL_MISSING" in _codes(a)
    # (b) stale — sidecar key differs from the manual-timeline recompute
    _manual_timeline(tmp_project)
    _fab_final(tmp_project, "sha256:wrong", version=1, output_sha256="auto")
    assert "CURRENT_FINAL_STALE" in _codes(BL.release_assessment(tmp_project))
    # (c) hash mismatch — sidecar output_sha256 no longer matches the bytes
    tl = _manual_timeline(tmp_project)
    p = _fab_final(tmp_project, _final_key(tmp_project, tl), version=2, output_sha256="auto")
    p.write_bytes(b"tampered-after-render")
    assert "CURRENT_FINAL_HASH_MISMATCH" in _codes(BL.release_assessment(tmp_project))


def test_18_incomplete_run_blocks(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl), run_id="run_x", output_sha256="auto")
    A.append_run_started(tmp_project, "run_x", target="final", gen="missing")
    A.append_attempt_started(tmp_project, "run_x", "att_lost", stage="render")
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False and "RUN_INCOMPLETE" in _codes(a)


def test_19_unresolved_paid_submission_blocks(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_current_final(tmp_project)
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_u",
                       request_digest="sha256:d", state=S.PREPARED)
        st.set_submission_state("sub_u", S.OUTCOME_UNKNOWN)
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False and "SUBMISSION_OUTCOME_UNKNOWN" in _codes(a)


def test_20_evidence_corruption_blocks_scoped_to_relevant_shot(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_current_final(tmp_project)
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_c",
                       request_digest="sha256:d", state=S.DISPATCHING)
        st.set_submission_state("sub_c", S.DISPATCHING)
    # a chain-breaking submission event (bad prev digest)
    A.append_submission_event(tmp_project, submission_id="sub_c", request_digest="sha256:d",
                              from_state=None, to_state=S.DISPATCHING, provider_id="cloud_test",
                              shot="S001", prev_event_digest="sha256:bogus")
    a = BL.release_assessment(tmp_project)
    corrupt = [b for b in a["blockers"] if b["code"] == "ATTEMPT_EVIDENCE_CORRUPT"]
    assert corrupt and corrupt[0]["scope"] == "shot:S001"


def test_21_qc_unavailable_stale_rejected_block(tmp_project, monkeypatch):
    _clean_current_final(tmp_project)

    def fake_assurance(project, qc_report=None):
        return [
            {"subject": {"kind": "shot", "id": "S010"}, "assurance_state": "unknown",
             "qc": {"status": "unavailable", "reason": "run_qc raised"}, "reasons": []},
            {"subject": {"kind": "shot", "id": "S011"}, "assurance_state": "rejected",
             "qc": {"status": "blocked", "reason": None}, "reasons": ["failed"]},
            {"subject": {"kind": "shot", "id": "S012"}, "assurance_state": "stale",
             "qc": None, "reasons": ["binding moved"]},
        ]

    monkeypatch.setattr(BL, "assurance_for_all", fake_assurance)
    a = BL.release_assessment(tmp_project)
    codes = _codes(a)
    assert {"QC_UNAVAILABLE", "QC_REJECTED", "QC_STALE"} <= codes
    assert a["ready"] is False
    # QC_UNAVAILABLE is scoped to the shot that could not be evaluated
    qc_un = next(b for b in a["blockers"] if b["code"] == "QC_UNAVAILABLE")
    assert qc_un["scope"] == "shot:S010"


def test_22_required_export_stale_missing_problem_block(tmp_project):
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl), output_sha256="auto")
    # a deliverable that EXISTS but is broken (0-byte ASS → 有问题/PROBLEMATIC)
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.captions_dir / "captions.ass").write_bytes(b"")
    a = BL.release_assessment(tmp_project)
    assert "REQUIRED_EXPORT_PROBLEM" in _codes(a)
    assert a["ready"] is False


def test_23_first_release_without_baseline_can_be_technical_ready(tmp_project, add_shot):
    add_shot(tmp_project, "S001")                          # no explicit expectations
    _clean_current_final(tmp_project)
    a = BL.release_assessment(tmp_project)
    assert a["baseline"]["status"] == "NO_BASELINE"
    assert a["blockers"] == []
    assert a["ready"] is True                              # NO_BASELINE never blocks first release


def test_24_baseline_content_change_requires_human_review(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl), version=1, output_sha256="auto")
    BL.approve_baseline(tmp_project, "final_v1", reason="baseline")
    # a genuinely different newer final becomes the candidate
    _fab_final(tmp_project, _final_key(tmp_project, tl), version=2,
               data=b"different-final-bytes", output_sha256="auto")
    a = BL.release_assessment(tmp_project)
    assert a["baseline"]["status"] == "VALID"
    assert a["regression_review"]["status"] == "CHANGED_REQUIRES_REVIEW"
    assert "REGRESSION_REVIEW_REQUIRED" in _codes(a)
    assert a["ready"] is False


def test_25_next_action_toolpolicy_metadata_matches_agent_surface(tmp_project):
    _manual_timeline(tmp_project)
    _fab_final(tmp_project, "sha256:wrong", output_sha256="auto")   # stale → build action
    a = BL.release_assessment(tmp_project)
    act = next(x for x in a["next_actions"] if x["reason_code"] == "CURRENT_FINAL_STALE")
    assert act["tool"] == "build"
    # the safety metadata must be READ from mcp.policy's registry, not hand-written
    from manju.mcp import policy as P
    from manju.mcp.tools import TOOL_DEFS

    pol = {t["name"]: t["policy"] for t in TOOL_DEFS}["build"]
    assert act["may_spend"] == (pol["spend"] != P.NEVER)
    assert act["may_network"] == (pol["network"] != P.NEVER)
    assert act["safe_to_auto_run"] is False                # build spends → never auto
    assert act["fix_owner"] == "human"


def test_26_assessment_materializes_nothing(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_current_final(tmp_project)
    before = {str(p) for p in tmp_project.root.rglob("*")}
    BL.release_assessment(tmp_project)
    after = {str(p) for p in tmp_project.root.rglob("*")}
    assert before == after                                 # no file created/deleted


def test_27_assessment_json_is_stable_relative_and_secret_free(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_current_final(tmp_project)
    a1 = BL.release_assessment(tmp_project)
    a2 = BL.release_assessment(tmp_project)
    assert a1 == a2                                         # deterministic
    blob = json.dumps(a1, ensure_ascii=False)
    assert "Authorization" not in blob and "http://" not in blob and "https://" not in blob
    if a1["candidate"]["path"]:
        assert not a1["candidate"]["path"].startswith("/")  # project-relative


def test_28_collaborative_exports_default_has_no_regression(tmp_project, monkeypatch):
    _clean_current_final(tmp_project)
    data = deliverables_data(tmp_project)
    assert [d["kind"] for d in data["deliverables"]]        # nine rows still present
    assert "counts" in data
    # additive: the composed release assessment rides the SAME exports payload
    assert "release_assessment" in data
    # engine and CLI agree (the surfaces-never-disagree pin still holds)
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["exports", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == deliverables_data(tmp_project)


def test_29_mcp_cli_reuse_the_same_core_service(tmp_project, monkeypatch):
    _two_finals_with_snapshots(tmp_project)
    BL.approve_baseline(tmp_project, "final_v1", reason="b", accept_known_risk=True)
    monkeypatch.chdir(tmp_project.root)
    # compare --against-baseline --json is exactly the core service output
    res = runner.invoke(app, ["compare", "--against-baseline", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["changes"] == BL.compare_against_baseline(tmp_project)["changes"]
    # exports --json embeds exactly the core release_assessment
    res2 = runner.invoke(app, ["exports", "--json"])
    assert res2.exit_code == 0, res2.output
    got = json.loads(res2.output)["release_assessment"]
    assert got["blockers"] == BL.release_assessment(tmp_project)["blockers"]


# ------------------------------------------------------------- CLI surface


def test_cli_approve_baseline_and_show(tmp_project, monkeypatch):
    _clean_current_final(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    approve = runner.invoke(app, ["exports", "--approve-baseline", "--reason", "发布基线", "--json"])
    assert approve.exit_code == 0, approve.output
    show = runner.invoke(app, ["exports", "--baseline", "--json"])
    assert show.exit_code == 0, show.output
    assert json.loads(show.output)["status"] == "VALID"
