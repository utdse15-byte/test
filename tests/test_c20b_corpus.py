"""AI_IDE_20B — corpus expansion self-tests: calibration runner (§5), provider
regression cards (§6), the new partitions (prompt/director, delivery/policy,
provider runtime, story/skill, cross-platform), and the extended §9 corpus
discipline (evidence-index freshness, honest gaps, no policy write-back).

Everything is offline + deterministic. The calibration rates are computed over
the FAKE reviewer profiles — they prove the HARNESS, never a real model (the
corpus is the annotated set; the fake is the subject). The twin's 7 declared
conflicts are the known-value denominators the rate pins verify against.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import manju.providers.registry as registry_mod
from manju.core.models import TakeSidecar
from manju.core.yamlio import read_yaml
from manju.providers import qualification as Q
from manju.providers import submission as S
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider
from manju.providers.manifest import ProviderManifest
from manju.qc.agent_review import VerdictError, qc_brief, read_v2_records, record_verdicts

from tests.fixtures.golden import GOLDEN_DIR
from tests.fixtures.golden.calibration import run_calibration, visual_cases
from tests.fixtures.golden.fake_reviewer import FakeVisionReviewer, load_manifest
from tests.fixtures.golden.provider_cards import (
    FORBIDDEN_SCORE_KEYS,
    assert_no_score,
    derive_card,
)

MANIFEST = load_manifest()
CASES = {c["id"]: c for c in MANIFEST["cases"]}
REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "manju"

NEW_PARTITIONS = ("prompt_director", "delivery_policy", "provider_runtime",
                  "story_skill", "cross_platform")
KNOWN_KINDS = {"committed_image", "generated_media", "declared_input",
               "live_intake", "live_pure", "live_injection", "indexed_evidence",
               "skill_eval", "contract_index", "honest_gap"}


def _cases(partition):
    return [c for c in MANIFEST["cases"] if c["partition"] == partition]


# ================================================================ calibration


@pytest.fixture(scope="module")
def calibration(tmp_path_factory):
    return run_calibration(tmp_path_factory.mktemp("c20b_calibration"))


def test_calibration_primary_is_perfectly_calibrated(calibration):
    p = calibration["profiles"]["primary"]
    # the primary agrees with the annotation by construction — every defect
    # dimension scores perfect precision/recall through the REAL pipeline.
    for dim, m in p["per_dimension"].items():
        if m["defect_cases"]:
            assert m["precision"] == 1.0 and m["recall"] == 1.0, (dim, m)
        assert m["fp"] == 0, (dim, m)
    assert p["missed_blocker"] == {"count": 0, "denominator": 1, "rate": 0.0}
    assert p["false_blocker"]["count"] == 0
    assert p["false_blocker"]["denominator"] == 17
    # the three deliberately-ambiguous cases answer honestly-unknown.
    assert p["unknown"]["count"] == 3 and p["unknown"]["denominator"] == 18
    assert p["overconfident_on_ambiguous"]["count"] == 0


def test_calibration_twin_rates_match_the_declared_conflicts(calibration):
    t = calibration["profiles"]["twin"]
    # the twin MISSES the one annotated blocker (identity mismatch) — 1/1.
    assert t["missed_blocker"] == {"count": 1, "denominator": 1, "rate": 1.0}
    # recall collapses exactly on the conflicted defect dimensions; prop (the
    # non-conflicted defect) stays perfect.
    r = {d: m["recall"] for d, m in t["per_dimension"].items() if m["defect_cases"]}
    assert r == {"identity": 0.0, "wardrobe": 0.0, "lighting": 0.0,
                 "style": 0.0, "prop": 1.0}
    # overconfident on 2 of the 3 ambiguous cases (declared).
    assert t["overconfident_on_ambiguous"]["count"] == 2
    assert t["overconfident_on_ambiguous"]["denominator"] == 3


def test_calibration_disagreement_is_the_declared_seven_of_eighteen(calibration):
    d = calibration["disagreement"]
    declared = {c["id"] for c in visual_cases(MANIFEST)
                if c["reviewer"]["primary"] != c["reviewer"]["twin"]}
    assert len(declared) == 7  # the known-value denominator source
    assert d["disagreements"] == 7 and d["denominator"] == 18
    assert set(d["case_ids"]) == declared
    assert d["rate"] == round(7 / 18, 6)


def test_calibration_blocker_disagreement_via_the_real_agreement_surface(calibration):
    # the identity-mismatch shot (8th case in id order) is the ONE blocker
    # disagreement, surfaced by the REAL qc.production.reviewer_agreement.
    d = calibration["disagreement"]
    assert d["blocker_disagreement_shots"] == ["S008"]
    ordered = [c["id"] for c in visual_cases(MANIFEST)]
    assert ordered[7] == "visual.identity.mismatch.charB"
    assert "UNKNOWN_REVIEWER_DISAGREEMENT" in \
        calibration["real_pipeline_surfaces"]["agreement_states"]


def test_calibration_engages_drift_and_route_surfaces(calibration):
    s = calibration["real_pipeline_surfaces"]
    assert s["packets_issued"] == 18 and s["records_stored"] == 36
    assert s["records_bound"] == 36
    # drift is latest-record-per-dimension over CURRENT-bound evidence: the twin
    # files after the primary, so only the dimension BOTH call mismatch (prop)
    # survives as drift — exactly the real 15 semantics, pinned here.
    assert s["drift_dimensions"] == ["prop_product"]
    assert s["route_vocabulary_size"] == 7 and s["routes_proposed"] == 1


def test_calibration_is_deterministic(tmp_path):
    a = run_calibration(tmp_path / "a")
    b = run_calibration(tmp_path / "b")
    a.pop("report_path", None), b.pop("report_path", None)
    assert a == b


def test_calibration_report_is_written_and_honest(tmp_path):
    rep = run_calibration(tmp_path)
    f = tmp_path / "calibration_proj.manju" / "reports" / "calibration" / "calibration.json"
    assert f.is_file()
    on_disk = json.loads(f.read_text(encoding="utf-8"))
    assert on_disk["schema"] == "manju.golden_corpus.calibration/20B"
    # honest fake-subject framing + honest-zero cost/latency + digest identity.
    assert "fake" in rep["subject_note"] or "harness" in rep["subject_note"]
    for p in rep["profiles"].values():
        cl = p["cost_latency"]
        assert cl["cost_total"] == 0.0 and cl["latency_ms"] == 0 and cl["note"]
        assert p["profile_digest"]
    digests = {p["profile_digest"] for p in rep["profiles"].values()}
    assert len(digests) == 2  # primary and twin are distinct models
    assert rep["policy_writeback"].startswith("NEVER")


def test_runtime_never_reads_the_calibration_output():
    # thresholds never auto-write production policy; runtime never reads the
    # calibration module, the cards module, or their outputs (contract §5 +
    # addendum ruling). Needles are the load-bearing artifact names — prose like
    # colorstats' "color-calibration idea" is not a read and must not trip this.
    needles = ("golden_corpus", "provider_cards", "fixtures/golden",
               "reports/calibration", "calibration.json",
               "run_calibration", "derive_card")
    hits = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for n in needles:
            if n in text:
                hits.append(f"{py.relative_to(REPO)}: {n}")
    assert not hits, hits


# ============================================================= provider cards


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    registry_mod._manifest_cache = None
    yield root
    registry_mod._manifest_cache = None


@pytest.fixture(autouse=True)
def _pin_canary_fixtures(monkeypatch):
    monkeypatch.setenv("MANJU_CANARY_FIXTURES_DIR",
                       str(REPO / "tests" / "fixtures" / "canary"))


@pytest.fixture
def user_project(tmp_path):
    from manju.core.container import Project

    return Project.create(tmp_path / "user", git_init=False)


def _scripted_qualification(user_project, providers_dir, monkeypatch, *,
                            caps="image_to_video, text_to_video"):
    """A full scripted canary via 14's own helpers (Canned transport)."""
    from tests.test_c14_qualification import run_canary, write_manifest

    pid = write_manifest(providers_dir, monkeypatch, "card_cloud", caps=caps)
    report = run_canary(user_project, pid)
    return pid, report


