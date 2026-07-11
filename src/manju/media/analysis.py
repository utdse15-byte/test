"""AI_IDE_19 WP1 — MediaAnalysis: a DERIVED evidence document (addendum ruling 1).

Analysis is *derived evidence, never editing truth* (contract §1, §23-line): the
LLM/VLM and any cloud video-understanding provider produce OBSERVATIONS about an
exact piece of source media; nothing here ever writes the Timeline. The document
is a deletable projection — deleting it loses the report, never the source; it is
re-analyzable and is NEVER a build/resume/cache/render input (pinned by a test).

One new derived schema — ``manju.media-analysis/v1`` — of the SAME class as the
qualification report: every evidence axis is OPTIONAL and unknown / low-confidence
observations are carried through honestly as ``UNKNOWN`` (contract §4: "未知或低
置信度必须保留"), never fabricated into confident data and never silently dropped.

Storage (addendum ruling 1): content-addressed under ``reports/analysis/`` keyed
by the exact source media hash, so the report is BOUND to the exact bytes it
analyzed. A same-name media replacement (new bytes over the old filename) is
detected on read the same way AI_IDE_18's alignment sidecar detects it — the
recorded ``source_media_hash`` no longer matches the bytes on disk → ``STALE``.

Fixture analysis FIRST (contract §2, addendum ruling 1): :func:`analyze_with_fixture`
is a deterministic, offline analyzer that reads a committed JSON observation set
(the AI_IDE_20A corpus pattern) and shapes it into the evidence document. The
cloud analyzer slot (:func:`analyze_with_provider`) is gated behind AI_IDE_14's
qualification ladder and returns a structured ``ANALYZER_NOT_QUALIFIED`` refusal
(mirroring the AI_IDE_15 reviewer gate) — never a silent proceed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.hashing import hash_file
from ..core.yamlio import atomic_write_text

SCHEMA = "manju.media-analysis/v1"

# read-state tokens (mirror media/timing.py)
ANALYZED = "ANALYZED"
STALE = "STALE"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"

# The OPTIONAL evidence axes (contract §4). Every one may be absent; the document
# always carries the KEY so a consumer never guesses whether an axis was analyzed.
AXES = (
    "transcription_ref",   # a ref to the 18 alignment sidecar / SRT — never a copy
    "shot_boundaries",     # shot cuts
    "scene_boundaries",    # scene changes
    "ocr",                 # on-screen text / logo
    "roi_tracks",          # person/object/ROI tracks (feed Smart Reframe)
    "audio_regions",       # dialogue / music / SFX regions
    "key_moments",         # highlight candidates — NEVER a final selection
    "quality_flags",       # black / silence / freeze
)

# default: an observation below this confidence is preserved but flagged UNKNOWN.
_DEFAULT_MIN_CONFIDENCE = 0.25


class AnalysisError(RuntimeError):
    """An analysis document could not be produced. ``str()`` carries no secret."""


# --------------------------------------------------------------- storage paths


def analysis_dir(project: Any) -> Path:
    """``reports/analysis/`` — a deletable derived projection, never a build input."""
    return Path(project.root) / "reports" / "analysis"


def _hash_stem(source_hash: str) -> str:
    return str(source_hash).split(":")[-1]


def report_path(project: Any, source_hash: str) -> Path:
    return analysis_dir(project) / f"{_hash_stem(source_hash)}.json"


# --------------------------------------------------------------- fixture analyzer


def _axis_value(raw: Any) -> tuple[Any, float | None, bool]:
    """Normalise one fixture axis into ``(value, confidence, is_unknown)``.

    * an explicit ``"UNKNOWN"`` string → the UNKNOWN sentinel, no confidence.
    * a ``{"value": …, "confidence": c}`` wrapper → the value + its confidence.
    * a bare value → the value, no declared confidence.
    """
    if raw is None:
        return None, None, True
    if isinstance(raw, str) and raw.upper() == UNKNOWN:
        return UNKNOWN, None, True
    if isinstance(raw, dict) and "value" in raw and "confidence" in raw:
        conf = raw.get("confidence")
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        return raw["value"], conf, False
    return raw, None, False


def analyze_with_fixture(source_hash: str, fixture: dict[str, Any], *,
                         source_ref: str | None = None,
                         min_confidence: float = _DEFAULT_MIN_CONFIDENCE) -> dict[str, Any]:
    """Deterministic offline analysis (fixture-first). Shape a committed JSON
    observation set into a ``manju.media-analysis/v1`` document BOUND to
    ``source_hash``. Two honesty invariants (contract §4):

    * every axis is OPTIONAL — an absent axis is carried as ``None`` (a known
      "not analyzed"), never invented;
    * a low-confidence (``< min_confidence``) or explicitly ``UNKNOWN`` axis is
      PRESERVED with its value and listed in ``unknown_axes`` — never promoted to
      a confident fact, never dropped.
    """
    if not source_hash:
        raise AnalysisError("media analysis must bind a non-empty source hash")
    evidence: dict[str, Any] = {}
    confidence: dict[str, float | None] = {}
    unknown: list[str] = []
    for axis in AXES:
        value, conf, is_unknown = _axis_value(fixture.get(axis))
        evidence[axis] = value
        confidence[axis] = conf
        if value is None:
            continue  # absent axis — a known gap, not an UNKNOWN observation
        if is_unknown or (conf is not None and conf < float(min_confidence)):
            unknown.append(axis)
    return {
        "schema": SCHEMA,
        "header": {
            "source_media_hash": source_hash,
            "source_ref": source_ref,
            "analyzer": {
                "provider": str(fixture.get("analyzer") or "fixture"),
                "profile_digest": fixture.get("profile_digest"),
                "rubric_digest": fixture.get("rubric_digest"),
            },
            "status": ANALYZED,
            "min_confidence": float(min_confidence),
        },
        "evidence": evidence,
        "confidence": confidence,
        "unknown_axes": unknown,
    }


# --------------------------------------------------------------- read / write


def write_report(project: Any, evidence: dict[str, Any]) -> Path:
    """Materialise the derived report content-addressed by the bound source hash.
    Atomic; deletable; never read by a build path."""
    src_hash = (evidence.get("header") or {}).get("source_media_hash")
    if not src_hash:
        raise AnalysisError("cannot store an analysis document with no source hash")
    path = report_path(project, src_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    return path


def read_report(project: Any, source_hash: str) -> dict[str, Any] | None:
    path = report_path(project, source_hash)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def analysis_status(evidence: dict[str, Any] | None, media_path: Path) -> str:
    """The live status of an analysis document w.r.t. the bytes on disk.

    ``MISSING`` (no evidence), ``STALE`` (the pinned ``source_media_hash`` no
    longer matches the file — a same-name replacement, contract §11 row 2), or
    ``ANALYZED``. The staleness check is evaluated on READ against the real file,
    so a silent swap can never pass as fresh."""
    if not evidence:
        return MISSING
    pinned = (evidence.get("header") or {}).get("source_media_hash")
    media_path = Path(media_path)
    if pinned and media_path.exists():
        try:
            if hash_file(media_path) != pinned:
                return STALE
        except OSError:
            return STALE
    return (evidence.get("header") or {}).get("status") or ANALYZED


def is_stale(evidence: dict[str, Any] | None, media_path: Path) -> bool:
    return analysis_status(evidence, media_path) == STALE


# --------------------------------------------------------------- derivations


def roi_tracks(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The ROI/subject tracks (contract §7) the Smart Reframe compiler consumes.
    Pure read of the evidence; an absent / UNKNOWN axis yields an empty list."""
    if not evidence:
        return []
    tracks = (evidence.get("evidence") or {}).get("roi_tracks")
    if not isinstance(tracks, list):
        return []
    return [t for t in tracks if isinstance(t, dict)]


