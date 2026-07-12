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
is a ZERO-WRITE pre-apply check; "禁止静默切断" is a BLOCKING diagnostic on the
proposal, never an engine hard-block on a human (addendum ruling 2).
:func:`apply_cutdown` re-runs THE SAME validator (never a lighter check) AND the
CAS at apply time, refusing on any blocking diagnostic (addendum ruling 3). The
confirmed ref feeds 13C's EDITORIAL_CUTDOWN ``cutdown_source.ref`` (an "approved
proposal", a ref type 13C already accepts) — this batch never invents a cut.

RANGE CONTRACT (addendum ruling 2, shared with qc/roughcut.py's ``_complement``).
A cutdown range is a half-open integer-millisecond interval ``[start_ms, end_ms)``
with ``start_ms < end_ms``. Exactly one normalized representation is used across
cutdown and roughcut: every bound is FINITE except an optional open-ended TAIL
whose ``end`` is ``None`` (meaning "to end of media"). An open tail MUST be
resolved against the known media duration before validate/apply completes —
:func:`validate_cutdown` flags a blocking ``OPEN_TAIL_UNRESOLVED`` when a ``None``
end is present without ``media_duration_ms``, and otherwise substitutes
``end = media_duration_ms`` so EVERY range is finite for the subsequent
inverted / out-of-bound / overlap / duplicate / keep-remove-conflict /
no-cut-zone / digest / hash checks. After resolution nothing is open-ended.
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


def _ranges(cutdown: dict, key: str) -> list[tuple[int, int | None]]:
    """Parse a range list per the module RANGE CONTRACT: each entry is
    ``[start_ms, end_ms]`` with an integer start and an integer end OR ``None``
    (the single open-ended tail, resolved later against the media duration)."""
    out: list[tuple[int, int | None]] = []
    for r in cutdown.get(key) or []:
        try:
            a = int(r[0])
            b = None if r[1] is None else int(r[1])
        except (TypeError, ValueError, IndexError):
            raise CutdownError(f"cutdown.{key} entries must be [start_ms, end_ms]")
        out.append((a, b))
    return out


def _resolve_tail(ranges: list[tuple[int, int | None]],
                  media_duration_ms: int | None) -> list[tuple[int, int]]:
    """Resolve the open tail (``end=None``) against the known duration. A ``None``
    end with no duration is dropped here (already flagged ``OPEN_TAIL_UNRESOLVED``
    by the caller); every returned range is finite."""
    res: list[tuple[int, int]] = []
    for a, b in ranges:
        if b is None:
            if media_duration_ms is None:
                continue
            b = int(media_duration_ms)
        res.append((a, b))
    return res


def _overlap_diags(ranges: list[tuple[int, int]], key: str) -> list[dict[str, Any]]:
    """Overlap / exact-duplicate diagnostics within one finite range list."""
    diags: list[dict[str, Any]] = []
    ordered = sorted((a, b) for a, b in ranges if b > a)
    for i in range(len(ordered)):
        a1, b1 = ordered[i]
        for j in range(i + 1, len(ordered)):
            a2, b2 = ordered[j]
            if a2 >= b1:
                break                       # sorted → no later range can overlap
            if a1 == a2 and b1 == b2:
                diags.append({"code": "RANGE_DUPLICATE", "severity": "blocking",
                              "detail": f"{key} range [{a1},{b1}) is duplicated"})
            else:
                diags.append({"code": "RANGE_OVERLAP", "severity": "blocking",
                              "detail": f"{key} ranges [{a1},{b1}) and [{a2},{b2}) "
                                        f"overlap"})
    return diags


def _hashable(x: Any) -> Any:
    return tuple(x) if isinstance(x, list) else x


def validate_cutdown(cutdown: dict[str, Any], *,
                     no_cut_zones: list[dict] | None = None,
                     media_duration_ms: int | None = None,
                     expected_hashes: dict[str, str] | None = None,
                     current_hashes: dict[str, str] | None = None,
                     current_analysis_digest: str | None = None) -> list[dict[str, Any]]:
    """The ONE ZERO-WRITE cutdown validator (contract §6, §11 rows 4-5, addendum
    ruling 3). Returns a list of diagnostics; a ``blocking`` one must stop the
    apply. :func:`apply_cutdown` calls THIS SAME function — there is no separate,
    lighter apply-time check. Checks (all blocking):

    * open tail — a ``None`` end with no ``media_duration_ms`` is
      ``OPEN_TAIL_UNRESOLVED``; otherwise it is resolved to the duration first
      (RANGE CONTRACT) so every check below runs on finite ranges;
    * ``RANGE_INVERTED`` / ``RANGE_OUT_OF_BOUNDS`` — empty/inverted or out-of-bound;
    * ``RANGE_OVERLAP`` / ``RANGE_DUPLICATE`` — overlapping/duplicate keep or remove;
    * ``KEEP_REMOVE_CONFLICT`` — a span both kept and removed;
    * ``REORDER_IDENTITY`` — a reorder plan with duplicate targets;
    * ``CUT_CROSSES_NO_CUT_ZONE`` — a remove that severs a dialogue span or removes
      / severs a key moment (禁止静默切断, addendum ruling 2 & M04);
    * ``ANALYSIS_DIGEST_MISMATCH`` — the cutdown's bound analysis digest no longer
      matches the current analysis (a stale binding, M09);
    * ``MEDIA_HASH_MISMATCH`` — a source whose live hash differs from the proposal's.

    This function writes NOTHING — building and checking a cutdown is inert."""
    diags: list[dict[str, Any]] = []
    keep_raw = _ranges(cutdown, "keep")
    remove_raw = _ranges(cutdown, "remove")

    # RANGE CONTRACT: resolve the open tail against the known duration first.
    if any(b is None for _, b in keep_raw + remove_raw) and media_duration_ms is None:
        diags.append({"code": "OPEN_TAIL_UNRESOLVED", "severity": "blocking",
                      "detail": "an open-ended range (end=None) requires "
                                "media_duration_ms to resolve before validate/apply"})
    keep = _resolve_tail(keep_raw, media_duration_ms)
    remove = _resolve_tail(remove_raw, media_duration_ms)

    for a, b in keep + remove:
        if b <= a:
            diags.append({"code": "RANGE_INVERTED", "severity": "blocking",
                          "detail": f"range [{a},{b}) is empty or inverted"})
        if a < 0 or (media_duration_ms is not None and b > media_duration_ms):
            diags.append({"code": "RANGE_OUT_OF_BOUNDS", "severity": "blocking",
                          "detail": f"range [{a},{b}) exceeds media bounds "
                                    f"[0,{media_duration_ms})"})

    # overlap / duplicate within each list
    diags += _overlap_diags(keep, "keep")
    diags += _overlap_diags(remove, "remove")

    # keep/remove conflict — a span cannot be both kept and removed
    for ka, kb in keep:
        if kb <= ka:
            continue
        for ra, rb in remove:
            if rb > ra and max(ka, ra) < min(kb, rb):
                diags.append({"code": "KEEP_REMOVE_CONFLICT", "severity": "blocking",
                              "detail": f"kept [{ka},{kb}) and removed [{ra},{rb}) "
                                        f"overlap"})

    # reorder identity — the reorder plan must not name a target twice
    reorder = cutdown.get("reorder") or []
    if isinstance(reorder, list) and reorder:
        keys = [_hashable(x) for x in reorder]
        if len(keys) != len(set(keys)):
            diags.append({"code": "REORDER_IDENTITY", "severity": "blocking",
                          "detail": "reorder plan names a target more than once"})

    # no-cut zones: a remove that severs a span, or removes / severs a key moment
    for a, b in remove:
        for z in no_cut_zones or []:
            zs, ze = z.get("start_ms"), z.get("end_ms")
            if zs is None or ze is None:
                continue
            severed = any(zs < boundary < ze for boundary in (a, b))
            point = z.get("point_ms")
            removed_point = point is not None and a <= point < b
            if severed or removed_point:
                where = point if removed_point else next(
                    boundary for boundary in (a, b) if zs < boundary < ze)
                diags.append({
                    "code": "CUT_CROSSES_NO_CUT_ZONE", "severity": "blocking",
                    "detail": f"a cut at {where}ms falls inside a no-cut zone "
                              f"[{zs},{ze}) ({z.get('reason')})",
                    "zone": z})

    # analysis-digest binding — a stale cutdown (analysis moved on) is refused
    if current_analysis_digest is not None:
        bound = cutdown.get("source_analysis_digest")
        if bound != current_analysis_digest:
            diags.append({"code": "ANALYSIS_DIGEST_MISMATCH", "severity": "blocking",
                          "detail": f"cutdown bound analysis {bound!r} but the "
                                    f"current analysis is {current_analysis_digest!r}"})

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
                  proposal_id: str,
                  no_cut_zones: list[dict] | None = None,
                  media_duration_ms: int | None = None,
                  current_analysis_digest: str | None = None) -> dict[str, Any]:
    """CAS + full re-validation at apply (contract §6, §11 row 6, addendum ruling
    3). A confirmed proposal does NOT bypass semantics: apply

    1. re-runs the CAS — every source's live hash must still match what the
       proposal recorded; a mismatch RAISES :class:`StaleCutdownError`;
    2. re-runs THE SAME :func:`validate_cutdown` (no lighter check) and RAISES
       :class:`CutdownError` on ANY blocking diagnostic — inverted / out-of-bound /
       overlap / keep-remove conflict / no-cut-zone (M04, M08) / stale analysis
       digest (M09) / unresolved open tail.

    On success returns the ``cutdown_source`` ref that 13C's EDITORIAL_CUTDOWN
    variant consumes plus, when a duration is known, the ``resolved_keep`` timeline
    (open tail resolved to the real duration) — the cut enters source through the
    existing proposal/roundtrip path, not a re-invention."""
    for src, exp in (expected_hashes or {}).items():
        if current_hashes.get(src) != exp:
            raise StaleCutdownError(
                f"cannot apply cutdown: source {src!r} moved since the proposal "
                f"(expected {exp}, got {current_hashes.get(src)}) — re-derive it")

    diags = validate_cutdown(cutdown, no_cut_zones=no_cut_zones,
                             media_duration_ms=media_duration_ms,
                             current_analysis_digest=current_analysis_digest)
    blocking = sorted({d["code"] for d in diags if d.get("severity") == "blocking"})
    if blocking:
        raise CutdownError("cannot apply cutdown: blocking diagnostics "
                           f"{blocking} — re-derive or resolve the proposal")

    resolved_keep = None
    if media_duration_ms is not None:
        resolved_keep = [[a, b] for a, b in
                         _resolve_tail(_ranges(cutdown, "keep"), media_duration_ms)]
    return {
        "cutdown_source": {
            "ref": f"proposal:{proposal_id}",
            "kind": "approved_proposal",
            "source_analysis_digest": cutdown.get("source_analysis_digest"),
        },
        "applied": True,
        "resolved_keep": resolved_keep,
    }
