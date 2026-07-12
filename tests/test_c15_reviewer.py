"""AI_IDE_15 — cloud visual reviewer, perceptual continuity, drift & repair
routing. Red-first, corpus-driven (contract §5-§11, addendum rulings 1-8, 10).

Everything lands against the 20A fake reviewer + twin (real cloud VLM is
unreachable and is gated behind AI_IDE_14 qualification). These tests prove:

* §6 dimension observations ride verdict v2 additively (Path A), validated in
  the SAME zero-write intake path;
* the reviewer NEVER writes accepted — assurance's pure function stays the
  acceptance authority (pinned from the reviewer direction);
* a review dispatched to a vision provider below DRY_RUN_VALID is refused with
  REVIEWER_NOT_QUALIFIED (real-VLM stand-in, addendum ruling 10);
* multi-reviewer disagreement on a blocker dimension ⇒ UNKNOWN_REVIEWER_
  DISAGREEMENT, never majority-voted away; human adjudication appends, keeps
  originals;
* the drift trend + 7-route proposal derive from current-bound evidence only;
  REGENERATE_REFERENCE / RESHOOT are route-level additions, not dispositions;
* the deterministic frame plan is first/25/50/75/last, hash-bound, repeatable;
  scene-change slots are SKIPPED_WITH_EVIDENCE (no scene detector in core);
* a changed reviewer profile digest makes an old verdict historical (cache key
  never reuses across models).
"""

from __future__ import annotations

import json

import pytest

from manju.qc.agent_review import (
    DIMENSION_OBSERVED,
    DIMENSION_SEVERITY,
    DISPOSITIONS,
    REVIEWER_DIMENSIONS,
    VERDICT_SCHEMA,
    VerdictError,
    qc_brief,
    read_v2_records,
    record_verdicts,
)
from manju.providers.qualification import (
    reviewer_admission,
    reviewer_admission_from_state,
)
from manju.qc.assurance import compute_assurance
from manju.qc.checks import QCReport
from manju.core.models import TakeSidecar

from tests.fixtures.golden import GOLDEN_DIR
from tests.fixtures.golden.fake_reviewer import (
    FakeVisionReviewer,
    install_manifests,
    load_manifest,
)

MANIFEST = load_manifest()
CASES = {c["id"]: c for c in MANIFEST["cases"]}


# --------------------------------------------------------------- scaffolding


def _register_image(project, shot_id, case_id):
    src = GOLDEN_DIR / CASES[case_id]["path"]
    take = project.register_take(shot_id, src, TakeSidecar(provider="test", spec_hash="h"))
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    return take


def _brief_row(project, shot_id):
    rows = {r["shot"]: r for r in qc_brief(project)["shots"]}
    return rows[shot_id]


def _v2(row, *, reviewer, dimension_observations=None, observations=None,
        findings=None, decision=None):
    """A v2 verdict echoing the brief row's binding, in the §6 additive shape."""
    v = {
        "schema": VERDICT_SCHEMA,
        "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": observations or [],
        "findings": findings or [],
        "reviewer": reviewer,
    }
    if dimension_observations is not None:
        v["dimension_observations"] = dimension_observations
    if decision is not None:
        v["decision"] = decision
    return v


def _dobs(dimension, observed, *, severity=None, subject_ref="linxia",
          variable=None, confidence=None, evidence=None):
    o = {"dimension": dimension, "observed": observed, "subject_ref": subject_ref}
    if severity is not None:
        o["severity"] = severity
    if variable is not None:
        o["suggested_repair_variable"] = variable
    if confidence is not None:
        o["confidence"] = confidence
    o["evidence_refs"] = evidence or ["frame:0"]
    o["explanation"] = f"{dimension} {observed}"
    return o


def _mk_shot(project, add_shot, shot_id, case_id, **quality):
    add_shot(project, shot_id, quality=quality or {"must_show": ["林夏(charA)出现"]})
    _register_image(project, shot_id, case_id)
    return _brief_row(project, shot_id)


# ================================================ §6 dimension observations


