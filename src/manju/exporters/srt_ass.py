"""Caption exporters: SRT (external sidecar) and ASS (burn-in styling).

Captions compile straight from ``Timeline.tracks.captions``. All times are
integer milliseconds in the model (§4.4) — fps never enters here. Text is
arbitrary UTF-8 (Chinese punctuation, embedded newlines, commas) so every
line is built field-by-field with the free-text payload kept strictly last.

ASS styling follows §7 step ④: white fill, black outline, bottom-centre
alignment, a MarginV inside the vertical safe area, and a CJK-friendly default
font. The style dict (from ``timeline/rules.yaml`` captions + ``bible/style.yaml``)
may override font / size / margin_v / primary_colour.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.models import Timeline
from ..core.yamlio import atomic_write_text

if TYPE_CHECKING:  # avoid an import cycle at module load; Project only needed for typing
    from ..core.container import Project

__all__ = [
    "ms_to_srt",
    "ms_to_ass",
    "compile_srt",
    "compile_ass",
    "export_captions",
]

DEFAULT_FONT = "Noto Sans CJK SC"
WHITE = "&H00FFFFFF"  # ASS colours are &HAABBGGRR
BLACK = "&H00000000"
SECONDARY = "&H000000FF"


# --------------------------------------------------------------- time codes


def ms_to_srt(ms: int) -> str:
    """Milliseconds -> SRT timestamp ``HH:MM:SS,mmm``."""
    ms = max(0, int(round(ms)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def ms_to_ass(ms: int) -> str:
    """Milliseconds -> ASS timestamp ``H:MM:SS.cc`` (centiseconds)."""
    ms = max(0, int(round(ms)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{milli // 10:02d}"


# ------------------------------------------------------------------- SRT


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def compile_srt(timeline: Timeline) -> str:
    """Numbered, blank-line-separated SRT cues (LF newlines, UTF-8 text)."""
    parts: list[str] = []
    for i, cap in enumerate(timeline.tracks.captions, start=1):
        text = _normalize_newlines(cap.text).strip("\n")
        parts.append(str(i))
        parts.append(f"{ms_to_srt(cap.start_ms)} --> {ms_to_srt(cap.end_ms)}")
        parts.append(text)
        parts.append("")  # blank line terminates the cue
    return "\n".join(parts) + ("\n" if parts else "")


# ------------------------------------------------------------------- ASS


def _resolve_style(style: dict[str, Any] | None, height: int) -> dict[str, Any]:
    style = dict(style or {})
    font = str(style.get("font") or DEFAULT_FONT)
    size = style.get("size")
    size = int(size) if size else max(36, height // 22)
    margin_v = style.get("margin_v")
    margin_v = int(margin_v) if margin_v else max(1, height // 12)  # safe area §7④
    primary = str(style.get("primary_colour") or WHITE)
    return {"font": font, "size": size, "margin_v": margin_v, "primary": primary}


def compile_ass(
    timeline: Timeline,
    *,
    width: int,
    height: int,
    style: dict[str, Any] | None = None,
) -> str:
    """Full ASS document: [Script Info] + one Default [V4+ Styles] + [Events]."""
    st = _resolve_style(style, height)
    h_margin = max(20, width // 20)

    script_info = "\n".join(
        [
            "[Script Info]",
            "; Manju One caption export (§7 step ④ burn-in style)",
            "ScriptType: v4.00+",
            f"PlayResX: {int(width)}",
            f"PlayResY: {int(height)}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
        ]
    )

    style_format = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding"
    )
    # BorderStyle 1 (outline+shadow), Outline=3, Shadow=0, Alignment=2 (bottom-centre).
    style_line = (
        "Style: Default,"
        f"{st['font']},{st['size']},{st['primary']},{SECONDARY},{BLACK},{BLACK},"
        f"0,0,0,0,100,100,0,0,1,3,0,2,{h_margin},{h_margin},{st['margin_v']},1"
    )
    styles = "\n".join(["[V4+ Styles]", style_format, style_line])

    event_format = (
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text"
    )
    event_lines = ["[Events]", event_format]
    for cap in timeline.tracks.captions:
        # Newlines -> hard \\N break; commas in the text are safe here because
        # Text is the final field and never gets split on commas.
        text = _normalize_newlines(cap.text).strip("\n").replace("\n", "\\N")
        prefix = (
            f"Dialogue: 0,{ms_to_ass(cap.start_ms)},{ms_to_ass(cap.end_ms)},"
            "Default,,0,0,0,,"
        )
        event_lines.append(prefix + text)
    events = "\n".join(event_lines)

    return "\n".join([script_info, "", styles, "", events, ""])


# ------------------------------------------------------------ project glue


def _caption_style(project: "Project") -> dict[str, Any]:
    """Assemble the ASS style dict from rules + bible; degrade silently."""
    style: dict[str, Any] = {}
    try:  # bible/style.yaml global style, then caption rules override it
        bible = project.load_bible()
        entry = bible.get("subtitle") or bible.get("style") or {}
        if isinstance(entry, dict):
            for key in ("font", "size", "margin_v", "primary_colour"):
                if entry.get(key) is not None:
                    style[key] = entry[key]
    except Exception:
        pass
    try:
        extra = project.load_rules().captions.model_dump()
        for key in ("font", "size", "margin_v", "primary_colour"):
            if extra.get(key) is not None:
                style[key] = extra[key]
    except Exception:
        pass
    return style


def export_captions(project: "Project", timeline: Timeline) -> dict[str, Path]:
    """Write ``captions/captions.srt`` and ``captions/captions.ass`` atomically.

    Manual takeover (§3, mirroring §6): when ``rules.captions.mode ==
    "manual"`` and a human-edited ``captions.srt`` exists, that SRT is truth —
    the compiler's version goes to ``captions.generated.srt`` for comparison,
    and the burned ASS is recompiled FROM the human cues so the film shows
    exactly what they wrote.

    Returns ``{"srt": <path>, "ass": <path>}`` — the files downstream must use.
    """
    style = _caption_style(project)
    srt_path = project.captions_dir / "captions.srt"
    ass_path = project.captions_dir / "captions.ass"

    if project.load_rules().captions.mode == "manual" and srt_path.exists():
        from ..core.models import CaptionLine, TimelineTracks
        from ..core.models import Timeline as _Timeline
        from ..providers.asr import parse_srt

        atomic_write_text(
            project.captions_dir / "captions.generated.srt", compile_srt(timeline)
        )
        human = parse_srt(srt_path.read_text(encoding="utf-8"))
        human_timeline = _Timeline(
            fps=timeline.fps, width=timeline.width, height=timeline.height,
            duration_ms=timeline.duration_ms,
            tracks=TimelineTracks(captions=[
                CaptionLine(start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
                for s in human
            ]),
        )
        atomic_write_text(
            ass_path,
            compile_ass(human_timeline, width=timeline.width, height=timeline.height,
                        style=style),
        )
        return {"srt": srt_path, "ass": ass_path}

    atomic_write_text(srt_path, compile_srt(timeline))
    atomic_write_text(
        ass_path,
        compile_ass(timeline, width=timeline.width, height=timeline.height, style=style),
    )
    return {"srt": srt_path, "ass": ass_path}