def test_card_from_scripted_qualification(user_project, providers_dir, monkeypatch):
    pid, _report = _scripted_qualification(user_project, providers_dir, monkeypatch)
    card = derive_card(user_project, pid, "image_to_video")
    q = card["qualification"]
    assert q["state"] == Q.CANARY_ARTIFACT_PASSED and q["stale"] is False
    assert card["tested"]["transport"] == "scripted"
    assert card["tested"]["mode"] == "run"
    # §6 已知不支持: the declared-but-untested capability is NEVER claimed.
    assert card["known_unsupported_or_untested"]["untested_capabilities"] == \
        ["text_to_video"]
    # real observed error semantics = the actual submission state sequence.
    assert card["error_semantics"]["observed_submission_states"] == \
        [S.PREPARED, S.DISPATCHING, S.ADMITTED, S.TERMINAL_SUCCESS]
    # artifact probe + all four binding digests ride the card.
    assert card["artifact_probe"]["artifact"]["content_sha256"]
    for k, v in card["digests"].items():
        assert v, k
    # cost is the receipt's; latency is honestly not-recorded.
    assert card["cost_latency"]["observed_cost"] == 0.1
    assert card["cost_latency"]["latency_ms"] is None
    assert card["derived_from"]["report_present"] is True


def test_card_goes_stale_when_the_profile_moves(user_project, providers_dir, monkeypatch):
    pid, _ = _scripted_qualification(user_project, providers_dir, monkeypatch)
    # move a staleness anchor: edit the manifest's cost (profile digest moves).
    mpath = providers_dir / pid / "provider.yaml"
    mpath.write_text(mpath.read_text(encoding="utf-8").replace(
        "per_second: 0.1", "per_second: 0.2"), encoding="utf-8")
    registry_mod._manifest_cache = None
    card = derive_card(user_project, pid, "image_to_video")
    assert card["qualification"]["stale"] is True
    assert card["qualification"]["state"] == Q.STALE
    assert any(r.startswith("stale:") and "provider_profile_digest" in r
               for r in card["qualification"]["reasons"])