def test_section6_dimension_observation_enums_are_the_contract_shape():
    # the 11 reviewer dimensions (§3) + the §6 observed / severity vocabularies,
    # DISTINCT from the per-expectation present/absent enum.
    assert "character_identity" in REVIEWER_DIMENSIONS
    assert len(REVIEWER_DIMENSIONS) == 11
    assert DIMENSION_OBSERVED == ("match", "mismatch", "uncertain", "not_visible")
    assert DIMENSION_SEVERITY == ("info", "warning", "blocker")


def test_dimension_observations_ride_verdict_v2_additively(tmp_project, add_shot):
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.mismatch.charB")
    payload = _v2(row, reviewer={"kind": "model_visual", "name": "r1"},
                  dimension_observations=[
                      _dobs("character_identity", "mismatch", severity="blocker",
                            variable="reference_asset"),
                      _dobs("scene", "match", severity="info"),
                  ])
    result = record_verdicts(tmp_project, payload)
    assert result["bindings"] == {"bound": 1}
    recs, malformed = read_v2_records(tmp_project)
    assert malformed == 0
    dobs = recs[0]["dimension_observations"]
    assert [d["dimension"] for d in dobs] == ["character_identity", "scene"]
    assert dobs[0]["observed"] == "mismatch" and dobs[0]["severity"] == "blocker"
    assert dobs[0]["suggested_repair_variable"] == "reference_asset"


def test_not_visible_observation_is_first_class(tmp_project, add_shot):
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.visibility.occluded.charA")
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "r1"},
        dimension_observations=[_dobs("character_identity", "not_visible")]))
    recs, _ = read_v2_records(tmp_project)
    assert recs[0]["dimension_observations"][0]["observed"] == "not_visible"


def test_bad_dimension_enum_rejects_whole_batch_zero_writes(tmp_project, add_shot):
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    before, _ = read_v2_records(tmp_project)
    bad = _v2(row, reviewer={"kind": "model_visual", "name": "r1"},
              dimension_observations=[_dobs("not_a_dimension", "match")])
    with pytest.raises(VerdictError):
        record_verdicts(tmp_project, bad)
    after, _ = read_v2_records(tmp_project)
    assert len(after) == len(before)  # zero writes on payload-invalid


def test_bad_observed_and_severity_enums_reject(tmp_project, add_shot):
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    for dobs in (
        [_dobs("character_identity", "bogus")],                       # bad observed
        [_dobs("character_identity", "match", severity="critical")],  # bad severity
        [{"dimension": "character_identity", "observed": "match",
          "suggested_repair_variable": "not_a_variable"}],           # bad variable
        [{"dimension": "character_identity", "observed": "match",
          "confidence": 1.5}],                                        # out-of-range
    ):
        with pytest.raises(VerdictError):
            record_verdicts(tmp_project, _v2(
                row, reviewer={"kind": "model_visual", "name": "r1"},
                dimension_observations=dobs))


def test_unsafe_ref_and_secret_in_dimension_observation_reject(tmp_project, add_shot):
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    with pytest.raises(VerdictError):  # absolute path escape (§12 路径剔除)
        record_verdicts(tmp_project, _v2(
            row, reviewer={"kind": "model_visual", "name": "r1"},
            dimension_observations=[_dobs("scene", "match", evidence=["/etc/passwd"])]))
    with pytest.raises(VerdictError):  # traversal
        record_verdicts(tmp_project, _v2(
            row, reviewer={"kind": "model_visual", "name": "r1"},
            dimension_observations=[_dobs("scene", "match", evidence=["../../x"])]))


# ============================================ reviewer never writes accepted


def test_dimension_observation_blocker_never_forces_acceptance(tmp_project, add_shot):
    # A reviewer reporting dimension observations (even all 'match') does NOT make
    # the shot accepted — acceptance stays qc/assurance.py's pure function over
    # the per-expectation observations. Here the reviewer files ONLY §6 dimension
    # observations (no per-expectation observation) -> the must_show is UNKNOWN.
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "r1"},
        dimension_observations=[_dobs("character_identity", "match", severity="info")]))
    a = compute_assurance(tmp_project, "S001", qc_report=QCReport(items=[]))
    assert a["assurance_state"] == "unknown"  # NOT accepted — no per-exp PASS
    # acceptance stays the pure assurance derivation (reviewer never writes it).
    assert a["schema"] == "manju.qc.assurance/v1"


