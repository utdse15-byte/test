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
* AUDIO (FP loop V1 — the T2-deferred increment): the four buses
  (voice/music/sfx/ambient) ride as CONNECTED ``<asset-clip>``\\ s nested in their
  owning spine clip, on a fixed lane (voice −1 … ambient −4) with the standard
  base role + verbatim bus subrole (``dialogue`` / ``music`` / ``effects.sfx`` /
  ``effects.ambient``). Placement is parent-relative (``child.offset =
  in_frames[i] + F − offsets[i]`` against the pulled-back spine geometry); gain
  becomes ``<adjust-volume>``; a connected clip MAY extend past its parent's end
  (legal — never split). HONEST omissions (an in-band ``<!-- MANJU --> `` note,
  never faked): a ``loop`` bed is fill-to-duration (a single pass would be wrong
  audio); a ``duration_ms is None`` clip resolves in the render to the source's
  natural length with NO static probe, so it is not statically resolvable; a
  ``ducking`` clip IS written (static gain) but its render-time sidechain
  relationship is noted; fades are gain-only this increment (no native fade
  element — the exact FCPXML fade shape is not emitted). Speed ramps / multicam /
  nested sequences remain out of scope.
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
from typing import TYPE_CHECKING, NamedTuple

from ..core.container import ProjectError
from ..core.timebase import Rate, Rounding, ms_to_frames
from ..core.yamlio import atomic_write_text

if TYPE_CHECKING:  # avoid an import cycle at module load (srt_ass/ttml/edl stance)
    from ..core.container import Project
    from ..core.models import AudioClip, Timeline, VideoClip

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


# --------------------------------------------------------------------------- #
# connected audio lanes (FP loop V1 — the T2-deferred increment)              #
# --------------------------------------------------------------------------- #

#: The four audio buses of ``TimelineTracks`` in their deterministic emit order.
_AUDIO_BUSES = ("voice", "music", "sfx", "ambient")

#: Fixed deterministic lane map (connected clips ride BELOW the spine, so the
#: lanes are negative) and the standard base-role + verbatim bus-subrole map.
_AUDIO_LANES = {"voice": -1, "music": -2, "sfx": -3, "ambient": -4}
_AUDIO_ROLES = {
    "voice": "dialogue",
    "music": "music",
    "sfx": "effects.sfx",
    "ambient": "effects.ambient",
}


def _audio_parent_index(f: int, offsets: list[int], frames: list[int]) -> int:
    """The spine clip a connected audio clip at absolute frame ``f`` attaches to:
    the FIRST clip whose PULLED-BACK interval ``[offsets[i], offsets[i]+frames[i])``
    contains ``f``. Iterating from 0 makes a dissolve-overlap tie resolve to the
    EARLIER clip; ``f`` at/after the last clip's end falls through to the LAST
    clip (a connected clip legally extends past its parent). Callers guarantee at
    least one spine clip."""
    for i in range(len(offsets)):
        if offsets[i] <= f < offsets[i] + frames[i]:
            return i
    return len(offsets) - 1


def _audio_name(source: str) -> str:
    """A stable clip/asset name from a project-relative audio ``source``: its
    basename (paths are ``/``-joined project-relative strings; CJK rides
    natively)."""
    return source.rsplit("/", 1)[-1]


def _audio_approx_notes(clip: "AudioClip", bus: str, name: str) -> list[str]:
    """Honest in-band notes for a WRITTEN connected clip whose render-time
    behaviour FCPXML does not carry: the ducking sidechain relationship (the clip
    still plays at its static gain — the mix is never silently claimed) and, on
    the gain-only fades branch, the omitted fade handles."""
    notes: list[str] = []
    if clip.ducking:
        notes.append(
            f" MANJU: audio '{name}' ({bus}) ducking is a render-time sidechain "
            "relationship (keyed under the voice bus) not expressible in FCPXML — "
            "the clip is written at its static gain; the ducking mix is NOT "
            "represented ")
    fi, fo = int(clip.fade_in_ms or 0), int(clip.fade_out_ms or 0)
    if fi > 0 or fo > 0:
        notes.append(
            f" MANJU: audio '{name}' ({bus}) fade_in {fi}ms / fade_out {fo}ms "
            "approximated as gain-only (no native FCPXML fade element emitted this "
            "increment) ")
    return notes


class _AudioPlanItem(NamedTuple):
    """One planned audio bus clip. ``kind`` is ``"write"`` (a connected asset-clip
    at ``child_off``/``in_pt``/``dur_f`` frames) or ``"omit"`` (a loop bed / an
    unresolvable duration — carried only as the honest in-band ``note``). ``parent``
    is the index of the owning spine clip."""

    kind: str
    parent: int
    bus: str
    name: str
    clip: "AudioClip"
    child_off: int = 0
    in_pt: int = 0
    dur_f: int = 0
    note: str = ""


