"""Subtitle-editor glue (round-T): read/edit/validate/serialize caption cues.

The GUI /subtitles page drives the §3 captions truth model WITHOUT opening any
external tool:

  * compiled mode — cues come from dialogue and ``captions/captions.srt`` is an
    OUTPUT of the build; the editor reads it (or the compiled timeline) for a
    starting point;
  * manual mode (``rules.captions.mode: manual``) — ``captions.srt`` is human
    truth: the compiler's version is kept in ``captions.generated.srt`` for
    comparison and the burned ASS is recompiled FROM the human cues verbatim
    (exporters/srt_ass.export_captions).

This module is pure and side-effect free apart from the explicit save/revert
helpers; every write goes through the project's atomic paths and the server
records the event. Nothing here parses rules.yaml by hand — it reads cues via
providers/asr.parse_srt and writes SRT via exporters/srt_ass.ms_to_srt so the
editor and the burner agree on the wire format byte-for-byte.
"""

from __future__ import annotations

from typing import Any

from ..core.container import Project
from ..exporters.srt_ass import ms_to_srt
from ..providers.asr import parse_srt

# QC caption-overlap tolerance (qc/checks._CAPTION_OVERLAP_TOL_MS): a bleed up to
# this is tolerated; beyond it two cues collide on one line and we warn inline.
CAPTION_OVERLAP_TOL_MS = 120


class CaptionEditError(ValueError):
    """A cue edit failed validation (empty text / inverted window / bad split).
    Carries a human-readable reason; nothing is written when raised."""


# ------------------------------------------------------------------ read side


def _timeline_cues(project: Project) -> list[dict[str, Any]]:
    tl = project.load_timeline()
    if tl is None:
        return []
    return [
        {"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text,
         "speaker": c.speaker or ""}
        for c in tl.tracks.captions
    ]


