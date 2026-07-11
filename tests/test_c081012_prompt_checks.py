"""AI_IDE_08_10_12C WP2 — production checks, reference transfer, surface
profiles, compiler trace. All additive on the EXISTING prompt workbench
(`manju prompt --json` stays the ONE projection; qc/prompt_checks stays the
ONE deterministic checker; DR04 preflight stays the ONE capability authority).

Red-first (verified by stashing the WP2 diffs and re-running — recorded in the
completion report): `production_checks`/`compiler_trace` were absent from the
bundle (KeyError), `qc.prompt_checks.production_checks` did not exist
(ImportError), and a dict-form ref binding degraded to a silently-broken
stringified path with NO way to declare controls/ignore (fixture 2).
"""

from __future__ import annotations

import pytest

from manju.build.promptlab import shot_prompt_bundle
from manju.core.spec import compute_spec_hash
from manju.providers.refs import resolve_refs
from manju.qc.prompt_checks import check_all, production_checks


def _codes(findings):
    return {f["code"] for f in findings}


def _ref_png(project, name="lin_ref.png"):
    refs_dir = project.root / "media" / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    p = refs_dir / name
    p.write_bytes(b"\x89PNG-fake-" + name.encode())
    return f"media/refs/{name}"


# ---------------------------------------------------------- clip scope (13.2 6-8)


def test_multiple_completed_actions_flagged(tmp_project, add_shot):
    """Fixture 1: three completed actions in one 5s clip."""
    add_shot(tmp_project, "S010", duration=5.0,
             action={"main": "她推门进店,然后走向货架,然后拿起硬币仔细看"})
    shot = tmp_project.load_shot("S010")
    findings = production_checks(tmp_project, shot)
    hit = next(f for f in findings
               if f["code"] == "CLIP_SCOPE_MULTIPLE_COMPLETED_ACTIONS")
    assert hit["severity"] == "warning"
    assert hit["auto_apply"] is False
    assert "shots/S010.yaml#/action" in hit["source_paths"]
    assert hit["proposal"]


def test_future_beat_leak_flagged(tmp_project, add_shot):
    """Fixture 1: a future reveal leaked into the clip's own prompt."""
    add_shot(tmp_project, "S010", action={"main": "她低头看硬币,即将抬头发现监控"})
    findings = production_checks(tmp_project, tmp_project.load_shot("S010"))
    assert "CLIP_SCOPE_FUTURE_BEAT_LEAK" in _codes(findings)


def test_endpoint_missing_fires_only_for_continuation_sources(tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "她低头看硬币"})  # no endpoint
    add_shot(tmp_project, "S002", continuity={"prev": "S001"})
    f1 = production_checks(tmp_project, tmp_project.load_shot("S001"))
    assert "CLIP_SCOPE_ENDPOINT_MISSING_FOR_CONTINUATION" in _codes(f1)

    # authoring the endpoint clears it
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("action", {}).__setitem__(
            "main", "她低头看硬币,最后定格在她的手"))
    f2 = production_checks(tmp_project, tmp_project.load_shot("S001"))
    assert "CLIP_SCOPE_ENDPOINT_MISSING_FOR_CONTINUATION" not in _codes(f2)

    # a shot nobody continues from never fires it
    add_shot(tmp_project, "S003", action={"main": "空镜"})
    f3 = production_checks(tmp_project, tmp_project.load_shot("S003"))
    assert "CLIP_SCOPE_ENDPOINT_MISSING_FOR_CONTINUATION" not in _codes(f3)


# ------------------------------------------------- reference transfer (13.2 9-11)


