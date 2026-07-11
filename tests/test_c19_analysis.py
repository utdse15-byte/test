"""AI_IDE_19 WP1 — MediaAnalysis derived evidence + fixture analyzer + gate.

§11 rows 1-3: analysis bound to exact media · same-name replacement → STALE ·
low-confidence axes preserved as UNKNOWN (never fabricated / never dropped).
Plus: exactly one new derived schema; the report is a deletable projection
(never a build input); the cloud analyzer slot refuses ANALYZER_NOT_QUALIFIED.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.core.hashing import hash_file
from manju.media import analysis


# ------------------------------------------------------------- fixtures / helpers

def _media(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# a committed-style fixture: the deterministic offline analysis observations
# (the "hand / fixture analysis" the contract §2 requires BEFORE any cloud slot).
FIXTURE = {
    "analyzer": "fixture",
    "profile_digest": "prof-1",
    "rubric_digest": "rubric-1",
    "shot_boundaries": [{"start_ms": 0, "end_ms": 1000}, {"start_ms": 1000, "end_ms": 2000}],
    "scene_boundaries": [{"start_ms": 0, "end_ms": 2000, "scene": "store"}],
    "roi_tracks": [
        {"subject_id": "linxia", "keyframes": [
            {"t_ms": 0, "cx": 0.5, "cy": 0.4, "w": 0.3, "h": 0.5, "confidence": 0.9},
        ], "priority": 1.0},
    ],
    "audio_regions": [{"start_ms": 0, "end_ms": 1000, "kind": "dialogue"}],
    "key_moments": [{"t_ms": 500, "label": "reveal", "confidence": 0.8}],
    "quality_flags": [{"kind": "black", "start_ms": 0, "end_ms": 40}],
    # an explicitly low-confidence axis + an explicit UNKNOWN
    "ocr": {"value": [{"t_ms": 0, "text": "OPEN"}], "confidence": 0.05},
    "transcription_ref": "UNKNOWN",
}


# --------------------------------------------------------------------- row 1

def test_evidence_binds_exact_source_hash(tmp_path):
    media = _media(tmp_path, "clip.mp4", b"exact-bytes-A")
    h = hash_file(media)
    ev = analysis.analyze_with_fixture(h, FIXTURE, source_ref="S001/take_01")
    assert ev["schema"] == analysis.SCHEMA
    assert ev["header"]["source_media_hash"] == h
    assert ev["header"]["source_ref"] == "S001/take_01"
    # a doc built for hash A is retrievable only under A (content-addressed)
    assert analysis.analysis_status(ev, media) == analysis.ANALYZED


def test_report_round_trips_content_addressed(tmp_project, tmp_path):
    media = _media(tmp_path, "clip.mp4", b"exact-bytes-A")
    h = hash_file(media)
    ev = analysis.analyze_with_fixture(h, FIXTURE)
    path = analysis.write_report(tmp_project, ev)
    assert path.exists()
    assert h.split(":")[-1] in path.name  # content-addressed filename
    got = analysis.read_report(tmp_project, h)
    assert got["header"]["source_media_hash"] == h


# --------------------------------------------------------------------- row 2

def test_same_name_replacement_is_stale(tmp_path):
    media = _media(tmp_path, "clip.mp4", b"original-bytes")
    ev = analysis.analyze_with_fixture(hash_file(media), FIXTURE, source_ref="S001/take_01")
    assert analysis.analysis_status(ev, media) == analysis.ANALYZED
    # a same-NAME replacement (new bytes over the old filename) → STALE on read
    media.write_bytes(b"REPLACED-bytes-different")
    assert analysis.analysis_status(ev, media) == analysis.STALE


def test_missing_analysis_is_missing(tmp_path):
    media = _media(tmp_path, "clip.mp4", b"bytes")
    assert analysis.analysis_status(None, media) == analysis.MISSING


def test_deleting_report_is_inert_reanalyzable(tmp_project, tmp_path):
    media = _media(tmp_path, "clip.mp4", b"bytes-A")
    h = hash_file(media)
    ev = analysis.analyze_with_fixture(h, FIXTURE)
    path = analysis.write_report(tmp_project, ev)
    before = media.read_bytes()
    path.unlink()  # delete the derived report
    assert media.read_bytes() == before          # source untouched
    assert analysis.read_report(tmp_project, h) is None
    # re-analyze reproduces the SAME evidence (deterministic fixture analyzer)
    ev2 = analysis.analyze_with_fixture(h, FIXTURE)
    assert ev2["evidence"] == ev["evidence"]


# --------------------------------------------------------------------- row 3

def test_low_confidence_axis_preserved_as_unknown(tmp_path):
    media = _media(tmp_path, "clip.mp4", b"bytes")
    ev = analysis.analyze_with_fixture(hash_file(media), FIXTURE, min_confidence=0.25)
    # the low-confidence ocr axis is KEPT (never dropped) and marked UNKNOWN
    assert "ocr" in ev["unknown_axes"]
    assert ev["confidence"]["ocr"] == 0.05
    assert ev["evidence"]["ocr"] is not None            # value carried through
    # an explicit UNKNOWN string axis is preserved, not fabricated into data
    assert "transcription_ref" in ev["unknown_axes"]
    assert ev["evidence"]["transcription_ref"] == analysis.UNKNOWN
    # a high-confidence axis is NOT flagged unknown
    assert "shot_boundaries" not in ev["unknown_axes"]


def test_all_axes_optional(tmp_path):
    media = _media(tmp_path, "clip.mp4", b"bytes")
    # an almost-empty fixture is valid — every evidence axis is optional (§4)
    ev = analysis.analyze_with_fixture(hash_file(media), {"analyzer": "fixture"})
    assert ev["schema"] == analysis.SCHEMA
    for axis in analysis.AXES:
        assert axis in ev["evidence"]  # present as a key
        assert ev["evidence"][axis] is None or ev["evidence"][axis] == analysis.UNKNOWN


# ------------------------------------------------------------- ROI extraction

def test_roi_tracks_extracted_for_reframe(tmp_path):
    media = _media(tmp_path, "clip.mp4", b"bytes")
    ev = analysis.analyze_with_fixture(hash_file(media), FIXTURE)
    tracks = analysis.roi_tracks(ev)
    assert len(tracks) == 1
    assert tracks[0]["subject_id"] == "linxia"
    assert tracks[0]["keyframes"][0]["cx"] == 0.5


# ------------------------------------------------------------- single schema

def test_exactly_one_new_public_schema():
    import inspect
    src = inspect.getsource(analysis)
    ids = set()
    for line in src.splitlines():
        if "manju." in line and "/v" in line and "=" in line and "SCHEMA" in line:
            ids.add(line.split("=", 1)[1].strip().strip('"'))
    assert ids == {"manju.media-analysis/v1"}


# ------------------------------------------------------------- cloud gate

def test_cloud_analyzer_refuses_when_not_qualified(tmp_project, tmp_path):
    # the analyzer gate lives in the PROVIDER layer (build-boundary guard §15):
    # media/analysis.py must not import qualification; the gate is provider-side.
    from manju.providers.qualification import analyzer_admission
    # no configured cloud analyzer provider reaches DRY_RUN_VALID here → refusal,
    # never a silent proceed / never a fabricated analysis.
    decision = analyzer_admission("no_such_cloud_analyzer", analysis.CAPABILITY,
                                  project=tmp_project)
    assert decision["admitted"] is False
    assert decision["refusal"] == "ANALYZER_NOT_QUALIFIED"


def test_report_dir_not_a_build_input(tmp_project, tmp_path):
    # the analysis report directory must never be read by build/resume/cache/render.
    import subprocess
    root = Path(analysis.__file__).resolve().parents[1]  # src/manju
    hits = subprocess.run(
        ["grep", "-rn", "reports/analysis", str(root),
         "--include=*.py", "--exclude-dir=__pycache__"],
        capture_output=True, text=True).stdout
    # only analysis.py itself may name the path (it writes/reads the projection)
    offenders = [ln for ln in hits.splitlines()
                 if "/media/analysis.py" not in ln and ln.strip()]
    assert offenders == [], f"build path reads the derived report: {offenders}"