def test_card_without_report_is_the_honest_config_floor(user_project, providers_dir, monkeypatch):
    from tests.test_c14_qualification import write_manifest

    pid = write_manifest(providers_dir, monkeypatch, "floor_cloud")
    card = derive_card(user_project, pid, "image_to_video")
    assert card["qualification"]["level"] == Q.CONFIG_VALID
    assert card["derived_from"]["report_present"] is False
    assert card["error_semantics"]["observed_submission_states"] == []
    assert card["artifact_probe"]["artifact"] is None


def test_card_is_a_deletable_derived_view(user_project, providers_dir, monkeypatch):
    import shutil

    pid, _ = _scripted_qualification(user_project, providers_dir, monkeypatch)
    assert derive_card(user_project, pid, "image_to_video")[
        "qualification"]["state"] == Q.CANARY_ARTIFACT_PASSED
    shutil.rmtree(Q.qualification_dir(user_project))
    card = derive_card(user_project, pid, "image_to_video")
    assert card["qualification"]["level"] == Q.CONFIG_VALID  # history lost, truth intact
    assert card["derived_from"]["report_present"] is False


def test_card_never_carries_an_aggregate_score(user_project, providers_dir, monkeypatch):
    pid, _ = _scripted_qualification(user_project, providers_dir, monkeypatch)
    card = derive_card(user_project, pid, "image_to_video")
    assert_no_score(card)  # raises on any forbidden key
    assert "score" not in json.dumps(card).lower().replace("_score_keys", "")
    assert card["routing_note"].startswith("无总分")
    # and the checker itself works (red path).
    with pytest.raises(AssertionError):
        assert_no_score({"nested": {"rank": 1}})
    assert "rank" in FORBIDDEN_SCORE_KEYS


# ============================================================ prompt_director


@pytest.fixture
def hermetic_providers(tmp_path, monkeypatch):
    """Prompt checks route through the registry — isolate it (explorer pitfall 1)."""
    root = tmp_path / "_prov"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    monkeypatch.delenv("MANJU_ROUTING", raising=False)
    registry_mod._manifest_cache = None
    yield root
    registry_mod._manifest_cache = None