def test_dict_binding_declares_transfer_and_conflict_rejected(tmp_project, add_shot):
    """Fixture 2: identity reference carrying wrong background/pose — the
    additive dict binding declares what transfers; controls∩ignore conflicts
    and unknown enums are never silently accepted."""
    ref = _ref_png(tmp_project)
    add_shot(tmp_project, "S010", generation={"params": {"refs": [
        {"ref": ref, "controls": ["character_identity"],
         "ignore": ["background", "pose"], "subject_ref": "character:linxia"},
    ]}})
    shot = tmp_project.load_shot("S010")
    refset = resolve_refs(tmp_project, shot, tmp_project.load_bible())
    item = next(it for it in refset.items if it.ref == ref)
    assert item.controls == ("character_identity",)
    assert item.ignore == ("background", "pose")
    assert item.subject_ref == "character:linxia"
    assert item.declared_transfer is True
    assert item.exists  # the dict form resolves the SAME path a string would
    findings = production_checks(tmp_project, shot, refset=refset)
    assert "REFERENCE_CONTROL_CONFLICT" not in _codes(findings)
    assert "REFERENCE_TRANSFER_UNDECLARED" not in _codes(findings)

    # conflict: the same facet controlled AND ignored
    tmp_project.update_shot_raw("S010", lambda d: d["generation"].__setitem__(
        "params", {"refs": [{"ref": ref, "controls": ["pose"], "ignore": ["pose"]}]}))
    shot = tmp_project.load_shot("S010")
    findings = production_checks(tmp_project, shot)
    hit = next(f for f in findings if f["code"] == "REFERENCE_CONTROL_CONFLICT")
    assert hit["severity"] == "warning"

    # unknown enum: never silently accepted
    tmp_project.update_shot_raw("S010", lambda d: d["generation"].__setitem__(
        "params", {"refs": [{"ref": ref, "controls": ["vibes"]}]}))
    findings = production_checks(tmp_project, tmp_project.load_shot("S010"))
    assert "REFERENCE_CONTROL_CONFLICT" in _codes(findings)


def test_legacy_string_ref_compatible_but_undeclared(tmp_project, add_shot):
    ref = _ref_png(tmp_project)
    add_shot(tmp_project, "S010", generation={"params": {"refs": [ref]}})
    shot = tmp_project.load_shot("S010")
    refset = resolve_refs(tmp_project, shot, tmp_project.load_bible())
    item = next(it for it in refset.items if it.ref == ref)
    assert item.exists and item.declared_transfer is False  # old form still works
    findings = production_checks(tmp_project, shot, refset=refset)
    hit = next(f for f in findings if f["code"] == "REFERENCE_TRANSFER_UNDECLARED")
    assert hit["severity"] == "advisory"  # compatible: visible, not blocking


def test_transfer_fields_enter_spec_hash_and_trace(tmp_project, add_shot):
    """§7.4: the additive fields ride generation.params, so they participate in
    spec_hash (and thus the request identity) and surface in the bundle trace."""
    ref = _ref_png(tmp_project)
    add_shot(tmp_project, "S010", generation={"params": {"refs": [
        {"ref": ref, "controls": ["character_identity"], "ignore": ["background"]}]}})
    bible = tmp_project.load_bible()
    h1 = compute_spec_hash(tmp_project.load_shot("S010"), bible)
    tmp_project.update_shot_raw("S010", lambda d: d["generation"]["params"]
                                .__setitem__("refs", [{"ref": ref,
                                    "controls": ["character_identity"],
                                    "ignore": ["background", "pose"]}]))
    h2 = compute_spec_hash(tmp_project.load_shot("S010"), bible)
    assert h1 != h2  # an ignore edit restages — it changes the real request

    bundle = shot_prompt_bundle(tmp_project, "S010")
    item = next(i for i in bundle["references"]["items"] if i["ref"] == ref)
    assert item["controls"] == ["character_identity"]
    assert item["ignore"] == ["background", "pose"]


def test_reference_source_stale_when_take_unselected(tmp_project, add_shot, make_take):
    """A promoted-take reference whose source shot moved on (fixture: refs
    another shot's non-selected take)."""
    add_shot(tmp_project, "S001")
    t1 = make_take(tmp_project, "S001", "h1")
    t2 = make_take(tmp_project, "S001", "h2")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", t2.name))
    ref = f"media/gen/S001/{t1.name}{t1.media_path.suffix}"
    add_shot(tmp_project, "S010", generation={"params": {"refs": [
        {"ref": ref, "controls": ["character_identity"]}]}})
    findings = production_checks(tmp_project, tmp_project.load_shot("S010"))
    hit = next(f for f in findings if f["code"] == "REFERENCE_SOURCE_STALE")
    assert t1.name in hit["message"] and t2.name in str(hit["message"])


# ------------------------------------------------- surface profile (13.2 12/15)


