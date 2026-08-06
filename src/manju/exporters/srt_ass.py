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

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.models import Timeline
from ..core.yamlio import atomic_write_text

if TYPE_CHECKING:  # avoid an import cycle at module load; Project only needed for typing
    from ..core.container import Project

__all__ = [
    "ms_to_srt",
    "ms_to_ass",
    "ms_to_vtt",
    "escape_ass_text",
    "compile_srt",
    "compile_ass",
    "compile_vtt",
    "compile_project_ass",
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


def ms_to_vtt(ms: int) -> str:
    """Milliseconds -> WebVTT timestamp ``HH:MM:SS.mmm`` (dot before ms, per the
    W3C WebVTT grammar — the one field that differs from SRT's comma)."""
    ms = max(0, int(round(ms)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{milli:03d}"


# ------------------------------------------------------------------- SRT


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _collapse_blank_lines(text: str) -> str:
    """An INTERIOR blank line in cue text would structurally terminate the
    SRT/VTT cue mid-text — every line after it reads as stray cue garbage in a
    real player/NLE. Collapse runs of newlines to one; single-line and normal
    multi-line cues are byte-identical."""
    return re.sub(r"\n[ \t]*\n+", "\n", text)


# ---------------------------------------------------------- ASS injection guard
#
# ASS override blocks (``{\pos(0,0)}``, ``{\alpha&HFF&}``, ...) and the three
# hard-coded control codes libass recognizes even OUTSIDE braces (``\N`` hard
# break, ``\n`` soft break, ``\h`` hard space) are parsed straight out of the
# Dialogue Text field — there is no backslash-escape for them in the ASS spec
# itself (a literal ``\{`` is not an escape; libass still sees the ``{`` and
# opens an override block, it just also renders a stray backslash next to it).
# The one neutralization every ASS/libass-based renderer actually respects is
# swapping the ASCII trigger characters for full-width lookalikes — visually
# almost identical, never parsed as control syntax. This is intentionally a
# DIFFERENT choice from SRT: SRT has no override-tag grammar to defeat, so
# ``compile_srt`` never touches the text.
_ASS_INJECTION_MAP = str.maketrans({
    "{": "｛",   # fullwidth left curly bracket — defeats override-block open
    "}": "｝",   # fullwidth right curly bracket — defeats override-block close
    "\\": "＼",  # fullwidth backslash — defeats bare \N/\n/\h control codes
})


def escape_ass_text(text: str) -> str:
    """Neutralize ASS override-tag syntax in free text before it is burned into
    a Dialogue line (round-W #31). Applied to the CUE'S OWN text only — never
    to the ``\\N`` breaks :func:`compile_ass` inserts itself for line-wrapping,
    which are real control codes and must stay literal backslash-N."""
    return text.translate(_ASS_INJECTION_MAP)


# Break preferentially after these (CJK + ASCII clause enders and space).
_BREAK_AFTER = ",。!?、;:…,.!?;: "


def break_lines(text: str, max_chars: int | None) -> str:
    """Insert hard line breaks so no line exceeds ``max_chars`` — the same
    per-line budget the compiler split cues by, now made real on screen
    (round-N review: the budget used to rely on the renderer's auto-wrap).

    Pure and lossless: no character is dropped. Text that already contains a
    newline is the author's own breaking — respected verbatim, never re-broken
    (the manual-captions path counts on this). Breaks prefer to land after
    punctuation or a space in the tail 40% of the window; otherwise the cut is
    hard at the budget."""
    if not max_chars or int(max_chars) <= 0 or "\n" in text:
        return text
    budget = int(max_chars)
    lines: list[str] = []
    rest = text
    while len(rest) > budget:
        window = rest[:budget]
        floor = max(1, int(budget * 0.6))
        cut = budget  # default: hard cut at the budget
        for i in range(budget - 1, floor - 1, -1):
            if window[i] in _BREAK_AFTER:
                cut = i + 1  # break AFTER the punctuation/space
                break
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip(" ")
    if rest:
        lines.append(rest)
    return "\n".join(lines)


def compile_srt(timeline: Timeline, *, max_chars_per_line: int | None = None) -> str:
    """Numbered, blank-line-separated SRT cues (LF newlines, UTF-8 text).
    ``max_chars_per_line`` breaks long cues at the declared budget (compiled
    mode only — callers re-emitting human cues pass None).

    SRT has NO role field (FP loop I, §5.5): a cue's optional ``role`` stays
    truth-side in ``timeline.json`` and the output bytes are identical with or
    without roles (pinned). The ASS writer's Name field is where a role lands.
    """
    parts: list[str] = []
    for i, cap in enumerate(timeline.tracks.captions, start=1):
        text = _collapse_blank_lines(_normalize_newlines(cap.text).strip("\n"))
        text = break_lines(text, max_chars_per_line)
        parts.append(str(i))
        parts.append(f"{ms_to_srt(cap.start_ms)} --> {ms_to_srt(cap.end_ms)}")
        parts.append(text)
        parts.append("")  # blank line terminates the cue
    return "\n".join(parts) + ("\n" if parts else "")


# ------------------------------------------------------------------- WebVTT


def compile_vtt(timeline: Timeline, *, max_chars_per_line: int | None = None) -> str:
    """A real WebVTT document (contract §10 / 13C ``CAPTIONS_VTT`` — the role
    13C declared but SKIPPED as "no such artifact exists"). Compiled from the
    SAME ``Timeline.tracks.captions`` as SRT/ASS, so all three agree cue-for-cue.

    Cue-level only: :class:`CaptionLine` carries no per-word timing, so inline
    karaoke ``<hh:mm:ss.mmm>`` tokens are honestly NOT emitted (that would be
    fabricated word boundaries — contract §5 UNALIGNED discipline). The header is
    the required ``WEBVTT`` signature; text is arbitrary UTF-8 (CJK safe)."""
    parts = ["WEBVTT", ""]
    for i, cap in enumerate(timeline.tracks.captions, start=1):
        text = _collapse_blank_lines(_normalize_newlines(cap.text).strip("\n"))
        text = break_lines(text, max_chars_per_line)
        # WebVTT cue text has grammar: a bare ``<`` opens a cue-span tag and a
        # bare ``&`` opens a character reference. Unescaped, a caption like
        # ``价格 < 100元`` renders as ``价格 `` — the ``<`` eats the rest of the
        # line. Escape the two significant characters (``&`` first, so we never
        # double-escape); ``>`` is legal literal text and is left untouched.
        # Captions with neither character (the common CJK/plain case, and every
        # existing fixture) are byte-identical. Parallels the TTML sibling's
        # ``xml.sax.saxutils.escape`` (SRT deliberately does NOT escape — it has
        # no override grammar, see compile_srt).
        text = text.replace("&", "&amp;").replace("<", "&lt;")
        parts.append(str(i))
        parts.append(f"{ms_to_vtt(cap.start_ms)} --> {ms_to_vtt(cap.end_ms)}")
        parts.append(text)
        parts.append("")
    return "\n".join(parts) + ("\n" if timeline.tracks.captions else "")


# ------------------------------------------------------------------- ASS


def _ass_name_field(cap: Any) -> str:
    """The Dialogue *Name* field (FP loop I, §5.5): the cue's optional role
    VERBATIM when set (``translation|sdh|forced|lyrics|speaker_label``), else
    the historical empty field — a role-less timeline renders BYTE-IDENTICAL
    ASS (pinned). Name sits mid field-grid, so the two characters that could
    shift the grid are swapped for lookalikes (comma → fullwidth comma,
    newline → space) and override syntax is neutralized exactly like cue text
    (round-W #31 stance) — a hostile role can never break the Dialogue line."""
    role = getattr(cap, "role", None)
    if not role:
        return ""
    safe = str(role).replace("\r", " ").replace("\n", " ").replace(",", "，")
    return escape_ass_text(safe)


def _style_int(value: Any, field: str) -> int:
    """A hand-edited style value must fail as ONE clean line naming the field
    (bible/style.yaml is human truth — `size: 大` is a plausible edit), not a
    raw ValueError traceback out of caption export."""
    from ..core.container import ProjectError

    try:
        return int(value)
    except (TypeError, ValueError):
        raise ProjectError(
            f"字幕样式 {field} 不是整数(得到 {value!r})——"
            "检查 bible/style.yaml / timeline/rules.yaml 的 captions 配置"
        ) from None


def _resolve_style(
    style: dict[str, Any] | None, width: int, height: int
) -> dict[str, Any]:
    style = dict(style or {})
    font = str(style.get("font") or DEFAULT_FONT)
    size = style.get("size")
    if size:
        size = _style_int(size, "size")  # an explicit size is user truth — never overridden
    else:
        size = max(36, height // 22)
        # The declared chars-per-line must actually FIT one rendered line —
        # before round N the height-only size made rules.captions
        # max_chars_per_line inert (a full-budget CJK cue wrapped to twice
        # the promised lines). Fit against the horizontally-usable width.
        max_chars = style.get("max_chars_per_line")
        if max_chars:
            usable = width - 2 * max(20, width // 20)
            size = min(size, max(24, usable // _style_int(max_chars, "max_chars_per_line")))
    margin_v = style.get("margin_v")
    margin_v = _style_int(margin_v, "margin_v") if margin_v else max(1, height // 12)  # §7④
    primary = str(style.get("primary_colour") or WHITE)
    # Round X (agent XG): outline width + alignment were hard-coded (3 / 2,
    # bottom-centre) — now an explicit style key overrides them, an ABSENT key
    # keeps the exact same historical constant, so an untouched project's ASS
    # output stays byte-identical (pinned in tests/test_edit_v3.py).
    outline = style.get("outline")
    outline = _style_int(outline, "outline") if outline not in (None, "") else 3
    alignment = style.get("alignment")
    alignment = _style_int(alignment, "alignment") if alignment not in (None, "") else 2
    return {"font": font, "size": size, "margin_v": margin_v, "primary": primary,
            "outline": outline, "alignment": alignment}


def compile_ass(
    timeline: Timeline,
    *,
    width: int,
    height: int,
    style: dict[str, Any] | None = None,
    apply_line_breaks: bool = True,
) -> str:
    """Full ASS document: [Script Info] + one Default [V4+ Styles] + [Events].
    ``apply_line_breaks=False`` re-emits cues verbatim — the manual-takeover
    path uses it so human cues are never re-broken (§3)."""
    st = _resolve_style(style, width, height)
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
    # BorderStyle 1 (outline+shadow), Shadow=0. Outline/Alignment default to
    # the historical 3 / 2 (bottom-centre) — see _resolve_style.
    style_line = (
        "Style: Default,"
        f"{st['font']},{st['size']},{st['primary']},{SECONDARY},{BLACK},{BLACK},"
        f"0,0,0,0,100,100,0,0,1,{st['outline']},0,{st['alignment']},"
        f"{h_margin},{h_margin},{st['margin_v']},1"
    )
    styles = "\n".join(["[V4+ Styles]", style_format, style_line])

    event_format = (
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text"
    )
    event_lines = ["[Events]", event_format]
    max_chars = (style or {}).get("max_chars_per_line") if apply_line_breaks else None
    for cap in timeline.tracks.captions:
        # Newlines -> hard \\N break; commas in the text are safe here because
        # Text is the final field and never gets split on commas. The cue's OWN
        # text is neutralized against ASS override-tag injection (round-W #31)
        # BEFORE the line-break pass, so a literal "{" / "}" / "\" a human,
        # ASR pass, or dialogue string ever contains never reaches libass as
        # control syntax; the "\N" this function inserts right after stays a
        # real break — it is added AFTER escaping runs.
        text = escape_ass_text(_normalize_newlines(cap.text).strip("\n"))
        text = break_lines(text, max_chars).replace("\n", "\\N")
        # Name carries the cue's optional role (FP loop I §5.5); "" when unset
        # keeps the exact historical "Default,,0,0,0,," prefix (byte-identity).
        prefix = (
            f"Dialogue: 0,{ms_to_ass(cap.start_ms)},{ms_to_ass(cap.end_ms)},"
            f"Default,{_ass_name_field(cap)},0,0,0,,"
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
        for key in ("font", "size", "margin_v", "primary_colour",
                    "max_chars_per_line", "outline", "alignment"):
            if extra.get(key) is not None:
                style[key] = extra[key]
    except Exception:
        pass
    return style


def _project_caption_timeline(
    project: "Project", timeline: Timeline
) -> tuple[Timeline, bool]:
    """Return the caption truth used for ASS and whether line breaks apply."""
    srt_path = project.captions_dir / "captions.srt"
    if project.load_rules().captions.mode != "manual" or not srt_path.exists():
        return timeline, True

    from ..core.models import CaptionLine, TimelineTracks
    from ..providers.asr import parse_srt

    human = parse_srt(srt_path.read_text(encoding="utf-8"))
    human_timeline = Timeline(
        fps=timeline.fps,
        width=timeline.width,
        height=timeline.height,
        duration_ms=timeline.duration_ms,
        tracks=TimelineTracks(captions=[
            CaptionLine(start_ms=cue.start_ms, end_ms=cue.end_ms, text=cue.text)
            for cue in human
        ]),
    )
    return human_timeline, False


def compile_project_ass(project: "Project", timeline: Timeline) -> str:
    """Pure ASS bytes source shared by export and animatic readiness."""
    caption_timeline, apply_line_breaks = _project_caption_timeline(project, timeline)
    return compile_ass(
        caption_timeline,
        width=timeline.width,
        height=timeline.height,
        style=_caption_style(project),
        apply_line_breaks=apply_line_breaks,
    )


def export_captions(project: "Project", timeline: Timeline) -> dict[str, Path]:
    """Write ``captions/captions.srt`` and ``captions/captions.ass`` atomically.

    Manual takeover (§3, mirroring §6): when ``rules.captions.mode ==
    "manual"`` and a human-edited ``captions.srt`` exists, that SRT is truth —
    the compiler's version goes to ``captions.generated.srt`` for comparison,
    and the burned ASS is recompiled FROM the human cues so the film shows
    exactly what they wrote.

    Returns ``{"srt": <path>, "ass": <path>, "vtt": <path>}`` — the files
    downstream must use. The ``vtt`` (AI_IDE_18 WP7) is compiled from the same
    truth as the ``srt`` in both auto and manual modes.
    """
    style = _caption_style(project)
    srt_path = project.captions_dir / "captions.srt"
    ass_path = project.captions_dir / "captions.ass"
    vtt_path = project.captions_dir / "captions.vtt"

    max_chars = style.get("max_chars_per_line")

    if project.load_rules().captions.mode == "manual" and srt_path.exists():
        atomic_write_text(
            project.captions_dir / "captions.generated.srt",
            compile_srt(timeline, max_chars_per_line=max_chars),
        )
        human_timeline, _ = _project_caption_timeline(project, timeline)
        atomic_write_text(
            ass_path,
            compile_project_ass(project, timeline),
        )
        # WebVTT re-emits the human cues verbatim too (no re-break).
        atomic_write_text(vtt_path, compile_vtt(human_timeline))
        return {"srt": srt_path, "ass": ass_path, "vtt": vtt_path}

    atomic_write_text(srt_path, compile_srt(timeline, max_chars_per_line=max_chars))
    atomic_write_text(
        ass_path,
        compile_project_ass(project, timeline),
    )
    atomic_write_text(vtt_path, compile_vtt(timeline, max_chars_per_line=max_chars))
    return {"srt": srt_path, "ass": ass_path, "vtt": vtt_path}