def _build_pd_shot(project, add_shot, case, sid="S010"):
    inputs = case["inputs"]
    kwargs = {"action": {"main": inputs["action_main"]}}
    if inputs.get("camera_movement"):
        kwargs["camera"] = {"movement": inputs["camera_movement"]}
    if inputs.get("ref"):
        refs_dir = project.root / "media" / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        rel = "media/refs/corpus_ref.png"
        (project.root / rel).write_bytes(
            (GOLDEN_DIR / "visual" / "identity_ref_charA.png").read_bytes())
        ref = ([rel] if inputs["ref"].get("legacy_string")
               else [{"ref": rel, "controls": inputs["ref"].get("controls", []),
                      "ignore": inputs["ref"].get("ignore", [])}])
        kwargs["generation"] = {"params": {"refs": ref}}
    add_shot(project, sid, **kwargs)
    if inputs.get("successor_prev"):
        add_shot(project, "S011", action={"main": "她抬头"},
                 continuity={"prev": sid})
    return project.load_shot(sid)


_PD_LIVE = [c for c in MANIFEST["cases"]
            if c["partition"] == "prompt_director" and c["kind"] == "declared_input"]


@pytest.mark.parametrize("case", _PD_LIVE, ids=lambda c: c["id"])
def test_prompt_director_cases_trigger_their_declared_codes(
        case, tmp_project, add_shot, hermetic_providers):
    from manju.qc.prompt_checks import check_shot, production_checks

    shot = _build_pd_shot(tmp_project, add_shot, case)
    fn = case["check_fn"]
    codes: set[str] = set()
    if fn in ("check_shot", "both"):
        codes |= {f["code"] for f in check_shot(tmp_project, shot)}
    if fn in ("production_checks", "both"):
        codes |= {f["code"] for f in production_checks(tmp_project, shot)}
    expected = set(case["expected_codes"])
    if expected:
        assert expected <= codes, f"{case['id']}: {expected - codes} missing in {codes}"
    else:
        # negative control: none of the partition's indexed codes may fire
        # (KEYFRAME_NOT_ADOPTED-style advisories from other features are fine).
        all_pd_codes = {code for c in _PD_LIVE for code in c["expected_codes"]}
        assert not (codes & all_pd_codes), codes & all_pd_codes


def test_pd_one_variable_rule_enforced_by_the_real_intake(tmp_project, add_shot):
    """§9.3 as declared by pd.one_variable.intake_enforced: a non-KEEP decision
    without primary_repair_variable is a payload-invalid batch — VerdictError,
    zero writes, through the REAL record_verdicts."""
    case = CASES["pd.one_variable.intake_enforced"]
    add_shot(tmp_project, "S001", quality={"must_show": ["林夏(charA)出现"]})
    src = GOLDEN_DIR / CASES["visual.identity.match.charA"]["path"]
    take = tmp_project.register_take("S001", src,
                                     TakeSidecar(provider="test", spec_hash="h"))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    row = {r["shot"]: r for r in qc_brief(tmp_project)["shots"]}["S001"]
    v = FakeVisionReviewer("primary", MANIFEST).build_verdict(row)
    v["decision"] = {"disposition": "REROLL"}  # no primary_repair_variable
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, v)
    assert case["expected_error_contains"] in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []  # zero writes


def test_pd_restart_row_is_an_honest_index_not_an_invented_check():
    case = CASES["pd.continuation.restart_gate"]
    assert case["kind"] == "indexed_evidence"
    assert "expected_codes" not in case and "check_fn" not in case
    assert set(case["nearest_codes"]) == {
        "CONTINUATION_SOURCE_NOT_ACCEPTED", "CONTINUATION_SOURCE_HASH_MISMATCH",
        "CONTINUATION_ENDPOINT_UNOBSERVED"}


# ============================================================ delivery_policy


def _set_profiles(project, profiles):
    from manju.core.yamlio import write_yaml

    data = read_yaml(project.root / "project.yaml") or {}
    data["delivery_profiles"] = profiles
    write_yaml(project.root / "project.yaml", data)


