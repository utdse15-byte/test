"""Cue ↔ shot identity helpers (WP1 interconnection spine).

Caption cues historically carried no shot id; ownership was reconstructed
temporally from the compiled video-clip window (see the original
``media.voicefix._shot_cues``). As of WP1 the compiler stamps
``CaptionLine.shot`` on every generated cue; this module is the single
shared resolver:

1. Prefer the stamped ``shot`` field when present.
2. Fall back to the time-window method for legacy timelines / human SRT
   cues that have no ``shot`` attribute.

Used by voice repair, ``manju impact``, and any future consumer that needs
"which cues belong to this shot".
"""

from __future__ import annotations

from typing import Any

from ..core.models import CaptionLine, Timeline, TimelineRules


def cues_for_shot(
    timeline: Timeline | None,
    shot_id: str,
    *,
    cues: list[dict[str, Any]] | list[CaptionLine] | None = None,
    padding_before_ms: int = 0,
) -> list[tuple[int, CaptionLine]]:
    """Return ``(0-based index, CaptionLine)`` pairs owned by ``shot_id``.

    When ``cues`` is supplied (e.g. parsed human SRT as plain dicts), those
    are used instead of ``timeline.tracks.captions``. Indices are into the
    supplied list / timeline caption track (0-based). Empty when there is
    no timeline and no cue list to place the shot against.
    """
    if cues is None:
        if timeline is None:
            return []
        raw = list(timeline.tracks.captions)
    else:
        raw = list(cues)

    normalized: list[CaptionLine] = []
    for c in raw:
        if isinstance(c, CaptionLine):
            normalized.append(c)
        else:
            normalized.append(CaptionLine(
                start_ms=int(c.get("start_ms", 0)),
                end_ms=int(c.get("end_ms", 0)),
                text=str(c.get("text", "")),
                speaker=str(c.get("speaker", "")),
                shot=str(c.get("shot", "")),
            ))

    # Prefer stamped shot field when ANY cue carries it for this shot
    stamped = [
        (i, c) for i, c in enumerate(normalized)
        if c.shot and c.shot == shot_id
    ]
    if stamped:
        return stamped

    # Temporal fallback: video-clip window contains cue.start_ms
    if timeline is None:
        return []
    clip = next((c for c in timeline.tracks.video if c.shot == shot_id), None)
    if clip is None:
        return []
    win_start = clip.start_ms
    win_end = clip.start_ms + clip.duration_ms
    return [
        (i, c) for i, c in enumerate(normalized)
        if win_start <= c.start_ms < win_end
    ]


def shot_cues_with_anchor(
    timeline: Timeline | None,
    shot_id: str,
    rules: TimelineRules,
    *,
    cues: list[dict[str, Any]] | list[CaptionLine] | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    """Voice-repair shaped view: cue dicts with 1-based ``index`` plus the
    shot's caption anchor (``cap_start`` = clip.start + padding_before).

    Kept as a thin adapter so ``media.voicefix`` can call one helper without
    changing its proportional-retime contract. Returns ``([], None)`` when
    the shot cannot be placed on the timeline.
    """
    if timeline is None:
        return [], None
    clip = next((c for c in timeline.tracks.video if c.shot == shot_id), None)
    if clip is None:
        return [], None
    cap_start = clip.start_ms + rules.timing.padding_before_ms
    pairs = cues_for_shot(timeline, shot_id, cues=cues)
    # 1-based index to match the pre-extraction voicefix contract
    out = [
        {
            "index": i + 1,
            "start_ms": c.start_ms,
            "end_ms": c.end_ms,
            "text": c.text,
            "speaker": c.speaker,
            "shot": c.shot or shot_id,
        }
        for i, c in pairs
    ]
    return out, cap_start
