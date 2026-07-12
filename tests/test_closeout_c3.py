"""AI_IDE_14_21_CLOSEOUT Track B — C3 red tests (M01–M14).

Scope: media analysis / cutdown / roughcut / reframe (contract §4). Each test is
named for the M-row it closes and is written red-first: it pins the contract's
required behaviour so the pre-fix code fails and the fix turns it green. No test
here weakens an existing pin; the existing C19/C20B suites remain the regression
floor.

Media (the one integration test) is generated at test time with the committed
ffmpeg helpers (the 20A/20B pattern) — nothing over 1KB is committed, no network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.hashing import hash_file
from manju.media import analysis, reframe
from manju.build import segments as seg
from manju.qc import roughcut


MIN_FIXTURE = {"analyzer": "fixture",
               "shot_boundaries": [{"start_ms": 0, "end_ms": 1000}]}

SOURCE_WH = (1920, 1080)
TARGET_WH = (1080, 1920)


# ============================================================ Analysis (M01–M03)

def test_m01_missing_media_does_not_read_analyzed(tmp_path):
    """Pinned evidence + a media file that has disappeared ⇒ MISSING, never a
    stale read-through to ANALYZED."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"original-bytes")
    ev = analysis.analyze_with_fixture(hash_file(media), MIN_FIXTURE)
    assert analysis.analysis_status(ev, media) == analysis.ANALYZED
    media.unlink()                                  # the bound source is gone
    assert analysis.analysis_status(ev, media) == analysis.MISSING
    assert analysis.analysis_status(ev, media) != analysis.ANALYZED


