"""Per-export conform-loss reports (``manju.conform-loss/v1``) — export honesty
+ exact one-frame drift detection for the NLE/exchange exits (roadmap §6.1/§6.2).

Every exporter under ``manju.exporters`` is one OPTIONAL exit, and none of them
carries the whole timeline: OTIO drops captions, a caption export drops the
picture, drafts collapse dB curves to linear scalars, and metadata stamps are
approximations — not native features. This module derives the honest statement
of that loss for ONE export: which timeline features were **preserved**,
**approximated** (survive only as metadata / a lossy re-encoding — with where),
**dropped** (vanish from the artifact entirely), or **unsupported** (outside
the target's scope by design).

Honesty rules
-------------
* The feature inventory comes from the TIMELINE side — what the compiled
  timeline actually contains. A feature absent from the timeline yields NO row
  (no vacuous "preserved"). Kenburns/motion, speed/retime and markers are not
  representable in today's ``Timeline`` model at all, so they can never appear
  in an inventory — honest absence, not silent support.
* Classification is an AUDIT of the exporter code (the ``where`` field cites
  the exact lines), not a guess. A timeline feature the target's rule table
  does not know is a hard error — never silently "fine" (§6.2).
* Drift math is EXACT: :mod:`manju.core.timebase` Fractions only, no float.
  On the INT path ``exporters/otio.py`` writes RationalTime values as
  ``round(ms*fps/1000, 6)`` at a float rate — an off-grid millisecond boundary
  becomes a silently-rounded FRACTIONAL frame value; the ``frame_drift`` block
  lists every such boundary with its exact residual (all-zero when fully
  grid-snapped, zero fabrication either way). On the RATIONAL path (R2's
  ``rate_echo``) the OTIO/EDL exports are frame-native — every RationalTime
  ``value`` is an EXACT whole frame (video telescopes ``duration_frames``), so
  residuals against the exact rational grid are 0 BY CONSTRUCTION (the closing
  pin of the rational-edit-rate track). The µs carriers (jianying/native_draft)
  can only approximate a 1001-family boundary on their ms×1000 grid — that ≤½ms
  approximation is recorded as an honest note, never left silent.
* ``reports/conform/*`` is a deletable, content-addressed, tamper-evident
  DERIVED output. It is never a build input (grep-pinned by
  ``tests/test_fp_conform.py``), and this module never writes into
  ``exports/`` — exported artifact bytes are untouched (read-only audit).

Reimport deltas REUSE :func:`manju.build.roundtrip.plan_roundtrip` — this
module adds zero diff logic of its own.

CLI exposure is deliberately NOT part of this loop (library + tests only);
surfacing a ``manju conform`` command is a later explicit decision.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator

from ..core.container import ProjectError
from ..core.hashing import hash_value, short_hash
from ..core.timebase import Rate, classify_rate, grid_drift_ms, ms_to_frames, one_frame_drift_at
from ..core.yamlio import read_json, write_json

if TYPE_CHECKING:
    from ..core.container import Project
    from ..core.models import Timeline

__all__ = [
    "SCHEMA",
    "KNOWN_FEATURES",
    "TARGET_CLASSIFIERS",
    "UNSUPPORTED_TARGETS",
    "classify_features",
    "conform_loss_report",
    "read_conform_report",
    "reimport_changes",
    "timeline_feature_inventory",
    "write_conform_report",
]

SCHEMA = "manju.conform-loss/v1"

_CATEGORIES = ("preserved", "approximated", "dropped", "unsupported")

# The audio buses of TimelineTracks, in model order.
_AUDIO_BUSES = ("voice", "music", "sfx", "ambient")

# Timeline features this module knows how to inventory. Fine-grained on
# purpose: each feature maps to exactly ONE category per target, so the
# completeness pin (inventory == union of categories, pairwise disjoint)
# stays set arithmetic with no "partially dropped" ambiguity.
KNOWN_FEATURES = (
    "video_clips",      # tracks.video clips with start/duration windows
    "video_in_points",  # VideoClip.source_in_ms > 0 (virtual trims)
    "audio_in_points",  # AudioClip.start_offset_ms > 0 (BGM/ambient seek)
    "transitions",      # VideoClip.transition_out
    "overlays",         # tracks.overlay (title/info cards, branding)
    "captions",         # tracks.captions
    "clip_volume",      # VideoClip.source_gain_db / source_mute
    "audio_gain",       # AudioClip.gain_db
    "audio_fade_in",    # AudioClip.fade_in_ms
    "audio_fade_out",   # AudioClip.fade_out_ms
    "ducking",          # AudioClip.ducking (+ duck_* shape)
    "audio_loops",      # AudioClip.loop (ambient beds)
    "audio_tracks",     # the voice/music/sfx/ambient buses themselves
    # Wave 3 §5.4: per-cue caption semantics most exits cannot carry — each is
    # a first-class loss row so a role/speaker never vanishes silently.
    "caption_roles",    # CaptionLine.role (wave-4b curated vocabulary)
    "caption_speakers",  # CaptionLine.speaker (dialogue attribution)
)


# --------------------------------------------------------------------------- #
# timeline-side feature inventory                                              #
# --------------------------------------------------------------------------- #


def _audio_clips(timeline: "Timeline") -> Iterator[Any]:
    for bus in _AUDIO_BUSES:
        yield from getattr(timeline.tracks, bus)


def timeline_feature_inventory(timeline: "Timeline") -> list[dict[str, str]]:
    """Structured rows ``{feature, detail, where}`` for every feature the
    compiled timeline ACTUALLY contains. Absent feature ⇒ no row."""
    t = timeline.tracks
    audio = list(_audio_clips(timeline))
    rows: list[dict[str, str]] = []

    def _add(feature: str, count: int, detail: str, where: str) -> None:
        if count > 0:
            rows.append({"feature": feature, "detail": detail, "where": where})

    _add("video_clips", len(t.video),
         f"{len(t.video)} video clip(s) with start/duration windows",
         "tracks.video")
    n = sum(1 for c in t.video if (c.source_in_ms or 0) > 0)
    _add("video_in_points", n, f"{n} clip(s) with source_in_ms > 0",
         "tracks.video[].source_in_ms")
    n = sum(1 for a in audio if (a.start_offset_ms or 0) > 0)
    _add("audio_in_points", n, f"{n} audio clip(s) with start_offset_ms > 0",
         "tracks.{voice,music,sfx,ambient}[].start_offset_ms")
    n = sum(1 for c in t.video if c.transition_out is not None)
    _add("transitions", n, f"{n} clip out-edge transition(s)",
         "tracks.video[].transition_out")
    _add("overlays", len(t.overlay), f"{len(t.overlay)} overlay item(s)",
         "tracks.overlay")
    _add("captions", len(t.captions), f"{len(t.captions)} caption cue(s)",
         "tracks.captions")
    # Caption sub-features (Wave 3 §5.4): a row ONLY when a cue actually
    # carries the field — a role-less/speaker-less project stays row-free.
    n = sum(1 for c in t.captions if getattr(c, "role", None))
    _add("caption_roles", n, f"{n} caption cue(s) carrying a role",
         "tracks.captions[].role")
    n = sum(1 for c in t.captions if c.speaker)
    _add("caption_speakers", n, f"{n} caption cue(s) carrying a speaker",
         "tracks.captions[].speaker")
    n = sum(1 for c in t.video if c.source_mute or c.source_gain_db)
    _add("clip_volume", n,
         f"{n} video clip(s) with non-default source gain/mute",
         "tracks.video[].source_gain_db|source_mute")
    n = sum(1 for a in audio if a.gain_db)
    _add("audio_gain", n, f"{n} audio clip(s) with non-zero gain_db",
         "tracks.{voice,music,sfx,ambient}[].gain_db")
    n = sum(1 for a in audio if (a.fade_in_ms or 0) > 0)
    _add("audio_fade_in", n, f"{n} audio clip(s) with fade_in_ms > 0",
         "tracks.{voice,music,sfx,ambient}[].fade_in_ms")
    n = sum(1 for a in audio if (a.fade_out_ms or 0) > 0)
    _add("audio_fade_out", n, f"{n} audio clip(s) with fade_out_ms > 0",
         "tracks.{voice,music,sfx,ambient}[].fade_out_ms")
    n = sum(1 for a in audio if a.ducking)
    _add("ducking", n, f"{n} audio clip(s) with sidechain ducking",
         "tracks.{voice,music,sfx,ambient}[].ducking")
    n = sum(1 for a in audio if a.loop)
    _add("audio_loops", n, f"{n} looped audio bed(s)",
         "tracks.{voice,music,sfx,ambient}[].loop")
    buses = [b for b in _AUDIO_BUSES if getattr(t, b)]
    _add("audio_tracks", len(buses),
         "populated buses: " + ",".join(buses),
         "tracks.voice|music|sfx|ambient")
    return rows


# --------------------------------------------------------------------------- #
# per-target rule tables — an AUDIT of the exporter code, line-cited           #
# --------------------------------------------------------------------------- #

_Rule = tuple[str, str, str]  # (category, detail, where)

# Every rule below was read out of the exporter named in `where` — do not edit
# a row without re-reading those lines. Categories:
#   preserved     lands as native structure in the artifact
#   approximated  survives only as metadata / a lossy re-encoding (says where,
#                 and whether the manju round-trip can read it back)
#   dropped       absent from the artifact entirely
#   unsupported   outside the target's scope by design (caption-only exits)
_RULES: dict[str, dict[str, _Rule]] = {
    "otio": {
        "video_clips": (
            "preserved",
            "one OTIO Clip.1 per video clip; timeline window as source_range, "
            "self-consistent available_range on an ExternalReference. R4: a "
            "rational timeline writes EXACT integer frames (duration_frames / "
            "telescoped starts) at a float64 rate; int is unchanged float ms×fps",
            "exporters/otio.py:157-211,262"),
        "video_in_points": (
            "preserved",
            "source_in_ms becomes source_range.start_time; available_range "
            "widened to cover in-point + window (int ms frames, or exact whole "
            "frames on the rational path)",
            "exporters/otio.py:170-211"),
        "audio_in_points": (
            "preserved",
            "start_offset_ms becomes the audio clip's source_range start "
            "(also mirrored in metadata.manju.start_offset_ms)",
            "exporters/otio.py:224-236"),
        "transitions": (
            "approximated",
            "no OTIO Transition objects are written; transition type/duration "
            "ride metadata.manju.transition_out only — round-trippable via "
            "`manju roundtrip`",
            "exporters/otio.py:167-168 (read back by build/roundtrip.py:387-433,"
            "552-602)"),
        "overlays": (
            "dropped",
            "export_otio never reads tracks.overlay — overlays are absent from "
            "the OTIO document entirely",
            "exporters/otio.py:252-297 (no overlay path)"),
        "captions": (
            "dropped",
            "export_otio never reads tracks.captions — no text track in the "
            "OTIO document; SRT/ASS/VTT are the caption exits",
            "exporters/otio.py:262-276 (video+audio tracks only)"),
        "caption_roles": (
            "dropped",
            "captions themselves are never written (no text track), so the "
            "per-cue role vanishes with them — roles ride the ASS Name / TTML "
            "x-manju:role exits",
            "exporters/otio.py:262-276 (no caption path)"),
        "caption_speakers": (
            "dropped",
            "captions themselves are never written (no text track), so the "
            "per-cue speaker vanishes with them — speakers ride the TTML "
            "ttm:agent / jianying text-material exits",
            "exporters/otio.py:262-276 (no caption path)"),
        "clip_volume": (
            "approximated",
            "source_mute/source_gain_db ride metadata.manju only (no OTIO "
            "audio-gain effect) — round-trippable via `manju roundtrip`",
            "exporters/otio.py:161-168 (read back by build/roundtrip.py:387-433)"),
        "audio_gain": (
            "dropped",
            "AudioClip.gain_db is never written — not even as metadata",
            "exporters/otio.py:213-239 (no gain path)"),
        "audio_fade_in": (
            "approximated",
            "fade_in_ms rides metadata.manju only; no OTIO effect",
            "exporters/otio.py:226-227"),
        "audio_fade_out": (
            "dropped",
            "AudioClip.fade_out_ms is never written — not even as metadata",
            "exporters/otio.py:213-239 (no fade_out path)"),
        "ducking": (
            "dropped",
            "sidechain ducking (ducking/duck_*) is never written",
            "exporters/otio.py:213-239 (no ducking path)"),
        "audio_loops": (
            "approximated",
            "OTIO has no loop semantics; the bed is laid at its span with "
            "loop intent recorded in metadata.manju.loop only",
            "exporters/otio.py:220-221"),
        "audio_tracks": (
            "approximated",
            "clips land as real Clip.1 children, but all four Manju buses "
            "flatten onto ONE OTIO Audio track; bus identity survives only in "
            "metadata.manju.track",
            "exporters/otio.py:219,262-273"),
    },
    "jianying": {
        "video_clips": (
            "preserved",
            "one draft material + segment per clip with target/source "
            "timeranges in microseconds (ms×1000, exact)",
            "exporters/jianying.py:110-160"),
        "video_in_points": (
            "preserved",
            "source_in_ms becomes source_timerange.start (µs); material "
            "duration widened to cover in-point + window",
            "exporters/jianying.py:119-149"),
        "audio_in_points": (
            "preserved",
            "start_offset_ms becomes the audio segment's source_timerange "
            "start (µs)",
            "exporters/jianying.py:193-195"),
        "transitions": (
            "approximated",
            "no native transition segment in the M1 skeleton; transition "
            "type/duration ride the manju_v stamp on the video material + "
            "segment — round-trippable via `manju roundtrip`",
            "exporters/jianying.py:124-132,150 (read back by "
            "build/roundtrip.py:387-433,604-646)"),
        "overlays": (
            "dropped",
            "the skeleton never reads tracks.overlay — no sticker/effect "
            "tracks are written",
            "exporters/jianying.py:98-280 (no overlay path)"),
        "captions": (
            "preserved",
            "one text material + text-track segment per cue (content, "
            "speaker, µs timerange) + manju stamp for round-trip identity",
            "exporters/jianying.py:214-244,262"),
        "caption_roles": (
            "dropped",
            "CaptionLine.role is never read — the text material and the manju "
            "caption stamp carry content/speaker/shot/times only, so a role "
            "does not reach the draft (and is NOT round-tripped)",
            "exporters/jianying.py:214-244 (no role path)"),
        "caption_speakers": (
            "preserved",
            "the text material carries the cue's speaker natively (the "
            "'speaker' field the draft schema already records); the manju "
            "stamp does not repeat it, so round-trip identity keys off "
            "content/times",
            "exporters/jianying.py:228-236 (text material 'speaker')"),
        "clip_volume": (
            "approximated",
            "native per-segment volume/muted fields carry a LINEAR scalar "
            "(dB collapsed via 10^(dB/20)); exact dB rides the manju_v stamp "
            "— round-trippable via `manju roundtrip`",
            "exporters/jianying.py:127-130,152-159 (read back by "
            "build/roundtrip.py:604-646)"),
        "audio_gain": (
            "approximated",
            "audio gain_db collapses to the segment's linear volume field; "
            "no manju stamp on audio segments, so it is NOT round-tripped",
            "exporters/jianying.py:197-201"),
        "audio_fade_in": (
            "dropped",
            "fade_in/out live in the render, not the draft schema",
            "exporters/jianying.py:197-199 (comment: fades not in schema)"),
        "audio_fade_out": (
            "dropped",
            "fade_in/out live in the render, not the draft schema",
            "exporters/jianying.py:197-199 (comment: fades not in schema)"),
        "ducking": (
            "dropped",
            "sidechain ducking is never written to the draft",
            "exporters/jianying.py:175-211 (no ducking path)"),
        "audio_loops": (
            "dropped",
            "a draft has no loop primitive; the bed is referenced once over "
            "its span (trim-to-source handoff) and the loop flag is not "
            "stamped",
            "exporters/jianying.py:163-169,175-202"),
        "audio_tracks": (
            "preserved",
            "one draft audio track per bus (voice/music always; sfx/ambient "
            "when populated) + a text track",
            "exporters/jianying.py:246-262"),
    },
    "native_draft": {
        "video_clips": (
            "preserved",
            "one VideoSegment per clip with target/source timeranges (µs); "
            "NOTE render-side padding of short takes becomes an implicit "
            "slow-down (source/target speed) in the draft",
            "exporters/native_draft.py:47-75"),
        "video_in_points": (
            "preserved",
            "source_in_ms becomes the source_timerange seek",
            "exporters/native_draft.py:50-61"),
        "audio_in_points": (
            "preserved",
            "start_offset_ms becomes the audio source_timerange seek; "
            "guarded — on library builds without the kwarg the in-point "
            "silently falls back to 0",
            "exporters/native_draft.py:85-104"),
        "transitions": (
            "dropped",
            "the adapter never reads transition_out — no transition call "
            "into pyJianYingDraft/pycapcut",
            "exporters/native_draft.py:35-156 (no transition path)"),
        "overlays": (
            "dropped",
            "the adapter never reads tracks.overlay",
            "exporters/native_draft.py:35-156 (no overlay path)"),
        "captions": (
            "preserved",
            "one TextSegment per cue (text + µs timerange, bottom safe-area "
            "transform); speaker is not carried",
            "exporters/native_draft.py:147-155"),
        "caption_roles": (
            "dropped",
            "the TextSegment call passes text + timerange + clip_settings "
            "only — CaptionLine.role never reaches the draft library",
            "exporters/native_draft.py:147-155 (no role argument)"),
        "caption_speakers": (
            "dropped",
            "the TextSegment call passes text + timerange + clip_settings "
            "only — CaptionLine.speaker never reaches the draft library "
            "(already stated in the captions row: 'speaker is not carried')",
            "exporters/native_draft.py:147-155 (no speaker argument)"),
        "clip_volume": (
            "approximated",
            "dB collapses to a linear volume kwarg; guarded — older library "
            "builds without the kwarg silently keep default volume; no manju "
            "stamps, so NOT round-trippable",
            "exporters/native_draft.py:62-74"),
        "audio_gain": (
            "approximated",
            "dB collapses to the AudioSegment's linear volume; NOT "
            "round-trippable (no stamps)",
            "exporters/native_draft.py:90-104"),
        "audio_fade_in": (
            "dropped",
            "fades are render-only; never passed to the draft library",
            "exporters/native_draft.py:77-104 (no fade path)"),
        "audio_fade_out": (
            "dropped",
            "fades are render-only; never passed to the draft library",
            "exporters/native_draft.py:77-104 (no fade path)"),
        "ducking": (
            "dropped",
            "sidechain ducking is never passed to the draft library",
            "exporters/native_draft.py:77-104 (no ducking path)"),
        "audio_loops": (
            "dropped",
            "no loop primitive in a draft; the bed is trimmed to the source "
            "length and plays ONCE (may be shorter than the render's looped "
            "bed)",
            "exporters/native_draft.py:77-104"),
        "audio_tracks": (
            "preserved",
            "one lane per bus; overlapping SFX spread greedily across sfx_N "
            "lanes (drafts forbid in-track overlap)",
            "exporters/native_draft.py:106-145"),
    },
    "srt_ass": {
        "captions": (
            "preserved",
            "cue text/times verbatim, ms-native (SRT/VTT) + styled burn-in "
            "document (ASS) with injection-neutralized text; all three agree "
            "cue-for-cue",
            "exporters/srt_ass.py:142-177,216-282"),
        "caption_roles": (
            "approximated",
            "the role survives in ONE of the three sibling documents only: "
            "the ASS Dialogue Name field carries it VERBATIM (injection-"
            "neutralized, FP loop I); SRT and VTT have no role slot and DROP "
            "it — a roled cue reads role-less in the .srt/.vtt",
            "exporters/srt_ass.py:188-200 (_ass_name_field), 142-150 "
            "(compile_srt: no role slot)"),
        "caption_speakers": (
            "dropped",
            "no caption exit reads CaptionLine.speaker — SRT/VTT/ASS all omit "
            "it (the ASS Name field carries the cue's ROLE, not the speaker); "
            "speaker attribution rides the TTML ttm:agent exit",
            "exporters/srt_ass.py:142-159,285-301 (no speaker path)"),
        # everything else falls to the caption-only fallback below
    },
    "ttml": {
        "captions": (
            "preserved",
            "one <p> per cue: begin/end as media-time HH:MM:SS.mmm (ms-exact), "
            "escaped UTF-8 text with <br/> line breaks; speaker → head "
            "ttm:agent + per-cue ttm:agent ref; role → standard hint where "
            "mapped (ttm:role / itts:forcedDisplay / agent stub) + VERBATIM "
            "x-manju:role on every roled cue. Honest subset: one default "
            "bottom-centre region only; RTL rides ONLY an explicit locale "
            "declaration (locales/<lang>/meta.yaml direction, U2 — this "
            "report's own compile carries none); vertical/ruby are NOT "
            "expressed (the cue model carries no layout semantics)",
            "exporters/ttml.py:114-144,147-213"),
        "caption_roles": (
            "preserved",
            "every roled cue carries the VERBATIM x-manju:role attribute, "
            "plus the standard hint where the binding table maps one (sdh → "
            "ttm:role='captions', translation → ttm:role='subtitles', forced "
            "→ itts:forcedDisplay, speaker_label → ttm:agent stub) — an "
            "unmappable role is never dropped silently",
            "exporters/ttml.py:90-96 (_TTM_ROLE_HINTS), 142-165 (_p_line)"),
        "caption_speakers": (
            "preserved",
            "one ttm:agent element per distinct speaker in head metadata "
            "(sorted → deterministic xml:ids) + a per-cue ttm:agent reference",
            "exporters/ttml.py:135-139 (_agent_ids), 148-153 (_p_line), "
            "202-215 (head metadata)"),
        # everything else falls to the caption-only fallback below
    },
    "edl": {
        "video_clips": (
            "preserved",
            "one CMX3600 V-track event per clip (C cut, or a two-line C+D "
            "dissolve on the incoming event); record TC from the cumulative "
            "timeline position, ms→frames ROUND_HALF_UP at the edit rate",
            "exporters/edl.py:238-322 (compile_edl)"),
        "video_in_points": (
            "preserved",
            "source_in_ms becomes the source-side in-point (ms→frames, "
            "00:00:00:00-based — generated media's zero timebase IS its source "
            "TC); source length reuses the record frame count",
            "exporters/edl.py:207-224 (_Placed)"),
        "audio_in_points": (
            "approximated",
            "A1/A2 (voice/music) audio events carry a zero-based source "
            "in-point from start_offset_ms (ms->frames, 00:00:00:00-based — "
            "generated media's zero timebase IS its source TC, the video "
            "source_in_ms precedent); sfx/ambient in-points are omitted with "
            "their buses (see audio_tracks)",
            "exporters/edl.py (_emit_audio: src_in = ms_to_frames("
            "start_offset_ms))"),
        "transitions": (
            "approximated",
            "clean cross-dissolves (xfade_fade, dur>0) become native CMX D "
            "events (type+duration preserved); EVERY other kind — dip-to-black "
            "fade, the xfade_* wipes/slides (CMX W events, out of scope), any "
            "unknown type — degrades to a hard cut with an in-band `* MANJU:` "
            "note (never a wrong dissolve)",
            "exporters/edl.py:227-235,289-322"),
        "overlays": (
            "dropped",
            "export_edl never reads tracks.overlay — titles/branding are not a "
            "cut-list primitive; absent from the EDL entirely",
            "exporters/edl.py:266 (video track only, no overlay path)"),
        "captions": (
            "dropped",
            "export_edl never reads tracks.captions — an EDL is a picture cut "
            "list; SRT/ASS/VTT/TTML are the caption exits",
            "exporters/edl.py:266 (video track only, no caption path)"),
        "caption_roles": (
            "dropped",
            "captions themselves are never written (picture cut list), so "
            "the per-cue role vanishes with them",
            "exporters/edl.py:266 (no caption path)"),
        "caption_speakers": (
            "dropped",
            "captions themselves are never written (picture cut list), so "
            "the per-cue speaker vanishes with them",
            "exporters/edl.py:266 (no caption path)"),
        "clip_volume": (
            "unsupported",
            "VideoClip own-audio level/mute is an audio-domain feature of the "
            "picture clip, not one of the voice/music buses this EDL emits as "
            "A1/A2; out of scope",
            "exporters/edl.py (module scope: no video own-audio path)"),
        "audio_gain": (
            "approximated",
            "gain_db is not a CMX3600 cut-list primitive; a nonzero gain on a "
            "placed A1/A2 clip survives only as a per-clip '* MANJU:' note "
            "(the level rides the mix, not the cut list)",
            "exporters/edl.py (_audio_loss_notes)"),
        "audio_fade_in": (
            "approximated",
            "fade_in_ms is not expressible in bare CMX3600; a nonzero fade-in "
            "on a placed A1/A2 clip survives only as a per-clip '* MANJU:' "
            "note",
            "exporters/edl.py (_audio_loss_notes)"),
        "audio_fade_out": (
            "approximated",
            "fade_out_ms is not expressible in bare CMX3600; a nonzero "
            "fade-out on a placed A1/A2 clip survives only as a per-clip "
            "'* MANJU:' note",
            "exporters/edl.py (_audio_loss_notes)"),
        "ducking": (
            "unsupported",
            "sidechain ducking (ducking/duck_*) has no CMX3600 primitive; not "
            "written and not noted per clip — out of scope by design",
            "exporters/edl.py (module scope: no ducking path)"),
        "audio_loops": (
            "unsupported",
            "loop=True beds are materialized by repetition at render time; "
            "CMX3600 has no loop primitive and there is no honest single-event "
            "source window here, so a looped clip is OMITTED (recorded as a "
            "'* MANJU:' note, never a fabricated event) — no materialization "
            "in this exporter (fcpxml pre-W1 stance)",
            "exporters/edl.py (_audio_omit_reason)"),
        "audio_tracks": (
            "approximated",
            "the VOICE bus becomes A1 events (CMX channel token 'A') and the "
            "MUSIC bus becomes A2 events (token 'A2') — a DECLARED two-channel "
            "subset (Y1). sfx + ambient have no classic third/fourth stereo "
            "pair and are recorded as in-band '* MANJU:' omission notes "
            "(bus/source/window), never squeezed into a channel",
            "exporters/edl.py (_emit_audio; _AUDIO_EMIT_BUSES/"
            "_AUDIO_OMIT_BUSES)"),
    },
    "fcpxml": {
        "video_clips": (
            "preserved",
            "one <asset-clip> per video clip on the library>event>project>"
            "sequence>spine; offset/start/duration as EXACT rational-seconds "
            "strings (N/Ds) on the edit rate's frameDuration timescale — FCPXML "
            "is natively rational, so an int project rides '1/24s' and a "
            "1001-family project rides '1001/24000s' with ZERO drift "
            "(duration_frames on the rational path, ms_to_frames on the int "
            "path — every boundary a whole frame)",
            "exporters/fcpxml.py (compile_fcpxml/_secs/_clip_frames)"),
        "video_in_points": (
            "preserved",
            "source_in_ms becomes the asset-clip start (source in-point, exact "
            "whole frame, 0 when untrimmed); the shared <asset> resource "
            "duration widens to cover in-point + window (self-consistent "
            "available media range, OTIO precedent)",
            "exporters/fcpxml.py (_clip_frames + asset available range)"),
        "audio_in_points": (
            "approximated",
            "start_offset_ms becomes the connected clip's start (exact whole "
            "frame) on every WRITTEN clip; clips the loop/None rule omits "
            "(see audio_tracks) take their in-points with them",
            "exporters/fcpxml.py (_plan_audio: start from start_offset_ms)"),
        "transitions": (
            "approximated",
            "a clean cross-dissolve (xfade_fade, 0<dur<BOTH adjacent clips) "
            "becomes a NATIVE FCPXML <transition> (Cross Dissolve effect, FCP "
            "overlap geometry — the incoming clip and every later element pull "
            "back by the transition frames); EVERY other kind — dip-to-black "
            "fade, the xfade_* wipes/slides, an unknown type, or a dissolve too "
            "long to overlap its clips — degrades to a hard cut with an in-band "
            "<!-- MANJU --> note (never a wrong dissolve, S2 precedent)",
            "exporters/fcpxml.py (_is_clean_dissolve + overlap guard + degraded "
            "note)"),
        "overlays": (
            "dropped",
            "compile_fcpxml never reads tracks.overlay — titles/branding are "
            "not written to the spine",
            "exporters/fcpxml.py (video track only, no overlay path)"),
        "captions": (
            "dropped",
            "compile_fcpxml never reads tracks.captions — captions ride the "
            "SRT/TTML exits by design (honest boundary; FCPXML titles are NOT "
            "emitted this loop)",
            "exporters/fcpxml.py (video spine only, no caption path)"),
        "caption_roles": (
            "dropped",
            "captions themselves are never written (they ride the SRT/TTML "
            "exits), so the per-cue role vanishes with the track",
            "exporters/fcpxml.py (video spine only, no caption path)"),
        "caption_speakers": (
            "dropped",
            "captions themselves are never written (they ride the SRT/TTML "
            "exits), so the per-cue speaker vanishes with the track",
            "exporters/fcpxml.py (video spine only, no caption path)"),
        "clip_volume": (
            "dropped",
            "video source gain/mute is an audio-domain adjustment; no "
            "<adjust-volume> is written on the video asset-clip this loop",
            "exporters/fcpxml.py (no volume path)"),
        "audio_gain": (
            "approximated",
            "<adjust-volume amount=\"{gain:g}dB\"> on every WRITTEN clip with "
            "a nonzero gain_db; clips the loop/None rule omits take their "
            "gain with them (see audio_tracks)",
            "exporters/fcpxml.py (V1 gain emission)"),
        "audio_fade_in": (
            "preserved",
            "REAL native FCPXML fade (Y3, DTD-sourced): on every WRITTEN clip, "
            "<adjust-volume amount=\"{gain:g}dB\"> → <param name=\"amount\"> → "
            "<fadeIn type=\"linear\" duration=\"N/Ds\"/>. The containment chain is "
            "Apple's archived FCPXML v1.7 DTD (adjust-volume(param*); param(name "
            "#REQUIRED)(fadeIn?,fadeOut?); fadeIn EMPTY, type %fadeType #IMPLIED, "
            "duration %time #REQUIRED). fade_in_ms → whole frames ROUND_HALF_UP → "
            "exact rational seconds; a fade longer than the clip clamps to the "
            "clip length with an in-band note (never an invalid over-long fade). "
            "type=\"linear\" mirrors the render's afade default curve (parity). "
            "The param name \"amount\" is a CONVENTION — the DTD requires a name "
            "attribute but does NOT mandate that string. Honest edges: a "
            "materialized loop pass carries no fade (whole-source repeat, stated "
            "in a note) and clips the loop/None rule omits take their fades with "
            "them (see audio_tracks)",
            "exporters/fcpxml.py (Y3 _fade_frames + _emit_adjust_volume param/fadeIn)"),
        "audio_fade_out": (
            "preserved",
            "REAL native FCPXML fade (Y3, DTD-sourced), same chain as "
            "audio_fade_in: <adjust-volume> → <param name=\"amount\"> → <fadeOut "
            "type=\"linear\" duration=\"N/Ds\"/> on every WRITTEN clip. "
            "fade_out_ms → whole frames ROUND_HALF_UP → exact rational seconds, "
            "clamped to the clip length with an in-band note when over-long; "
            "type=\"linear\" for afade parity; param name \"amount\" is a "
            "convention (DTD requires only a name attribute). Loop passes carry "
            "no fade and omitted clips take their fades with them (see audio_tracks)",
            "exporters/fcpxml.py (Y3 _fade_frames + _emit_adjust_volume param/fadeOut)"),
        "ducking": (
            "approximated",
            "the ducked clip IS written at its static gain; the render-time "
            "sidechain RELATIONSHIP has no FCPXML primitive and rides an "
            "in-band approximation note — the mix is never silently claimed",
            "exporters/fcpxml.py (V1 ducking ruling + _audio_approx_notes)"),
        "audio_loops": (
            "approximated",
            "materialized at export time as whole passes + a trimmed tail from "
            "the PROBED natural length (render parity: -stream_loop); "
            "cumulative pass boundaries telescope to the clip's exact frame "
            "total. compile without probed lengths (the pure default) keeps the "
            "honest omission — a bed whose source cannot be probed is omitted "
            "with an in-band note, never a fabricated length",
            "exporters/fcpxml.py (W1 _plan_audio loop materialization + "
            "export_fcpxml probe wiring)"),
        "audio_tracks": (
            "approximated",
            "V1: non-loop clips with resolvable duration_ms become CONNECTED "
            "role/lane asset-clips (voice -1/dialogue, music -2/music, sfx "
            "-3/effects.sfx, ambient -4/effects.ambient) at exact frame "
            "placement on the pulled-back spine geometry; loop beds and "
            "natural-length (duration None) sfx are OMITTED with in-band "
            "notes — a loop-only timeline gets no audio. The remainder is "
            "still WRITER scope, not a format limit; nothing is ever faked",
            "exporters/fcpxml.py (V1 _plan_audio + connected emission)"),
    },
    "openclap": {
        "video_clips": (
            "preserved",
            "one standard VIDEO segment per clip (startTimeInMs/endTimeInMs/"
            "assetUrl) + stable non-secret provenance (prompt/provider)",
            "exporters/openclap/exporter.py:122-153,265-278"),
        "video_in_points": (
            "approximated",
            "no standard clap in-point field; sourceInMs rides the x-manju "
            "extension only",
            "exporters/openclap/exporter.py:136-137"),
        "audio_in_points": (
            "dropped",
            "AudioClip.start_offset_ms is never read — absent from the .clap "
            "entirely",
            "exporters/openclap/exporter.py:156-187 (no in-point path)"),
        "transitions": (
            "dropped",
            "transition_out is never read; the export emits no TRANSITION "
            "segments (that category exists only for foreign imports)",
            "exporters/openclap/exporter.py:122-153 (no transition path)"),
        "overlays": (
            "approximated",
            "full overlay dumps ride the meta x-manju.overlays extension "
            "only — no standard clap segments",
            "exporters/openclap/exporter.py:251-252"),
        "captions": (
            "approximated",
            "full caption dumps ride the meta x-manju.captions extension "
            "only — no standard clap caption segments",
            "exporters/openclap/exporter.py:249-250"),
        "caption_roles": (
            "approximated",
            "the x-manju.captions dumps are full CaptionLine model dumps, so "
            "a set role rides the extension verbatim (a default None role is "
            "serializer-dropped) — never a standard clap field",
            "exporters/openclap/exporter.py:249-250 (model_dump per cue)"),
        "caption_speakers": (
            "approximated",
            "the x-manju.captions dumps are full CaptionLine model dumps, so "
            "the speaker rides the extension verbatim — never a standard "
            "clap field",
            "exporters/openclap/exporter.py:249-250 (model_dump per cue)"),
        "clip_volume": (
            "approximated",
            "sourceMute/sourceGainDb ride the x-manju extension only",
            "exporters/openclap/exporter.py:132-135"),
        "audio_gain": (
            "approximated",
            "standard outputGain carries the linear scalar (rounded 6dp); "
            "the exact dB value rides x-manju.gainDb",
            "exporters/openclap/exporter.py:167-168,184-185"),
        "audio_fade_in": (
            "dropped",
            "fades are never read — absent from the .clap entirely",
            "exporters/openclap/exporter.py:156-187 (no fade path)"),
        "audio_fade_out": (
            "dropped",
            "fades are never read — absent from the .clap entirely",
            "exporters/openclap/exporter.py:156-187 (no fade path)"),
        "ducking": (
            "dropped",
            "sidechain ducking is never read — absent from the .clap",
            "exporters/openclap/exporter.py:156-187 (no ducking path)"),
        "audio_loops": (
            "approximated",
            "loop intent rides x-manju.loop only",
            "exporters/openclap/exporter.py:169-170"),
        "audio_tracks": (
            "preserved",
            "one deterministic clap track number per bus (video=0, voice=1, "
            "music=2, sfx=3, ambient=4) with DIALOGUE/MUSIC/SOUND categories",
            "exporters/openclap/exporter.py:47-52,280-298"),
    },
    "xmeml": {
        "video_clips": (
            "preserved",
            "one <clipitem> per video clip on the single video <track>; "
            "start/end are the telescoped record-frame integers, in/out the "
            "source-frame integers — every time a WHOLE frame at the exact "
            "edit rate on the timebase+ntsc carrier (1001 family: timebase = "
            "nominal + ntsc TRUE, zero drift; int: ntsc FALSE). duration_frames "
            "stamps consumed directly, ms_to_frames telescoped otherwise",
            "exporters/xmeml.py (compile_xmeml video track; _clip_frames)"),
        "video_in_points": (
            "preserved",
            "source_in_ms becomes the clipitem <in> (ms→frames ROUND_HALF_UP; "
            "<out> = in + window) — REAL numbers, the -1 conventions are not "
            "used",
            "exporters/xmeml.py (video clipitem in/out)"),
        "audio_in_points": (
            "approximated",
            "start_offset_ms becomes <in> on every WRITTEN voice/music "
            "clipitem; sfx/ambient clips (omitted buses) and loop/None-"
            "duration clips take their in-points with them (see audio_tracks)",
            "exporters/xmeml.py (audio clipitem in/out)"),
        "transitions": (
            "approximated",
            "a clean cross-dissolve (xfade_fade, 0<dur<BOTH adjacent clips) "
            "becomes a native <transitionitem> (Cross Dissolve effectid, "
            "centre alignment straddling the cut, whole-frame start/end); "
            "EVERY other kind — dip-to-black fade, the xfade_* wipes/slides, "
            "an unknown type, or a dissolve too long for its clips — degrades "
            "to a hard cut with an in-band <!-- MANJU --> note (never a wrong "
            "dissolve, the fcpxml/EDL stance)",
            "exporters/xmeml.py (_is_clean_dissolve + overlap guard + "
            "degraded note)"),
        "overlays": (
            "dropped",
            "compile_xmeml never reads tracks.overlay — titles/branding are "
            "not written",
            "exporters/xmeml.py (video track only, no overlay path)"),
        "captions": (
            "dropped",
            "compile_xmeml never reads tracks.captions — captions ride the "
            "SRT/ASS/VTT/TTML exits by design (the fcpxml honest boundary)",
            "exporters/xmeml.py (no caption path)"),
        "caption_roles": (
            "dropped",
            "captions themselves are never written, so the per-cue role "
            "vanishes with the track — roles ride the ASS Name / TTML "
            "x-manju:role exits",
            "exporters/xmeml.py (no caption path)"),
        "caption_speakers": (
            "dropped",
            "captions themselves are never written, so the per-cue speaker "
            "vanishes with the track — speakers ride the TTML ttm:agent / "
            "jianying text-material exits",
            "exporters/xmeml.py (no caption path)"),
        "clip_volume": (
            "dropped",
            "video source gain/mute is never written — the minimal clipitem "
            "subset carries no audio <filter>/levels on video clipitems",
            "exporters/xmeml.py (no volume path)"),
        "audio_gain": (
            "dropped",
            "AudioClip.gain_db is never written — no <filter>/levels in the "
            "minimal clipitem subset; the level rides the render mix (writer "
            "scope, not a format limit)",
            "exporters/xmeml.py (no gain path)"),
        "audio_fade_in": (
            "dropped",
            "fade handles are not written — no <filter> keyframes in the "
            "minimal clipitem subset; fades ride the render / native FCPXML "
            "fade exits",
            "exporters/xmeml.py (no fade path)"),
        "audio_fade_out": (
            "dropped",
            "fade handles are not written — no <filter> keyframes in the "
            "minimal clipitem subset; fades ride the render / native FCPXML "
            "fade exits",
            "exporters/xmeml.py (no fade path)"),
        "ducking": (
            "dropped",
            "the render-time sidechain relationship is never written — a "
            "ducked voice/music clipitem plays flat in the XMEML (no keyframed "
            "level primitive in this minimal subset); the ducking mix is never "
            "silently claimed",
            "exporters/xmeml.py (no ducking path)"),
        "audio_loops": (
            "dropped",
            "a loop bed is OMITTED with an in-band <!-- MANJU --> comment — "
            "no materialization in this minimal writer, never a fabricated "
            "single pass (the fcpxml pre-W1 omission stance; fcpxml itself "
            "materializes with render parity)",
            "exporters/xmeml.py (loop/None omission comments)"),
        "audio_tracks": (
            "approximated",
            "the VOICE bus becomes audio track 1 and the MUSIC bus audio "
            "track 2 — a DECLARED two-track subset (the EDL A1/A2 stance); a "
            "voice clip sharing its video clip's source is tied to it with "
            "reciprocal <link> pairs (linked A/V); sfx/ambient clips and "
            "loop/None-duration clips are OMITTED with in-band comments — "
            "never squeezed into a track, nothing faked (writer scope, not a "
            "format limit)",
            "exporters/xmeml.py (audio tracks + links + omission comments)"),
    },
}

# Per-target fallback for features with no explicit row. Only the caption-only
# exits have one: EVERYTHING non-caption is out of scope by design and the
# report must say so instead of silently passing (§6.2).
_FALLBACK_RULES: dict[str, _Rule] = {
    "srt_ass": (
        "unsupported",
        "caption-only exit: SRT/ASS/VTT carry subtitle cues only — this "
        "feature is outside the format's scope by design",
        "exporters/srt_ass.py:1-12 (module scope: captions only)"),
    "ttml": (
        "unsupported",
        "caption-only exit: the TTML/IMSC1 document carries subtitle cues "
        "only — this feature is outside the format's scope by design",
        "exporters/ttml.py:1-50 (module scope: captions only)"),
}

#: target name -> rule table. One entry per exporter module under
#: src/manju/exporters/ (native_draft covers both pyJianYingDraft and pycapcut
#: — one shared builder). tests/test_fp_conform.py pins that every exporter
#: module on disk appears here or in UNSUPPORTED_TARGETS.
TARGET_CLASSIFIERS: dict[str, dict[str, _Rule]] = _RULES

#: Exporter modules with NO conform classifier, each with the reason. Adding
#: another exporter without a
#: classifier or a row here fails tests/test_fp_conform.py (no silent
#: degradation, doc §6.2).
UNSUPPORTED_TARGETS: dict[str, str] = {
    "handoff_guide": (
        "pure human-readable view of a frozen prompt/reference handoff, not an "
        "NLE or timeline exporter; controls remain advisory and ASSET_MAP.md "
        "is integrity-bound by provider_handoff"
    ),
    "provider_handoff": (
        "offline prompt/reference handoff bundle, not an NLE or timeline export; "
        "its loss/eligibility honesty is carried by handoff.json"
    ),
    # W2 (orchestrator-declared coverage): the FCPXML import-plan module is
    # read-only ANALYSIS, not a writer exit — conform-loss classifies what
    # WRITERS lose; the plan document (manju.fcpxml-import-plan/v1) carries
    # its own honesty rows (unsupported_transitions / unknown_elements /
    # needs_relink) inside itself, so a writer-style classifier here would
    # be a second voice for the same facts.
    "fcpxml_import": "read-only import-plan analysis (never a writer exit); "
                     "loss honesty lives in the plan document itself",
    # Y4 (orchestrator-declared coverage): same class as fcpxml_import — the
    # CMX3600 import-plan module analyzes, never writes; the plan document
    # (manju.edl-import-plan/v1) carries its own honesty rows.
    "edl_import": "read-only import-plan analysis (never a writer exit); "
                  "loss honesty lives in the plan document itself",
}

# One static honest-scope note per target, prepended to the doc's notes.
_SCOPE_NOTES: dict[str, str] = {
    "otio": "OTIO 0.15-flavoured JSON written without the opentimelineio "
            "package (schema-lite fallback exit; times are frames at the "
            "edit fps).",
    "jianying": "M1 skeleton draft pending pyJianYingDraft integration "
                "(§13/§14); manju stamps make volume/transition edits "
                "round-trippable via `manju roundtrip`.",
    "native_draft": "adapter-wall export via pyJianYingDraft/pycapcut; the "
                    "draft structure is library-owned and not audited here.",
    "srt_ass": "caption-only exit — SRT/ASS/VTT carry subtitle cues only; "
               "this export is NOT a picture/audio conform.",
    "ttml": "caption-only exit — IMSC1-Text-Profile-shaped TTML1 (media time "
            "base, ms precision, one default bottom-centre region; no "
            "ttp:profile conformance claim — no external validator runs); "
            "NOT a picture/audio conform.",
    "openclap": "open .clap snapshot; non-mappable semantics ride the "
                "namespaced x-manju extension (never fabricated standard "
                "fields).",
    "edl": "CMX3600 video cut list with real SMPTE timecode (record TC from "
           "the timeline position; FCM DROP/NON-DROP per rate; colon-NDF / "
           "semicolon-DF). V track + a DECLARED A1/A2 audio subset (Y1: "
           "voice->A1, music->A2, blocked-by-track after the V block; "
           "sfx/ambient omitted with per-clip '* MANJU:' notes, never "
           "squeezed); source TC is 00:00:00:00-based (takes carry no "
           "recorded reel/TC).",
    "fcpxml": "FCPXML 1.9 video spine (library>event>project>sequence>spine) — "
              "the one NLE exit where our rational time rides NATIVELY: every "
              "offset/start/duration is an EXACT whole-frame rational-seconds "
              "string on the format's frameDuration timescale ('1/24s' for int, "
              "'1001/24000s' for the 1001 family), so there is ZERO drift for "
              "either. Cross-dissolves are native <transition>s (others cut + "
              "note, never a wrong dissolve); captions ride the SRT/TTML exits "
              "(honestly omitted). Audio: connected role/lane asset-clips with "
              "gain (V1), loop beds materialized at render parity (W1), "
              "DTD-sourced fades (Y3); remaining omissions (ducking relation, "
              "unprobeable loops, None durations) are per-clip in-band notes.",
    "xmeml": "XMEML (FCP7 XML Interchange Format v4) single-sequence writer — "
             "the legacy-NLE exit Premiere/Resolve import on Windows. Every "
             "<start>/<end>/<in>/<out> is a WHOLE-FRAME INTEGER on the "
             "timebase+ntsc clock (1001 family: timebase=nominal + ntsc TRUE "
             "IS the exact rational rate — zero drift; int: ntsc FALSE); "
             "file://localhost pathurls are percent-encoded with the drive-"
             "letter colon literal, containment-checked against the project "
             "root. Clean cross-dissolves are native <transitionitem>s "
             "(others cut + note, never a wrong dissolve); voice/music ride a "
             "DECLARED two-track audio subset with linked A/V on shared "
             "sources (sfx/ambient/loops omitted with in-band comments); "
             "captions/overlays ride their own exits; markers are omitted "
             "because the Timeline model carries no marker truth (nothing "
             "invented).",
}

# import-time typo guard: every rule key must be a known feature.
for _target, _table in _RULES.items():
    _bad = set(_table) - set(KNOWN_FEATURES)
    if _bad:  # pragma: no cover - a typo here is a programming error
        raise RuntimeError(f"conform rules for {_target}: unknown features {_bad}")


def _require_known_target(target: str) -> None:
    if target in TARGET_CLASSIFIERS:
        return
    if target in UNSUPPORTED_TARGETS:
        raise ProjectError(
            f"conform-loss: target {target!r} 尚未分类 — {UNSUPPORTED_TARGETS[target]}")
    raise ProjectError(
        f"conform-loss: 未知导出目标 {target!r} — 可选: "
        f"{', '.join(sorted(TARGET_CLASSIFIERS))}")


def classify_features(
    target: str, inventory: Iterable[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Classify inventory rows into the four categories for ``target``.

    Every present feature lands in exactly one category; a feature the rule
    table does not know is a HARD error (adding a timeline feature later
    forces an explicit classification per target — no silent degradation).
    """
    _require_known_target(target)
    rules = TARGET_CLASSIFIERS[target]
    fallback = _FALLBACK_RULES.get(target)
    out: dict[str, list[dict[str, str]]] = {c: [] for c in _CATEGORIES}
    for row in inventory:
        feat = str(row.get("feature"))
        rule = rules.get(feat, fallback)
        if rule is None:
            raise ProjectError(
                f"conform-loss: target {target!r} 无法分类时间线特性 {feat!r} — "
                "为它补一条 preserved/approximated/dropped/unsupported 规则"
                "(诚实优先,不允许静默通过)")
        category, detail, where = rule
        out[category].append({"feature": feat, "detail": detail, "where": where})
    return out


