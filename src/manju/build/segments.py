"""AI_IDE_19 WP2 coherent A/V segments + WP3 cutdown decision list.

Both are PURE derivations over MediaAnalysis evidence (media/analysis.py) — no
LLM/VLM, no Timeline writes. The Agent turns segments into an explicit, diffable
cutdown decision list; the human confirms; the EXISTING Director proposal /
roundtrip envelope carries it into source (addendum rulings 2 & 3).

WP2 (§5): :func:`derive_segments` reports, per coherent segment, the contract's
fields — start/end, scene & shot identity, dialogue completeness (via AI_IDE_18's
word timing), visual & audio continuity, cut risk (from boundary adjacency),
key subjects, and the source evidence refs. Missing / low-signal evidence yields
an honest ``unknown``, never a fabricated boundary.

WP3 (§6): a cutdown decision list is a ``keep / remove / reorder / transition /
reason / source_analysis_digest`` PROPOSAL PAYLOAD — NOT a new public schema. It
rides the existing Director ``ProposalAction`` envelope. :func:`validate_cutdown`
is a ZERO-WRITE pre-apply check (current media hash match + time-boundary sanity +
no-cut-zone diagnostics); "禁止静默切断" is a BLOCKING diagnostic on the proposal,
never an engine hard-block on a human (addendum ruling 2). :func:`apply_cutdown`
is a CAS: a source that moved since the proposal was built refuses. The confirmed
ref feeds 13C's EDITORIAL_CUTDOWN ``cutdown_source.ref`` (an "approved proposal",
a ref type 13C already accepts) — this batch never invents a cut.
"""

from __future__ import annotations

from typing import Any

# cut-risk levels
LOW = "low"
MEDIUM = "medium"
HIGH = "high"
UNKNOWN = "unknown"


class CutdownError(RuntimeError):
    """A cutdown decision list is malformed. ``str()`` carries no secret."""


class StaleCutdownError(CutdownError):
    """A cutdown was applied against media that moved since it was built (CAS)."""


# ------------------------------------------------------------------ WP2 segments


def _shot_boundaries(evidence: dict[str, Any] | None) -> list[dict]:
    ev = (evidence or {}).get("evidence") or {}
    b = ev.get("shot_boundaries")
    return [x for x in b if isinstance(x, dict)] if isinstance(b, list) else []


def _scene_at(scenes: list[dict], start_ms: int, end_ms: int) -> str | None:
    """The scene whose span covers the segment's midpoint (or None → UNKNOWN)."""
    mid = (start_ms + end_ms) / 2
    for sc in scenes:
        if isinstance(sc, dict) and (sc.get("start_ms") or 0) <= mid < (sc.get("end_ms") or 0):
            return sc.get("scene")
    return None


def _spans_boundary(regions: list[dict], boundary_ms: int, kind: str | None) -> bool:
    """True if any region of ``kind`` (any when None) strictly straddles the
    boundary — i.e. a cut there would sever a continuous span."""
    for r in regions:
        if not isinstance(r, dict):
            continue
        if kind is not None and r.get("kind") != kind:
            continue
        if (r.get("start_ms") or 0) < boundary_ms < (r.get("end_ms") or 0):
            return True
    return False


def _word_splits_boundary(word_timing: list[dict] | None, boundary_ms: int) -> bool:
    """True if a word cue (AI_IDE_18 alignment evidence) strictly straddles the
    boundary — the dialogue would be cut mid-word."""
    for w in word_timing or []:
        if isinstance(w, dict) and (w.get("start_ms") or 0) < boundary_ms < (w.get("end_ms") or 0):
            return True
    return False


