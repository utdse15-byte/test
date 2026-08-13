"""FCPXML WRITER — the one NLE exit where our rational time rides NATIVELY (FP
loop T2, roadmap §5 item 5 / §6).

This module WRITES. Reading FCPXML back is two separate paths, neither of them
here: :mod:`manju.exporters.fcpxml_import` plans an arbitrary third-party
document (PLAN ONLY, by design), and :mod:`manju.build.roundtrip` diffs a
document Manju itself exported against the baseline written below. The document
is a minimal-valid FCPXML 1.9 subset compiled from the SAME
``Timeline.tracks.video`` truth the render, OTIO and EDL exits read, so the
picture agrees clip-for-clip across every exit. It expresses exactly what the
timeline carries and is loud, in-band, about everything it does not.

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
  (legal — never split). A ``loop`` bed MATERIALIZES (FP loop W1) into
  whole-source passes + a trimmed tail when its natural length is probed and
  passed in ``loop_lengths`` — render parity with ``media/render.py``'s
  ``-stream_loop -1`` + atrim; pass boundaries are cumulative so the durations
  telescope to the clip's exact frame total. HONEST omissions (an in-band
  ``<!-- MANJU --> `` note, never faked): a ``loop`` bed with NO probed length
  (the pure default / a probe failure) stays fill-to-duration and is omitted (a
  single pass would be wrong audio); a ``duration_ms is None`` clip resolves in
  the render to the source's
  natural length with NO static probe, so it is not statically resolvable; a
  ``ducking`` clip IS written (static gain) but its render-time sidechain
  relationship is noted; audio fades are REAL native FCPXML fades (FP loop Y3 —
  the DTD chain ``adjust-volume`` → ``param name="amount"`` →
  ``fadeIn``/``fadeOut type="linear" duration=…``, from Apple's archived FCPXML
  v1.7 DTD; ms→frames ROUND_HALF_UP, an over-long fade clamps to the clip with an
  in-band note; a materialized loop pass carries no fade — a whole-source repeat).
  Speed ramps / multicam / nested sequences remain out of scope.
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


def _comment(text: str) -> ET.Element:
    """THE one owner of ``<!-- … -->`` emission in this module — every note goes
    through here so it is always well-formed. XML forbids ``--`` inside a comment
    and forbids a comment ending in ``-`` (XML §2.5). User-controlled names (shot
    ids, audio-source basenames) flow verbatim into these informational notes; a
    name like ``a--b`` or one ending in ``-`` otherwise produces a not-well-formed
    FCPXML document that FCP cannot open and that the sibling ``fcpxml_import``
    parser rejects. A note containing neither is emitted UNCHANGED, so
    every existing export stays byte-identical (round-trip identity lives in the
    real ``name`` attributes, never in these comments)."""
    if "--" in text:
        text = text.replace("--", "—")  # em dash — kills every ASCII ``--`` run
    if text.endswith("-"):
        text += " "
    return ET.Comment(text)


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


def _audio_approx_notes(
    clip: "AudioClip", bus: str, name: str, *, fades_expressed: bool = False,
) -> list[str]:
    """Honest in-band notes for a connected clip whose render-time behaviour
    FCPXML does not carry: the ducking sidechain relationship (the clip still
    plays at its static gain — the mix is never silently claimed) and, WHEN the
    fades are NOT expressed as native elements, the un-expressed fade handles.

    ``fades_expressed`` is True on a WRITTEN clip (Y3 emits real
    ``<param><fadeIn/><fadeOut/></param>`` — see :func:`_emit_adjust_volume`), so
    no fade note is added there. It is False on a MATERIALIZED loop: each pass
    repeats the WHOLE source, so a per-pass fade handle would be wrong — the loop
    carries no fade, and that omission is stated here (never silent)."""
    notes: list[str] = []
    if clip.ducking:
        notes.append(
            f" MANJU: audio '{name}' ({bus}) ducking is a render-time sidechain "
            "relationship (keyed under the voice bus) not expressible in FCPXML — "
            "the clip is written at its static gain; the ducking mix is NOT "
            "represented ")
    if not fades_expressed:
        fi, fo = int(clip.fade_in_ms or 0), int(clip.fade_out_ms or 0)
        if fi > 0 or fo > 0:
            notes.append(
                f" MANJU: audio '{name}' ({bus}) fade_in {fi}ms / fade_out {fo}ms "
                "not expressed as a native fade — a materialized loop repeats the "
                "whole source per pass (render parity: -stream_loop) and carries "
                "no fade handles ")
    return notes


def _fade_frames(
    clip: "AudioClip", dur_f: int, bus: str, name: str, rate: Rate,
) -> tuple[int, int, list[str]]:
    """The clip's fadeIn/fadeOut lengths as EXACT whole frames (ms → frames
    ROUND_HALF_UP), each CLAMPED to the connected clip's frame length ``dur_f``.

    A fade longer than the whole clip is not a valid FCPXML fade, so it clamps to
    the clip length and records an honest in-band note (the original over-long ms
    is named — never a silent truncation, never an emitted over-long fade).
    Returns ``(fade_in_frames, fade_out_frames, clamp_notes)``."""
    notes: list[str] = []
    fi_ms, fo_ms = int(clip.fade_in_ms or 0), int(clip.fade_out_ms or 0)
    fi = ms_to_frames(fi_ms, rate, Rounding.ROUND_HALF_UP) if fi_ms > 0 else 0
    fo = ms_to_frames(fo_ms, rate, Rounding.ROUND_HALF_UP) if fo_ms > 0 else 0
    if fi > dur_f:
        notes.append(
            f" MANJU: audio '{name}' ({bus}) fade_in {fi_ms}ms ({fi} frames) is "
            f"longer than the clip ({dur_f} frames) — clamped to the clip length ")
        fi = dur_f
    if fo > dur_f:
        notes.append(
            f" MANJU: audio '{name}' ({bus}) fade_out {fo_ms}ms ({fo} frames) is "
            f"longer than the clip ({dur_f} frames) — clamped to the clip length ")
        fo = dur_f
    return fi, fo, notes


def _emit_adjust_volume(
    parent_clip: ET.Element, clip: "AudioClip", rate: Rate,
    fi_frames: int = 0, fo_frames: int = 0,
) -> None:
    """Emit ``<adjust-volume>`` on a connected asset-clip carrying gain and/or a
    native fade (the DTD chain, Apple FCPXML v1.7):

        <adjust-volume amount="{gain:g}dB">
          <param name="amount">
            <fadeIn  type="linear" duration="N/Ds"/>
            <fadeOut type="linear" duration="N/Ds"/>
          </param>
        </adjust-volume>

    * ``amount`` is always ``"{gain:g}dB"`` (``0.0`` → ``"0dB"``, harmless — the
      DTD default for ``amount`` is ``"0dB"``). V1's drop-when-zero is preserved
      ONLY when there are NO fades: with a fade, ``adjust-volume`` is the REQUIRED
      container (``<!ELEMENT adjust-volume (param*)>``,
      ``<!ELEMENT param (fadeIn?, fadeOut?, …)>``), so it opens even at 0 gain.
    * ``type="linear"`` is DELIBERATE — it mirrors the render's afade default
      (triangular/linear) so the exported curve matches what we actually render.
    * the param name string ``"amount"`` is a CONVENTION: the DTD requires a
      ``name`` ATTRIBUTE (``#REQUIRED``) but does NOT mandate this string. This is
      recorded here and in REPORTS/FP_FCPXML_FADES.md; it is never claimed as
      DTD-mandated.

    ``fi_frames``/``fo_frames`` default to 0 (the loop path passes neither — a
    materialized loop pass carries gain only, no fade)."""
    has_fades = fi_frames > 0 or fo_frames > 0
    if not clip.gain_db and not has_fades:
        return  # V1 drop-when-absent — byte-identical to the gain-only branch
    av = ET.SubElement(parent_clip, "adjust-volume", {"amount": f"{clip.gain_db:g}dB"})
    if not has_fades:
        return
    param = ET.SubElement(av, "param", {"name": "amount"})  # name is a convention
    if fi_frames > 0:
        ET.SubElement(param, "fadeIn",
                      {"type": "linear", "duration": _secs(fi_frames, rate)})
    if fo_frames > 0:
        ET.SubElement(param, "fadeOut",
                      {"type": "linear", "duration": _secs(fo_frames, rate)})


class _AudioPlanItem(NamedTuple):
    """One planned audio bus clip. ``kind`` is ``"write"`` (a connected asset-clip
    at ``child_off``/``in_pt``/``dur_f`` frames), ``"loop"`` (one materialized
    whole-source pass — same shape as ``"write"`` with ``in_pt`` 0, the first pass
    carrying the materialization ``note``), or ``"omit"`` (a loop bed with no
    probed length / an unresolvable duration — carried only as the honest in-band
    ``note``). ``parent`` is the index of the owning spine clip."""

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
    loop_lengths: dict[str, int] | None = None,
) -> tuple[list[_AudioPlanItem], list[str], dict[str, int], dict[str, str]]:
    """Plan the four audio buses into connected-clip items + their deduped asset
    resources (source → first-appearance order, widened available range).

    The faithful subset (addendum): a clip is WRITTEN when it is non-loop with a
    resolvable ``duration_ms``. A loop bed MATERIALIZES into whole-source passes +
    a trimmed tail WHEN its source carries a probed natural length in
    ``loop_lengths`` (render parity with ``media/render.py``'s ``-stream_loop -1``
    + atrim); without a probed length — the pure default ``None``, a probe
    failure, or a non-positive/None duration — it is honestly OMITTED (a note
    records why) rather than faked. A ``duration_ms is None`` non-loop clip
    resolves, in the render's ``_build_audio_graph``, to the source's natural
    length WITHOUT any static probe, so it too is omitted. Connected clips need a
    spine host, so an empty video track draws no audio at all."""
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
                nat_ms = None if loop_lengths is None else loop_lengths.get(clip.source)
                if (nat_ms is not None and int(nat_ms) > 0
                        and clip.duration_ms is not None and int(clip.duration_ms) > 0):
                    # MATERIALIZE: whole-source passes + a trimmed tail. Boundaries
                    # are CUMULATIVE — b_k = ms_to_frames(min(k·N, D)) — so the pass
                    # durations telescope to the clip's exact whole-frame total with
                    # no per-pass rounding drift (same argument as R2). Each pass
                    # repeats the WHOLE source from 0 (parity with -stream_loop).
                    d_ms, n_ms = int(clip.duration_ms), int(nat_ms)
                    kk = (d_ms + n_ms - 1) // n_ms          # ceil(D/N), exact int
                    bounds = [ms_to_frames(min(k * n_ms, d_ms), rate,
                                           Rounding.ROUND_HALF_UP)
                              for k in range(kk + 1)]
                    first = True
                    for k in range(kk):
                        dur_f = bounds[k + 1] - bounds[k]
                        if dur_f <= 0:                      # sub-frame pass — skip
                            continue
                        abs_f = f + bounds[k]
                        ppi = _audio_parent_index(abs_f, offsets, frames)
                        if clip.source not in avail:        # dedup + widen like V1
                            order.append(clip.source)
                            avail[clip.source] = dur_f
                            names[clip.source] = name
                        else:
                            avail[clip.source] = max(avail[clip.source], dur_f)
                        note = ""
                        if first:                           # note on first pass only
                            note = (f" MANJU: audio '{name}' ({bus}) materialized "
                                    f"loop ({kk} passes, natural {n_ms}ms, render "
                                    "parity: -stream_loop) ")
                            first = False
                        plan.append(_AudioPlanItem(
                            "loop", ppi, bus, name, clip,
                            child_off=in_frames[ppi] + abs_f - offsets[ppi],
                            in_pt=0, dur_f=dur_f, note=note))
                    if not first:                           # at least one pass emitted
                        continue
                # honest omission: no probed natural length (pure default / probe
                # failure / zero) or no fill target — a single pass would be wrong
                # audio. Byte-identical to the pre-materialization behaviour.
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


def compile_fcpxml(
    timeline: "Timeline", *, rate: Rate, name: str = "",
    loop_lengths: dict[str, int] | None = None,
) -> str:
    """The full FCPXML 1.9 document as a string (LF endings, trailing newline,
    deterministic bytes).

    ``rate`` is the EXACT edit rate (a :class:`~manju.core.timebase.Rate`) — pass
    ``timeline.frame_rate`` (the rational echo when present, else the int ``fps``
    promoted to a whole-number rate). ``name`` becomes the event/project name.
    Times are exact rational-seconds strings on ``rate``'s ``frameDuration``
    timescale; every clip boundary is a whole frame, so there is no drift for
    int OR 1001-family projects (the rational-native advantage).

    ``loop_lengths`` (source → probed natural ms) is the PURE materialization
    seam. ``None`` (the default) keeps today's behaviour byte-identical: loop
    beds are omitted with the honest in-band note. When a loop bed's source
    carries a positive entry, the bed MATERIALIZES into whole-source passes + a
    trimmed tail (render parity with ``-stream_loop``); the IO layer
    :func:`export_fcpxml` fills this dict by probing. A source left out of the
    dict (probe failure / zero) takes the honest omission path — never a faked
    length. This function performs NO IO of its own.
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
        timeline, clips, offsets, frames, in_frames, rate, loop_lengths)

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
            spine.append(_comment(
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
            # Native fades (Y3): ms → whole frames (ROUND_HALF_UP), each clamped to
            # the clip length; emit gain and/or the fade container on the clip. A
            # zero-gain zero-fade clip still drops the element (V1 byte-identity).
            fi_f, fo_f, clamp_notes = _fade_frames(
                clip, item.dur_f, bus, item.name, rate)
            _emit_adjust_volume(child, clip, rate, fi_f, fo_f)
            for note in clamp_notes:  # honest note when an over-long fade clamped
                parent.append(_comment(note))
            # fades ARE expressed here, so no gain-only fade note; ducking unchanged.
            for note in _audio_approx_notes(clip, bus, item.name, fades_expressed=True):
                parent.append(_comment(note))
        elif item.kind == "loop":
            # One materialized whole-source pass — same asset-clip shape as a
            # written clip, but ``start`` is always 0 (the source repeats from the
            # top, parity with -stream_loop). Gain rides EVERY pass. The first
            # pass (note set) also carries the materialization note + any
            # ducking/fade approximation notes, emitted ONCE.
            clip, bus = item.clip, item.bus
            child = ET.SubElement(parent, "asset-clip", {
                "ref": audio_id[clip.source],
                "lane": str(_AUDIO_LANES[bus]),
                "offset": _secs(item.child_off, rate),
                "name": item.name,
                "start": _secs(item.in_pt, rate),  # 0s — whole-source repeat
                "duration": _secs(item.dur_f, rate),
                "audioRole": _AUDIO_ROLES[bus],
            })
            # Gain rides EVERY pass; a materialized loop pass carries NO fade (each
            # pass repeats the whole source, so a per-pass fade would be wrong) —
            # _emit_adjust_volume with no fade frames is byte-identical to V1/W1.
            _emit_adjust_volume(child, clip, rate)
            if item.note:  # first pass only
                parent.append(_comment(item.note))
                # fades NOT expressed on the passes → the honest fade note (if any).
                for note in _audio_approx_notes(clip, bus, item.name):
                    parent.append(_comment(note))
        else:  # honest omission (loop bed / unresolvable duration) — a note only
            parent.append(_comment(item.note))

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


def _probe_loop_lengths(project: "Project", timeline: "Timeline") -> dict[str, int]:
    """Probe the NATURAL duration (ms) of every distinct loop-bed source via the
    EXISTING media probe machinery (``media.probe.probe_duration_ms`` — the same
    prober render/compile use; never a second one) so the pure
    :func:`compile_fcpxml` can materialize whole passes + a trimmed tail.

    A source that cannot be resolved (out of project), fails to probe, or reports
    a non-positive/None duration is LEFT OUT of the dict, so the compiler takes
    the honest omission path for it — never a fabricated length. This is the ONE
    IO seam; ``probe_duration_ms`` itself never raises."""
    from ..media.probe import probe_duration_ms

    lengths: dict[str, int] = {}
    for bus in _AUDIO_BUSES:
        for clip in getattr(timeline.tracks, bus):
            if not clip.loop or clip.source in lengths:
                continue
            try:
                abspath = project.resolve(clip.source)
            except Exception:
                continue  # out-of-project loop source → honest omission, no probe
            dur = probe_duration_ms(abspath)
            if dur is not None and int(dur) > 0:
                lengths[clip.source] = int(dur)
    return lengths


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

    This IO layer probes every loop-bed source (:func:`_probe_loop_lengths`) so
    the pure compiler can MATERIALIZE the beds into whole-source passes + a
    trimmed tail (render parity with ``-stream_loop``); an unprobeable source is
    honestly omitted, never faked.
    """
    config = project.load_config()
    for c in timeline.tracks.video:
        _require_contained_source(project, c.source, label=f"{c.shot}/{c.take}")
    # audio buses too — "the SAME containment semantics render/OTIO enforce"
    # (otio._audio_clip checks every bus clip; only checking video let an
    # out-of-project audio path ride verbatim into the document)
    for bus in _AUDIO_BUSES:
        for ac in getattr(timeline.tracks, bus):
            _require_contained_source(
                project, ac.source,
                label=f"{bus}:{Path(ac.source).stem or bus}")
    rate = timeline.frame_rate
    loop_lengths = _probe_loop_lengths(project, timeline)
    text = compile_fcpxml(timeline, rate=rate, name=config.name,
                          loop_lengths=loop_lengths)
    path = (Path(dest) if dest is not None
            else project.exports_dir / "fcpxml" / f"{config.name}.fcpxml")
    atomic_write_text(path, text)
    # WP6: baseline for round-trip (derived, never fails export).
    #
    # Without this an FCPXML edit had nothing to diff against, so a Resolve
    # round-trip could not be planned at all. The baseline stores the same
    # PROJECTION the round-trip reader builds — not the XML text — because a
    # baseline payload is JSON, and because both sides must be read by one
    # parser or "the clip moved" could mean two different things.
    try:
        from ..build.roundtrip import fcpxml_projection, write_baseline

        from .fcpxml_import import parse_fcpxml

        write_baseline(
            project, "fcpxml", path.stem, fcpxml_projection(parse_fcpxml(text)),
            compiled_from=str(timeline.meta.compiled_from or ""),
        )
    except Exception:
        pass
    return path