def test_surface_profile_unknown_and_stale_visible(tmp_project, add_shot):
    """Fixture 10: unknown/expired surface rules — visible, never guessed."""
    # unknown declared id -> warning + conservative unknown profile in trace
    add_shot(tmp_project, "S010", generation={"params": {"surface_profile": "sora-99"}})
    findings = production_checks(tmp_project, tmp_project.load_shot("S010"))
    assert "SURFACE_PROFILE_UNKNOWN" in _codes(findings)
    trace = shot_prompt_bundle(tmp_project, "S010")["compiler_trace"]
    assert trace["surface_profile"]["id"] == "unknown"
    assert trace["surface_profile"]["freshness"] == "UNKNOWN"

    # superseded pin -> STALE warning
    add_shot(tmp_project, "S011", generation={"params": {"surface_profile": "generic-video-v0"}})
    findings = production_checks(tmp_project, tmp_project.load_shot("S011"))
    assert "SURFACE_PROFILE_STALE" in _codes(findings)

    # current pin -> clean, CURRENT in trace
    add_shot(tmp_project, "S012", generation={"params": {"surface_profile": "generic-video-v1"}})
    findings = production_checks(tmp_project, tmp_project.load_shot("S012"))
    assert not ({"SURFACE_PROFILE_STALE", "SURFACE_PROFILE_UNKNOWN"} & _codes(findings))
    trace = shot_prompt_bundle(tmp_project, "S012")["compiler_trace"]
    assert trace["surface_profile"]["freshness"] == "CURRENT"
    assert trace["surface_profile"]["digest"].startswith("sha256:")

    # undeclared -> honest UNKNOWN in the trace, but NOT a blocking finding
    add_shot(tmp_project, "S013")
    findings = production_checks(tmp_project, tmp_project.load_shot("S013"))
    assert "SURFACE_PROFILE_UNKNOWN" not in _codes(findings)
    trace = shot_prompt_bundle(tmp_project, "S013")["compiler_trace"]
    assert trace["surface_profile"]["freshness"] == "UNKNOWN"


def test_prompt_budget_exceeded_against_declared_profile(tmp_project, add_shot):
    add_shot(tmp_project, "S010", generation={
        "params": {"surface_profile": "generic-video-v1"},
        "prompt_override": "字" * 2401,
    })
    findings = production_checks(tmp_project, tmp_project.load_shot("S010"))
    hit = next(f for f in findings if f["code"] == "PROMPT_BUDGET_EXCEEDED")
    assert hit["severity"] == "warning"


def test_profile_id_change_moves_spec_hash(tmp_project, add_shot):
    """§7.5: an authored profile pin is generation.params — changing it moves
    spec_hash (and therefore the request identity)."""
    add_shot(tmp_project, "S010", generation={"params": {"surface_profile": "generic-video-v1"}})
    bible = tmp_project.load_bible()
    h1 = compute_spec_hash(tmp_project.load_shot("S010"), bible)
    tmp_project.update_shot_raw("S010", lambda d: d["generation"]["params"]
                                .__setitem__("surface_profile", "generic-video-v0"))
    h2 = compute_spec_hash(tmp_project.load_shot("S010"), bible)
    assert h1 != h2


# ------------------------------------------------- bundle + --check integration


def test_bundle_carries_production_checks_and_trace_additively(tmp_project, add_shot):
    add_shot(tmp_project, "S010")
    bundle = shot_prompt_bundle(tmp_project, "S010")
    # legacy keys untouched
    for key in ("shot", "spec_hash", "image_prompt", "video_prompt",
                "director_prompt", "negative_prompt", "references",
                "provider", "cost", "checks"):
        assert key in bundle
    # additive keys
    assert isinstance(bundle["production_checks"], list)
    trace = bundle["compiler_trace"]
    assert trace["source_revision"] == bundle["spec_hash"]
    assert trace["prompt_bundle_digest"].startswith("sha256:")
    assert trace["continuation_source"] is None  # no continuity.prev declared
    assert all(f["auto_apply"] is False for f in bundle["production_checks"])


def test_check_all_includes_production_findings(tmp_project, add_shot):
    add_shot(tmp_project, "S010",
             action={"main": "她推门进店,然后走向货架,然后拿起硬币仔细看"})
    findings = check_all(tmp_project)
    assert "CLIP_SCOPE_MULTIPLE_COMPLETED_ACTIONS" in {
        f.get("code") for f in findings if f["shot"] == "S010"}


def test_clean_project_stays_clean_under_check(tmp_project, add_shot):
    """Compatibility: an ordinary shot with no continuation/refs/profile pins
    produces ZERO new blocking findings — `manju prompt --check` exit behavior
    for existing projects is unchanged."""
    from manju.qc.prompt_checks import has_blocking

    add_shot(tmp_project, "S001")
    findings = check_all(tmp_project)
    assert not has_blocking(findings), findings