def _clean_final(project):
    from manju.core.models import (Timeline, TimelineMeta, TimelineRules,
                                   TimelineTracks, VideoClip)
    from manju.core.yamlio import write_yaml
    from manju.core.hashing import hash_file
    from manju.media.render import final_content_key

    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    tl = Timeline(meta=TimelineMeta(compiled_from="fp", mode="manual"),
                  fps=24, width=1080, height=1920, duration_ms=2000,
                  tracks=TimelineTracks(video=[VideoClip(
                      shot="S001", take="take_01",
                      source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=2000)]))
    project.save_timeline(tl)
    key = final_content_key(project, tl, ass_file=None, target="final")
    project.final_dir.mkdir(parents=True, exist_ok=True)
    p = project.final_dir / "final_v1.mp4"
    p.write_bytes(b"final-bytes")
    (project.final_dir / "final_v1.key.json").write_text(json.dumps(
        {"final_key": key, "target": "final", "output_sha256": hash_file(p),
         "created_at": "2026-07-11T10:00:00+00:00"}), encoding="utf-8")


def test_dp_platform_profile_checks_incl_disclosure_wording(tmp_project, add_shot):
    from manju.build import delivery as D

    case = CASES["dp.platform.profile_checks"]
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"yt": {
        "variant_kind": "platform_package", "platform": "youtube",
        "frame": {"width": 1080, "height": 1920}, "max_duration_ms": 60_000}})
    man = D.build_manifest(tmp_project, "yt")
    checks = {c["code"]: c["status"] for c in man["platform_handoff"]["checks"]}
    for code, status in case["expected_checks"].items():
        assert checks.get(code) == status, (code, checks)
    # synthetic-media disclosure: explicit-profile only, FINAL JUDGMENT HUMAN —
    # the wording must say a human decides (addendum: assert wording).
    detail = next(c["detail"] for c in man["platform_handoff"]["checks"]
                  if c["code"] == "DISCLOSURE_REVIEW")
    assert "human" in detail.lower() or "人" in detail
    assert man["platform_handoff"]["upload_supported"] is False


def test_dp_platform_credential_leak_blocks(tmp_project, add_shot):
    from manju.build import delivery as D

    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    meta = tmp_project.root / "platform_meta.json"
    meta.write_text(json.dumps({"title": "x", "upload_token":
                                "sk-abcdefghijklmnopqrstuvwxyz012345"}),
                    encoding="utf-8")
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package",
                                       "platform": "youtube"}})
    man = D.build_manifest(tmp_project, "yt", metadata_file="platform_meta.json")
    checks = {c["code"]: c["status"] for c in man["platform_handoff"]["checks"]}
    assert checks.get("CREDENTIALS_PRESENT") == "FAIL"
    diags = {d["code"] for d in man["diagnostics"]}
    assert "PLATFORM_CREDENTIAL_LEAK" in diags


def test_dp_format_only_invariant_pure():
    from manju.build.delivery import check_format_only_invariant

    bad = check_format_only_invariant("sha256:aaaa", "sha256:bbbb")
    assert [d["code"] for d in bad] == ["VARIANT_KIND_MISMATCH"]
    assert bad[0]["severity"] == "blocking"
    assert check_format_only_invariant("sha256:aaaa", "sha256:aaaa") == []


def test_dp_cutdown_boundary_codes():
    from manju.build.segments import validate_cutdown

    case = CASES["dp.cutdown.no_cut_zone"]
    zone = [{"start_ms": 1000, "end_ms": 2000, "reason": "对白中"}]
    v1 = validate_cutdown({"keep": [], "remove": [[500, 1500]]}, no_cut_zones=zone)
    v2 = validate_cutdown({"keep": [], "remove": [[1500, 500]]})
    codes = {d["code"] for d in v1} | {d["code"] for d in v2}
    assert set(case["expected_codes"]) <= codes
    assert all(d["severity"] == "blocking" for d in v1 + v2)