def test_confidence_never_participates_in_acceptance(tmp_project, add_shot):
    # a HIGH-confidence 'match' dimension observation still cannot accept.
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "r1"},
        dimension_observations=[_dobs("character_identity", "match", confidence=1.0)]))
    a = compute_assurance(tmp_project, "S001", qc_report=QCReport(items=[]))
    assert a["assurance_state"] != "accepted"


# ============================================ REVIEWER_NOT_QUALIFIED gate


def test_admission_predicate_enforces_closeout_floors():
    """14_21 closeout Q10 update (was: admits DRY_RUN_VALID and up): the
    unattended/paid floor is PRODUCTION_READY, the explicitly human-interactive
    floor is CANARY_ARTIFACT_PASSED — DRY_RUN_VALID launches nothing real."""
    from manju.providers import qualification as Q
    dry = {"level": Q.DRY_RUN_VALID, "state": Q.DRY_RUN_VALID, "stale": False,
           "blocked_reason": None}
    r = reviewer_admission_from_state(dry)
    assert r["admitted"] is False and r["refusal"] == "REVIEWER_NOT_QUALIFIED"
    assert reviewer_admission_from_state(dry, interactive=True)["admitted"] is False
    prod = {"level": Q.PRODUCTION_READY, "state": Q.PRODUCTION_READY,
            "stale": False, "blocked_reason": None}
    admit = reviewer_admission_from_state(prod)
    assert admit["admitted"] is True and admit["refusal"] is None
    canary = {"level": Q.CANARY_ARTIFACT_PASSED, "state": Q.CANARY_ARTIFACT_PASSED,
              "stale": False, "blocked_reason": None}
    assert reviewer_admission_from_state(canary)["admitted"] is False  # unattended
    assert reviewer_admission_from_state(canary, interactive=True)["admitted"] is True
    for lvl in (Q.UNTESTED, Q.CONFIG_VALID):
        r = reviewer_admission_from_state(
            {"level": lvl, "state": lvl, "stale": False, "blocked_reason": None})
        assert r["admitted"] is False and r["refusal"] == "REVIEWER_NOT_QUALIFIED"
    blocked = reviewer_admission_from_state(
        {"level": Q.UNTESTED, "state": Q.BLOCKED, "stale": False,
         "blocked_reason": "provider_absent"})
    assert blocked["admitted"] is False and blocked["refusal"] == "REVIEWER_NOT_QUALIFIED"


def test_stale_qualification_is_refused():
    from manju.providers import qualification as Q
    r = reviewer_admission_from_state(
        {"level": Q.CANARY_ARTIFACT_PASSED, "state": Q.STALE, "stale": True,
         "blocked_reason": None})
    assert r["admitted"] is False and r["refusal"] == "REVIEWER_NOT_QUALIFIED"


def test_fabricated_unqualified_vision_manifest_is_refused(tmp_path, monkeypatch):
    # the real-reviewer stand-in (addendum ruling 10): a vision provider that is
    # not qualified -> a review dispatch is refused with REVIEWER_NOT_QUALIFIED.
    import manju.providers.registry as registry_mod

    provdir = tmp_path / "_providers"
    (provdir / "cloud_vlm").mkdir(parents=True)
    (provdir / "cloud_vlm" / "provider.yaml").write_text(
        "id: cloud_vlm\ntype: vision\nadapter: generic_cloud\n"
        "capabilities: [vision]\n", encoding="utf-8")
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(provdir))
    registry_mod._manifest_cache = None
    try:
        decision = reviewer_admission("cloud_vlm", capability="vision")
        assert decision["admitted"] is False
        assert decision["refusal"] == "REVIEWER_NOT_QUALIFIED"
        # 14_21 closeout Q10: the unattended/paid default floor is now
        # PRODUCTION_READY (was DRY_RUN_VALID pre-closeout).
        assert decision["min_required"] == "PRODUCTION_READY"
    finally:
        registry_mod._manifest_cache = None