def _plan_audio(
    timeline: "Timeline", clips: list["VideoClip"], offsets: list[int],
    frames: list[int], in_frames: list[int], rate: Rate,
) -> tuple[list[_AudioPlanItem], list[str], dict[str, int], dict[str, str]]:
    """Plan the four audio buses into connected-clip items + their deduped asset
    resources (source → first-appearance order, widened available range).

    The faithful subset (addendum): a clip is WRITTEN when it is non-loop with a
    resolvable ``duration_ms``. A loop bed is fill-to-duration semantics (a single
    pass would be wrong audio) and a ``duration_ms is None`` clip resolves, in the
    render's ``_build_audio_graph``, to the source's natural length WITHOUT any
    static probe — so both are honestly OMITTED (a note records why) rather than
    faked. Connected clips need a spine host, so an empty video track draws no
    audio at all."""
    plan: list[_AudioPlanItem] = []
    order: list[str] = []
    avail: dict[str, int] = {}
    names: dict[str, str] = {}
    if not clips:
        return plan, order, avail, names
    for bus in _AUDIO_BUSES:
        for clip in getattr(timeline.tracks, bus):
            f = ms_to_frames(int(clip.start_ms), rate, Rounding.ROUND_HALF_UP)
            pi = _audio_parent_index(f, offsets, frames)
            name = _audio_name(clip.source)
            if clip.loop:
                plan.append(_AudioPlanItem(
                    "omit", pi, bus, name, clip,
                    note=(f" MANJU: audio '{name}' ({bus}) is a loop bed "
                          "(fill-to-duration) — a single pass would be wrong audio; "
                          "omitted this increment (a future loop may materialize "
                          "repeats) ")))
                continue
            if clip.duration_ms is None:
                plan.append(_AudioPlanItem(
                    "omit", pi, bus, name, clip,
                    note=(f" MANJU: audio '{name}' ({bus}) at {int(clip.start_ms)}ms "
                          "has no resolvable duration — the render plays the source's "
                          "natural length (no static probe in _build_audio_graph); "
                          "omitted rather than guessed ")))
                continue
            dur_f = ms_to_frames(int(clip.duration_ms), rate, Rounding.ROUND_HALF_UP)
            in_pt = ms_to_frames(int(clip.start_offset_ms or 0), rate,
                                 Rounding.ROUND_HALF_UP)
            need = in_pt + dur_f
            if clip.source not in avail:
                order.append(clip.source)
                avail[clip.source] = need
                names[clip.source] = name
            else:
                avail[clip.source] = max(avail[clip.source], need)
            plan.append(_AudioPlanItem(
                "write", pi, bus, name, clip,
                child_off=in_frames[pi] + f - offsets[pi], in_pt=in_pt, dur_f=dur_f))
    return plan, order, avail, names


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

    # --- connected audio lanes: plan every bus clip in deterministic order ----- #
    # Bus order voice/music/sfx/ambient, then clip-list order. A clip becomes a
    # CONNECTED asset-clip on its owning spine clip (found by absolute frame F);
    # loop beds and None-duration clips are honestly NOT written (an in-band note
    # records why), reusing the SAME asset dedup/widening as the video track.
    audio_plan, audio_order, audio_avail, audio_names = _plan_audio(
        timeline, clips, offsets, frames, in_frames, rate)

    asset_id = {src: f"r{n + 2}" for n, src in enumerate(order)}
    audio_id = {src: f"r{len(order) + n + 2}" for n, src in enumerate(audio_order)}
    has_dissolve = any(d > 0 for d in dissolves)
    effect_id = (f"r{len(order) + len(audio_order) + 2}" if has_dissolve else None)

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
    for src in audio_order:
        # audio source: hasAudio, no video format ref; available range widens to
        # cover in-point + window exactly like the video assets above.
        ET.SubElement(resources, "asset", {
            "id": audio_id[src],
            "name": audio_names[src],
            "src": src,               # project-relative (containment-checked)
            "start": "0s",
            "duration": _secs(audio_avail[src], rate),
            "hasAudio": "1",
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
    spine_clip_elems: list[ET.Element] = []
    for i, c in enumerate(clips):
        spine_clip_elems.append(ET.SubElement(spine, "asset-clip", {
            "ref": asset_id[c.source],
            "offset": _secs(offsets[i], rate),
            "name": c.shot,
            "start": _secs(in_frames[i], rate),
            "duration": _secs(frames[i], rate),
            "format": "r1",
            "tcFormat": "NDF",
        }))
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

    # Nest the planned connected audio clips INSIDE their owning spine clip. A
    # connected clip's ``offset`` is parent-relative (child.offset =
    # in_frames[i] + F − offsets[i]); it MAY extend past the parent's end (legal
    # FCPXML — never split). Notes (loop/None omission, ducking/fade approximation)
    # ride as in-band comments on the same parent so nothing is silently dropped.
    for item in audio_plan:
        parent = spine_clip_elems[item.parent]
        if item.kind == "write":
            clip, bus = item.clip, item.bus
            child = ET.SubElement(parent, "asset-clip", {
                "ref": audio_id[clip.source],
                "lane": str(_AUDIO_LANES[bus]),
                "offset": _secs(item.child_off, rate),
                "name": item.name,
                "start": _secs(item.in_pt, rate),
                "duration": _secs(item.dur_f, rate),
                "audioRole": _AUDIO_ROLES[bus],
            })
            if clip.gain_db:  # <adjust-volume> only when the gain is non-zero
                ET.SubElement(child, "adjust-volume",
                              {"amount": f"{clip.gain_db:g}dB"})
            for note in _audio_approx_notes(clip, bus, item.name):
                parent.append(ET.Comment(note))
        else:  # honest omission (loop bed / unresolvable duration) — a note only
            parent.append(ET.Comment(item.note))

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
