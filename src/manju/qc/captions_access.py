"""Caption readability / accessibility advisories (roadmap §5.5) — record + advise, NEVER block.

``manju.caption-accessibility/v1`` is a DERIVED, DELETABLE projection over the
caption cue set (the same truth ``compile_srt``/``compile_ass`` render):
per-cue reading speed and line geometry, timing-window advisories, and role
coverage. It follows the ``media/technical_profile.py`` stance exactly:

* **Advisory only.** Every row carries ``severity: "advisory"``. Nothing in
  this module raises, gates, or feeds ``QCReport``/`manju check`/build/export —
  a human or a platform profile decides what any advisory means. (The one
  check-time surface for captions stays ``qc/checks.py:_technical_captions``,
  which this loop extends with the unknown-role WARN; that warn never flips
  ``QCReport.ok`` either.)
* **CJK-aware counting (the documented rule).** Character weight comes from
  Unicode East Asian Width (UAX #11): ``W`` (CJK ideographs, kana, hangul,
  ideographic punctuation) and ``F`` (fullwidth forms) count **2**; newlines
  count **0** (a break is not read); everything else — including spaces,
  halfwidth (``H``), narrow (``Na``/``N``) and *ambiguous* (``A``) characters —
  counts **1**. CPS = weighted chars x 1000 / cue duration ms, so "你好世界"
  over 2000 ms is exactly 4.0, "hello" over 1000 ms exactly 5.0.
* **Measures what renders.** In compiled mode the same ``break_lines`` budget
  the SRT/ASS writers apply is applied before lines are measured; in manual
  mode the human cues are measured verbatim (never re-broken, §3).
* **Reproducible + content-addressed.** No wall clock anywhere; the
  ``report_digest`` hashes the whole semantic body, and ``write_accessibility``
  stores the document under ``reports/captions/<digest-hex>.caption-accessibility.json``.
  Deleting the file loses the report, never any truth.

The default thresholds are a starting point (roughly the strict end of common
platform guidance: Netflix-style ~20 weighted CPS ≈ 10 full-width chars/sec,
42-weighted-char lines ≈ 21 full-width chars, 5/6 s floor, 7 s ceiling,
2-frame minimum gap at 24 fps). They are keyword knobs precisely so a platform
profile can re-derive the report with its own numbers.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from statistics import median
from typing import Any

from ..core.hashing import hash_value
from ..core.models import CAPTION_ROLES, CaptionLine, Timeline
from ..core.yamlio import atomic_write_text
from ..exporters.srt_ass import break_lines

__all__ = [
    "SCHEMA",
    "char_weight",
    "weighted_len",
    "cue_metrics",
    "captions_accessibility",
    "accessibility_for_project",
    "write_accessibility",
]

SCHEMA = "manju.caption-accessibility/v1"

# Advisory thresholds — knobs, not policy (docstring). All overridable per call.
DEFAULT_CPS_CEILING = 20.0          # weighted chars/sec (~10 full-width/sec)
DEFAULT_LINE_WEIGHT_CEILING = 42    # weighted chars per rendered line
DEFAULT_MAX_LINES = 2               # rendered lines per cue
DEFAULT_DURATION_FLOOR_MS = 833     # ~5/6 second on screen
DEFAULT_DURATION_CEILING_MS = 7000  # 7 seconds
DEFAULT_MIN_GAP_MS = 83             # 2 frames @ 24 fps

_CPS_BUCKETS = ((5.0, "<=5"), (10.0, "5-10"), (15.0, "10-15"),
                (20.0, "15-20"), (25.0, "20-25"))


# ------------------------------------------------------------ CJK-aware counts


def char_weight(ch: str) -> int:
    """UAX #11 weight: W/F -> 2, newline -> 0, everything else (incl. A) -> 1."""
    if ch in ("\n", "\r"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def weighted_len(text: str) -> int:
    return sum(char_weight(ch) for ch in text)


def cue_metrics(cue: CaptionLine, *, max_chars_per_line: int | None = None) -> dict[str, Any]:
    """Per-cue readability facts. ``max_chars_per_line`` applies the SAME
    ``break_lines`` budget the compiled-mode exporters apply, so line counts and
    weights describe what actually renders; ``None`` (manual mode) measures the
    author's own breaks verbatim."""
    text = (cue.text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    rendered = break_lines(text, max_chars_per_line)
    lines = rendered.split("\n") if rendered else []
    line_weights = [weighted_len(line) for line in lines]
    weighted = sum(line_weights)
    duration_ms = int(cue.end_ms) - int(cue.start_ms)
    cps: float | None = None
    if duration_ms > 0:
        cps = round(weighted * 1000 / duration_ms, 2)
    return {
        "start_ms": int(cue.start_ms),
        "end_ms": int(cue.end_ms),
        "duration_ms": duration_ms,
        "role": getattr(cue, "role", None),
        # the MEASURED form (post-break in compiled mode) — it is what
        # lines/line_weights describe, it makes advisory rows self-explanatory
        # for a human, and it binds report_digest to the actual content (two
        # cue sets with identical geometry but different words must differ).
        "text": rendered,
        "weighted_chars": weighted,
        "cps": cps,
        "lines": len(lines),
        "line_weights": line_weights,
    }


# ----------------------------------------------------------------- the report


def captions_accessibility(
    timeline: Timeline | None,
    *,
    cues: list[CaptionLine] | None = None,
    cue_source: str = "timeline",
    max_chars_per_line: int | None = None,
    cps_ceiling: float = DEFAULT_CPS_CEILING,
    line_weight_ceiling: int = DEFAULT_LINE_WEIGHT_CEILING,
    max_lines: int = DEFAULT_MAX_LINES,
    duration_floor_ms: int = DEFAULT_DURATION_FLOOR_MS,
    duration_ceiling_ms: int = DEFAULT_DURATION_CEILING_MS,
    min_gap_ms: int = DEFAULT_MIN_GAP_MS,
) -> dict[str, Any]:
    """PURE: cue set -> the ``manju.caption-accessibility/v1`` document.

    Every advisory row is ``severity: "advisory"`` by construction — the
    document never carries another severity and never feeds a gate.
    """
    if cues is None:
        cues = list(timeline.tracks.captions) if timeline is not None else []

    metrics = [
        {"index": i, **cue_metrics(c, max_chars_per_line=max_chars_per_line)}
        for i, c in enumerate(cues)
    ]

    advisories: list[dict[str, Any]] = []

    def advise(code: str, subject: str, detail: str, **extra: Any) -> None:
        advisories.append({"code": code, "severity": "advisory",
                           "subject": subject, "detail": detail, **extra})

    # ---- per-cue rows
    for m in metrics:
        subject = f"cue #{m['index']}"
        if m["cps"] is not None and m["cps"] > cps_ceiling:
            advise("CPS_HIGH", subject,
                   f"reading speed {m['cps']} weighted chars/sec is over the "
                   f"{cps_ceiling} advisory ceiling(CJK 记 2:约 {cps_ceiling / 2:g} 个全角字/秒)",
                   cps=m["cps"], ceiling=cps_ceiling)
        for li, lw in enumerate(m["line_weights"]):
            if lw > line_weight_ceiling:
                advise("LINE_TOO_LONG", subject,
                       f"line {li + 1} is {lw} weighted chars (ceiling "
                       f"{line_weight_ceiling}; CJK counts 2)",
                       line=li + 1, line_weight=lw, ceiling=line_weight_ceiling)
        if m["lines"] > max_lines:
            advise("TOO_MANY_LINES", subject,
                   f"{m['lines']} rendered lines (advisory ceiling {max_lines})",
                   lines=m["lines"], ceiling=max_lines)
        if m["duration_ms"] < duration_floor_ms:
            advise("DURATION_UNDER_FLOOR", subject,
                   f"on screen {m['duration_ms']}ms, under the "
                   f"{duration_floor_ms}ms advisory floor",
                   duration_ms=m["duration_ms"], floor_ms=duration_floor_ms)
        elif m["duration_ms"] > duration_ceiling_ms:
            advise("DURATION_OVER_CEILING", subject,
                   f"on screen {m['duration_ms']}ms, over the "
                   f"{duration_ceiling_ms}ms advisory ceiling",
                   duration_ms=m["duration_ms"], ceiling_ms=duration_ceiling_ms)
        role = m["role"]
        if role is not None and role not in CAPTION_ROLES:
            advise("ROLE_UNKNOWN", subject,
                   f"role “{role}” is outside the vocabulary "
                   f"({'|'.join(CAPTION_ROLES)}) — recorded verbatim, advisory only",
                   role=role)

    # ---- adjacent-window rows (same ordering stance as qc/checks.py)
    ordered = sorted(metrics, key=lambda m: (m["start_ms"], m["end_ms"]))
    for prev, cur in zip(ordered, ordered[1:]):
        gap = cur["start_ms"] - prev["end_ms"]
        pair = f"cue #{prev['index']}/#{cur['index']}"
        if gap < 0:
            advise("CUE_OVERLAP", pair,
                   f"windows overlap by {-gap}ms "
                   f"(#{prev['index']} ends {prev['end_ms']}ms, "
                   f"#{cur['index']} starts {cur['start_ms']}ms)",
                   overlap_ms=-gap)
        elif 0 < gap < min_gap_ms:
            advise("GAP_TOO_SHORT", pair,
                   f"only {gap}ms between cues (advisory minimum {min_gap_ms}ms; "
                   "butt-joined 0ms is deliberate and not flagged)",
                   gap_ms=gap)

    # ---- forced cues sharing the screen with non-forced cues (advisory)
    forced = [m for m in metrics if m["role"] == "forced"]
    others = [m for m in metrics if m["role"] != "forced"]
    for f in forced:
        for o in others:
            overlap = min(f["end_ms"], o["end_ms"]) - max(f["start_ms"], o["start_ms"])
            if overlap > 0:
                advise("FORCED_OVERLAPS_NONFORCED",
                       f"cue #{f['index']}/#{o['index']}",
                       f"forced cue #{f['index']} shares {overlap}ms of screen "
                       f"time with non-forced cue #{o['index']} — most platforms "
                       "render forced narratives only when subtitles are OFF; "
                       "a human/platform profile decides",
                       overlap_ms=overlap)

    body = {
        "schema": SCHEMA,
        "header": {
            "cue_source": cue_source,
            "cue_count": len(metrics),
            "max_chars_per_line": max_chars_per_line,
            "counting_rule": "UAX#11 East Asian Width: W/F=2, newline=0, else 1 (incl. A)",
            "thresholds": {
                "cps_ceiling": cps_ceiling,
                "line_weight_ceiling": line_weight_ceiling,
                "max_lines": max_lines,
                "duration_floor_ms": duration_floor_ms,
                "duration_ceiling_ms": duration_ceiling_ms,
                "min_gap_ms": min_gap_ms,
            },
        },
        "cues": metrics,
        "advisories": advisories,
        "summary": _summary(metrics, advisories, cps_ceiling),
    }
    return {**body, "report_digest": hash_value(body)}


def _summary(metrics: list[dict], advisories: list[dict], cps_ceiling: float) -> dict[str, Any]:
    speeds = sorted(m["cps"] for m in metrics if m["cps"] is not None)
    buckets = {label: 0 for _, label in _CPS_BUCKETS}
    buckets[">25"] = 0
    for s in speeds:
        for edge, label in _CPS_BUCKETS:
            if s <= edge:
                buckets[label] += 1
                break
        else:
            buckets[">25"] += 1
    reading_speed: dict[str, Any] = {
        "measured_cues": len(speeds),
        "min": speeds[0] if speeds else None,
        "max": speeds[-1] if speeds else None,
        "mean": round(sum(speeds) / len(speeds), 2) if speeds else None,
        "median": round(median(speeds), 2) if speeds else None,
        "over_ceiling": sum(1 for s in speeds if s > cps_ceiling),
        "buckets": buckets,
    }

    role_counts: dict[str, int] = {}
    unroled = 0
    for m in metrics:
        if m["role"] is None:
            unroled += 1
        else:
            role_counts[m["role"]] = role_counts.get(m["role"], 0) + 1
    role_coverage = {
        "counts": dict(sorted(role_counts.items())),
        "unroled": unroled,
        "unknown_roles": sorted(r for r in role_counts if r not in CAPTION_ROLES),
    }

    by_code: dict[str, int] = {}
    for a in advisories:
        by_code[a["code"]] = by_code.get(a["code"], 0) + 1
    return {
        "reading_speed": reading_speed,
        "role_coverage": role_coverage,
        "totals": {"cues": len(metrics), "advisories": len(advisories),
                   "by_code": dict(sorted(by_code.items()))},
    }


# ------------------------------------------------------------ project glue


def accessibility_for_project(project: Any) -> dict[str, Any]:
    """Derive the report from the SAME truth ``export_captions`` renders:
    compiled mode analyses ``timeline.tracks.captions`` broken at the declared
    budget; manual takeover (rules.captions.mode == "manual" + a human
    ``captions.srt``) analyses the human cues verbatim — SRT carries no role
    field, so role coverage is honestly empty there. Degrades to an empty cue
    set (never a crash) when no timeline exists yet."""
    rules = None
    try:
        rules = project.load_rules().captions
    except Exception:
        pass

    srt_path = project.captions_dir / "captions.srt"
    if rules is not None and rules.mode == "manual" and srt_path.exists():
        from ..providers.asr import parse_srt

        # errors="replace": a human-authored SRT may be saved non-UTF-8 (GBK on
        # a Windows box); undecodable bytes become U+FFFD rather than crashing
        # the report generator (this function must never raise; CLAUDE.md mandate).
        human = parse_srt(srt_path.read_text(encoding="utf-8", errors="replace"))
        cues = [CaptionLine(start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
                for s in human]
        return captions_accessibility(
            None, cues=cues, cue_source="captions/captions.srt (manual — no role field in SRT)",
            max_chars_per_line=None)  # human cues are never re-broken (§3)

    timeline = None
    try:
        timeline = project.load_timeline()
    except Exception:
        timeline = None
    return captions_accessibility(
        timeline,
        cue_source="timeline" if timeline is not None else "none (no compiled timeline)",
        max_chars_per_line=getattr(rules, "max_chars_per_line", None))


def write_accessibility(project: Any, doc: dict[str, Any]) -> Path:
    """Materialise the report content-addressed by its own digest. Atomic;
    deletable; never read back by build/export/check (derived projection)."""
    hexpart = str(doc.get("report_digest", "")).split(":", 1)[-1]
    if not hexpart:
        raise ValueError("cannot store a caption accessibility report with no digest")
    path = project.reports_dir / "captions" / f"{hexpart}.caption-accessibility.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return path