def test_admission_never_reaches_a_transport(tmp_path, monkeypatch):
    # §12 断网/timeout 不触发生成 Provider: the gate is a pure qualification check;
    # it must never open a network transport (core imports no vendor SDK).
    import manju.providers.registry as registry_mod

    def explode(*a, **k):
        raise AssertionError("reviewer admission must NOT open a transport")

    monkeypatch.setattr("manju.providers.generic_cloud.default_transport", explode,
                        raising=False)
    provdir = tmp_path / "_p"
    (provdir / "vlm").mkdir(parents=True)
    (provdir / "vlm" / "provider.yaml").write_text(
        "id: vlm\ntype: vision\nadapter: generic_cloud\ncapabilities: [vision]\n",
        encoding="utf-8")
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(provdir))
    registry_mod._manifest_cache = None
    try:
        assert reviewer_admission("vlm", capability="vision")["admitted"] is False
    finally:
        registry_mod._manifest_cache = None


def test_fake_reviewer_double_bypasses_the_gate(tmp_project, add_shot):
    # the offline double drives record_verdicts DIRECTLY (no real dispatch) — the
    # calibration path is never gated. Proven by 20A's flow still working here.
    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    reviewer = FakeVisionReviewer("primary", MANIFEST)
    result = record_verdicts(tmp_project, reviewer.build_verdict(row))
    assert result["bindings"] == {"bound": 1}


# ============================================ multi-reviewer disagreement (§7)


def test_multi_reviewer_blocker_disagreement_is_unknown(tmp_project, add_shot):
    from manju.qc.production import reviewer_agreement

    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.mismatch.charB")
    # primary catches the identity blocker; the twin misses it (calls it match).
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "qc_primary",
                       "profile_digest": "pa"},
        dimension_observations=[_dobs("character_identity", "mismatch",
                                      severity="blocker", variable="reference_asset")]))
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "qc_twin",
                       "profile_digest": "pb"},
        dimension_observations=[_dobs("character_identity", "match", severity="info")]))
    view = reviewer_agreement(tmp_project, "S001")
    assert view["state"] == "UNKNOWN_REVIEWER_DISAGREEMENT"
    assert view["blocker_disagreement"] is True
    assert {r["name"] for r in view["reviewers"]} == {"qc_primary", "qc_twin"}


def test_reviewer_agreement_when_both_agree(tmp_project, add_shot):
    from manju.qc.production import reviewer_agreement

    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    for name in ("qc_primary", "qc_twin"):
        record_verdicts(tmp_project, _v2(
            row, reviewer={"kind": "model_visual", "name": name, "profile_digest": name},
            dimension_observations=[_dobs("character_identity", "match", severity="info")]))
    view = reviewer_agreement(tmp_project, "S001")
    assert view["state"] == "AGREEMENT"
    assert view["blocker_disagreement"] is False


def test_human_adjudication_appends_and_keeps_originals(tmp_project, add_shot):
    from manju.qc.production import reviewer_agreement

    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.mismatch.charB")
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "qc_primary", "profile_digest": "pa"},
        dimension_observations=[_dobs("character_identity", "mismatch", severity="blocker")]))
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "qc_twin", "profile_digest": "pb"},
        dimension_observations=[_dobs("character_identity", "match")]))
    # a human adjudicates — an ADDITIONAL appended verdict; originals untouched.
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "human", "name": "operator"},
        dimension_observations=[_dobs("character_identity", "mismatch", severity="blocker")]),
        actor="human")
    recs, _ = read_v2_records(tmp_project)
    assert len(recs) == 3  # both model verdicts survive alongside the adjudication
    view = reviewer_agreement(tmp_project, "S001")
    assert len(view["adjudications"]) == 1
    assert view["adjudications"][0]["actor"] == "human"


# ============================================ drift trend + repair routes (§8)