def derive_segments(evidence: dict[str, Any] | None, *,
                    word_timing: list[dict] | None = None) -> list[dict[str, Any]]:
    """Pure §5 derivation over analysis evidence → coherent A/V segments. The
    final cut decision is the human/Agent's (this only reports); a segment whose
    boundary evidence is absent is honestly ``cut_risk="unknown"``."""
    shots = _shot_boundaries(evidence)
    ev = (evidence or {}).get("evidence") or {}
    scenes = ev.get("scene_boundaries") if isinstance(ev.get("scene_boundaries"), list) else []
    regions = ev.get("audio_regions") if isinstance(ev.get("audio_regions"), list) else []
    moments = ev.get("key_moments") if isinstance(ev.get("key_moments"), list) else []
    roi = ev.get("roi_tracks") if isinstance(ev.get("roi_tracks"), list) else []
    have_boundaries = bool(shots)

    segments: list[dict[str, Any]] = []
    for i, sb in enumerate(shots):
        start, end = int(sb.get("start_ms") or 0), int(sb.get("end_ms") or 0)
        scene = _scene_at(scenes, start, end)
        # dialogue completeness: does the segment END sever a word / dialogue span?
        dlg_split = _word_splits_boundary(word_timing, end) or _spans_boundary(
            regions, end, "dialogue")
        # a key moment strictly inside → severing it at the end is risky
        moment_at_end = any(isinstance(m, dict) and (m.get("t_ms") == end)
                            for m in moments)
        boundary_music = _spans_boundary(regions, end, "music")
        # cut risk from boundary adjacency: the end aligns with a shot boundary by
        # construction; risk rises when it would sever dialogue or a key action
        # moment (禁止静默切断), and is MEDIUM when scene completeness can't be
        # confirmed. A continuous music underscore is REPORTED (audio_continuity)
        # but is not itself a high-risk cut driver.
        is_last = i == len(shots) - 1
        if not have_boundaries:
            risk = UNKNOWN
        elif is_last:
            risk = LOW
        elif dlg_split or moment_at_end:
            risk = HIGH
        elif scene is None:
            risk = MEDIUM  # scene evidence missing → completeness unconfirmed
        else:
            risk = LOW
        subjects = sorted({t.get("subject_id") for t in roi
                           if isinstance(t, dict) and _roi_overlaps(t, start, end)}
                          - {None})
        segments.append({
            "start_ms": start,
            "end_ms": end,
            "shot_index": i,
            "scene": scene if scene is not None else UNKNOWN,
            "dialogue_complete": (not dlg_split),
            "visual_continuity": (scene is not None),
            "audio_continuity": boundary_music,
            "cut_risk": risk,
            "key_subjects": subjects,
            "evidence_refs": _evidence_refs(evidence),
        })
    return segments


def _roi_overlaps(track: dict, start_ms: int, end_ms: int) -> bool:
    for kf in track.get("keyframes") or []:
        if isinstance(kf, dict) and start_ms <= (kf.get("t_ms") or 0) <= end_ms:
            return True
    return False


def _evidence_refs(evidence: dict[str, Any] | None) -> dict[str, Any]:
    header = (evidence or {}).get("header") or {}
    return {"source_media_hash": header.get("source_media_hash"),
            "analyzer": (header.get("analyzer") or {}).get("provider")}


# ------------------------------------------------------------------ WP3 cutdown


def build_cutdown(*, keep: list, remove: list, reason: str,
                  source_analysis_digest: str,
                  reorder: list | None = None,
                  transition: Any = None) -> dict[str, Any]:
    """Assemble the §6 cutdown decision list — an explicit, diffable dict. It is
    a PROPOSAL PAYLOAD, never a stored schema; nothing is written here."""
    return {
        "keep": [list(r) for r in keep],
        "remove": [list(r) for r in remove],
        "reorder": list(reorder or []),
        "transition": transition,
        "reason": str(reason),
        "source_analysis_digest": source_analysis_digest,
    }


