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

# Key moments carry a VERSIONED tolerance window (contract §4, addendum ruling 1):
# a key moment at ``t`` protects ``[t - tol, t + tol]`` as a no-cut zone. The value
# and its version enter the analysis header (so a digest moves if it is retuned)
# and every derived no-cut-zone diagnostic (so a silent retune is always visible).
KEY_MOMENT_TOLERANCE_MS = 200
KEY_MOMENT_TOLERANCE_VERSION = "km-tol-1"


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
                         min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
                         key_moment_tolerance_ms: int = KEY_MOMENT_TOLERANCE_MS) -> dict[str, Any]:
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
            "key_moment_tolerance_ms": int(key_moment_tolerance_ms),
            "key_moment_tolerance_version": KEY_MOMENT_TOLERANCE_VERSION,
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


def _looks_like_hash(value: Any) -> bool:
    """A source hash is a non-empty ``algo:hex`` token or a bare hex digest."""
    if not isinstance(value, str) or len(value) < 8:
        return False
    stem = value.split(":")[-1]
    return len(stem) >= 8 and all(c in "0123456789abcdefABCDEF" for c in stem)


def validate_report(data: Any, source_hash: str | None = None) -> list[dict[str, Any]]:
    """Structured validation of a stored analysis document (addendum ruling 1).
    Returns a list of blocking diagnostics; an empty list means the document is a
    well-formed ``manju.media-analysis/v1`` report. Checks schema shape, the
    source-hash FORM, the minimal structure, and — when ``source_hash`` is given —
    that the document is content-addressed to the media it is stored under (a
    tampered / mis-filed report fails). This is what makes a malformed / tampered
    report a *rejection*, never a silent consumption."""
    if not isinstance(data, dict):
        return [{"code": "REPORT_NOT_OBJECT", "severity": "blocking",
                 "detail": "analysis report is not a JSON object"}]
    diags: list[dict[str, Any]] = []
    if data.get("schema") != SCHEMA:
        diags.append({"code": "REPORT_SCHEMA_MISMATCH", "severity": "blocking",
                      "detail": f"schema {data.get('schema')!r} is not {SCHEMA!r}"})
    header = data.get("header")
    if not isinstance(header, dict):
        diags.append({"code": "REPORT_HEADER_MISSING", "severity": "blocking",
                      "detail": "header is absent or not an object"})
        return diags
    smh = header.get("source_media_hash")
    if not _looks_like_hash(smh):
        diags.append({"code": "REPORT_SOURCE_HASH_MALFORMED", "severity": "blocking",
                      "detail": "header.source_media_hash is missing or malformed"})
    elif source_hash is not None and _hash_stem(smh) != _hash_stem(source_hash):
        diags.append({"code": "REPORT_SOURCE_HASH_MISMATCH", "severity": "blocking",
                      "detail": "report is not content-addressed to the requested "
                                "media (tampered or mis-filed)"})
    if not isinstance(data.get("evidence"), dict):
        diags.append({"code": "REPORT_EVIDENCE_MISSING", "severity": "blocking",
                      "detail": "evidence block is absent or not an object"})
    return diags


def read_report(project: Any, source_hash: str) -> dict[str, Any] | None:
    """Read a stored analysis report. A missing file, unreadable JSON, or a report
    that fails :func:`validate_report` (malformed / tampered / mis-filed) all yield
    ``None`` — a structured rejection, never a consumed half-truth (M02)."""
    path = report_path(project, source_hash)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if validate_report(data, source_hash):
        return None
    return data


def analysis_status(evidence: dict[str, Any] | None, media_path: Path) -> str:
    """The live status of an analysis document w.r.t. the bytes on disk.

    ``MISSING`` (no evidence, OR pinned evidence whose bound media file is gone —
    a report that outlived its source never reads through to ANALYZED, M01),
    ``STALE`` (the pinned ``source_media_hash`` no longer matches the file — a
    same-name replacement, contract §11 row 2), or ``ANALYZED``. The check is
    evaluated on READ against the real file, so neither a missing source nor a
    silent swap can pass as fresh."""
    if not evidence:
        return MISSING
    pinned = (evidence.get("header") or {}).get("source_media_hash")
    media_path = Path(media_path)
    if pinned:
        if not media_path.exists():
            return MISSING          # the report's bound media has disappeared
        try:
            if hash_file(media_path) != pinned:
                return STALE
        except OSError:
            return STALE
    return (evidence.get("header") or {}).get("status") or ANALYZED


def is_stale(evidence: dict[str, Any] | None, media_path: Path) -> bool:
    return analysis_status(evidence, media_path) == STALE