def test_dp_reframe_crop_jump_and_safe_area_statuses():
    from manju.media import reframe

    case = CASES["dp.reframe.crop_jump_safe_area"]

    def track(*centers, subject="linxia"):
        return {"subject_id": subject, "priority": 1.0,
                "keyframes": [{"t_ms": t, "cx": cx, "cy": cy, "w": 0.2, "h": 0.3}
                              for t, cx, cy in centers]}

    # rate-limited crop jump
    ok = reframe.compile_crop_keyframes(
        [track((0, 0.1, 0.5), (100, 0.9, 0.5))],
        source_wh=(1920, 1080), target_wh=(1080, 1920), max_px_per_s=1000)
    assert ok["status"] == "ok"
    assert abs(ok["keyframes"][1]["x"] - ok["keyframes"][0]["x"]) <= 1000 * 0.1 + 1
    # safe box wider than the crop window -> blanking fallback
    blank = reframe.compile_crop_keyframes(
        [track((0, 0.5, 0.5))], source_wh=(1920, 1080), target_wh=(1080, 1920),
        max_px_per_s=1000,
        safe_areas=[{"x": 0.02, "y": 0.4, "w": 0.96, "h": 0.2}])
    assert blank["status"] == "blanking"
    # two subjects far apart -> needs_manual, never a silent pick
    manual = reframe.compile_crop_keyframes(
        [track((0, 0.1, 0.5)), track((0, 0.9, 0.5), subject="rival")],
        source_wh=(1920, 1080), target_wh=(1080, 1920), max_px_per_s=1000)
    assert manual["status"] == "needs_manual"
    assert {ok["status"], blank["status"], manual["status"]} == \
        set(case["expected_statuses"])
    # FORMAT_ONLY is structural: nothing else ever changes.
    for out in (ok, blank, manual):
        assert out["changes"] == {"duration": False, "selection": False,
                                  "audio": False, "subtitle": False}


def test_dp_wordlist_is_deterministic_advisory_annotate_only():
    from manju.qc.roughcut import rough_cut_proposal

    case = CASES["dp.wordlist.brand_claims_advisory"]
    fixture = read_yaml(GOLDEN_DIR / case["wordlist"])
    assert fixture["license"] == "self-made-synthetic"
    assert fixture["disposition"] == "advisory_only"
    assert fixture["final_judgment"] == "human"  # 最终法律判断归人
    proposal = rough_cut_proposal({"cues": case["inputs"]["cues"]},
                                  sensitive_terms=list(fixture["terms"]))
    hits = [a for a in proposal["annotations"] if a["kind"] == "sensitive"]
    assert len(hits) == case["expected"]["sensitive_hits"]
    # deterministic/advisory ONLY: every hit is an annotation, never a cut.
    assert all(a["action"] == "annotate" for a in proposal["annotations"])
    assert proposal["default_action"] == "annotate"
    assert proposal["reversible"] is True
    # determinism: same fixture, same result.
    assert proposal == rough_cut_proposal({"cues": case["inputs"]["cues"]},
                                          sensitive_terms=list(fixture["terms"]))


def test_dp_voice_provenance_gate():
    from manju.build.voiceid import template_export_gate, voice_profile

    case = CASES["dp.voice.provenance_missing"]
    missing = voice_profile({"voice": "沙哑旁白"})  # a voice with NO rights info
    gate = template_export_gate(missing)
    assert gate["blocked"] is case["expected"]["blocked"]
    assert any(case["expected"]["reason_contains"] in r for r in gate["reasons"])
    complete = voice_profile({"voice": "沙哑旁白", "voice_locked": True,
                              "voice_provenance": {"source": "自录",
                                                   "license_or_consent": "本人授权"}})
    assert template_export_gate(complete)["blocked"] is False
    assert template_export_gate(complete)["shareable"] is True


def test_dp_known_roles_and_the_textless_honest_gap():
    from manju.build.delivery import KNOWN_ROLES
    from manju.media.masters import ROLE_KIND

    case = CASES["dp.stems.mne_textless"]
    for role in case["known_roles_must_contain"]:
        assert role in KNOWN_ROLES
    # the honest gap the case records: TEXTLESS_MASTER is a recognised role that
    # the masters renderer does NOT produce (M&E it does). CLOSEOUT C5 ruling 2
    # rename: that M&E role is now M_AND_E_BUS_EXCLUSION_MASTER (its name states
    # the bus-exclusion claim); both old names stay in KNOWN_ROLES above.
    assert "M_AND_E_BUS_EXCLUSION_MASTER" in ROLE_KIND
    assert "TEXTLESS_MASTER" not in ROLE_KIND


def test_dp_gap_row_is_recorded_honestly():
    case = CASES["dp.gap.codec_loudness_profile"]
    assert case["kind"] == "honest_gap" and case["gap"] is True
    # and the gap is REAL: no codec/loudness-vs-profile check exists in delivery.
    text = (SRC / "build" / "delivery.py").read_text(encoding="utf-8")
    assert "CODEC" not in re.findall(r'"([A-Z_]+)"', text), \
        "a codec check appeared — retire dp.gap.codec_loudness_profile"