def _ranges(cutdown: dict, key: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for r in cutdown.get(key) or []:
        try:
            a, b = int(r[0]), int(r[1])
        except (TypeError, ValueError, IndexError):
            raise CutdownError(f"cutdown.{key} entries must be [start_ms, end_ms]")
        out.append((a, b))
    return out


def validate_cutdown(cutdown: dict[str, Any], *,
                     no_cut_zones: list[dict] | None = None,
                     media_duration_ms: int | None = None,
                     expected_hashes: dict[str, str] | None = None,
                     current_hashes: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """ZERO-WRITE pre-apply validation (contract §6, §11 rows 4-5). Returns a list
    of diagnostics; a ``blocking`` one must stop the apply. Checks:

    * time-boundary sanity — ordered, in-bounds ranges;
    * no-cut-zone — a remove boundary strictly inside a dialogue / key-moment zone
      is a BLOCKING ``CUT_CROSSES_NO_CUT_ZONE`` (禁止静默切断, addendum ruling 2);
    * current media hash match — a source whose live hash differs from what the
      proposal recorded is a BLOCKING ``MEDIA_HASH_MISMATCH``.

    This function writes NOTHING — building and checking a cutdown is inert."""
    diags: list[dict[str, Any]] = []
    keep = _ranges(cutdown, "keep")
    remove = _ranges(cutdown, "remove")

    for a, b in keep + remove:
        if b <= a:
            diags.append({"code": "RANGE_INVERTED", "severity": "blocking",
                          "detail": f"range [{a},{b}) is empty or inverted"})
        if a < 0 or (media_duration_ms is not None and b > media_duration_ms):
            diags.append({"code": "RANGE_OUT_OF_BOUNDS", "severity": "blocking",
                          "detail": f"range [{a},{b}) exceeds media bounds "
                                    f"[0,{media_duration_ms})"})

    # a remove boundary landing strictly inside a no-cut zone severs it
    for a, b in remove:
        for z in no_cut_zones or []:
            zs, ze = z.get("start_ms"), z.get("end_ms")
            if zs is None or ze is None:
                continue
            for boundary in (a, b):
                if zs < boundary < ze:
                    diags.append({
                        "code": "CUT_CROSSES_NO_CUT_ZONE", "severity": "blocking",
                        "detail": f"a cut at {boundary}ms falls inside a no-cut "
                                  f"zone [{zs},{ze}) ({z.get('reason')})",
                        "zone": z})

    if expected_hashes is not None and current_hashes is not None:
        for src, exp in expected_hashes.items():
            if current_hashes.get(src) != exp:
                diags.append({"code": "MEDIA_HASH_MISMATCH", "severity": "blocking",
                              "detail": f"source {src!r} moved since the cutdown "
                                        f"was built (expected {exp}, "
                                        f"got {current_hashes.get(src)})"})
    return diags


def cutdown_proposal_action(cutdown: dict[str, Any], *, timeline_target: str,
                            shot: str | None = None) -> dict[str, Any]:
    """The Director ``ProposalAction`` payload for a cutdown — it RIDES the
    existing envelope (no new schema). A plain action dict a proposal can carry;
    building it writes nothing. Priced 0 (a cut is a local source edit)."""
    action = {"type": "cutdown", "target": str(timeline_target), "cutdown": cutdown}
    if shot is not None:
        action["shot"] = shot
    return action


def apply_cutdown(cutdown: dict[str, Any], *,
                  expected_hashes: dict[str, str],
                  current_hashes: dict[str, str],
                  proposal_id: str) -> dict[str, Any]:
    """CAS apply (contract §6, §11 row 6). Re-checks that every source's live hash
    still matches what the proposal recorded; a mismatch RAISES
    :class:`StaleCutdownError` (never a silent overwrite). On success returns the
    ``cutdown_source`` ref that 13C's EDITORIAL_CUTDOWN variant consumes — the cut
    enters source through the existing proposal/roundtrip path, not a re-invention."""
    for src, exp in (expected_hashes or {}).items():
        if current_hashes.get(src) != exp:
            raise StaleCutdownError(
                f"cannot apply cutdown: source {src!r} moved since the proposal "
                f"(expected {exp}, got {current_hashes.get(src)}) — re-derive it")
    return {
        "cutdown_source": {
            "ref": f"proposal:{proposal_id}",
            "kind": "approved_proposal",
            "source_analysis_digest": cutdown.get("source_analysis_digest"),
        },
        "applied": True,
    }