# --------------------------------------------------------------- derivations


def roi_tracks(evidence: dict[str, Any] | None, *,
               allow_unknown: bool = False,
               min_confidence: float = _DEFAULT_MIN_CONFIDENCE) -> list[dict[str, Any]]:
    """The ROI/subject tracks (contract §7) the Smart Reframe compiler consumes.
    Pure read of the evidence; an absent axis yields an empty list.

    Low-confidence / UNKNOWN evidence must NOT flow into an automatic consumer by
    default (contract §4, addendum ruling 1, M03): if the ``roi_tracks`` axis was
    flagged ``UNKNOWN`` (in ``unknown_axes``), or an individual track declares a
    ``confidence`` below ``min_confidence``, it is withheld unless the caller opts
    in with ``allow_unknown=True`` (the explicit human override)."""
    if not evidence:
        return []
    if not allow_unknown and "roi_tracks" in (evidence.get("unknown_axes") or []):
        return []
    tracks = (evidence.get("evidence") or {}).get("roi_tracks")
    if not isinstance(tracks, list):
        return []
    out: list[dict[str, Any]] = []
    for t in tracks:
        if not isinstance(t, dict):
            continue
        if not allow_unknown:
            conf = t.get("confidence")
            if conf is not None:
                try:
                    if float(conf) < float(min_confidence):
                        continue        # low-confidence track — withheld by default
                except (TypeError, ValueError):
                    pass
        out.append(t)
    return out


def no_cut_zones(evidence: dict[str, Any] | None, *,
                 key_moment_tolerance_ms: int | None = None) -> list[dict[str, Any]]:
    """Explicit "禁止静默切断" regions derivable from analysis evidence (contract
    §5): the span of every dialogue audio region and every key moment. A cut that
    severs one of these is a blocking diagnostic on a PROPOSAL (build/segments.py),
    never an engine hard-block on a human.

    A key moment is a POINT, so it is protected by a versioned tolerance window
    ``[t - tol, t + tol]`` carrying ``point_ms`` (the moment itself) plus the
    ``tolerance_ms`` / ``tolerance_version`` that produced it (addendum ruling 1):
    the tolerance rides every diagnostic, so a silent retune is always visible.
    Removing the moment (its ``point_ms`` falls inside a remove range) or landing a
    cut inside the window are both blocking in the validator (M04)."""
    if not evidence:
        return []
    ev = evidence.get("evidence") or {}
    header = evidence.get("header") or {}
    tol = key_moment_tolerance_ms
    if tol is None:
        tol = header.get("key_moment_tolerance_ms", KEY_MOMENT_TOLERANCE_MS)
    tol = int(tol)
    tol_ver = header.get("key_moment_tolerance_version", KEY_MOMENT_TOLERANCE_VERSION)
    zones: list[dict[str, Any]] = []
    regions = ev.get("audio_regions")
    if isinstance(regions, list):
        for r in regions:
            if isinstance(r, dict) and r.get("kind") == "dialogue":
                zones.append({"start_ms": r.get("start_ms"), "end_ms": r.get("end_ms"),
                              "reason": "dialogue", "kind": "span"})
    moments = ev.get("key_moments")
    if isinstance(moments, list):
        for m in moments:
            if isinstance(m, dict) and m.get("t_ms") is not None:
                t = int(m["t_ms"])
                zones.append({"start_ms": t - tol, "end_ms": t + tol, "point_ms": t,
                              "reason": "key_moment", "kind": "key_moment",
                              "tolerance_ms": tol, "tolerance_version": tol_ver})
    return zones


def analysis_digest(evidence: dict[str, Any] | None) -> str | None:
    """A stable content digest of the derived analysis document — the value a
    cutdown binds as ``source_analysis_digest``. It covers the schema, the bound
    source hash, the observed evidence + unknown axes, and the key-moment tolerance,
    so ANY change to the analysis (including a retuned tolerance) moves the digest
    and a stale binding is detectable at validate/apply time (addendum ruling 3,
    M09). Pure read; writes nothing."""
    if not evidence:
        return None
    import hashlib

    header = evidence.get("header") or {}
    payload = {
        "schema": evidence.get("schema"),
        "source_media_hash": header.get("source_media_hash"),
        "key_moment_tolerance_ms": header.get("key_moment_tolerance_ms"),
        "key_moment_tolerance_version": header.get("key_moment_tolerance_version"),
        "evidence": evidence.get("evidence"),
        "unknown_axes": evidence.get("unknown_axes"),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


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