# =========================================================== provider_runtime


def _pr_manifest():
    return ProviderManifest.model_validate({
        "id": "pr_cloud", "type": "video", "adapter": "generic_cloud",
        "capabilities": ["text_to_video"], "auth": {"key_env": "PR_CLOUD_KEY"},
        "submit": {"url": "https://api.example.com/v1/videos",
                   "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
                   "job_id_path": "$.data.task_id"},
        "poll": {"url": "https://api.example.com/v1/videos/{job_id}",
                 "status_path": "$.data.status",
                 "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed"},
                 "result_url_path": "$.data.video_url"},
    })


class _OneShotTransport:
    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def __call__(self, method, url, headers, body):
        self.calls += 1
        raise self.exc


def _pr_req(project, add_shot, sid="S001"):
    shot = add_shot(project, sid, generation={"candidates": 1})
    return GenerationRequest(project=project, shot=shot, bible=project.load_bible(),
                             spec_hash="sha256:test", duration_ms=3000,
                             candidates=1, params={"seed": 7})


def test_pr_live_preflight_reject_is_not_dispatched(tmp_project, add_shot, monkeypatch):
    case = CASES["pr.preflight_reject"]
    monkeypatch.delenv("PR_CLOUD_KEY", raising=False)
    transport = _OneShotTransport(AssertionError("never reached"))
    provider = GenericCloudProvider(_pr_manifest(), transport=transport,
                                    sleep_fn=lambda _s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_pr_req(tmp_project, add_shot))
    assert exc.value.kind is FailureKind.invalid
    assert exc.value.disposition == case["expected"]["disposition"] == S.NOT_DISPATCHED
    assert transport.calls == 0  # provably pre-transport


def test_pr_live_post_send_timeout_is_outcome_unknown(tmp_project, add_shot, monkeypatch):
    case = CASES["pr.timeout_after_send"]
    monkeypatch.setenv("PR_CLOUD_KEY", "k")
    exc_in = ProviderFailure(FailureKind.timeout, "read timeout", detail={},
                             disposition=S.OUTCOME_UNKNOWN_DISPOSITION)
    transport = _OneShotTransport(exc_in)
    provider = GenericCloudProvider(_pr_manifest(), transport=transport,
                                    sleep_fn=lambda _s: None)
    req = _pr_req(tmp_project, add_shot)
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.disposition == case["expected"]["disposition"] \
        == S.OUTCOME_UNKNOWN_DISPOSITION
    assert transport.calls == 1  # fail-closed: exactly one attempt, no retry


def test_pr_partition_covers_the_contract_fault_list():
    dims = {c["dimension"] for c in _cases("provider_runtime")}
    assert dims == {"preflight_reject", "explicit_remote_reject",
                    "timeout_before_send", "timeout_after_send", "poll_transient",
                    "download_corruption", "idempotency_reconcile",
                    "cancel_remote_may_continue", "malformed_evidence"}


# ================================================================ story_skill


@pytest.mark.parametrize("case", _cases("story_skill"), ids=lambda c: c["id"])
def test_skill_eval_contract(case, tmp_project):
    from manju.core.skills import load_skill, skill_text

    info = load_skill(tmp_project, case["skill_id"])  # the REAL loader
    text = skill_text(tmp_project, case["skill_id"])
    for section in case["required_sections"]:
        assert section in text, f"{case['skill_id']} missing {section}"
    for phrase in case["boundary_phrases"]:
        assert phrase in text, f"{case['skill_id']} missing boundary {phrase!r}"
    for phrase in case["forbidden_phrases"]:
        assert phrase not in text, f"{case['skill_id']} contains forbidden {phrase!r}"
    assert info.id == case["skill_id"]


def test_skill_review_vocabulary_matches_the_code_enums(tmp_project):
    from manju.core.skills import skill_text
    from manju.qc.agent_review import DISPOSITIONS

    text = skill_text(tmp_project, "review-take-and-route-repair")
    for d in DISPOSITIONS:
        assert d in text, f"disposition {d} missing from the review skill"