# --------------------------------------------------------------------------- #
# frame drift — exact timebase math, zero fabrication                          #
# --------------------------------------------------------------------------- #

# Which timeline tracks the target actually lays on its frame/µs grid.
# srt_ass is deliberately absent: SRT/ASS/VTT are ms-native caption documents
# and fps never enters them (exporters/srt_ass.py:45-70).
_DRIFT_TRACKS: dict[str, tuple[str, ...]] = {
    "otio": ("video", *_AUDIO_BUSES),
    "jianying": ("video", *_AUDIO_BUSES, "captions"),
    "native_draft": ("video", *_AUDIO_BUSES, "captions"),
    "openclap": ("video", *_AUDIO_BUSES),
    # V-only cut list: only the video track lands on the record/source frame
    # grid (audio/captions/overlays are not exported).
    "edl": ("video",),
    # FCPXML video spine: only the video track lands on the frame grid this loop
    # (audio is a deferred writer increment; captions ride SRT/TTML).
    "fcpxml": ("video",),
    # XMEML: the video track + the DECLARED voice/music audio subset land as
    # whole-frame clipitems (sfx/ambient/captions are not exported).
    "xmeml": ("video", "voice", "music"),
}

_NOT_TIME_BEARING = {
    "srt_ass": "srt/ass/vtt are ms-native caption documents; fps never "
               "enters them (exporters/srt_ass.py:45-70) — no frame grid to "
               "drift against",
    "ttml": "ttml is an ms-native caption document (media time base, "
            "HH:MM:SS.mmm — exporters/ttml.py:71,169); fps never enters it — "
            "no frame grid to drift against",
}