def no_cut_zones(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Explicit "禁止静默切断" regions derivable from analysis evidence (contract
    §5): the span of every dialogue audio region and every key moment. A cut that
    lands strictly inside one of these is a blocking diagnostic on a PROPOSAL
    (build/segments.py), never an engine hard-block on a human."""
    if not evidence:
        return []
    ev = evidence.get("evidence") or {}
    zones: list[dict[str, Any]] = []
    regions = ev.get("audio_regions")
    if isinstance(regions, list):
        for r in regions:
            if isinstance(r, dict) and r.get("kind") == "dialogue":
                zones.append({"start_ms": r.get("start_ms"), "end_ms": r.get("end_ms"),
                              "reason": "dialogue"})
    moments = ev.get("key_moments")
    if isinstance(moments, list):
        for m in moments:
            if isinstance(m, dict) and m.get("t_ms") is not None:
                t = int(m["t_ms"])
                zones.append({"start_ms": t, "end_ms": t, "reason": "key_moment"})
    return zones


# --------------------------------------------------------------- cloud slot (gated)

# The capability a cloud video-understanding provider must advertise + be
# qualified for. Fixture analysis validates the data contract FIRST (contract §2);
# the cloud slot is unreachable until a real provider is configured + qualified.
#
# The qualification GATE (``ANALYZER_NOT_QUALIFIED``) deliberately lives in the
# PROVIDER layer — ``providers.qualification.analyzer_admission`` — and is called
# from cli.py, NEVER imported here: media/ must not read the qualification report
# (the build-boundary guard §15, the same rule the AI_IDE_15 reviewer gate obeys).
# This module owns only the DERIVED evidence document + the fixture analyzer.
CAPABILITY = "media_analysis"
