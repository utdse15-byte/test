"""AI_IDE_19 WP5a — Speech rough cut: annotate-only proposals.

A PURE advisor over AI_IDE_18's word/speaker alignment evidence (contract §8):
it flags filler words (口头禅), long pauses (长停顿), repeated sentences (重复句),
obvious mistakes (明显失误) and, optionally, sensitive ranges — as CANDIDATES.

Two disciplines the contract pins (§8, §11 row 14):

* **Default annotate-only** — nothing is deleted automatically; every finding is
  a mark a human reviews. ``reversible`` is True and the proposal keeps the
  original transcript ref, ranges and reasons, so anything can be restored.
* **Never touches the Timeline** — this module writes nothing and imports no
  timeline writer. When a human chooses to act, :func:`to_cutdown` turns the
  SELECTED annotations into a WP3 cutdown PROPOSAL payload that rides the existing
  Director proposal / roundtrip cut path — the rough cut itself never edits truth.

It consumes the 18 evidence sidecar directly (``header.source_media_hash`` +
``cues[]``); there is no new transcript store.
"""

from __future__ import annotations

from typing import Any

# annotation kinds
FILLER = "filler"
LONG_PAUSE = "long_pause"
REPETITION = "repetition"
MISTAKE = "mistake"
SENSITIVE = "sensitive"

# a small, conservative default filler set (Mandarin + English disfluencies).
_DEFAULT_FILLERS = ("呃", "嗯", "那个", "就是", "um", "uh", "like", "you know")


def _cues(evidence: dict[str, Any]) -> list[dict]:
    cues = evidence.get("cues") if isinstance(evidence, dict) else None
    return [c for c in cues if isinstance(c, dict)] if isinstance(cues, list) else []


def _norm(text: str) -> str:
    return "".join(str(text).split()).strip().lower()


def rough_cut_proposal(evidence: dict[str, Any], *,
                       fillers: list[str] | None = None,
                       long_pause_ms: int = 800,
                       sensitive_terms: list[str] | None = None,
                       mistake_markers: list[str] | None = None) -> dict[str, Any]:
    """Build an annotate-only speech rough-cut proposal from 18 alignment
    evidence. Returns a PROPOSAL PAYLOAD (no schema, no writes): every finding is
    a candidate annotation carrying its original range + reason + cue index."""
    cues = _cues(evidence)
    fillers = [str(f) for f in (fillers if fillers is not None else _DEFAULT_FILLERS)]
    filler_set = {_norm(f) for f in fillers}
    sensitive = [str(s) for s in (sensitive_terms or [])]
    markers = [str(m) for m in (mistake_markers or [])]

    annotations: list[dict[str, Any]] = []

    def add(kind: str, cue: dict, idx: int, reason: str) -> None:
        annotations.append({
            "kind": kind,
            "start_ms": int(cue.get("start_ms", 0) or 0),
            "end_ms": int(cue.get("end_ms", 0) or 0),
            "text": str(cue.get("text") or ""),
            "speaker": cue.get("speaker"),
            "reason": reason,
            "cue_index": idx,
            "action": "annotate",   # never a delete — a human decides
        })

    prev_text: str | None = None
    prev_end: int | None = None
    for i, cue in enumerate(cues):
        text = str(cue.get("text") or "")
        norm = _norm(text)
        start = int(cue.get("start_ms", 0) or 0)
        # long pause BEFORE this cue
        if prev_end is not None and (start - prev_end) >= int(long_pause_ms):
            annotations.append({
                "kind": LONG_PAUSE, "start_ms": prev_end, "end_ms": start,
                "text": "", "speaker": cue.get("speaker"),
                "reason": f"{start - prev_end}ms 停顿(≥{long_pause_ms}ms)",
                "cue_index": i, "action": "annotate"})
        if norm and norm in filler_set:
            add(FILLER, cue, i, f"口头禅/filler: {text!r}")
        if prev_text is not None and norm and norm == prev_text:
            add(REPETITION, cue, i, f"重复句/repetition of {text!r}")
        for term in sensitive:
            if term and term in text:
                add(SENSITIVE, cue, i, f"敏感词/sensitive: {term!r}")
        for mk in markers:
            if mk and mk in text:
                add(MISTAKE, cue, i, f"明显失误/mistake marker: {mk!r}")
        prev_text = norm
        prev_end = int(cue.get("end_ms", 0) or 0)

    header = evidence.get("header") or {}
    return {
        "kind": "speech_rough_cut",
        "default_action": "annotate",       # §8: only annotate by default
        "reversible": True,
        "source_transcript_ref": header.get("source_media_hash"),
        "annotations": annotations,
    }


def to_cutdown(proposal: dict[str, Any], *, select: list[int],
               source_analysis_digest: str) -> dict[str, Any]:
    """Turn the SELECTED annotations into a WP3 cutdown proposal payload (the
    explicit human action). The rough cut never edits the Timeline — acting on it
    routes through the cut path. Unselected findings stay as marks (reversible).

    The selected spans are MERGED deterministically (addendum ruling 4, M06):
    overlapping / adjacent removes collapse into a sorted, non-overlapping remove
    list BEFORE the complement is taken, so the payload is order-independent and
    the keep/remove pair is internally consistent for the validator."""
    from ..build.segments import build_cutdown

    anns = proposal.get("annotations") or []
    raw = [[anns[i]["start_ms"], anns[i]["end_ms"]] for i in select
           if 0 <= i < len(anns)]
    remove = _merge_ranges(raw)
    reasons = "; ".join(anns[i]["reason"] for i in select if 0 <= i < len(anns))
    # keep = the complementary spans (the cut engine materialises them); the
    # diffable intent is the explicit merged remove list plus the keep gaps.
    keep = _complement(remove)
    return build_cutdown(keep=keep, remove=remove,
                         reason=f"speech rough cut: {reasons}",
                         source_analysis_digest=source_analysis_digest)


def _merge_ranges(ranges: list[list[int]]) -> list[list[int]]:
    """Sort and merge overlapping / adjacent ``[start, end)`` spans into a
    deterministic, non-overlapping list (addendum ruling 4). Inverted / empty
    spans (end <= start) are dropped. The result is order-independent."""
    norm = sorted((int(a), int(b)) for a, b in ranges if int(b) > int(a))
    merged: list[list[int]] = []
    for a, b in norm:
        if merged and a <= merged[-1][1]:      # overlap OR adjacency (a == prev end)
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _complement(remove: list[list[int]]) -> list[list[int]]:
    """Keep spans = the gaps between removed spans (from 0), honoring the shared
    RANGE CONTRACT (build/segments.py): finite gaps plus a single open-ended tail
    ``[cursor, None]`` resolved against the media duration at validate/apply.
    ``remove`` is expected already merged (see :func:`_merge_ranges`); it is
    re-sorted here for safety."""
    if not remove:
        return [[0, None]]
    ordered = sorted(remove)
    keep: list[list[int]] = []
    cursor = 0
    for a, b in ordered:
        if a > cursor:
            keep.append([cursor, a])
        cursor = max(cursor, b)
    keep.append([cursor, None])   # tail to end-of-media (resolved at apply)
    return keep