# Microsecond-carrier exchange targets. jianying / native_draft store times as
# µs (ms×1000), NOT frame counts, so a rational (1001-family) frame boundary
# cannot ride them exactly — it is carried as its cumulative-boundary millisecond
# value, within ≤½ms of the true rational frame boundary (timebase's documented
# cumulative-boundary bound). A rational project gets ONE honest note per carrier
# (recorded, never silent); srt/vtt are ms-native by definition and handled by
# _NOT_TIME_BEARING above (that reason IS their "why no row").
_MS_CARRIER_NOTE: dict[str, str] = {
    "jianying": (
        "rational timeline on a MICROSECOND carrier: jianying stores times as µs "
        "(ms×1000), so a 1001-family frame boundary is carried as its cumulative-"
        "boundary millisecond value — within ≤½ms of the exact rational frame "
        "boundary (timebase cumulative-boundary bound). The µs stream APPROXIMATES "
        "the rational grid; the exact whole-frame truth lives in the OTIO/EDL exits."),
    "native_draft": (
        "rational timeline on a MICROSECOND carrier: the pyJianYingDraft/pycapcut "
        "draft stores µs timeranges (ms×1000), so a 1001-family frame boundary "
        "rides its cumulative-boundary millisecond value — within ≤½ms of the exact "
        "rational frame boundary. The µs draft APPROXIMATES the rational grid; "
        "OTIO/EDL carry the exact frames."),
}