def _seed_drift(tmp_project, add_shot):
    """Three shots in order, each with a current-bound §6 dimension observation."""
    r1 = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.mismatch.charB")
    record_verdicts(tmp_project, _v2(
        r1, reviewer={"kind": "model_visual", "name": "r", "profile_digest": "p"},
        dimension_observations=[_dobs("character_identity", "mismatch",
                                      severity="blocker", variable="reference_asset")]))
    r2 = _mk_shot(tmp_project, add_shot, "S002", "visual.scene.drift.lighting",
                  must_show=["场景一致"])
    record_verdicts(tmp_project, _v2(
        r2, reviewer={"kind": "model_visual", "name": "r", "profile_digest": "p"},
        dimension_observations=[_dobs("scene", "mismatch", severity="warning",
                                      variable="lighting")]))
    r3 = _mk_shot(tmp_project, add_shot, "S003", "visual.style.gradient.charA",
                  must_show=["风格一致"])
    record_verdicts(tmp_project, _v2(
        r3, reviewer={"kind": "model_visual", "name": "r", "profile_digest": "p"},
        dimension_observations=[_dobs("style", "mismatch", severity="info",
                                      variable="post_grade")]))


def test_drift_trend_is_dimension_by_shot_order(tmp_project, add_shot):
    from manju.qc.production import drift_trend

    _seed_drift(tmp_project, add_shot)
    trend = drift_trend(tmp_project)
    assert trend["shot_order"] == ["S001", "S002", "S003"]
    assert set(trend["dimensions"]) >= {"character_identity", "scene", "style"}
    ident = trend["dimensions"]["character_identity"]
    assert ident[0]["shot"] == "S001" and ident[0]["observed"] == "mismatch"
    assert ident[0]["severity"] == "blocker"


def test_repair_routes_use_the_seven_route_vocabulary(tmp_project, add_shot):
    from manju.qc.production import SEVEN_ROUTES, repair_routes

    _seed_drift(tmp_project, add_shot)
    out = repair_routes(tmp_project)
    assert set(out["route_vocabulary"]) == set(SEVEN_ROUTES)
    assert len(SEVEN_ROUTES) == 7
    routes = {r["route"] for r in out["routes"]}
    # an identity blocker on a reference variable escalates to REGENERATE_REFERENCE.
    assert "REGENERATE_REFERENCE" in routes
    for r in out["routes"]:
        assert r["requires_confirmation"] is True
        assert r["do_not_execute_automatically"] is True
        assert r["primary_variable"]
        assert r["affected_shots"]
        assert "class" in r["estimated_cost"]


def test_regenerate_reference_and_reshoot_are_route_level_not_dispositions(tmp_project, add_shot):
    from manju.qc.production import ROUTE_LEVEL_ADDITIONS, SEVEN_ROUTES

    # the two additions live in the ROUTE vocabulary but NOT in the verdict
    # disposition vocabulary (addendum ruling 5 — record this distinction).
    assert set(ROUTE_LEVEL_ADDITIONS) == {"REGENERATE_REFERENCE", "RESHOOT"}
    for token in ROUTE_LEVEL_ADDITIONS:
        assert token in SEVEN_ROUTES
        assert token not in DISPOSITIONS


def test_drift_only_from_current_bound_evidence(tmp_project, add_shot):
    from manju.qc.production import drift_trend

    r1 = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.mismatch.charB")
    record_verdicts(tmp_project, _v2(
        r1, reviewer={"kind": "model_visual", "name": "r", "profile_digest": "p"},
        dimension_observations=[_dobs("character_identity", "mismatch", severity="blocker")]))
    # replace the shot's selected media with different golden bytes -> the prior
    # verdict's binding moves; drift must not surface a stale observation.
    _register_image(tmp_project, "S001", "visual.identity.match.charA")
    trend = drift_trend(tmp_project)
    assert trend["dimensions"].get("character_identity", []) == []


# ============================================ reviewer-profile cache currency


def test_changed_reviewer_profile_makes_verdict_historical(tmp_project, add_shot):
    # §11 cache key: a changed reviewer profile digest makes old verdicts
    # historical — the review cache never reuses across models (§12 不跨模型复用).
    from manju.qc.production import verdict_reviewer_current

    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "r", "profile_digest": "PROFILE_A"},
        dimension_observations=[_dobs("character_identity", "match")]))
    recs, _ = read_v2_records(tmp_project)
    rec = recs[0]
    assert verdict_reviewer_current(rec, "PROFILE_A") is True
    assert verdict_reviewer_current(rec, "PROFILE_B") is False  # model changed


