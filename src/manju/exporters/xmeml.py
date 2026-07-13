"""XMEML WRITER — FCP7 XML Interchange Format v4, the legacy-NLE exit for
Windows editors (Premiere Pro / 达芬奇 Resolve 都吃这一格式;Wave 3 §5.2).

WRITER only — there is no XMEML import path (no round-trip is claimed
anywhere). The document is one ``<xmeml version="4">`` with ONE ``<sequence>``
compiled from the SAME ``Timeline.tracks`` truth the render/OTIO/EDL/FCPXML
exits read, so the picture agrees clip-for-clip across every exit.

Why the timebase+ntsc pair is drift-free / 为什么 timebase+ntsc 无漂移
----------------------------------------------------------------------
XMEML expresses every time as a WHOLE-FRAME INTEGER on the sequence clock::

    <rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>   # 24 exact
    <rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate>    # 24000/1001 exact

``timebase`` is the NOMINAL integer label (``Rate.nominal_int``) and
``ntsc TRUE`` declares the exact 1001-family rate — together they ARE the
rational edit rate, so a 23.976 project rides its true clock with **zero
drift**: every ``<start>/<end>/<in>/<out>`` below is an exact whole frame
(``core.timebase.ms_to_frames`` Fractions / the compiler's ``duration_frames``
stamp — never a float). Video frame counts telescope exactly like the FCPXML
writer: the R2 rational stamp when present, else ``ms_to_frames`` differences,
so event N's end frame is event N+1's start frame exactly.

Structure produced (exports/xmeml/<project-name>.xml)
-----------------------------------------------------
* ``sequence``: name, duration (frames), rate (timebase+ntsc);
* ``media > video > track``: one ``<clipitem>`` per ``tracks.video`` clip —
  name (the shot label, the FCPXML stance), rate, ``start``/``end`` (record
  frames from the telescoped timeline position), ``in``/``out`` (source frames
  from ``source_in_ms``; REAL numbers — the ``-1`` conventions are not used);
* ``<file>`` per unique source: id, name, ``<pathurl>`` as a
  ``file://localhost/...`` URI (:func:`_file_url` — forward slashes, spaces/
  CJK/reserved chars percent-encoded, the drive-letter colon kept literal;
  defined once, referenced by bare ``<file id=…/>`` afterwards). The absolute
  path comes from ``project.resolve`` — a source escaping the project root
  REFUSES the export (the render/OTIO/FCPXML containment semantics);
* ``media > audio``: exactly two tracks — the VOICE bus then the MUSIC bus, a
  DECLARED two-track subset (EDL's A1/A2 stance). When a voice clip shares its
  source file with a video clip the two clipitems are tied with ``<link>``
  pairs (linked A/V);
* clean cross-dissolves (``xfade_fade``, ``0 < dur <`` BOTH adjacent clips)
  become a native ``<transitionitem>`` (Cross Dissolve, centre alignment
  straddling the cut); EVERY other transition kind degrades to a plain cut
  with an in-band ``<!-- MANJU -->`` note — a wrong dissolve is never emitted
  (the FCPXML/EDL precedent).

Honest omissions / 诚实边界(声明,不伪造)
--------------------------------------------
* MARKERS are omitted because today's ``Timeline`` model carries NO marker
  truth — inventing marker positions would be fabrication (时间线模型没有
  marker 事实,所以不写 marker,绝不编造);
* captions/overlays ride the SRT/ASS/VTT/TTML exits — not written here;
* sfx/ambient buses, loop beds (no materialization in this minimal writer)
  and ``duration_ms is None`` clips are OMITTED with in-band ``<!-- MANJU -->``
  comments (每一次省略都有注释,绝不静默); gain/fades/ducking are not
  expressed in this minimal clipitem subset;
* every feature is classified per the timeline inventory by
  :mod:`manju.exporters.conform` (target ``"xmeml"``).

Determinism: fixed element order, sequential ``clipitem-N``/``file-N`` ids by
first appearance, exact integer frame math, 2-space ``ElementTree`` indent, LF
endings, trailing newline — the same project+timeline renders byte-identical
XMEML forever. XML escaping is ElementTree's (CJK rides natively).
"""

from __future__ import annotations

import itertools
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from ..core.container import ProjectError
from ..core.timebase import Rate, Rounding, ms_to_frames
from ..core.yamlio import atomic_write_text

if TYPE_CHECKING:  # avoid an import cycle at module load (fcpxml/srt_ass stance)
    from ..core.container import Project
    from ..core.models import AudioClip, Timeline, VideoClip

__all__ = ["compile_xmeml", "export_xmeml"]

