"""AI_IDE_19 WP2 coherent segments + WP3 cutdown decision list.

§11 rows 4-6: a cut may not cross an explicit no-cut zone (blocking PROPOSAL
diagnostic) · an EDL/cutdown proposal is ZERO-WRITE · apply is a CAS. Plus:
the cutdown decision list is a PROPOSAL PAYLOAD on the existing Director envelope
(no new public schema) and feeds 13C's EDITORIAL_CUTDOWN cutdown_source.ref.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.build import segments as seg
from manju.media import analysis


EVID = analysis.analyze_with_fixture("sha256:abc", {
    "analyzer": "fixture",
    "shot_boundaries": [{"start_ms": 0, "end_ms": 1000},
                        {"start_ms": 1000, "end_ms": 2000},
                        {"start_ms": 2000, "end_ms": 3000}],
    "scene_boundaries": [{"start_ms": 0, "end_ms": 2000, "scene": "store"},
                         {"start_ms": 2000, "end_ms": 3000, "scene": "street"}],
    "audio_regions": [{"start_ms": 400, "end_ms": 900, "kind": "dialogue"},
                      {"start_ms": 0, "end_ms": 3000, "kind": "music"}],
    "key_moments": [{"t_ms": 1500, "label": "reveal", "confidence": 0.9}],
    "roi_tracks": [{"subject_id": "linxia", "keyframes": [
        {"t_ms": 0, "cx": 0.5, "cy": 0.5, "w": 0.3, "h": 0.4}]}],
})

# 18 alignment word cues (the evidence speech consumes; a word spans 400-900ms)
WORD_TIMING = [{"start_ms": 400, "end_ms": 900, "text": "这不可能", "speaker": "linxia"}]


# --------------------------------------------------------------------- WP2

def test_segments_carry_contract_fields():
    segments = seg.derive_segments(EVID, word_timing=WORD_TIMING)
    assert len(segments) == 3
    s = segments[0]
    for field in ("start_ms", "end_ms", "scene", "shot_index", "dialogue_complete",
                  "visual_continuity", "audio_continuity", "cut_risk",
                  "key_subjects", "evidence_refs"):
        assert field in s
    assert segments[0]["scene"] == "store"
    assert segments[2]["scene"] == "street"
    assert "linxia" in segments[0]["key_subjects"]


def test_dialogue_completeness_from_word_timing():
    # a boundary at 1000ms does NOT split the 400-900 word → first seg complete;
    # construct a cut that DOES split it and prove incompleteness is detected.
    segments = seg.derive_segments(EVID, word_timing=WORD_TIMING)
    assert segments[0]["dialogue_complete"] is True
    # a segment whose END lands inside the word span is flagged incomplete
    split = seg.derive_segments(EVID, word_timing=[
        {"start_ms": 800, "end_ms": 1200, "text": "word", "speaker": "x"}])
    assert split[0]["dialogue_complete"] is False   # 1000 boundary splits 800-1200


def test_cut_risk_from_boundary_adjacency_and_unknown():
    segments = seg.derive_segments(EVID, word_timing=WORD_TIMING)
    # boundary at 1000 aligns with a shot boundary and no dialogue crosses → low
    assert segments[0]["cut_risk"] == "low"
    # with NO shot-boundary evidence, cut risk is honestly UNKNOWN
    bare = analysis.analyze_with_fixture("sha256:x", {"analyzer": "fixture"})
    bseg = seg.derive_segments(bare)
    assert bseg == [] or all(b["cut_risk"] == "unknown" for b in bseg)


# ----------------------------------------------------------------- WP3 row 4

def test_cut_inside_no_cut_zone_blocks():
    zones = analysis.no_cut_zones(EVID)  # dialogue 400-900 + key moment @1500
    # a cutdown that removes [600, 800) cuts INSIDE the dialogue zone → blocking
    cutdown = {"keep": [[0, 600], [800, 3000]], "remove": [[600, 800]],
               "reason": "tighten", "source_analysis_digest": "sha256:abc"}
    diags = seg.validate_cutdown(cutdown, no_cut_zones=zones)
    codes = {d["code"] for d in diags if d["severity"] == "blocking"}
    assert "CUT_CROSSES_NO_CUT_ZONE" in codes
    # a clean cut on a real boundary (remove a whole non-dialogue tail) → no block
    clean = {"keep": [[0, 2000]], "remove": [[2000, 3000]],
             "reason": "drop street", "source_analysis_digest": "sha256:abc"}
    clean_diags = seg.validate_cutdown(clean, no_cut_zones=zones)
    assert not any(d["severity"] == "blocking" for d in clean_diags)


def test_time_boundary_sanity():
    zones = []
    bad = {"keep": [[0, 5000]], "remove": [], "source_analysis_digest": "x"}
    diags = seg.validate_cutdown(bad, no_cut_zones=zones, media_duration_ms=3000)
    assert any(d["code"] == "RANGE_OUT_OF_BOUNDS" for d in diags)


# ----------------------------------------------------------------- WP3 row 5

def test_edl_proposal_is_zero_write(tmp_project):
    before = _snapshot(tmp_project.root)
    segs = seg.derive_segments(EVID, word_timing=WORD_TIMING)
    cutdown = seg.build_cutdown(keep=[[0, 2000]], remove=[[2000, 3000]],
                                reason="x", source_analysis_digest="sha256:abc")
    seg.validate_cutdown(cutdown, no_cut_zones=analysis.no_cut_zones(EVID))
    seg.cutdown_proposal_action(cutdown, timeline_target="main")
    after = _snapshot(tmp_project.root)
    assert before == after, "deriving/validating/proposing a cutdown wrote to disk"


# ----------------------------------------------------------------- WP3 row 6

def test_apply_is_cas():
    cutdown = seg.build_cutdown(keep=[[0, 2000]], remove=[[2000, 3000]],
                                reason="x", source_analysis_digest="sha256:abc")
    expected = {"src.mp4": "sha256:v1"}
    # CAS passes when the live media hashes still match what the proposal saw
    ref = seg.apply_cutdown(cutdown, expected_hashes=expected,
                            current_hashes={"src.mp4": "sha256:v1"},
                            proposal_id="0001")
    assert ref["cutdown_source"]["ref"] == "proposal:0001"
    # CAS FAILS when a source moved since the proposal (stale) — refuse, no write
    with pytest.raises(seg.StaleCutdownError):
        seg.apply_cutdown(cutdown, expected_hashes=expected,
                          current_hashes={"src.mp4": "sha256:v2-MOVED"},
                          proposal_id="0001")


# --------------------------------------------------- cutdown is a proposal payload

def test_cutdown_is_proposal_payload_not_a_schema():
    import inspect
    src = inspect.getsource(seg)
    # no new public "manju.*/v*" schema id is defined in this module
    assert "/v1" not in src or "manju." not in src.split("/v1")[0].rsplit("=", 1)[-1]
    action = seg.cutdown_proposal_action(
        seg.build_cutdown(keep=[[0, 1]], remove=[], reason="", source_analysis_digest="x"),
        timeline_target="main")
    # rides the existing Director envelope: a plain action dict with a type
    assert isinstance(action, dict) and "type" in action


def test_cutdown_feeds_13c_editorial_cutdown_boundary(tmp_project):
    """The confirmed cutdown ref satisfies 13C's EDITORIAL_CUTDOWN cutdown_source
    requirement — no CUTDOWN_SOURCE_REQUIRED diagnostic."""
    from manju.build import delivery
    ref = seg.apply_cutdown(
        seg.build_cutdown(keep=[[0, 2000]], remove=[[2000, 3000]], reason="x",
                          source_analysis_digest="sha256:abc"),
        expected_hashes={}, current_hashes={}, proposal_id="0007")
    profile = {"variant_kind": "EDITORIAL_CUTDOWN", "cutdown_source": ref["cutdown_source"]}
    variant, diags = delivery._build_variant(tmp_project, profile, "cut", None, None)
    assert not any(d["code"] == "CUTDOWN_SOURCE_REQUIRED" for d in diags)


# --------------------------------------------------------------------- helpers

def _snapshot(root: Path) -> set:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