def test_agreement_view_can_mark_superseded_profile_historical(tmp_project, add_shot):
    from manju.qc.production import reviewer_agreement

    row = _mk_shot(tmp_project, add_shot, "S001", "visual.identity.match.charA")
    record_verdicts(tmp_project, _v2(
        row, reviewer={"kind": "model_visual", "name": "r", "profile_digest": "OLD"},
        dimension_observations=[_dobs("character_identity", "match")]))
    view = reviewer_agreement(tmp_project, "S001", current_reviewer_digest="NEW")
    assert view["reviewers"][0]["current"] is False  # old profile -> historical


# ============================================ frame plan (§5 WP1)


def test_frame_plan_is_first_quarters_and_last():
    from manju.media.frames import FRAME_PLAN_LABELS, frame_plan

    plan = frame_plan(2000, 24.0, "sha256:deadbeef")
    labels = [p["label"] for p in plan["positions"]]
    assert labels == list(FRAME_PLAN_LABELS) == ["first", "p25", "mid", "p75", "last"]
    at = {p["label"]: p["at_ms"] for p in plan["positions"]}
    assert at["first"] == 0 and at["p25"] == 500 and at["mid"] == 1000 and at["p75"] == 1500
    # last is clamped to a real extractable frame (< duration).
    assert 1900 <= at["last"] < 2000


def test_frame_plan_is_deterministic_and_hash_bound():
    from manju.media.frames import frame_plan

    a = frame_plan(2000, 24.0, "sha256:aaaa")
    b = frame_plan(2000, 24.0, "sha256:aaaa")
    c = frame_plan(2000, 24.0, "sha256:bbbb")  # different media bytes
    assert a["plan_digest"] == b["plan_digest"]
    assert a["plan_digest"] != c["plan_digest"]  # digest binds the media hash


def test_frame_plan_scene_slots_skipped_with_evidence():
    from manju.media.frames import frame_plan

    plan = frame_plan(2000, 24.0, "sha256:aaaa")
    assert plan["scene_change_slots"]["status"] == "SKIPPED_WITH_EVIDENCE"
    assert "scene" in plan["scene_change_slots"]["reason"].lower()


def test_frame_plan_degrades_on_unknown_duration():
    from manju.media.frames import frame_plan

    plan = frame_plan(0, None, "sha256:aaaa")
    assert [p["label"] for p in plan["positions"]] == ["first"]
    assert plan["positions"][0]["at_ms"] == 0


def test_brief_row_carries_the_frame_plan(tmp_project, add_shot, tmp_path):
    # wired into the brief additively (existing first/mid/last frames untouched).
    # a real clip (with duration) so the full 5-position plan resolves.
    from tests.fixtures.golden import make_bad_media

    clip = make_bad_media.gen_good(tmp_path)
    take = tmp_project.register_take("S001", clip, TakeSidecar(provider="test", spec_hash="h"))
    add_shot(tmp_project, "S001", quality={"must_show": ["x"]})
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    row = _brief_row(tmp_project, "S001")
    assert set(row["frames"]) == {"first", "mid", "last"}  # legacy unchanged
    fp = row["frame_plan"]
    assert [p["label"] for p in fp["positions"]] == ["first", "p25", "mid", "p75", "last"]
    assert fp["plan_digest"]


def test_frame_extraction_at_plan_positions_is_content_addressed(tmp_project, add_shot, tmp_path):
    # §12 抽帧稳定: extraction reuses the EXISTING .manju/frames cache (no 2nd cache).
    from manju.media.frames import extract_frame, frame_plan
    from tests.fixtures.golden import make_bad_media
    from manju.media.probe import probe

    clip = make_bad_media.gen_good(tmp_path)
    take = tmp_project.register_take("S001", clip, TakeSidecar(provider="test", spec_hash="h"))
    add_shot(tmp_project, "S001", quality={"must_show": ["x"]})
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    info = probe(take.media_path)
    from manju.core.hashing import hash_file
    plan = frame_plan(info.duration_ms, info.fps, hash_file(take.media_path))
    rel = tmp_project.relpath(take.media_path)
    at = plan["positions"][2]["at_ms"]  # mid
    first = extract_frame(tmp_project, rel, at)
    again = extract_frame(tmp_project, rel, at)
    assert first == again and first.exists()  # cache hit, no second file