def _srt_cues(text: str, *, speakers: list[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(parse_srt(text)):
        out.append({
            "start_ms": seg.start_ms, "end_ms": seg.end_ms, "text": seg.text,
            # SRT carries no speaker — borrow the compiled speaker by position
            # (display-only reference; never persisted back into the SRT).
            "speaker": speakers[i] if speakers and i < len(speakers) else "",
        })
    return out


def read_current_cues(project: Project) -> dict[str, Any]:
    """The cue table the /subtitles page renders, plus the caption mode.

    Source of truth for the display: the on-disk ``captions.srt`` (what actually
    burns today) when present, else the compiled timeline's caption track, else
    empty with a note. ``mode`` is ``rules.captions.mode``; ``generated`` is
    whether a compiled-comparison SRT exists (manual mode)."""
    try:
        mode = project.load_rules().captions.mode
    except Exception:
        mode = "compiled"
    srt_path = project.captions_dir / "captions.srt"
    generated = (project.captions_dir / "captions.generated.srt").exists()
    speakers = [c["speaker"] for c in _timeline_cues(project)]
    if srt_path.exists():
        cues = _srt_cues(srt_path.read_text(encoding="utf-8"), speakers=speakers)
        source = "captions.srt"
    else:
        cues = _timeline_cues(project)
        source = "timeline" if cues else "none"
    for i, c in enumerate(cues, start=1):
        c["index"] = i
    return {"cues": cues, "mode": mode, "source": source, "generated": generated}


# ---------------------------------------------------------------- normalize


def _one_cue(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CaptionEditError("each cue must be an object")
    try:
        start = int(raw.get("start_ms"))
        end = int(raw.get("end_ms"))
    except (TypeError, ValueError):
        raise CaptionEditError("cue start_ms/end_ms must be integers (ms)")
    text = str(raw.get("text") if raw.get("text") is not None else "")
    return {"start_ms": start, "end_ms": end, "text": text,
            "speaker": str(raw.get("speaker") or "")}


def normalize_cues(payload: Any) -> list[dict[str, Any]]:
    """Coerce a client cue payload into a clean, start-sorted cue list.
    Shape/type errors raise :class:`CaptionEditError`; semantic checks
    (empty/inverted/overlap) are :func:`validate_cues`'s job."""
    if not isinstance(payload, list):
        raise CaptionEditError("cues must be a list")
    cues = [_one_cue(c) for c in payload]
    cues.sort(key=lambda c: (c["start_ms"], c["end_ms"]))
    return cues


def validate_cues(cues: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a normalized cue list.

    Hard errors block the save: an empty cue, a negative start, or a
    zero/negative window (parse_srt would silently drop it). Warnings are
    surfaced inline but never block: an overlap beyond the QC tolerance."""
    errors: list[str] = []
    warnings: list[str] = []
    for i, c in enumerate(cues, start=1):
        if not c["text"].strip():
            errors.append(f"字幕 #{i} 文本为空 (empty cue text)")
        if c["start_ms"] < 0:
            errors.append(f"字幕 #{i} 起点为负 (negative start {c['start_ms']}ms)")
        if c["end_ms"] <= c["start_ms"]:
            errors.append(
                f"字幕 #{i} 时间窗无效 (end {c['end_ms']}ms ≤ start {c['start_ms']}ms)")
    for i in range(len(cues) - 1):
        overlap = cues[i]["end_ms"] - cues[i + 1]["start_ms"]
        if overlap > CAPTION_OVERLAP_TOL_MS:
            warnings.append(
                f"字幕 #{i + 1} 与 #{i + 2} 重叠 {overlap}ms "
                f"(超出 {CAPTION_OVERLAP_TOL_MS}ms 容差)")
    return errors, warnings


def cues_to_srt(cues: list[dict[str, Any]]) -> str:
    """Serialize cues to SRT — the human-truth wire format. Uses the burner's
    own :func:`ms_to_srt`, and NEVER re-wraps text (§3: human cues are verbatim)."""
    parts: list[str] = []
    for i, c in enumerate(cues, start=1):
        text = str(c["text"]).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        parts.append(str(i))
        parts.append(f"{ms_to_srt(c['start_ms'])} --> {ms_to_srt(c['end_ms'])}")
        parts.append(text)
        parts.append("")
    return "\n".join(parts) + ("\n" if parts else "")


# ---------------------------------------------------------------- split/merge


def split_cue(cues: list[dict[str, Any]], index: int, at_ms: int) -> list[dict[str, Any]]:
    """Divide the cue at ``index`` at the timestamp ``at_ms`` into two cues.

    The window splits at ``at_ms`` (which must sit strictly inside the cue); the
    text splits proportionally by character offset so the halves stay editable.
    Returns a NEW list (pure)."""
    if not (0 <= index < len(cues)):
        raise CaptionEditError(f"no cue at index {index}")
    c = cues[index]
    if not (c["start_ms"] < int(at_ms) < c["end_ms"]):
        raise CaptionEditError(
            f"split point {at_ms}ms must fall inside the cue "
            f"({c['start_ms']}–{c['end_ms']}ms)")
    span = c["end_ms"] - c["start_ms"]
    frac = (int(at_ms) - c["start_ms"]) / span
    text = str(c["text"])
    cut = round(len(text) * frac)
    first = {"start_ms": c["start_ms"], "end_ms": int(at_ms),
             "text": text[:cut].strip(), "speaker": c.get("speaker", "")}
    second = {"start_ms": int(at_ms), "end_ms": c["end_ms"],
              "text": text[cut:].strip(), "speaker": c.get("speaker", "")}
    return cues[:index] + [first, second] + cues[index + 1:]


def merge_cues(cues: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    """Merge the cue at ``index`` with the one after it into a single cue
    spanning both windows (text concatenated). Returns a NEW list (pure)."""
    if not (0 <= index < len(cues) - 1):
        raise CaptionEditError(f"no adjacent cue to merge after index {index}")
    a, b = cues[index], cues[index + 1]
    merged = {
        "start_ms": min(a["start_ms"], b["start_ms"]),
        "end_ms": max(a["end_ms"], b["end_ms"]),
        "text": (a["text"].strip() + b["text"].strip()),
        "speaker": a.get("speaker", "") or b.get("speaker", ""),
    }
    return cues[:index] + [merged] + cues[index + 2:]
