"""FCPXML WRITER — the one NLE exit where our rational time rides NATIVELY (FP
loop T2, roadmap §5 item 5 / §6).

WRITER only — there is no FCPXML import path this loop (no round-trip / import-plan
is claimed anywhere). The document is a minimal-valid FCPXML 1.9 subset compiled
from the SAME ``Timeline.tracks.video`` truth the render, OTIO and EDL exits read,
so the picture agrees clip-for-clip across every exit. It expresses exactly what
the timeline carries and is loud, in-band, about everything it does not.

Why FCPXML is special: rational time is NATIVE
-----------------------------------------------
FCPXML expresses every time as an exact rational number of seconds — a
``"numerator/denominatorS"`` string — and the sequence's ``<format>`` carries a
``frameDuration`` that IS the reciprocal of the edit rate::

    24     fps  -> frameDuration="1/24s"      one frame  =  "1/24s"
    24000/1001  -> frameDuration="1001/24000s"  one frame = "1001/24000s"

So a clip of ``F`` whole frames is written as ``"{F·den}/{num}s"`` on the rate's
own timescale (denominator = the frame-rate numerator; never reduced, so
frame-alignment is verifiable by inspection — the numerator is always an integer
multiple of the ``frameDuration`` numerator — and the bytes are deterministic).
This is the ONLY exit where an int project (``"1/24s"``) AND a 1001-family project
(``"1001/24000s"``) both ride the EXACT frame grid with **zero drift** — unlike
OTIO, whose int path carries a fractional-frame RationalTime value, FCPXML never
writes a fractional frame. ``exporters/conform.py`` pins that advantage: the
``frame_drift`` block reports ``all_zero_by_construction`` for BOTH paths.

Frame counts are taken from the compiler's exact truth when present:
``VideoClip.duration_frames`` (the R2 rational stamp) is consumed directly;
otherwise the count telescopes ``ms_to_frames`` differences (every int project —
byte-identical to what the ms grid always held). The record spine offsets
telescope those whole-frame counts, so event N's end frame is event N+1's start
frame exactly (continuity on both paths).

Structure produced (§3, exports/fcpxml)
---------------------------------------
``library > event > project > sequence > spine`` with:

* one ``<format>`` resource carrying the REAL rational ``frameDuration`` +
  width/height;
* one ``<asset>`` resource per distinct source (project-relative ``src``,
  containment-checked exactly like OTIO's ``target_url``; the available media
  duration widens to cover in-point + window, self-consistent like OTIO);
* one ``<asset-clip>`` per video clip on the spine — ``offset``/``start``/
  ``duration`` as exact rational-seconds strings; ``start`` is the source
  in-point (``source_in_ms`` → whole frame, 0 when untrimmed).

Transitions (cross-dissolve family only — S2's never-a-wrong-dissolve precedent)
--------------------------------------------------------------------------------
A clip whose ``transition_out`` is a clean cross-dissolve (``xfade_fade``,
duration > 0 AND shorter than BOTH adjacent clips) becomes a NATIVE FCPXML
``<transition>`` referencing the ``Cross Dissolve`` video effect, laid with FCP's
overlap geometry: the incoming clip (and every later element) is pulled back by
the transition's frame count so the two clips overlap for exactly the dissolve —
the correct editorial overlap FCP itself writes. EVERY OTHER transition kind —
dip-to-black ``fade``, the ``xfade_*`` wipes/slides, an unknown type, or a
cross-dissolve too long to overlap its clips — degrades to a plain cut with an
in-band ``<!-- MANJU: … -->`` note. A wrong dissolve is never emitted.

Honest scope boundaries (recorded, not silently claimed)
--------------------------------------------------------
* CAPTIONS ride the SRT/TTML exits — NOT written here (honest boundary; FCPXML
  titles are deliberately not emitted). Overlays/branding are likewise not a
  spine primitive and are not written.
* AUDIO is not written this loop: FCPXML's connected-clip role/lane model CAN
  carry Manju's four buses (unlike CMX EDL's flat A-channel), so this is a
  WRITER-scope boundary — a deferred increment — not a format limit; audio is
  honestly omitted rather than faked. Speed ramps / multicam / nested sequences
  are out of scope.
* Every feature is classified per the timeline inventory by
  :mod:`manju.exporters.conform` (target ``"fcpxml"``).

Determinism: fixed resource/element order (format, assets by first appearance,
effect last), fixed attribute order, exact rational strings (no float, no
wall-clock), 2-space ``ElementTree`` indentation, LF endings, trailing newline —
the same timeline renders byte-identical FCPXML forever. XML escaping and
validity are ElementTree's (proper attribute/text escaping; CJK rides natively).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.container import ProjectError
from ..core.timebase import Rate, Rounding, ms_to_frames
from ..core.yamlio import atomic_write_text

if TYPE_CHECKING:  # avoid an import cycle at module load (srt_ass/ttml/edl stance)
    from ..core.container import Project
    from ..core.models import Timeline, VideoClip

__all__ = ["compile_fcpxml", "export_fcpxml"]

#: FCPXML document version we target (1.9 — a minimal-valid subset; no DTD is
#: emitted, so the document parses DTD-less).
FCPXML_VERSION = "1.9"

#: The dissolve family that maps CLEANLY onto a native FCPXML ``<transition>``.
#: Only the true cross-dissolve; wipes/slides and dip-to-black degrade to a cut +
#: an in-band note instead of a wrong dissolve (see the module docstring).
_CLEAN_DISSOLVE_TYPES = frozenset({"xfade_fade"})

#: FCP's built-in Cross Dissolve video-transition effect UID.
_CROSS_DISSOLVE_UID = "FFVideoTransitionCrossDissolve"
_CROSS_DISSOLVE_NAME = "Cross Dissolve"


def _secs(frames: int, rate: Rate) -> str:
    """A whole-frame count as an EXACT FCPXML rational-seconds string on ``rate``.

    ``F`` frames = ``F · (den/num)`` s = ``"{F·den}/{num}s"`` — the timescale is
    the rate numerator, and the value is NEVER reduced, so frame-alignment is
    verifiable by inspection (the numerator is always an integer multiple of the
    ``frameDuration`` numerator ``den``) and the bytes are deterministic. Zero is
    the canonical ``"0s"``; one frame is the ``frameDuration`` itself
    (``"1/24s"`` / ``"1001/24000s"``).
    """
    if frames == 0:
        return "0s"
    return f"{frames * rate.denominator}/{rate.numerator}s"


def _clip_frames(clip: "VideoClip", rate: Rate) -> int:
    """The clip's EXACT whole-frame length. Consumes the compiler's rational
    ``duration_frames`` stamp when present (R2/R4 truth — the ms grid cannot hold
    a 1001-family boundary exactly); otherwise telescopes the ``ms_to_frames``
    difference (every int project — byte-identical to the ms grid it always
    used). The start still telescopes via ``ms_to_frames(start_ms)``, which for an
    R2-compiled timeline recovers the exact cumulative frame, so continuity holds
    on both paths."""
    df = getattr(clip, "duration_frames", None)
    if df is not None:
        return int(df)
    start = int(clip.start_ms)
    return (ms_to_frames(start + int(clip.duration_ms), rate, Rounding.ROUND_HALF_UP)
            - ms_to_frames(start, rate, Rounding.ROUND_HALF_UP))


def _is_clean_dissolve(clip: "VideoClip") -> bool:
    """True for a clip carrying a clean cross-dissolve out-edge (``xfade_fade`` at
    a positive duration). The per-boundary overlap guard (fits inside BOTH
    adjacent clips) is applied at layout time — see :func:`compile_fcpxml`."""
    t = clip.transition_out
    return t is not None and t.type in _CLEAN_DISSOLVE_TYPES and t.duration_ms > 0


def compile_fcpxml(timeline: "Timeline", *, rate: Rate, name: str = "") -> str:
    """The full FCPXML 1.9 document as a string (LF endings, trailing newline,
    deterministic bytes).

    ``rate`` is the EXACT edit rate (a :class:`~manju.core.timebase.Rate`) — pass
    ``timeline.frame_rate`` (the rational echo when present, else the int ``fps``
    promoted to a whole-number rate). ``name`` becomes the event/project name.
    Times are exact rational-seconds strings on ``rate``'s ``frameDuration``
    timescale; every clip boundary is a whole frame, so there is no drift for
    int OR 1001-family projects (the rational-native advantage).
    """
    clips: list[VideoClip] = list(timeline.tracks.video)

    # --- per-clip frame geometry (whole frames on the exact grid) ------------- #
    frames = [_clip_frames(c, rate) for c in clips]
    in_frames = [ms_to_frames(int(c.source_in_ms or 0), rate, Rounding.ROUND_HALF_UP)
                 for c in clips]

    # --- spine offsets with cross-dissolve overlap pullback ------------------- #
    # ``dissolves[i]`` is the native-dissolve frame count from clip i into i+1 (0
    # if none). A clean cross-dissolve overlaps the two clips by its duration, so
    # the incoming clip and every later element pull back by that many frames —
    # FCP's own geometry. The guard (0 < dt < both adjacent clip lengths) keeps
    # the overlap valid; a dissolve that would meet/exceed either clip degrades to
    # a cut + note (never a wrong dissolve).
    offsets: list[int] = []
    dissolves = [0] * len(clips)
    cursor = 0
    for i, c in enumerate(clips):
        offsets.append(cursor)
        cursor += frames[i]
        if _is_clean_dissolve(c) and i + 1 < len(clips):
            dt = ms_to_frames(int(c.transition_out.duration_ms), rate,
                              Rounding.ROUND_HALF_UP)
            if 0 < dt < frames[i] and dt < frames[i + 1]:
                dissolves[i] = dt
                cursor -= dt
    seq_frames = cursor

    def _is_degraded(i: int) -> bool:
        """A SET transition (not a plain cut/None) that we did NOT emit as a
        native dissolve — degraded to a cut with an honest note."""
        t = clips[i].transition_out
        if t is None or t.type == "cut":
            return False
        return dissolves[i] == 0

    # --- resource ids: format r1, assets r2.. (first appearance), effect last -- #
    order: list[str] = []
    avail: dict[str, int] = {}
    asset_name: dict[str, str] = {}
    for i, c in enumerate(clips):
        need = in_frames[i] + frames[i]
        if c.source not in avail:
            order.append(c.source)
            avail[c.source] = need
            asset_name[c.source] = c.shot
        else:
            avail[c.source] = max(avail[c.source], need)
    asset_id = {src: f"r{n + 2}" for n, src in enumerate(order)}
    has_dissolve = any(d > 0 for d in dissolves)
    effect_id = f"r{len(order) + 2}" if has_dissolve else None

    # --- build the tree ------------------------------------------------------- #
    fcpxml = ET.Element("fcpxml", {"version": FCPXML_VERSION})
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(resources, "format", {
        "id": "r1",
        "name": f"ManjuFormat{int(timeline.width)}x{int(timeline.height)}"
                f"p{rate.nominal_int}",
        "frameDuration": _secs(1, rate),
        "width": str(int(timeline.width)),
        "height": str(int(timeline.height)),
    })
    for src in order:
        ET.SubElement(resources, "asset", {
            "id": asset_id[src],
            "name": asset_name[src],
            "src": src,               # project-relative (containment-checked)
            "start": "0s",            # generated media's own timebase is zero-based
            "duration": _secs(avail[src], rate),
            "hasVideo": "1",
            "format": "r1",
        })
    if effect_id is not None:
        ET.SubElement(resources, "effect", {
            "id": effect_id,
            "name": _CROSS_DISSOLVE_NAME,
            "uid": _CROSS_DISSOLVE_UID,
        })

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": name})
    project = ET.SubElement(event, "project", {"name": name})
    sequence = ET.SubElement(project, "sequence", {
        "format": "r1",
        "duration": _secs(seq_frames, rate),
        "tcStart": "0s",
        "tcFormat": "NDF",  # honest subset: no drop-frame timecode display claim
    })
    spine = ET.SubElement(sequence, "spine")
    for i, c in enumerate(clips):
        ET.SubElement(spine, "asset-clip", {
            "ref": asset_id[c.source],
            "offset": _secs(offsets[i], rate),
            "name": c.shot,
            "start": _secs(in_frames[i], rate),
            "duration": _secs(frames[i], rate),
            "format": "r1",
            "tcFormat": "NDF",
        })
        if dissolves[i] > 0:
            # The native cross-dissolve sits in the overlap [offsets[i+1], clip
            # end); its offset IS the (pulled-back) incoming clip's offset.
            transition = ET.SubElement(spine, "transition", {
                "name": _CROSS_DISSOLVE_NAME,
                "offset": _secs(offsets[i + 1], rate),
                "duration": _secs(dissolves[i], rate),
            })
            ET.SubElement(transition, "filter-video", {
                "ref": effect_id, "name": _CROSS_DISSOLVE_NAME,
            })
        elif _is_degraded(i):
            t = c.transition_out
            spine.append(ET.Comment(
                f" MANJU: transition '{t.type}' ({t.duration_ms}ms) at {c.shot} "
                "approximated as a hard cut (no clean FCPXML cross-dissolve "
                "mapping) "))

    ET.indent(fcpxml, space="  ")
    body = ET.tostring(fcpxml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def _require_contained_source(project: "Project", source: str, *, label: str) -> None:
    """Refuse to write ``source`` into the FCPXML unless it stays inside the
    project root — the SAME containment semantics ``render``/OTIO enforce. The
    original project-relative ``src`` string is kept byte-identically (FCP
    resolves a relative URL against the document); this call is pure validation
    (its return is discarded — only a containment failure matters)."""
    try:
        project.resolve(source)
    except Exception as exc:
        raise ProjectError(
            f"导出失败:{label} 的素材路径超出项目边界或不合法: {source!r} — "
            "FCPXML 导出不允许引用项目外文件(与渲染 render / OTIO 的边界语义一致)。"
            "请先 `manju import` 把素材放进项目,再导出。"
        ) from exc


def export_fcpxml(
    project: "Project", timeline: "Timeline", dest: Path | None = None
) -> Path:
    """Write ``exports/fcpxml/<project name>.fcpxml`` atomically (or ``dest`` when
    given) and return the path — the OTIO/EDL calling convention, one format.

    The edit rate is ``timeline.frame_rate`` (the rational echo when the project
    is R2-compiled, else the int ``fps`` promoted to a whole-number rate), so the
    document carries the EXACT ``frameDuration`` — ``"1001/24000s"`` for a
    1001-family project, ``"1/24s"`` for an int one — with every time a whole-frame
    rational-seconds string.
    """
    config = project.load_config()
    for c in timeline.tracks.video:
        _require_contained_source(project, c.source, label=f"{c.shot}/{c.take}")
    rate = timeline.frame_rate
    text = compile_fcpxml(timeline, rate=rate, name=config.name)
    path = (Path(dest) if dest is not None
            else project.exports_dir / "fcpxml" / f"{config.name}.fcpxml")
    atomic_write_text(path, text)
    return path
