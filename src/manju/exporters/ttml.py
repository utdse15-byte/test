"""TTML/IMSC1 caption WRITER (FP loop S1, roadmap §5.5 / user item 4).

WRITER only — there is no TTML import path this loop. The document is an
IMSC1-Text-Profile-shaped TTML1 file compiled from the SAME
``Timeline.tracks.captions`` truth as SRT/ASS/VTT (``exporters/srt_ass.py``),
so every caption exit agrees cue-for-cue. It expresses exactly what
:class:`~manju.core.models.CaptionLine` actually carries and nothing more:

* ``start_ms``/``end_ms`` → ``begin``/``end`` as media-time ``HH:MM:SS.mmm``
  (integer milliseconds are the model's native precision — §4.4; fps never
  enters; the rational R-track upgrades timing later);
* ``text`` → escaped UTF-8 ``<p>`` content, author/budget line breaks as
  ``<br/>`` (same ``break_lines`` budget as SRT/VTT — reused, never re-derived);
* ``speaker`` → one ``ttm:agent`` element per distinct speaker in head
  metadata (sorted → deterministic ids) + a ``ttm:agent`` reference per cue;
* wave-4b ``role`` → a standard hint where the loop's binding table maps one
  (sdh → ``ttm:role="captions"``, translation → ``ttm:role="subtitles"``,
  forced → ``itts:forcedDisplay="true"``, speaker_label → agent ref or the
  shared stub agent) and ALWAYS the verbatim role on ``x-manju:role`` — an
  unmappable role is never dropped silently. NOTE the honest nuance: TTML1's
  registered ``ttm:role`` tokens are singular ("caption") and include no
  "subtitles"; the two hint values follow this loop's binding mapping table,
  and the authoritative, lossless value is always ``x-manju:role``.

Honest scope boundaries (recorded, not silently claimed):

* ONE default region (bottom centre, ``tts:displayAlign="after"``) — the cue
  model carries no positioning, so no other region could be honest;
* vertical writing and ruby stay OUT OF SCOPE: they need layout semantics
  (``writingMode``/ruby containers) the cue model lacks. RTL is supported ONLY
  as an explicit per-locale declaration (``locales/<lang>/meta.yaml``'s
  ``direction: rtl|ltr`` → the additive ``direction`` param → ``tts:direction``
  and, for rtl, ``tts:unicodeBidi="embed"`` on the content div) — NEVER inferred
  from the language code, and the cue model itself still carries no direction
  (a project that declares none emits a byte-identical bare ``<div>``);
* ``xml:lang`` is ``""`` unless a caller passes a language — the base project
  records NO locale (locales are per-language overlays, ``core/locale.py``);
* no ``ttp:profile`` conformance claim: no external validator runs here (no
  new deps), so the document does not claim what it has not proven;
* the ``x-manju`` prefix (namespace ``urn:x-manju:ttml``) mirrors the
  openclap ``x-manju`` extension-key stance: non-mappable semantics ride a
  namespaced extension, never fabricated standard fields.

Determinism: fixed skeleton, sorted per-element attribute order, sorted agent
ids, LF endings, no wall-clock anywhere — the same timeline renders
byte-identical TTML forever. Escaping is stdlib-only (``xml.sax.saxutils``);
the XML-1.0-illegal C0 controls are substituted with U+FFFD (documented loss:
those code points cannot exist in any XML document, escaped or not).

Manual takeover (§3): like ``export_captions``, when ``rules.captions.mode ==
"manual"`` and a human-edited ``captions.srt`` exists, the TTML is recompiled
FROM the human cues so the sidecar says exactly what they wrote. SRT carries
no roles/speakers, so the manual-mode TTML honestly has none either (format
honesty, FP loop I).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

from ..core.models import CaptionLine, Timeline
from ..core.yamlio import atomic_write_text
from .srt_ass import _caption_style, _normalize_newlines, break_lines, ms_to_vtt

if TYPE_CHECKING:  # avoid an import cycle at module load (srt_ass stance)
    from ..core.container import Project

__all__ = ["ms_to_ttml", "compile_ttml", "export_ttml"]

#: Milliseconds -> TTML media-time ``HH:MM:SS.mmm``. The WebVTT timestamp
#: grammar is byte-identical to a TTML clock-time with a millisecond fraction,
#: so this IS ``srt_ass.ms_to_vtt`` — reuse by import, zero drift (§5.5).
ms_to_ttml = ms_to_vtt

# Fixed root namespace declarations, sorted by attribute name. Always all
# declared (even when a document uses none of ttm/itts/x-manju) so the root
# element is byte-stable across role-less and roled projects.
_NS = (
    ("xmlns", "http://www.w3.org/ns/ttml"),
    ("xmlns:itts", "http://www.w3.org/ns/ttml/profile/imsc1#styling"),
    ("xmlns:ttm", "http://www.w3.org/ns/ttml#metadata"),
    ("xmlns:ttp", "http://www.w3.org/ns/ttml#parameter"),
    ("xmlns:tts", "http://www.w3.org/ns/ttml#styling"),
    ("xmlns:x-manju", "urn:x-manju:ttml"),
)

# The loop's binding role→ttm:role hint table. Roles absent here get no
# ttm:role (forced is expressed as itts:forcedDisplay, speaker_label as a
# ttm:agent reference, lyrics and every unknown role as x-manju:role only —
# which EVERY roled cue carries verbatim anyway).
_TTM_ROLE_HINTS = {"sdh": "captions", "translation": "subtitles"}

_STUB_AGENT_ID = "agent.unknown"

# XML 1.0 cannot carry these C0 controls at all (not even as character
# references). U+FFFD substitution is the documented, deliberate loss; TAB/LF
# stay (legal), CR is normalized away with the other newline forms first.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _xml_clean(value: str) -> str:
    return _XML_ILLEGAL.sub("�", value)


def _attr_str(pairs: list[tuple[str, str]]) -> str:
    """Serialize attributes SORTED by name (the determinism pin — the writer
    controls the order, so the order is canonical). ``quoteattr`` keeps
    newlines/tabs in values as character references, so a hostile role string
    survives a parse round-trip verbatim."""
    return " ".join(
        f"{name}={quoteattr(_xml_clean(value))}"
        for name, value in sorted(pairs, key=lambda kv: kv[0])
    )


def _div_open(direction: str | None) -> str:
    """The content ``<div>`` open tag. ``direction`` is a HUMAN-declared layout
    direction from ``locales/<lang>/meta.yaml`` (never inferred from the
    language code). Falsy (``None``/``""``) → exactly ``    <div>`` — the
    drop-when-absent stance, byte-identical to a project that declares none
    (R1/S4 precedent). ``"rtl"`` also emits ``tts:unicodeBidi="embed"`` so the
    bidi embedding level is explicit; ``"ltr"`` sets ``tts:direction`` only.
    Attributes ride the same sorted ``_attr_str`` order as every other tag."""
    if not direction:
        return "    <div>"
    pairs = [("tts:direction", direction)]
    if direction == "rtl":
        pairs.append(("tts:unicodeBidi", "embed"))
    return "    <div " + _attr_str(pairs) + ">"


def _agent_ids(captions: list[CaptionLine]) -> dict[str, str]:
    """Deterministic ``speaker -> xml:id``: distinct non-empty speakers,
    sorted (codepoint order), numbered from 1."""
    speakers = sorted({c.speaker for c in captions if c.speaker})
    return {s: f"agent.{i}" for i, s in enumerate(speakers, start=1)}


def _p_line(cap: CaptionLine, agent_ids: dict[str, str],
            max_chars: int | None) -> str:
    attrs: list[tuple[str, str]] = [
        ("begin", ms_to_ttml(cap.start_ms)),
        ("end", ms_to_ttml(cap.end_ms)),
    ]
    role = cap.role
    agent = agent_ids.get(cap.speaker) if cap.speaker else None
    if role == "speaker_label" and agent is None:
        agent = _STUB_AGENT_ID  # the addendum's stub — identity unclaimed
    if agent:
        attrs.append(("ttm:agent", agent))
    if role:
        if role == "forced":
            attrs.append(("itts:forcedDisplay", "true"))
        hint = _TTM_ROLE_HINTS.get(role)
        if hint:
            attrs.append(("ttm:role", hint))
        # VERBATIM, always — mappable or not, a role is never dropped silently
        attrs.append(("x-manju:role", role))
    text = _normalize_newlines(cap.text).strip("\n")
    text = break_lines(text, max_chars)
    content = "<br/>".join(escape(_xml_clean(part)) for part in text.split("\n"))
    return f"<p {_attr_str(attrs)}>{content}</p>"


def compile_ttml(
    timeline: Timeline,
    *,
    lang: str = "",
    max_chars_per_line: int | None = None,
    direction: str | None = None,
) -> str:
    """The full IMSC1-shaped TTML1 document as a string (LF endings, trailing
    newline, deterministic bytes).

    ``lang`` lands on ``xml:lang`` verbatim; the honest default is ``""``
    because the base project records no locale (callers that KNOW — e.g. a
    locale overlay build — may pass one). ``max_chars_per_line`` applies the
    same line budget as ``compile_srt``/``compile_vtt`` (compiled mode only —
    manual-takeover callers pass ``None`` so human cues are never re-broken).

    ``direction`` is an EXPLICIT per-locale layout declaration (from
    ``locales/<lang>/meta.yaml``, validated by ``core.locale.load_locale_meta``
    to ``"rtl"``/``"ltr"``) — NEVER inferred from ``lang``. ``"rtl"`` puts
    ``tts:direction="rtl"`` + ``tts:unicodeBidi="embed"`` on the content div,
    ``"ltr"`` puts ``tts:direction`` only, and the default ``None`` leaves the
    div bare so the output is byte-identical to a project that declares none.
    """
    caps = timeline.tracks.captions
    agent_ids = _agent_ids(caps)
    need_stub = any(c.role == "speaker_label" and not c.speaker for c in caps)

    tt_attrs = " ".join(f"{n}={quoteattr(v)}" for n, v in _NS)
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f"<tt {tt_attrs} ttp:timeBase=\"media\" "
        f"xml:lang={quoteattr(_xml_clean(lang))}>",
        "  <head>",
    ]
    if agent_ids or need_stub:
        out.append("    <metadata>")
        for speaker, aid in sorted(agent_ids.items()):
            out.append("      <ttm:agent "
                       + _attr_str([("type", "character"), ("xml:id", aid)])
                       + ">")
            out.append("        <ttm:name type=\"alias\">"
                       f"{escape(_xml_clean(speaker))}</ttm:name>")
            out.append("      </ttm:agent>")
        if need_stub:
            out.append("      <ttm:agent "
                       + _attr_str([("type", "other"),
                                    ("xml:id", _STUB_AGENT_ID)]) + "/>")
        out.append("    </metadata>")
    out += [
        "    <styling>",
        "      <style " + _attr_str([
            ("tts:color", "white"),
            ("tts:fontFamily", "proportionalSansSerif"),
            ("xml:id", "s.default"),
        ]) + "/>",
        "    </styling>",
        "    <layout>",
        # ONE default region: bottom-centre band inside the safe area (§7④
        # spirit); percentages only — honest for every render resolution.
        "      <region " + _attr_str([
            ("tts:displayAlign", "after"),
            ("tts:extent", "80% 15%"),
            ("tts:origin", "10% 80%"),
            ("tts:textAlign", "center"),
            ("xml:id", "r.bottom"),
        ]) + "/>",
        "    </layout>",
        "  </head>",
        "  <body " + _attr_str([("region", "r.bottom"),
                                ("style", "s.default")]) + ">",
        _div_open(direction),
    ]
    out += ["      " + _p_line(c, agent_ids, max_chars_per_line) for c in caps]
    out += ["    </div>", "  </body>", "</tt>"]
    return "\n".join(out) + "\n"


def export_ttml(
    project: "Project", timeline: Timeline, dest: Path | None = None
) -> Path:
    """Write ``captions/captions.ttml`` atomically (or ``dest`` when given)
    and return the path — the srt_ass calling convention, one format.

    Manual takeover (§3): when ``rules.captions.mode == "manual"`` and a
    human-edited ``captions.srt`` exists, the human cues are the truth this
    TTML is compiled from (mirrors ``export_captions``' ASS/VTT re-burn — all
    caption exits keep saying the same thing). ``captions.generated.srt``
    remains the SRT exporter's job; nothing else is written here.
    """
    path = Path(dest) if dest is not None else project.captions_dir / "captions.ttml"
    max_chars = _caption_style(project).get("max_chars_per_line")

    srt_path = project.captions_dir / "captions.srt"
    if project.load_rules().captions.mode == "manual" and srt_path.exists():
        from ..core.models import TimelineTracks
        from ..providers.asr import parse_srt

        human = parse_srt(srt_path.read_text(encoding="utf-8"))
        timeline = Timeline(
            fps=timeline.fps, width=timeline.width, height=timeline.height,
            duration_ms=timeline.duration_ms,
            tracks=TimelineTracks(captions=[
                CaptionLine(start_ms=s.start_ms, end_ms=s.end_ms, text=s.text)
                for s in human
            ]),
        )
        max_chars = None  # human cues are truth — never re-broken (§3)

    atomic_write_text(path, compile_ttml(timeline, max_chars_per_line=max_chars))
    return path