def test_m02_malformed_report_not_consumed(tmp_project, tmp_path):
    """read_report validates schema shape / source-hash form / minimal structure;
    a tampered report is a structured rejection (None), never consumed."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"A")
    h = hash_file(media)
    ev = analysis.analyze_with_fixture(h, MIN_FIXTURE)
    path = analysis.write_report(tmp_project, ev)
    assert analysis.read_report(tmp_project, h) is not None      # valid → consumed

    # tamper 1: wrong schema id
    bad = json.loads(json.dumps(ev)); bad["schema"] = "evil/v9"
    path.write_text(json.dumps(bad), encoding="utf-8")
    assert analysis.read_report(tmp_project, h) is None

    # tamper 2: content-address broken (header hash ≠ the file it is stored under)
    bad2 = json.loads(json.dumps(ev))
    bad2["header"]["source_media_hash"] = "sha256:DIFFERENT0000"
    path.write_text(json.dumps(bad2), encoding="utf-8")
    assert analysis.read_report(tmp_project, h) is None

    # tamper 3: minimal structure gone (no evidence block)
    bad3 = json.loads(json.dumps(ev)); del bad3["evidence"]
    path.write_text(json.dumps(bad3), encoding="utf-8")
    assert analysis.read_report(tmp_project, h) is None

    # the rejection is STRUCTURED (inspectable diagnostics), not a bare swallow
    diags = analysis.validate_report(bad3, h)
    assert diags and all(isinstance(d, dict) and "code" in d for d in diags)


def test_m03_low_confidence_roi_not_auto_consumed():
    """A low-confidence / UNKNOWN roi_tracks axis must not flow into automatic
    consumers by default; only an explicit allow_unknown override surfaces it."""
    fx = {"analyzer": "fixture",
          "roi_tracks": {"value": [{"subject_id": "x",
                                    "keyframes": [{"t_ms": 0, "cx": 0.5, "cy": 0.5}]}],
                         "confidence": 0.05}}
    ev = analysis.analyze_with_fixture("sha256:m03", fx)
    assert "roi_tracks" in ev["unknown_axes"]              # sanity: flagged low-conf
    assert analysis.roi_tracks(ev) == []                   # default: not consumed
    assert len(analysis.roi_tracks(ev, allow_unknown=True)) == 1   # explicit override


# ============================================================ Cutdown (M04, M07–M09)

def test_m04_key_moment_cut_is_blocked():
    """Cutting through a key moment is a BLOCKING no-cut diagnostic; the moment's
    tolerance window is explicit + versioned so a silent retune is visible."""
    ev = analysis.analyze_with_fixture("sha256:m04", {
        "analyzer": "fixture",
        "key_moments": [{"t_ms": 1500, "label": "reveal", "confidence": 0.9}]})
    zones = analysis.no_cut_zones(ev)
    km = [z for z in zones if z.get("reason") == "key_moment"]
    assert km, "no key-moment no-cut zone derived"

    cutdown = {"keep": [[0, 1000], [2000, 3000]], "remove": [[1000, 2000]],
               "reason": "drop middle", "source_analysis_digest": "sha256:m04"}
    diags = seg.validate_cutdown(cutdown, no_cut_zones=zones)
    blocking = {d["code"] for d in diags if d["severity"] == "blocking"}
    assert "CUT_CROSSES_NO_CUT_ZONE" in blocking

    # tolerance is explicit + versioned and rides the diagnostic
    assert km[0].get("tolerance_ms") is not None
    assert km[0].get("tolerance_version")


def test_m07_inverted_cutdown_apply_is_refused():
    """apply_cutdown runs THE SAME validator and refuses an inverted range."""
    cutdown = seg.build_cutdown(keep=[[0, 400]], remove=[[800, 400]], reason="oops",
                                source_analysis_digest="sha256:x")
    with pytest.raises(seg.CutdownError):
        seg.apply_cutdown(cutdown, expected_hashes={}, current_hashes={},
                          proposal_id="p1")


def test_m08_apply_refuses_blocking_no_cut_diagnostic():
    """apply_cutdown refuses when a no-cut-zone diagnostic is blocking (not a
    separate lighter check at apply time)."""
    ev = analysis.analyze_with_fixture("sha256:m08", {
        "analyzer": "fixture", "key_moments": [{"t_ms": 1500}]})
    zones = analysis.no_cut_zones(ev)
    cutdown = seg.build_cutdown(keep=[[0, 1000], [2000, 3000]], remove=[[1000, 2000]],
                                reason="x", source_analysis_digest="sha256:m08")
    with pytest.raises(seg.CutdownError):
        seg.apply_cutdown(cutdown, expected_hashes={}, current_hashes={},
                          proposal_id="p2", no_cut_zones=zones)


def test_m09_apply_refuses_analysis_digest_mismatch():
    """A cutdown whose bound analysis digest no longer matches the current
    analysis is refused; a matching binding applies."""
    cutdown = seg.build_cutdown(keep=[[0, 1000]], remove=[[1000, 2000]], reason="x",
                                source_analysis_digest="sha256:OLD")
    ok = seg.apply_cutdown(cutdown, expected_hashes={}, current_hashes={},
                           proposal_id="p3", current_analysis_digest="sha256:OLD")
    assert ok["applied"] is True
    with pytest.raises(seg.CutdownError):
        seg.apply_cutdown(cutdown, expected_hashes={}, current_hashes={},
                          proposal_id="p3", current_analysis_digest="sha256:NEW")


# ============================================================ Roughcut (M05–M06)

def test_m05_open_tail_validates_after_duration_resolution():
    """A roughcut open tail (end=None) is unresolved-blocking without a duration
    and validates clean once the duration is known."""
    prop = {"annotations": [{"kind": "filler", "start_ms": 0, "end_ms": 300,
                             "reason": "f"}]}
    cutdown = roughcut.to_cutdown(prop, select=[0], source_analysis_digest="sha256:x")
    assert any(r[1] is None for r in cutdown["keep"])       # the open tail exists

    diags = seg.validate_cutdown(cutdown)                   # no duration → blocking
    assert any(d["code"] == "OPEN_TAIL_UNRESOLVED" and d["severity"] == "blocking"
               for d in diags)

    diags2 = seg.validate_cutdown(cutdown, media_duration_ms=2000)  # resolved
    assert not any(d["code"] == "OPEN_TAIL_UNRESOLVED" for d in diags2)
    assert not any(d["severity"] == "blocking" for d in diags2)


def test_m06_overlapping_remove_ranges_normalize_deterministically():
    """Overlapping/adjacent selected removes merge into a deterministic, sorted,
    non-overlapping remove list before the complement is taken."""
    prop = {"annotations": [
        {"kind": "a", "start_ms": 200, "end_ms": 600, "reason": "r1"},
        {"kind": "b", "start_ms": 100, "end_ms": 300, "reason": "r2"},
        {"kind": "c", "start_ms": 500, "end_ms": 900, "reason": "r3"}]}
    cutdown = roughcut.to_cutdown(prop, select=[0, 1, 2], source_analysis_digest="s")
    assert cutdown["remove"] == [[100, 900]]                # merged overlaps
    again = roughcut.to_cutdown(prop, select=[2, 1, 0], source_analysis_digest="s")
    assert again["remove"] == cutdown["remove"]            # order-independent


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="ffmpeg required")
def test_roughcut_to_cutdown_validate_apply_on_real_media(tmp_project, tmp_path):
    """The real integration path (ruling 4): roughcut → to_cutdown → validate →
    apply on a freshly generated tiny clip, asserting the resolved output timeline."""
    src = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=256x144:rate=25:duration=2",
                    "-pix_fmt", "yuv420p", str(src)], check=True, capture_output=True)
    from manju.media.probe import probe
    dur = probe(src).duration_ms
    src_hash = hash_file(src)

    evidence = {"header": {"source_media_hash": src_hash},
                "cues": [{"start_ms": 0, "end_ms": 300, "text": "呃", "speaker": "a"},
                         {"start_ms": 300, "end_ms": 900, "text": "正式内容", "speaker": "a"}]}
    prop = roughcut.rough_cut_proposal(evidence, fillers=["呃"])
    sel = [i for i, a in enumerate(prop["annotations"]) if a["kind"] == roughcut.FILLER]
    assert sel

    an = analysis.analyze_with_fixture(src_hash, {"analyzer": "fixture"})
    an_digest = analysis.analysis_digest(an)
    cutdown = roughcut.to_cutdown(prop, select=sel, source_analysis_digest=an_digest)

    # open tail unresolved without a duration; clean once resolved + bound + CAS-ok
    assert any(d["code"] == "OPEN_TAIL_UNRESOLVED" for d in seg.validate_cutdown(cutdown))
    diags = seg.validate_cutdown(cutdown, media_duration_ms=dur,
                                 current_analysis_digest=an_digest,
                                 expected_hashes={"src": src_hash},
                                 current_hashes={"src": src_hash})
    assert not any(d["severity"] == "blocking" for d in diags)

    out = seg.apply_cutdown(cutdown, expected_hashes={"src": src_hash},
                            current_hashes={"src": src_hash}, proposal_id="rc1",
                            media_duration_ms=dur, current_analysis_digest=an_digest)
    assert out["applied"] is True
    # the filler [0,300) is removed; the kept timeline is [300, real-duration)
    assert out["resolved_keep"] == [[300, dur]]


# ============================================================ Reframe (M10–M14)

def _track(subject, *centers, confidence=None):
    t = {"subject_id": subject, "priority": 1.0,
         "keyframes": [{"t_ms": t, "cx": cx, "cy": cy, "w": 0.2, "h": 0.3}
                       for t, cx, cy in centers]}
    if confidence is not None:
        t["confidence"] = confidence
    return t


def test_m10_unsorted_reframe_input_normalized_deterministically():
    """Unsorted per-track keyframes are normalized (sorted) deterministically; the
    output keyframe times come out sorted and strictly increasing."""
    unsorted = _track("x", (1000, 0.8, 0.5), (0, 0.2, 0.5), (500, 0.5, 0.5))
    out = reframe.compile_crop_keyframes([unsorted], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=1_000_000)
    times = [k["t_ms"] for k in out["keyframes"]]
    assert times == sorted(times)
    assert all(times[i] < times[i + 1] for i in range(len(times) - 1))
    out2 = reframe.compile_crop_keyframes([unsorted], source_wh=SOURCE_WH,
                                          target_wh=TARGET_WH, max_px_per_s=1_000_000)
    assert [k["t_ms"] for k in out2["keyframes"]] == times


def test_m11_unequal_track_sampling_aligns_by_timestamp():
    """Two tracks describing the same motion at different sampling rates align by
    TIMESTAMP (union grid + interpolation), not by array index."""
    a = _track("a", (0, 0.2, 0.5), (1000, 0.8, 0.5))
    b = _track("b", (0, 0.2, 0.5), (500, 0.5, 0.5), (1000, 0.8, 0.5))
    out = reframe.compile_crop_keyframes([a, b], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=1_000_000)
    assert out["status"] == reframe.OK             # identical motion → no conflict
    times = [k["t_ms"] for k in out["keyframes"]]
    assert times == [0, 500, 1000]                 # union of both tracks' timestamps
    # both agree cx=0.8 at t=1000 → x = 0.8*1920 - 608/2 = 1232 (timestamp-aligned,
    # not the index-aligned average of 0.8 and 0.5 that lands at 944)
    assert abs(out["keyframes"][-1]["x"] - 1232) <= 2


def test_m12_no_roi_is_explicit_fallback_not_smart_ok():
    """No ROI evidence ⇒ an explicit centre-crop fallback status, never a silent
    OK masquerading as an intelligent crop."""
    out = reframe.compile_crop_keyframes([], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=5000)
    assert out["status"] == "center_crop_fallback"
    assert out["status"] != reframe.OK
    assert out["keyframes"], "fallback still yields a usable centred crop"
    assert out["changes"] == {"duration": False, "selection": False,
                              "audio": False, "subtitle": False}


def test_m13_low_confidence_roi_is_needs_manual():
    """A low-confidence ROI is not framed by a guess — the compile reports
    needs_manual."""
    low = _track("x", (0, 0.5, 0.5), (500, 0.6, 0.5), confidence=0.1)
    out = reframe.compile_crop_keyframes([low], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=5000)
    assert out["status"] == reframe.NEEDS_MANUAL


def test_m14_output_keyframe_time_strictly_increases():
    """Duplicate input timestamps are deduped so output keyframe times strictly
    increase (a stable frame grid, never two frames at the same time)."""
    dup = _track("x", (0, 0.3, 0.5), (0, 0.7, 0.5), (500, 0.6, 0.5))
    out = reframe.compile_crop_keyframes([dup], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=1_000_000)
    times = [k["t_ms"] for k in out["keyframes"]]
    assert all(times[i] < times[i + 1] for i in range(len(times) - 1)), times