def _rational_rate(timeline: "Timeline") -> Rate | None:
    """The timeline's exact rational edit rate IFF it carries R2's rational echo,
    else ``None`` (the int-grid path, byte-identical behaviour). Read defensively
    via ``getattr`` — a legacy/duck-typed timeline without the echo stays on the
    integer grid; a whole-number echo also returns ``None`` (only a genuine
    1001-family rate takes the frame-exact path)."""
    echo = getattr(timeline, "rate_echo", None)
    if echo is None:
        return None
    rate = getattr(echo, "rate", None)
    return rate if isinstance(rate, Rate) and rate.exact_int is None else None


def _rational_frame_boundaries(
    timeline: "Timeline", tracks: tuple[str, ...], rate: Rate,
) -> Iterator[tuple[str, int]]:
    """``(label, whole_frame_index)`` for every clip boundary the target exports
    on the rational path. VIDEO telescopes the compiler's ``duration_frames`` (the
    exact cumulative whole-frame boundary — the SAME walk the compiler and the R4
    OTIO exporter do); audio/captions use :func:`ms_to_frames` (the nearest whole
    frame). Every yielded value is an INTEGER frame index — that is precisely why
    the residual vs the rational frame grid is zero BY CONSTRUCTION."""
    for track in tracks:
        if track == "video":
            cum = 0  # telescoping cumulative frame position
            for c in timeline.tracks.video:
                label = f"video:{c.shot}/{c.take}"
                yield f"{label}.start", cum
                df = getattr(c, "duration_frames", None)
                if df is None:  # hand-assembled rational clip without the stamp
                    df = ms_to_frames(int(c.duration_ms), rate)
                cum += int(df)
                yield f"{label}.end", cum
        elif track == "captions":
            for i, cap in enumerate(timeline.tracks.captions):
                yield f"caption[{i}].start", ms_to_frames(int(cap.start_ms), rate)
                yield f"caption[{i}].end", ms_to_frames(int(cap.end_ms), rate)
        else:
            for i, a in enumerate(getattr(timeline.tracks, track)):
                dur = a.duration_ms if a.duration_ms is not None else (
                    timeline.duration_ms or 0)
                start = int(a.start_ms)
                yield f"{track}[{i}].start", ms_to_frames(start, rate)
                yield f"{track}[{i}].end", ms_to_frames(start + int(dur), rate)