def test_skill_localize_data_pack_fields(tmp_project):
    case = CASES["sk.localize_dialogue"]
    pack = read_yaml(REPO / "skills" / "localize-dialogue" / case["data_pack"])
    assert pack["output_contract"]["per_line_fields"] == case["per_line_fields"] \
        if "output_contract" in pack else True
    # the declared fields exist somewhere in the pack regardless of nesting.
    blob = json.dumps(pack, ensure_ascii=False)
    for f in case["per_line_fields"]:
        assert f in blob


def test_skill_continue_names_the_gate_codes(tmp_project):
    from manju.core.skills import skill_text

    case = CASES["sk.continue_from_accepted_take"]
    text = skill_text(tmp_project, "continue-from-accepted-take")
    for code in case["named_codes"]:
        assert code in text


# ============================================================= cross_platform


_EVIDENCE_RE = re.compile(r"^(tests/[\w/]+\.py)(?:::(\w+))?$")


def test_every_evidence_ref_in_the_whole_corpus_is_fresh():
    """The index-freshness rule: every evidence ref names a REAL file, and when
    it names a test function that function must exist — the corpus can never
    silently rot into pointing at renamed/deleted tests."""
    checked = 0
    for c in MANIFEST["cases"]:
        for ref in c.get("evidence") or []:
            m = _EVIDENCE_RE.match(ref)
            assert m, f"{c['id']}: malformed evidence ref {ref!r}"
            path = REPO / m.group(1)
            assert path.is_file(), f"{c['id']}: missing evidence file {ref}"
            if m.group(2):
                assert f"def {m.group(2)}(" in path.read_text(encoding="utf-8"), \
                    f"{c['id']}: evidence test {ref} no longer exists"
            checked += 1
    assert checked >= 40  # the index is substantial, not vestigial


def test_xp_core_imports_no_provider_sdk_or_llm():
    # the whole-tree executable version of the §3 contract row (xp.core_no_sdk).
    forbidden = re.compile(
        r"^\s*(import|from)\s+(openai|anthropic|google\.generativeai|genai"
        r"|mistralai|cohere|ollama|replicate)\b", re.M)
    hits = [str(py.relative_to(REPO)) for py in SRC.rglob("*.py")
            if forbidden.search(py.read_text(encoding="utf-8", errors="replace"))]
    assert not hits, hits


def test_xp_ci_matrix_is_linux_only_so_skip_rows_are_justified():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ubuntu-latest" in ci
    assert "windows" not in ci.lower() and "macos" not in ci.lower()
    case = CASES["xp.windows_macos"]
    assert case["status"] == "SKIPPED_WITH_EVIDENCE"
    assert case["ci_evidence"] == ".github/workflows/ci.yml"


def test_xp_statuses_are_known_tokens():
    allowed = {"EXECUTED", "SKIPPED_WITH_EVIDENCE", "HONEST_GAP", "PARTIAL",
               "HUMAN_EVIDENCE_REQUIRED"}
    for c in _cases("cross_platform"):
        assert c["status"] in allowed, (c["id"], c["status"])
    # every EXECUTED row must actually cite evidence; gaps must not.
    for c in _cases("cross_platform"):
        if c["status"] == "EXECUTED":
            assert c["evidence"], f"{c['id']} EXECUTED without evidence"
        if c["status"] == "HONEST_GAP":
            assert not c["evidence"]


# =========================================================== 20B discipline


def test_20b_partition_counts_and_kinds():
    for part in NEW_PARTITIONS:
        declared = MANIFEST["partitions"][part]["count"]
        assert declared == len(_cases(part)), part
    for c in MANIFEST["cases"]:
        assert c["kind"] in KNOWN_KINDS, (c["id"], c["kind"])


def test_20b_committed_golden_tree_stays_under_one_megabyte():
    total = sum(p.stat().st_size for p in GOLDEN_DIR.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts)
    assert total < 1_000_000, total


def test_20b_wordlist_fixture_is_committed_and_licensed():
    p = GOLDEN_DIR / "policy" / "brand_claims_wordlist.v1.yaml"
    assert p.is_file()
    data = read_yaml(p)
    assert data["license"] == "self-made-synthetic"
    assert data["final_judgment"] == "human"
    assert len(data["terms"]) >= 5