#: XMEML document version we target (FCP7 XML Interchange Format v4 — a
#: minimal-valid subset; no DOCTYPE is emitted, so the document parses DTD-less).
XMEML_VERSION = "4"

#: The dissolve family that maps CLEANLY onto a native <transitionitem>. Only
#: the true cross-dissolve; everything else degrades to a cut + an in-band note
#: instead of a wrong dissolve (the fcpxml/EDL never-a-wrong-dissolve stance).
_CLEAN_DISSOLVE_TYPES = frozenset({"xfade_fade"})

_CROSS_DISSOLVE = "Cross Dissolve"

#: The two audio buses this minimal writer lays as tracks, in emit order — a
#: DECLARED subset (EDL's A1/A2 stance). sfx/ambient are omitted with notes.
_AUDIO_EMIT_BUSES = ("voice", "music")
_AUDIO_OMIT_BUSES = ("sfx", "ambient")

#: A leading "/C:"-style Windows drive prefix on an already-/-normalized path.
_DRIVE_RE = re.compile(r"^/([A-Za-z]:)(/.*)?$")


def _file_url(abs_path: str) -> str:
    """An absolute path as a ``file://localhost/...`` URI (pure string math).

    Accepts BOTH separators (``C:\\Users\\晓 明\\f.mov`` and ``D:/a b/f.mov``)
    and POSIX paths. Forward slashes throughout; every path character outside
    the RFC 3986 unreserved set is percent-encoded (spaces → ``%20``, CJK →
    UTF-8 escapes, ``&#%`` → escapes) EXCEPT the two structural characters a
    legacy-NLE pathurl needs literal: the ``/`` separators and the drive-letter
    colon (``file://localhost/C:/Users/%E6%99%93.../f.mov`` — FCP7/Premiere
    resolve the drive only when the colon is unescaped)."""
    p = str(abs_path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p  # a drive-lettered Windows path gains the URI root slash
    m = _DRIVE_RE.match(p)
    if m:
        drive, rest = m.group(1), m.group(2) or ""
        return "file://localhost/" + drive + quote(rest, safe="/")
    return "file://localhost" + quote(p, safe="/")


def _clip_frames(clip: "VideoClip", rate: Rate) -> int:
    """The clip's EXACT whole-frame length — the fcpxml writer's rule, mirrored:
    consume the compiler's rational ``duration_frames`` stamp when present (the
    ms grid cannot hold a 1001-family boundary exactly), else telescope the
    ``ms_to_frames`` difference (every int project — byte-identical to the ms
    grid it always used)."""
    df = getattr(clip, "duration_frames", None)
    if df is not None:
        return int(df)
    start = int(clip.start_ms)
    return (ms_to_frames(start + int(clip.duration_ms), rate, Rounding.ROUND_HALF_UP)
            - ms_to_frames(start, rate, Rounding.ROUND_HALF_UP))


def _frames(ms: int, rate: Rate) -> int:
    return ms_to_frames(int(ms), rate, Rounding.ROUND_HALF_UP)


def _resolve_contained(project: "Project", source: str, *, label: str) -> str:
    """The absolute path for ``source`` — REFUSING anything that escapes the
    project root (the render/OTIO/FCPXML containment semantics). The absolute
    path is required here (pathurl is absolute in XMEML), so unlike fcpxml the
    resolved value is consumed, not discarded."""
    try:
        return str(project.resolve(source))
    except Exception as exc:
        raise ProjectError(
            f"导出失败:{label} 的素材路径超出项目边界或不合法: {source!r} — "
            "XMEML 导出不允许引用项目外文件(与渲染 render / OTIO / FCPXML "
            "的边界语义一致)。请先 `manju import` 把素材放进项目,再导出。"
        ) from exc


def _rate_elem(parent: ET.Element, rate: Rate) -> None:
    """``<rate><timebase>N</timebase><ntsc>TRUE|FALSE</ntsc></rate>`` — the
    nominal integer label + the NTSC flag ARE the exact rational rate (1001
    family: timebase = ``nominal_int``, ntsc TRUE; integer: ntsc FALSE)."""
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = str(rate.nominal_int)
    ET.SubElement(r, "ntsc").text = "TRUE" if rate.is_ntsc else "FALSE"


class _FileTable:
    """Deduped ``<file>`` resources: one full definition (id/name/pathurl) per
    unique source at first appearance, a bare ``<file id=…/>`` reference after
    — sequential ids by first appearance keep the bytes deterministic."""

    def __init__(self, project: "Project") -> None:
        self._project = project
        self._ids: dict[str, str] = {}

    def emit(self, parent: ET.Element, source: str, *, label: str) -> None:
        if source in self._ids:
            ET.SubElement(parent, "file", {"id": self._ids[source]})
            return
        fid = f"file-{len(self._ids) + 1}"
        self._ids[source] = fid
        abspath = _resolve_contained(self._project, source, label=label)
        file_el = ET.SubElement(parent, "file", {"id": fid})
        ET.SubElement(file_el, "name").text = source.rsplit("/", 1)[-1]
        ET.SubElement(file_el, "pathurl").text = _file_url(abspath)


def _is_clean_dissolve(clip: "VideoClip") -> bool:
    t = clip.transition_out
    return t is not None and t.type in _CLEAN_DISSOLVE_TYPES and t.duration_ms > 0


def compile_xmeml(project: "Project", timeline: "Timeline", *, name: str = "") -> str:
    """The full XMEML v4 document as a string (LF endings, trailing newline,
    deterministic bytes). Pure computation — nothing is written; the one
    side effect is the containment REFUSAL for an out-of-project source
    (``project.resolve`` is path math against the project root).

    The edit rate is ``timeline.frame_rate`` (the R2 rational echo when
    present, else the int ``fps`` promoted) — all frame math rides
    ``core.timebase`` Fractions, never a float. ``name`` becomes the sequence
    name (the IO layer passes the project name)."""
    rate = timeline.frame_rate
    clips: list[VideoClip] = list(timeline.tracks.video)

    # --- video frame geometry: telescoped record spine, exact whole frames --- #
    frames = [_clip_frames(c, rate) for c in clips]
    in_f = [_frames(c.source_in_ms or 0, rate) for c in clips]
    offsets: list[int] = []
    cursor = 0
    for f in frames:
        offsets.append(cursor)
        cursor += f
    video_end = cursor

    # dissolves[i]: native cross-dissolve frames from clip i into i+1 (0 = none).
    # The overlap guard (0 < dt < BOTH adjacent clips) keeps a centre-aligned
    # transitionitem inside real material; anything else degrades to a cut +
    # note — never a wrong dissolve.
    dissolves = [0] * len(clips)
    for i, c in enumerate(clips):
        if _is_clean_dissolve(c) and i + 1 < len(clips):
            dt = _frames(c.transition_out.duration_ms, rate)
            if 0 < dt < frames[i] and dt < frames[i + 1]:
                dissolves[i] = dt

    files = _FileTable(project)
    clip_seq = itertools.count(1)  # sequential clipitem ids, document order

    root = ET.Element("xmeml", {"version": XMEML_VERSION})
    sequence = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    ET.SubElement(sequence, "name").text = name
    duration_el = ET.SubElement(sequence, "duration")  # text set after audio lays
    _rate_elem(sequence, rate)
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    vtrack = ET.SubElement(video, "track")

    video_items: list[ET.Element] = []
    video_sources: list[str] = []
    for i, c in enumerate(clips):
        label = f"{c.shot}/{c.take}"
        item = ET.SubElement(vtrack, "clipitem", {"id": f"clipitem-{next(clip_seq)}"})
        ET.SubElement(item, "name").text = c.shot
        _rate_elem(item, rate)
        # record window (timeline position) and source window — REAL numbers on
        # the exact frame grid; the -1 in/out conventions are deliberately not
        # used, so every field is a verifiable whole frame.
        ET.SubElement(item, "start").text = str(offsets[i])
        ET.SubElement(item, "end").text = str(offsets[i] + frames[i])
        ET.SubElement(item, "in").text = str(in_f[i])
        ET.SubElement(item, "out").text = str(in_f[i] + frames[i])
        files.emit(item, c.source, label=label)
        video_items.append(item)
        video_sources.append(c.source)
        if dissolves[i] > 0:
            # centre-aligned across the cut at offsets[i+1]; dt//2 before the
            # cut, the remainder after — exact integers, no half-frame values.
            dt = dissolves[i]
            cut = offsets[i] + frames[i]
            trans = ET.SubElement(vtrack, "transitionitem")
            _rate_elem(trans, rate)
            ET.SubElement(trans, "start").text = str(cut - dt // 2)
            ET.SubElement(trans, "end").text = str(cut - dt // 2 + dt)
            ET.SubElement(trans, "alignment").text = "center"
            effect = ET.SubElement(trans, "effect")
            ET.SubElement(effect, "name").text = _CROSS_DISSOLVE
            ET.SubElement(effect, "effectid").text = _CROSS_DISSOLVE
            ET.SubElement(effect, "effectcategory").text = "Dissolve"
            ET.SubElement(effect, "effecttype").text = "transition"
            ET.SubElement(effect, "mediatype").text = "video"
        elif c.transition_out is not None and c.transition_out.type != "cut":
            t = c.transition_out
            vtrack.append(ET.Comment(
                f" MANJU: transition '{t.type}' ({t.duration_ms}ms) at {c.shot} "
                "approximated as a hard cut (no clean XMEML cross-dissolve "
                "mapping / 无法映射为干净的交叉溶解,退化为硬切) "))

    # --- audio: the declared voice/music two-track subset ---------------------- #
    audio = ET.SubElement(media, "audio")
    audio_end = 0
    bus_items: dict[str, list[tuple[ET.Element, "AudioClip"]]] = {}
    for bus in _AUDIO_EMIT_BUSES:
        track = ET.SubElement(audio, "track")
        bus_items[bus] = []
        for a in getattr(timeline.tracks, bus):
            aname = a.source.rsplit("/", 1)[-1]
            if a.loop:
                # No materialization in this minimal writer: a single pass would
                # be wrong audio (the fcpxml pre-W1 stance) — omitted, stated.
                track.append(ET.Comment(
                    f" MANJU: audio '{aname}' ({bus}) is a loop bed "
                    "(fill-to-duration) — not materialized in this minimal XMEML "
                    "writer; omitted rather than faked / 循环床音不伪造单遍,"
                    "此处省略 "))
                continue
            if a.duration_ms is None:
                track.append(ET.Comment(
                    f" MANJU: audio '{aname}' ({bus}) at {int(a.start_ms)}ms has "
                    "no resolvable duration (the render plays the source's "
                    "natural length) — omitted rather than guessed "))
                continue
            start_f = _frames(a.start_ms, rate)
            dur_f = _frames(a.duration_ms, rate)
            src_in = _frames(a.start_offset_ms or 0, rate)
            item = ET.SubElement(track, "clipitem",
                                 {"id": f"clipitem-{next(clip_seq)}"})
            ET.SubElement(item, "name").text = aname
            _rate_elem(item, rate)
            ET.SubElement(item, "start").text = str(start_f)
            ET.SubElement(item, "end").text = str(start_f + dur_f)
            ET.SubElement(item, "in").text = str(src_in)
            ET.SubElement(item, "out").text = str(src_in + dur_f)
            files.emit(item, a.source, label=f"{bus}:{aname}")
            bus_items[bus].append((item, a))
            audio_end = max(audio_end, start_f + dur_f)

    # sfx/ambient: the declared omission — one in-band note per clip, so the
    # subset boundary is visible in the artifact itself (never silent).
    for bus in _AUDIO_OMIT_BUSES:
        for a in getattr(timeline.tracks, bus):
            aname = a.source.rsplit("/", 1)[-1]
            audio.append(ET.Comment(
                f" MANJU: audio '{aname}' ({bus}) at {int(a.start_ms)}ms omitted "
                "— this XMEML writer lays the declared voice/music two-track "
                "subset only (EDL A1/A2 stance); never squeezed into a track "))

    # --- linked A/V: a voice clip sharing a video clip's source ---------------- #
    # First-match pairing in document order (deterministic); each pair carries
    # the classic reciprocal <link> pair (video ref + audio ref) in BOTH items.
    paired_audio: set[int] = set()
    for i, vitem in enumerate(video_items):
        for j, (aitem, aclip) in enumerate(bus_items.get("voice", ())):
            if j in paired_audio or aclip.source != video_sources[i]:
                continue
            paired_audio.add(j)
            for holder in (vitem, aitem):
                for ref, mtype in ((vitem, "video"), (aitem, "audio")):
                    link = ET.SubElement(holder, "link")
                    ET.SubElement(link, "linkclipref").text = ref.get("id")
                    ET.SubElement(link, "mediatype").text = mtype
            break

    duration_el.text = str(max(video_end, audio_end))

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def export_xmeml(
    project: "Project", timeline: "Timeline", dest: Path | None = None
) -> Path:
    """Write ``exports/xmeml/<project name>.xml`` atomically (or ``dest`` when
    given) and return the path — the OTIO/EDL/FCPXML calling convention.

    The edit rate is ``timeline.frame_rate``, so a 1001-family project rides
    ``timebase + ntsc TRUE`` exactly and an int project ``ntsc FALSE`` — every
    emitted time a whole frame. An out-of-project source refuses the export
    (:func:`_resolve_contained`) before anything is written."""
    config = project.load_config()
    text = compile_xmeml(project, timeline, name=config.name)
    path = (Path(dest) if dest is not None
            else project.exports_dir / "xmeml" / f"{config.name}.xml")
    atomic_write_text(path, text)
    return path