def _boundaries(timeline: "Timeline", tracks: tuple[str, ...]) -> Iterator[tuple[str, int]]:
    """``(label, ms)`` for every clip boundary the target exports."""
    for track in tracks:
        if track == "video":
            for c in timeline.tracks.video:
                label = f"video:{c.shot}/{c.take}"
                start = int(c.start_ms)
                yield f"{label}.start", start
                yield f"{label}.end", start + int(c.duration_ms)
        elif track == "captions":
            for i, cap in enumerate(timeline.tracks.captions):
                yield f"caption[{i}].start", int(cap.start_ms)
                yield f"caption[{i}].end", int(cap.end_ms)
        else:
            for i, a in enumerate(getattr(timeline.tracks, track)):
                dur = a.duration_ms if a.duration_ms is not None else (
                    timeline.duration_ms or 0)
                start = int(a.start_ms)
                yield f"{track}[{i}].start", start
                yield f"{track}[{i}].end", start + int(dur)


def _fcpxml_rate_mismatch(
    timeline: "Timeline", source_rates: dict[str, Any] | None,
    edit_rate: Rate, notes: list[str],
) -> list[dict[str, Any]]:
    """Probed-source-rate vs edit-rate drift rows for FCPXML (same exact
    grid_drift_ms / one_frame_drift_at math as the shared path; a same-clock
    source yields NO row, an unclassifiable one an honest ``unknown`` row)."""
    mismatch: list[dict[str, Any]] = []
    if not source_rates:
        return mismatch
    by_source = {str(k): v for k, v in source_rates.items()}
    video_sources = {c.source for c in timeline.tracks.video}
    for missing in sorted(set(by_source) - video_sources):
        notes.append(
            f"source_rates key {missing!r} matches no timeline video clip "
            "— ignored (nothing fabricated)")
    edit_nominal = edit_rate.nominal_int
    for c in timeline.tracks.video:
        if c.source not in by_source:
            continue
        raw = by_source[c.source]
        probed = raw if isinstance(raw, Rate) else classify_rate(raw)
        label = f"video:{c.shot}/{c.take}"
        if not isinstance(probed, Rate):
            mismatch.append({
                "clip": label, "source": c.source, "source_rate": "unknown",
                "note": "probed rate not classifiable "
                        "(timebase.classify_rate → UNKNOWN); no drift numbers "
                        "fabricated",
            })
            continue
        if probed.fraction == edit_rate.fraction:
            continue  # same clock — no drift row to invent
        drift = grid_drift_ms(int(c.duration_ms), edit_nominal, probed)
        reach = one_frame_drift_at(edit_nominal, probed)
        mismatch.append({
            "clip": label, "source": c.source, "source_rate": str(probed),
            "edit_fps": edit_nominal, "clip_duration_ms": int(c.duration_ms),
            "grid_drift_ms_over_clip": str(drift),
            "one_frame_drift_at_ms": str(reach),
            "exceeds_one_frame": bool(abs(drift) >= Fraction(1000, edit_nominal)),
        })
    return mismatch


