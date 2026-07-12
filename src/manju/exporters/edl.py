"""CMX3600 EDL WRITER — a video cut list with real SMPTE timecode (FP loop S2,
roadmap §5 item 5 / §6).

WRITER only — there is no EDL import path this loop. The document is a classic
CMX3600 edit decision list compiled from the SAME ``Timeline.tracks.video`` truth
the render and OTIO export read, so the cut list agrees event-for-event with the
picture. It expresses exactly what the timeline actually carries and is loud,
in-band, about everything it cannot:

Record timecode (the cut list's spine)
--------------------------------------
Each clip occupies ``[start_ms, start_ms+duration_ms)`` on the record timeline
(the compiler lays clips abutting — ``timeline/compiler.py`` advances a single
``cursor`` — so event N's record-out equals event N+1's record-in exactly). The
record TC is that cumulative position converted ms→frames via
:mod:`manju.core.timebase` (``ms_to_frames``, ROUND_HALF_UP) at the timeline
rate, offset by ``start_timecode`` (default ``01:00:00:00`` — the broadcast
hour). Durations are taken FROM THE RECORD SIDE and reused on the source side so
a clip's source length always equals its record length (a cut list must have
matching handles); this also makes continuity exact under rounding.

Frame-code mode (DROP / NON-DROP)
---------------------------------
Integer rates (24/25/30/50/60 …) are NON-DROP: a single ``FCM: NON-DROP FRAME``
header, all-colon ``HH:MM:SS:FF`` timecodes. When the timeline carries the R2
rational ``edit_rate`` echo (consumed defensively — ``getattr`` with an int-fps
fallback) OR the project declares a drop-frame-legal NTSC rate (29.97 / 59.94),
the record TC is DROP FRAME: an ``FCM: DROP FRAME`` header and ``HH:MM:SS;FF``
(semicolon before the frames field — the CMX-legal drop convention that
:class:`manju.core.timebase.Timecode` already renders). Drop-frame is legal ONLY
for 30000/1001 and 60000/1001 (23.976 and 48000/1001 stay NON-DROP though NTSC),
exactly as the timebase enforces.

Source timecode honesty
-----------------------
Takes carry NO recorded source timecode today (no sidecar reel/TC — audited:
:class:`manju.core.models.TakeSidecar` has none). Generated media's own timebase
is zero-based, so its honest source TC IS ``00:00:00:00``-based: a clip's source
in-point is ``ms_to_frames(source_in_ms)`` and its source-out is that plus the
record duration. A virtual trim (``source_in_ms > 0``) therefore rides through
truthfully; an untrimmed clip reads from ``00:00:00:00``. This is stated in the
conform-loss report, never dressed up as real tape TC.

Reel names
----------
CMX reels are ≤ 8 ASCII chars. The reel is a deterministic 8-char fold of the
take name (uppercased ``[A-Z0-9]``; an all-non-ASCII name — e.g. CJK — folds to
a hash-derived ASCII reel so the column stays legal). Distinct take names that
fold to the same 8 chars are disambiguated deterministically by a numeric suffix
in timeline order; the SAME take always maps to the SAME reel. The full,
untruncated take name (UTF-8, CJK and all) rides the ``* FROM CLIP NAME:``
comment — columns stay ASCII, identity is never lost.

Transitions
-----------
V-track cut (``C``) events are the honest core. A clip whose ``transition_out``
is a clean cross-dissolve (``xfade_fade``, duration > 0) becomes a standard
CMX3600 two-line dissolve on the INCOMING event: a zero-duration ``C`` "from"
line freezing the outgoing frame, then a ``D`` line dissolving into the incoming
clip over the transition's frame count (``* FROM CLIP NAME`` / ``* TO CLIP
NAME`` comments name both). EVERY OTHER transition kind — dip-to-black
``fade`` (which would need BL-reel events), the ``xfade_*`` wipes/slides (CMX
``W`` events, out of scope this loop), and any unknown type — degrades to a hard
cut with an in-band ``* MANJU:`` note stating the loss. A wrong dissolve is
never emitted.

Audio (a DECLARED A1/A2 subset — never a silent squeeze)
--------------------------------------------------------
The V block is followed by a declared two-channel audio subset: the VOICE bus
becomes A1 events (CMX channel token ``A``) and the MUSIC bus becomes A2 events
(token ``A2``). Each placeable audio clip is one CMX cut event whose SOURCE TC
is zero-based from ``start_offset_ms`` (the same generated-media honesty S2
applies to video ``source_in_ms`` — it is stated as such, never dressed up as
real tape TC) and whose RECORD TC rides the SAME timebase machinery as the
picture (``ms_to_frames`` ROUND_HALF_UP, offset by the start frame). Source
length reuses the record frame count. Event numbering is a SINGLE monotone
sequence across V → A1 → A2 (blocked-by-track: audio events follow the whole V
block, which keeps every V event byte-identical to the V-only world and needs no
cross-track record-TC tie-break).

sfx + ambient are NOT squeezed into channels — the classic CMX3600 form has no
third/fourth stereo pair, so every sfx/ambient clip is recorded as an in-band
``* MANJU:`` omission note naming its bus/source/window instead of a fabricated
channel. Gain and fades are not expressible in bare CMX3600 → a per-clip note
whenever nonzero (never silent). A looped bed (``loop=True``) or an open-ended
clip (``duration_ms is None``) is NEVER fabricated into a single cut event — it
gets an omission note (loop is materialized by repetition at render time; an
open clip has no frame-exact out-point). Ducking has no CMX primitive at all and
stays out of scope (classified by the conform audit, not per clip).

Scope (loud, not silent)
------------------------
V + the declared A1/A2 audio subset above. Overlays/titles and captions are not
in a cut list (SRT/TTML are the caption exits). Speed ramps, nested sequences
and multicam are out of scope. All of this is classified per feature by
:mod:`manju.exporters.conform` (target ``"edl"``).

Determinism: fixed header, fixed column layout, LF endings, trailing newline,
deterministic reels, no wall-clock — the same timeline renders byte-identical
EDL forever.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.hashing import hash_text, short_hash
from ..core.timebase import Rate, Timecode, ms_to_frames
from ..core.yamlio import atomic_write_text

if TYPE_CHECKING:  # avoid an import cycle at module load (srt_ass/ttml stance)
    from ..core.container import Project
    from ..core.models import Timeline, VideoClip

__all__ = ["compile_edl", "export_edl"]

# CMX reel column limit (chars). Channel-column labels (CMX3600 tokens): V is
# the picture track; A is A1 audio, A2 is A2 audio. Manju maps the VOICE bus to
# A1 and the MUSIC bus to A2 — a DECLARED two-channel subset (see the module
# docstring's Audio section). sfx/ambient have no classic third/fourth pair and
# are omitted with in-band notes rather than squeezed into a channel.
_REEL_MAX = 8
_CHANNEL = "V"
_CH_A1 = "A"    # voice bus -> A1
_CH_A2 = "A2"   # music bus -> A2
# (bus attribute, channel token) for the buses that become audio events, in the
# order they are emitted after the V block. sfx/ambient are NOT here.
_AUDIO_EMIT_BUSES = (("voice", _CH_A1), ("music", _CH_A2))
# Buses with no CMX channel in the classic form — recorded as omission notes.
_AUDIO_OMIT_BUSES = ("sfx", "ambient")

# The dissolve family that maps CLEANLY onto a CMX ``D`` event. Only the true
# cross-dissolve; wipes/slides are ``W`` events (out of scope) and dip-to-black
# would need BL-reel events — both degrade to a cut + an in-band note instead of
# a wrong dissolve (see the module docstring).
_CLEAN_DISSOLVE_TYPES = frozenset({"xfade_fade"})

# Drop-frame is legal only for these two NTSC rates (mirrors timebase._DF_LEGAL
# via the public Rate API: NTSC family AND a 30/60 nominal label).
_DF_NOMINALS = frozenset({30, 60})

_NON_ASCII_ALNUM = re.compile(r"[^A-Z0-9]")
_TC_SPLIT = re.compile(r"[:;.]")
# Printable ASCII for the TITLE line (columns stay ASCII; the full project name
# is header-only, not a clip identity — the take names ride the comments).
_TITLE_UNSAFE = re.compile(r"[^\x20-\x7E]")


def _rate_is_drop_legal(rate: Rate) -> bool:
    """True for 30000/1001 and 60000/1001 — the only drop-frame-legal rates."""
    return rate.is_ntsc and rate.nominal_int in _DF_NOMINALS


def _reel_base(take: str) -> str:
    """8-char ASCII reel stem from a take name. Uppercased ``[A-Z0-9]`` kept;
    when nothing survives (an all-CJK / all-symbol name) fall back to a
    hash-derived ASCII reel so the reel column is ALWAYS legal."""
    cleaned = _NON_ASCII_ALNUM.sub("", take.upper())
    if not cleaned:
        cleaned = short_hash(hash_text(take), _REEL_MAX).upper()
    return cleaned[:_REEL_MAX]


def _assign_reels(clips: "list[VideoClip]") -> dict[str, str]:
    """Deterministic ``take name -> 8-char reel``. Distinct takes folding to the
    same stem are disambiguated by a numeric suffix in timeline order; the same
    take always yields the same reel."""
    reels: dict[str, str] = {}
    used: set[str] = set()
    for clip in clips:
        take = clip.take
        if take in reels:
            continue
        base = _reel_base(take)
        reel = base
        n = 2
        while reel in used:
            suffix = str(n)
            reel = base[: max(0, _REEL_MAX - len(suffix))] + suffix
            n += 1
        used.add(reel)
        reels[take] = reel
    return reels


def _ascii_title(name: str) -> str:
    """ASCII-fold a project name for the TITLE line (non-ASCII → ``_``, runs
    collapsed). A wholly non-ASCII name (e.g. CJK) folds to ``UNTITLED`` — the
    editorial identity of media rides the ``* FROM CLIP NAME`` comments, not the
    header."""
    folded = re.sub(r"_+", "_", _TITLE_UNSAFE.sub("_", name)).strip("_ ").strip()
    return folded or "UNTITLED"


def _start_frame(start_timecode: str, rate: Rate, drop_frame: bool) -> int:
    """Frame index of ``start_timecode`` interpreted in the active DF mode.

    The record spine is offset by this so the first event starts at (default)
    the broadcast hour ``01:00:00:00``. Validated by ``Timecode.to_frames`` (a
    dropped frame number that does not exist at DF raises)."""
    parts = _TC_SPLIT.split(start_timecode.strip())
    if len(parts) != 4:
        raise ValueError(f"malformed start_timecode: {start_timecode!r}")
    try:
        h, m, s, f = (int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"malformed start_timecode: {start_timecode!r}") from exc
    return Timecode(h, m, s, f, rate, drop_frame).to_frames()


def _tc(frame: int, rate: Rate, drop_frame: bool) -> str:
    """Render a frame index as a CMX-legal timecode (colon NDF, ``;`` DF)."""
    return str(Timecode.from_frames(frame, rate, drop_frame))


def _event_line(num: int, reel: str, channel: str, op: str, tdur: str,
                src_in: str, src_out: str, rec_in: str, rec_out: str) -> str:
    """One fixed-column CMX3600 event row. ``channel`` is the track-column token
    (``V`` picture, ``A``/``A2`` audio). ``tdur`` is the 3-digit transition
    frame count for a ``D`` event, or ``""`` (blank column) for a cut."""
    return (f"{num:03d}  {reel:<{_REEL_MAX}} {channel:<4} {op:<4} {tdur:>3} "
            f"{src_in} {src_out} {rec_in} {rec_out}")


class _Placed:
    """A video clip resolved onto the record/source frame grids."""

    __slots__ = ("clip", "reel", "rec_in", "rec_out", "src_in", "src_out")

    def __init__(self, clip: "VideoClip", reel: str, start_frame: int, rate: Rate):
        start_ms = int(clip.start_ms)
        # R4 (S2 left duration_frames unconsumed): a rational (R2-compiled) clip
        # carries the EXACT whole-frame length as ``duration_frames`` — consume it
        # so the record/source spans are frame-exact instead of the ms→frame
        # telescoping difference (which the ms grid cannot hold precisely for the
        # 1001 family). When it is absent (every int project — the field is None),
        # fall back to the ms difference: BYTE-IDENTICAL to S2's golden pins. The
        # record IN still telescopes via ``ms_to_frames(start_ms)``, which for an
        # R2-compiled timeline recovers the exact cumulative frame (start_ms is
        # ``frames_to_ms(cum)`` and ms_to_frames∘frames_to_ms is the identity),
        # so continuity (event N out == event N+1 in) stays exact on both paths.
        df = getattr(clip, "duration_frames", None)
        if df is not None:
            dur_frames = int(df)
        else:
            dur_frames = (ms_to_frames(start_ms + int(clip.duration_ms), rate)
                          - ms_to_frames(start_ms, rate))
        self.clip = clip
        self.reel = reel
        self.rec_in = start_frame + ms_to_frames(start_ms, rate)
        self.rec_out = self.rec_in + dur_frames
        self.src_in = ms_to_frames(int(clip.source_in_ms or 0), rate)
        self.src_out = self.src_in + dur_frames


def _is_clean_dissolve(clip: "VideoClip | None") -> bool:
    t = clip.transition_out if clip is not None else None
    return t is not None and t.type in _CLEAN_DISSOLVE_TYPES and t.duration_ms > 0


def _is_degraded_transition(clip: "VideoClip") -> bool:
    """A set transition that CMX cannot carry cleanly and we degrade to a cut."""
    t = clip.transition_out
    if t is None or t.type == "cut":
        return False  # no transition / an explicit hard cut — nothing lost
    return t.type not in _CLEAN_DISSOLVE_TYPES  # xfade_fade(>0) is the clean case


# --------------------------------------------------------------------------- #
# audio (declared A1/A2 subset — voice -> A, music -> A2)                       #
# --------------------------------------------------------------------------- #


def _assign_audio_reels(clips: "list", used: set[str]) -> dict[str, str]:
    """Deterministic ``source path -> 8-char reel`` for audio clips, folded from
    the source stem and disambiguated against ``used`` (the reels already handed
    to the V events) so the reel column stays a SINGLE namespace: an audio
    source that folds to a video reel's stem gets a numeric suffix, never a
    silent collision. The same source always maps to the same reel."""
    reels: dict[str, str] = {}
    for clip in clips:
        ident = clip.source
        if ident in reels:
            continue
        base = _reel_base(Path(ident).stem)
        reel = base
        n = 2
        while reel in used:
            suffix = str(n)
            reel = base[: max(0, _REEL_MAX - len(suffix))] + suffix
            n += 1
        used.add(reel)
        reels[ident] = reel
    return reels


def _audio_omit_reason(clip) -> str | None:
    """Why an audio clip cannot become an HONEST single cut event, or ``None``
    when it can. An open-ended clip (``duration_ms is None``) has no frame-exact
    out-point; a looped bed (``loop=True``) is materialized by repetition at
    render time and has no single source window — emitting one event would
    fabricate source that is not there. Both get an omission note, never an
    event."""
    if clip.duration_ms is None:
        return (f"start {clip.start_ms}ms omitted - open-ended (no duration_ms); "
                "CMX3600 needs a frame-exact out-point")
    if clip.loop:
        end = int(clip.start_ms) + int(clip.duration_ms)
        return (f"[{clip.start_ms}..{end}ms) loop=True omitted - CMX3600 has no "
                "loop primitive; the render materializes the bed by repetition "
                "(no honest single-event source window)")
    return None


def _audio_loss_notes(bus: str, clip) -> list[str]:
    """Per-clip ``* MANJU:`` notes for a PLACED audio clip whose gain/fades bare
    CMX3600 cannot carry (a mix decision, not a cut-list primitive) — nonzero
    only, and never silent."""
    notes: list[str] = []
    if clip.gain_db:
        notes.append(
            f"* MANJU: {bus} clip '{clip.source}' gain {clip.gain_db:+g}dB not "
            "expressible in bare CMX3600 (a mix level, not a cut-list primitive)")
    fi = int(clip.fade_in_ms or 0)
    fo = int(clip.fade_out_ms or 0)
    if fi or fo:
        notes.append(
            f"* MANJU: {bus} clip '{clip.source}' fades (in {fi}ms / out {fo}ms) "
            "not expressible in bare CMX3600")
    return notes


def _audio_window(clip) -> str:
    """``[start..end ms)`` for a bounded clip, ``start Nms`` for an open one."""
    if clip.duration_ms is None:
        return f"start {clip.start_ms}ms"
    return f"[{clip.start_ms}..{int(clip.start_ms) + int(clip.duration_ms)}ms)"


def _emit_audio(timeline: "Timeline", start_frame: int, rate: Rate,
                tc, used_reels: set[str], next_num: int) -> list[str]:
    """The declared A1/A2 audio blocks, appended AFTER the V events. voice -> A
    (A1), music -> A2; each placeable clip is one CMX cut event whose SOURCE TC
    is zero-based from ``start_offset_ms`` (generated-media honesty, the S2
    video precedent) and whose RECORD TC rides the same timebase machinery as
    the picture. Event numbers continue the single monotone V-then-A1-then-A2
    sequence; omitted clips (loop / open-ended, and the whole sfx/ambient buses)
    consume no number and are recorded as in-band notes."""
    tracks = timeline.tracks
    lines: list[str] = []

    for bus, channel in _AUDIO_EMIT_BUSES:
        clips = list(getattr(tracks, bus))
        reels = _assign_audio_reels(clips, used_reels)
        for clip in clips:
            reason = _audio_omit_reason(clip)
            if reason is not None:
                lines.append(f"* MANJU: {bus} clip '{clip.source}' {reason}")
                continue
            start_ms = int(clip.start_ms)
            dur_frames = (ms_to_frames(start_ms + int(clip.duration_ms), rate)
                          - ms_to_frames(start_ms, rate))
            rec_in = start_frame + ms_to_frames(start_ms, rate)
            rec_out = rec_in + dur_frames
            src_in = ms_to_frames(int(clip.start_offset_ms or 0), rate)
            src_out = src_in + dur_frames
            lines.append(_event_line(
                next_num, reels[clip.source], channel, "C", "",
                tc(src_in), tc(src_out), tc(rec_in), tc(rec_out)))
            lines.append(f"* FROM CLIP NAME: {clip.source}")
            lines.extend(_audio_loss_notes(bus, clip))
            next_num += 1

    # sfx + ambient: NOT squeezed into a channel — a note per omitted clip names
    # its bus / source / window so nothing vanishes silently.
    for bus in _AUDIO_OMIT_BUSES:
        for clip in getattr(tracks, bus):
            lines.append(
                f"* MANJU: {bus} clip '{clip.source}' {_audio_window(clip)} "
                "omitted - CMX3600 classic form carries A1/A2 only "
                "(voice->A1, music->A2); sfx/ambient have no channel")
    return lines


def compile_edl(
    timeline: "Timeline",
    *,
    rate: Rate,
    drop_frame: bool | None = None,
    start_timecode: str = "01:00:00:00",
    title: str = "",
) -> str:
    """The full CMX3600 EDL as a string (LF endings, trailing newline,
    deterministic bytes).

    ``rate`` is the record/source frame rate (a :class:`~manju.core.timebase.Rate`).
    ``drop_frame`` ``None`` auto-derives the frame-code mode from the rate
    (29.97/59.94 ⇒ DROP FRAME, everything else NON-DROP); pass ``True``/``False``
    to force it. ``title`` becomes the ASCII-folded ``TITLE:`` header.
    """
    if drop_frame is None:
        drop_frame = _rate_is_drop_legal(rate)
    if drop_frame and not _rate_is_drop_legal(rate):
        raise ValueError(
            f"drop-frame EDL requested at {rate}, but drop-frame is legal only "
            "for 30000/1001 and 60000/1001")

    start_frame = _start_frame(start_timecode, rate, drop_frame)
    clips = list(timeline.tracks.video)
    reels = _assign_reels(clips)
    placed = [_Placed(c, reels[c.take], start_frame, rate) for c in clips]

    out: list[str] = [
        f"TITLE:   {_ascii_title(title)}",
        f"FCM: {'DROP FRAME' if drop_frame else 'NON-DROP FRAME'}",
    ]

    def tc(frame: int) -> str:
        return _tc(frame, rate, drop_frame)

    for i, p in enumerate(placed):
        num = i + 1
        prev = placed[i - 1] if i > 0 else None
        incoming = prev if _is_clean_dissolve(prev.clip if prev else None) else None

        if incoming is not None:
            # Standard CMX3600 dissolve: a zero-duration "from" line freezing the
            # outgoing frame, then the "D" line dissolving into this clip.
            dur_frames = ms_to_frames(int(incoming.clip.transition_out.duration_ms), rate)
            a_out = tc(incoming.src_out)
            b_recin = tc(p.rec_in)
            out.append(_event_line(num, incoming.reel, _CHANNEL, "C", "",
                                   a_out, a_out, b_recin, b_recin))
            out.append(_event_line(num, p.reel, _CHANNEL, "D", f"{dur_frames:03d}",
                                   tc(p.src_in), tc(p.src_out),
                                   b_recin, tc(p.rec_out)))
            out.append(f"* FROM CLIP NAME: {incoming.clip.take}")
            out.append(f"* TO CLIP NAME: {p.clip.take}")
        else:
            out.append(_event_line(num, p.reel, _CHANNEL, "C", "",
                                   tc(p.src_in), tc(p.src_out),
                                   tc(p.rec_in), tc(p.rec_out)))
            out.append(f"* FROM CLIP NAME: {p.clip.take}")

        # In-band honesty for a transition CMX cannot carry cleanly (the note
        # sits with the clip that OWNS the out-edge transition).
        if _is_degraded_transition(p.clip):
            t = p.clip.transition_out
            out.append(
                f"* MANJU: transition '{t.type}' ({t.duration_ms}ms) at "
                f"{p.clip.shot} approximated as hard cut (no clean CMX3600 "
                "dissolve mapping)")

    # The declared A1/A2 audio subset, appended AFTER the V block (blocked-by-
    # track, single monotone numbering continuing from the last V event). Audio
    # events only appear when a bus is populated, so a V-only timeline renders
    # byte-identically to the S2 world.
    out.extend(_emit_audio(timeline, start_frame, rate, tc,
                           set(reels.values()), len(placed) + 1))

    return "\n".join(out) + "\n"


def _record_rate(project: "Project", timeline: "Timeline", config) -> Rate:
    """The record/source rate for the EDL, honouring (in order): the R2
    rational ``edit_rate`` echo on the timeline (consumed defensively —
    ``getattr``), a project-declared rational ``edit_rate`` that mirrors
    ``timeline.fps``, else the legacy integer ``fps``."""
    echo = getattr(timeline, "edit_rate", None)
    if echo is not None:
        candidate = getattr(echo, "rate", echo)
        if isinstance(candidate, Rate):
            return candidate
    try:
        declared = project.edit_rate(config)
        if isinstance(declared, Rate) and declared.nominal_int == int(timeline.fps or 0):
            return declared
    except Exception:  # a broken/absent project rate never blocks the export
        pass
    return Rate.from_fraction(int(timeline.fps or 24), 1)


def export_edl(
    project: "Project",
    timeline: "Timeline",
    dest: Path | None = None,
    *,
    start_timecode: str = "01:00:00:00",
) -> Path:
    """Write ``exports/edl/<project name>.edl`` atomically (or ``dest`` when
    given) and return the path — the OTIO calling convention, one format.

    The frame-code mode is derived from the timeline/project rate (see
    :func:`_record_rate`): integer rates are NON-DROP; a drop-frame-legal NTSC
    rate (29.97/59.94) yields a DROP FRAME EDL. ``start_timecode`` is the record
    origin (default the broadcast hour).
    """
    config = project.load_config()
    rate = _record_rate(project, timeline, config)
    text = compile_edl(timeline, rate=rate, start_timecode=start_timecode,
                       title=config.name)
    path = Path(dest) if dest is not None else project.exports_dir / "edl" / f"{config.name}.edl"
    atomic_write_text(path, text)
    return path