# The two frame-NATIVE exits: every time they emit is an exact whole-frame
# value on the exact edit rate, for INT projects too, so the ms→frame residual
# is 0 BY CONSTRUCTION on both the int and the 1001-family path. FCPXML carries
# the frames as rational-seconds strings (N/D on the frameDuration timescale);
# XMEML carries them as bare frame integers on the timebase+ntsc clock (ntsc
# TRUE + timebase nominal IS the exact 1001-family rate). Per-target grid label
# + honest note (the fcpxml text is byte-identical to its pre-xmeml wording).
_FRAME_NATIVE_GRID: dict[str, str] = {
    "fcpxml": "rational-native",
    "xmeml": "frame-native",
}
_FRAME_NATIVE_NOTE: dict[str, str] = {
    "fcpxml": (
        "frame_drift measured against the exact edit grid {rate}: FCPXML "
        "carries every time as an exact whole-frame rational-seconds string "
        "(frameDuration-native) for BOTH int and 1001-family projects, so the "
        "ms→frame residual is 0 BY CONSTRUCTION — no drift row is needed even for "
        "an int project (the rational-native advantage; OTIO's int path can carry "
        "fractional frames, FCPXML never does)."),
    "xmeml": (
        "frame_drift measured against the exact edit grid {rate}: XMEML carries "
        "every <start>/<end>/<in>/<out> as a WHOLE-FRAME INTEGER on the "
        "sequence's timebase+ntsc clock (ntsc TRUE + timebase nominal IS the "
        "exact 1001-family rate), so the ms→frame residual is 0 BY CONSTRUCTION "
        "for BOTH int and 1001-family projects — the timebase+ntsc pair is "
        "drift-free (the fcpxml rational-native stance on an integer-frame "
        "carrier)."),
}


def _frame_native_drift(
    target: str, timeline: "Timeline", source_rates: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """The ``frame_drift`` block for the frame-NATIVE exits (fcpxml/xmeml).

    Both write EVERY exported boundary as an exact whole frame on the exact
    edit rate — for INT projects too, never a float ms×fps value — so the
    residual is 0 BY CONSTRUCTION on BOTH the int and the 1001-family path
    (contrast OTIO's int path, which writes fractional-frame RationalTime
    values). The zero is still DERIVED from the frame truth per boundary,
    never fabricated; the per-target carrier story rides the grid label + the
    honest note (see :data:`_FRAME_NATIVE_GRID` / :data:`_FRAME_NATIVE_NOTE`).
    """
    notes: list[str] = []
    try:
        edit_rate = timeline.frame_rate  # exact Rate: echo when rational, else int fps
    except Exception:
        return {
            "checked": False,
            "reason": f"timeline edit rate unresolvable (fps={timeline.fps!r}) — "
                      "no drift math on a broken grid (QC owns the bad fps)",
        }, notes

    off_grid: list[dict[str, Any]] = []
    max_residual = Fraction(0)
    count = 0
    # VIDEO telescopes duration_frames (exact cumulative whole-frame boundary),
    # ms_to_frames on the stampless fallback — every value an INTEGER frame index,
    # which is precisely why the residual vs the frame grid is 0 BY CONSTRUCTION.
    for label, frames in _rational_frame_boundaries(
            timeline, _DRIFT_TRACKS[target], edit_rate):
        count += 1
        exact = Fraction(int(frames))
        residual = abs(exact - round(exact))   # == 0 for a whole frame
        if residual > max_residual:
            max_residual = residual
        if residual > 0:  # unreachable for a whole frame — kept honest
            off_grid.append({
                "clip": label, "frames_exact": str(exact),
                "residual_frames": str(residual),
            })

    mismatch = _fcpxml_rate_mismatch(timeline, source_rates, edit_rate, notes)
    block = {
        "checked": True,
        "grid": _FRAME_NATIVE_GRID[target],
        "edit_rate": str(edit_rate),        # "24000/1001" or "24"
        "edit_fps": edit_rate.nominal_int,
        "boundaries_checked": count,
        "off_grid": off_grid,
        "cumulative_max_residual": str(max_residual),
        "all_zero": not off_grid,
        "all_zero_by_construction": True,
        "rate_mismatch": mismatch,
    }
    notes.append(_FRAME_NATIVE_NOTE[target].format(rate=edit_rate))
    return block, notes


def _frame_drift(
    target: str,
    timeline: "Timeline",
    source_rates: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """The ``frame_drift`` block + any notes (unmatched source_rates keys)."""
    notes: list[str] = []
    reason = _NOT_TIME_BEARING.get(target)
    if reason is not None:
        return {"checked": False, "reason": reason}, notes

    # T2/W3: FCPXML and XMEML are frame-NATIVE — their drift is 0 by
    # construction for the INT path too (rational-seconds strings / whole-frame
    # integers on timebase+ntsc), so they take a dedicated branch that the
    # int/rational logic below never touches (existing targets unchanged).
    if target in _FRAME_NATIVE_GRID:
        return _frame_native_drift(target, timeline, source_rates)

    # R4: when the timeline carries R2's rational echo, measure residuals against
    # the EXACT rational frame grid (24000/1001 …); otherwise the integer fps grid
    # exactly as before (int projects are byte-identical).
    rational = _rational_rate(timeline)

    fps_int = int(timeline.fps or 0)
    try:
        int_rate = Rate.from_fraction(fps_int, 1)
    except (TypeError, ValueError):
        return {
            "checked": False,
            "reason": f"timeline fps {timeline.fps!r} is not a positive "
                      "integer — no drift math on a broken grid (QC owns "
                      "reporting the bad fps)",
        }, notes

    # The grid we measure against + the clock the source-rate mismatch compares to.
    edit_rate = rational if rational is not None else int_rate

    off_grid: list[dict[str, Any]] = []
    max_residual = Fraction(0)
    count = 0
    if rational is not None:
        # RATIONAL PATH (R2-compiled + R4-exported): the exporter writes EXACT
        # whole frames — video durations telescope from duration_frames, audio/
        # caption windows are ms_to_frames nearest whole frames. Every exported
        # boundary is thus an integer on the rational grid, so its residual is 0
        # BY CONSTRUCTION (the closing pin of the R-track). We still COMPUTE each
        # residual from the frame truth — the zero is derived, never fabricated.
        for label, frames in _rational_frame_boundaries(
                timeline, _DRIFT_TRACKS[target], rational):
            count += 1
            exact = Fraction(int(frames))          # a whole-frame index
            residual = abs(exact - round(exact))   # == 0 for an integer
            if residual > max_residual:
                max_residual = residual
            if residual > 0:  # unreachable for integer frames — kept honest
                off_grid.append({
                    "clip": label,
                    "frames_exact": str(exact),
                    "residual_frames": str(residual),
                })
    else:
        for label, ms in _boundaries(timeline, _DRIFT_TRACKS[target]):
            count += 1
            # residual = |ms·fps/1000 − round(ms·fps/1000)| — exact Fractions,
            # nearest frame via timebase.ms_to_frames (ROUND_HALF_UP).
            exact = Fraction(ms * int_rate.numerator, 1000 * int_rate.denominator)
            residual = abs(exact - ms_to_frames(ms, int_rate))
            if residual > max_residual:
                max_residual = residual
            if residual > 0:
                off_grid.append({
                    "clip": label,
                    "ms": ms,
                    "frames_exact": str(exact),
                    "residual_frames": str(residual),
                })

    mismatch: list[dict[str, Any]] = []
    if source_rates:
        by_source = {str(k): v for k, v in source_rates.items()}
        video_sources = {c.source for c in timeline.tracks.video}
        for missing in sorted(set(by_source) - video_sources):
            notes.append(
                f"source_rates key {missing!r} matches no timeline video clip "
                "— ignored (nothing fabricated)")
        # The edit grid the probed source is compared against: the exact rational
        # edit rate when present (so a same-clock 1001 source yields NO row), else
        # the integer fps grid. The grid_drift_ms / one_frame_drift_at helpers take
        # an integer edit fps, so the drift numbers use the nominal label.
        edit_nominal = edit_rate.nominal_int
        for c in timeline.tracks.video:
            if c.source not in by_source:
                continue
            raw = by_source[c.source]
            probed = raw if isinstance(raw, Rate) else classify_rate(raw)
            label = f"video:{c.shot}/{c.take}"
            if not isinstance(probed, Rate):
                mismatch.append({
                    "clip": label,
                    "source": c.source,
                    "source_rate": "unknown",
                    "note": "probed rate not classifiable "
                            "(timebase.classify_rate → UNKNOWN); no drift "
                            "numbers fabricated",
                })
                continue
            if probed.fraction == edit_rate.fraction:
                continue  # same clock — no drift row to invent
            drift = grid_drift_ms(int(c.duration_ms), edit_nominal, probed)
            reach = one_frame_drift_at(edit_nominal, probed)
            mismatch.append({
                "clip": label,
                "source": c.source,
                "source_rate": str(probed),
                "edit_fps": edit_nominal,
                "clip_duration_ms": int(c.duration_ms),
                "grid_drift_ms_over_clip": str(drift),
                "one_frame_drift_at_ms": str(reach),
                "exceeds_one_frame": bool(abs(drift) >= Fraction(1000, edit_nominal)),
            })

    if rational is not None:
        block = {
            "checked": True,
            "grid": "rational",
            "edit_rate": str(rational),          # e.g. "24000/1001"
            "edit_fps": rational.nominal_int,     # the nominal label (24, 30 …)
            "boundaries_checked": count,
            "off_grid": off_grid,
            "cumulative_max_residual": str(max_residual),
            "all_zero": not off_grid,
            "all_zero_by_construction": True,
            "rate_mismatch": mismatch,
        }
        notes.append(
            f"frame_drift measured against the exact rational grid {rational}: "
            "this project is R2-compiled (video on cumulative whole-frame "
            "boundaries) and R4-exported (integer RationalTime frame values), so "
            "every exported boundary is a whole frame — the ms→frame rounding "
            "residual is 0 BY CONSTRUCTION.")
        carrier_note = _MS_CARRIER_NOTE.get(target)
        if carrier_note is not None:
            notes.append(carrier_note)
    else:
        block = {
            "checked": True,
            "edit_fps": fps_int,
            "boundaries_checked": count,
            "off_grid": off_grid,
            "cumulative_max_residual": str(max_residual),
            "all_zero": not off_grid,
            "rate_mismatch": mismatch,
        }
    return block, notes


# --------------------------------------------------------------------------- #
# exported-artifact cross-check (read-only; never a classification input)      #
# --------------------------------------------------------------------------- #


def _count_otio_clips(doc: dict[str, Any]) -> tuple[int, int]:
    video = audio = 0
    tracks = doc.get("tracks")
    children = tracks.get("children") if isinstance(tracks, dict) else tracks
    for tr in children or []:
        if not isinstance(tr, dict):
            continue
        n = len([c for c in tr.get("children") or [] if isinstance(c, dict)])
        if str(tr.get("kind")) == "Video":
            video += n
        elif str(tr.get("kind")) == "Audio":
            audio += n
    return video, audio


def _count_jianying_segments(doc: dict[str, Any]) -> tuple[int, int]:
    video = text = 0
    for tr in doc.get("tracks") or []:
        if not isinstance(tr, dict):
            continue
        n = len([s for s in tr.get("segments") or [] if isinstance(s, dict)])
        if tr.get("type") == "video":
            video += n
        elif tr.get("type") == "text":
            text += n
    return video, text


def _exported_notes(
    project: "Project", target: str, timeline: "Timeline",
    exported: dict[str, Any] | str | Path,
) -> list[str]:
    """Cross-check notes about the ACTUAL exported artifact (read-only)."""
    doc: dict[str, Any] | None = None
    text: str | None = None
    label = "exported document"
    if isinstance(exported, (str, Path)):
        p = Path(exported)
        if not p.exists():
            raise ProjectError(f"conform-loss: 导出文件不存在: {p}")
        try:
            label = f"exported file {project.relpath(p)!r}"
        except Exception:
            label = f"exported file {p.name!r}"
        suffix = p.suffix.lower()
        if suffix in (".otio", ".json"):
            try:
                loaded = read_json(p)
            except Exception:
                return [f"{label} is not readable JSON — no cross-check performed"]
            doc = loaded if isinstance(loaded, dict) else None
        elif suffix in (".srt", ".vtt", ".ass", ".ttml", ".edl", ".fcpxml", ".xml"):
            text = p.read_text(encoding="utf-8")
        else:
            return [f"{label} not parsed (opaque/binary payload) — "
                    "classification is an exporter-code audit"]
    elif isinstance(exported, dict):
        doc = exported or None
        if doc is None:
            return ["no exported document provided for cross-check — "
                    "classification is an exporter-code audit"]
    else:
        raise ProjectError(
            f"conform-loss: exported 参数必须是 dict 或 Path,不是 "
            f"{type(exported).__name__}")

    t = timeline.tracks
    if target == "otio" and doc is not None:
        video, audio = _count_otio_clips(doc)
        want_a = sum(len(getattr(t, b)) for b in _AUDIO_BUSES)
        note = (f"{label}: {video} video / {audio} audio clip(s) vs timeline "
                f"{len(t.video)} / {want_a}")
        if (video, audio) != (len(t.video), want_a):
            note += " — MISMATCH: verify the export is fresh"
        return [note]
    if target == "jianying" and doc is not None:
        video, txt = _count_jianying_segments(doc)
        note = (f"{label}: {video} video / {txt} text segment(s) vs timeline "
                f"{len(t.video)} / {len(t.captions)}")
        if (video, txt) != (len(t.video), len(t.captions)):
            note += " — MISMATCH: verify the export is fresh"
        return [note]
    if target == "srt_ass" and text is not None:
        cues = text.count("-->")
        note = f"{label}: {cues} cue(s) vs timeline {len(t.captions)}"
        if cues != len(t.captions):
            note += " — MISMATCH: verify the export is fresh"
        return [note]
    if target == "ttml" and text is not None:
        import xml.etree.ElementTree as ET  # lazy: only this branch parses XML

        try:
            cues = len(ET.fromstring(text).findall(
                ".//{http://www.w3.org/ns/ttml}p"))
        except ET.ParseError:
            return [f"{label} is not readable XML — no cross-check performed"]
        note = f"{label}: {cues} cue(s) vs timeline {len(t.captions)}"
        if cues != len(t.captions):
            note += " — MISMATCH: verify the export is fresh"
        return [note]
    if target == "edl" and text is not None:
        import re  # lazy: only this branch scans event rows

        # One event NUMBER per clip; a dissolve reuses the incoming clip's
        # number for both its lines, so distinct numbers == clip count.
        nums = {m.group(1) for m in re.finditer(r"(?m)^(\d{3})\s", text)}
        note = (f"{label}: {len(nums)} event(s) vs timeline "
                f"{len(t.video)} video clip(s)")
        if len(nums) != len(t.video):
            note += " — MISMATCH: verify the export is fresh"
        return [note]
    if target == "fcpxml" and text is not None:
        import xml.etree.ElementTree as ET  # lazy: only this branch parses XML

        try:
            # one primary-storyline <asset-clip> per video clip (no namespace)
            clips = len(ET.fromstring(text).findall(".//spine/asset-clip"))
        except ET.ParseError:
            return [f"{label} is not readable XML — no cross-check performed"]
        note = (f"{label}: {clips} asset-clip(s) vs timeline "
                f"{len(t.video)} video clip(s)")
        if clips != len(t.video):
            note += " — MISMATCH: verify the export is fresh"
        return [note]
    if target == "xmeml" and text is not None:
        import xml.etree.ElementTree as ET  # lazy: only this branch parses XML

        try:
            # one video-track <clipitem> per video clip (audio clipitems ride
            # the audio tracks and are counted by neither side here)
            clips = len(ET.fromstring(text).findall(
                "./sequence/media/video/track/clipitem"))
        except ET.ParseError:
            return [f"{label} is not readable XML — no cross-check performed"]
        note = (f"{label}: {clips} clipitem(s) vs timeline "
                f"{len(t.video)} video clip(s)")
        if clips != len(t.video):
            note += " — MISMATCH: verify the export is fresh"
        return [note]
    if target == "native_draft":
        return [f"{label}: library-owned draft structure not audited "
                "(adapter wall) — no cross-check"]
    return [f"{label}: no structural cross-check for target {target!r} — "
            "classification is an exporter-code audit"]


# --------------------------------------------------------------------------- #
# the report                                                                   #
# --------------------------------------------------------------------------- #


def conform_loss_report(
    project: "Project",
    target: str,
    timeline: "Timeline",
    exported: dict[str, Any] | str | Path,
    *,
    source_rates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive the ``manju.conform-loss/v1`` document for one export.

    ``exported`` is the artifact the exporter just wrote (a loaded dict or its
    path) — used ONLY for read-only cross-check notes, never mutated.
    ``source_rates`` optionally maps a video clip's ``source`` path to its
    probed rate (:class:`~manju.core.timebase.Rate`, or anything
    :func:`~manju.core.timebase.classify_rate` accepts); a rate that differs
    from the edit fps yields exact ``grid_drift_ms`` / ``one_frame_drift_at``
    numbers, an unclassifiable one yields an honest ``unknown`` row.
    """
    _require_known_target(target)
    inventory = timeline_feature_inventory(timeline)
    categories = classify_features(target, inventory)
    frame_drift, drift_notes = _frame_drift(target, timeline, source_rates)
    exported_notes = _exported_notes(project, target, timeline, exported)

    notes = [
        "timeline features present: " + (
            ", ".join(f"{r['feature']}({r['detail']})" for r in inventory)
            or "none"),
        _SCOPE_NOTES[target],
        *exported_notes,
        *drift_notes,
    ]
    return {
        "schema": SCHEMA,
        "target": target,
        "preserved": categories["preserved"],
        "approximated": categories["approximated"],
        "dropped": categories["dropped"],
        "unsupported": categories["unsupported"],
        "frame_drift": frame_drift,
        "reimport": None,
        "notes": notes,
    }


def reimport_changes(project: "Project", edited_path: str | Path) -> dict[str, Any]:
    """The conform doc's ``reimport`` block — a thin summary over the EXISTING
    :func:`manju.build.roundtrip.plan_roundtrip` (zero new diff logic).

    ``changed`` = rows plan_roundtrip diffed against the export baseline (ok or
    conflict), ``unchanged`` = its explicit no-op row, ``unknown`` = rows it
    could not match/act on. Attach the result as ``doc["reimport"]``.
    """
    from ..build.roundtrip import plan_roundtrip  # lazy: reuse, not a dep cycle

    plan = plan_roundtrip(project, Path(edited_path))
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for row in plan.get("rows") or []:
        if not isinstance(row, dict):
            continue
        summary = {
            "class": row.get("class"),
            "state": row.get("state"),
            "target": row.get("target"),
        }
        if row.get("class") == "no_changes":
            unchanged.append(summary)
        elif row.get("state") == "unmatched" or not row.get("action"):
            unknown.append(summary)
        else:
            changed.append(summary)
    return {
        "kind": plan.get("kind"),
        "baseline": plan.get("baseline"),
        "truth_moved": bool(plan.get("truth_moved")),
        "changed": changed,
        "unchanged": unchanged,
        "unknown": unknown,
    }


# --------------------------------------------------------------------------- #
# derived-output store: reports/conform/<target>_<digest>.json                 #
# --------------------------------------------------------------------------- #


def write_conform_report(project: "Project", doc: dict[str, Any]) -> Path:
    """Write ``reports/conform/<target>_<digest>.json`` — deletable derived
    output, content-addressed over the doc facts (no wall-clock anywhere, so
    the same doc writes byte-identically forever). Never a build input."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ProjectError(
            f"conform-loss: 只能写 {SCHEMA} 文档(收到 "
            f"{doc.get('schema') if isinstance(doc, dict) else type(doc).__name__!r})")
    target = str(doc.get("target") or "")
    _require_known_target(target)
    facts = {k: v for k, v in doc.items() if k != "digest"}
    digest = hash_value(facts)
    payload = dict(facts)
    payload["digest"] = digest
    out = project.reports_dir / "conform" / f"{target}_{short_hash(digest)}.json"
    write_json(out, payload)
    return out


def read_conform_report(path: str | Path) -> dict[str, Any]:
    """Load + verify a stored conform report. Any tampering with the facts or
    the digest is rejected — the report is evidence, not editable state."""
    p = Path(path)
    try:
        data = read_json(p)
    except Exception as exc:
        raise ProjectError(f"conform-loss 报告不可读: {p} — {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectError(f"conform-loss 报告不是 JSON 对象: {p}")
    recorded = data.pop("digest", None)
    if not isinstance(recorded, str) or not recorded:
        raise ProjectError(
            f"conform-loss 报告缺少 digest(被篡改或不是本工具写出): {p}")
    if hash_value(data) != recorded:
        raise ProjectError(
            f"conform-loss 报告与其 digest 不一致(内容被篡改): {p}")
    if data.get("schema") != SCHEMA:
        raise ProjectError(
            f"conform-loss 报告 schema 不受支持: {data.get('schema')!r}(期望 {SCHEMA})")
    return data
