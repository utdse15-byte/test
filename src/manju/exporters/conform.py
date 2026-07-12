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
            "bottom-centre region only; RTL/vertical/ruby are NOT expressed "
            "(the cue model carries no layout semantics)",
            "exporters/ttml.py:114-144,147-213"),
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
            "unsupported",
            "audio is out of scope for this V-only CMX EDL (see audio_tracks); "
            "no audio event carries a source in-point",
            "exporters/edl.py:1-80 (module scope: V track only)"),
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
        "clip_volume": (
            "unsupported",
            "own-audio level/mute is an audio-domain feature; this V-only EDL "
            "carries no audio channel at all (see audio_tracks)",
            "exporters/edl.py:1-80 (module scope: V track only)"),
        "audio_gain": (
            "unsupported",
            "audio is out of scope for this V-only CMX EDL (see audio_tracks)",
            "exporters/edl.py:1-80 (module scope: V track only)"),
        "audio_fade_in": (
            "unsupported",
            "audio is out of scope for this V-only CMX EDL (see audio_tracks)",
            "exporters/edl.py:1-80 (module scope: V track only)"),
        "audio_fade_out": (
            "unsupported",
            "audio is out of scope for this V-only CMX EDL (see audio_tracks)",
            "exporters/edl.py:1-80 (module scope: V track only)"),
        "ducking": (
            "unsupported",
            "audio is out of scope for this V-only CMX EDL (see audio_tracks)",
            "exporters/edl.py:1-80 (module scope: V track only)"),
        "audio_loops": (
            "unsupported",
            "audio is out of scope for this V-only CMX EDL (see audio_tracks)",
            "exporters/edl.py:1-80 (module scope: V track only)"),
        "audio_tracks": (
            "unsupported",
            "Manju's four buses (voice/music/sfx/ambient) cannot ride CMX's "
            "flat A-channel model faithfully; this loop exports the V track "
            "only — audio is honestly out of scope by design, not silently "
            "dropped",
            "exporters/edl.py:1-80 (module scope: V track only)"),
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
            "unsupported",
            "video spine only this loop — audio is not written (see "
            "audio_tracks), so no audio in-point rides the document",
            "exporters/fcpxml.py (module scope: video spine only this loop)"),
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
        "clip_volume": (
            "dropped",
            "video source gain/mute is an audio-domain adjustment; no "
            "<adjust-volume> is written on the video asset-clip this loop",
            "exporters/fcpxml.py (no volume path)"),
        "audio_gain": (
            "unsupported",
            "video spine only this loop — audio is not written (see "
            "audio_tracks)",
            "exporters/fcpxml.py (module scope: video spine only this loop)"),
        "audio_fade_in": (
            "unsupported",
            "video spine only this loop — audio is not written (see "
            "audio_tracks)",
            "exporters/fcpxml.py (module scope: video spine only this loop)"),
        "audio_fade_out": (
            "unsupported",
            "video spine only this loop — audio is not written (see "
            "audio_tracks)",
            "exporters/fcpxml.py (module scope: video spine only this loop)"),
        "ducking": (
            "unsupported",
            "video spine only this loop — audio is not written (see "
            "audio_tracks)",
            "exporters/fcpxml.py (module scope: video spine only this loop)"),
        "audio_loops": (
            "unsupported",
            "video spine only this loop — audio is not written (see "
            "audio_tracks)",
            "exporters/fcpxml.py (module scope: video spine only this loop)"),
        "audio_tracks": (
            "unsupported",
            "video spine only this loop: FCPXML's connected-clip role/lane "
            "model CAN carry Manju's four buses (unlike CMX EDL's flat "
            "A-channel), so this is a WRITER-scope boundary — a deferred "
            "increment — not a format limit; audio is honestly omitted rather "
            "than faked",
            "exporters/fcpxml.py (module scope: video spine only this loop)"),
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

#: Exporter modules with NO conform classifier, each with the reason. Empty
#: today — every shipped exporter is classified. Adding exporter #6 without a
#: classifier or a row here fails tests/test_fp_conform.py (no silent
#: degradation, doc §6.2).
UNSUPPORTED_TARGETS: dict[str, str] = {}

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
           "semicolon-DF). V track only — audio is out of scope by design; "
           "source TC is 00:00:00:00-based (takes carry no recorded reel/TC).",
    "fcpxml": "FCPXML 1.9 video spine (library>event>project>sequence>spine) — "
              "the one NLE exit where our rational time rides NATIVELY: every "
              "offset/start/duration is an EXACT whole-frame rational-seconds "
              "string on the format's frameDuration timescale ('1/24s' for int, "
              "'1001/24000s' for the 1001 family), so there is ZERO drift for "
              "either. Cross-dissolves are native <transition>s (others cut + "
              "note, never a wrong dissolve); captions ride the SRT/TTML exits "
              "and audio is a deferred writer increment — both honestly omitted.",
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


def _fcpxml_frame_drift(
    timeline: "Timeline", source_rates: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """The ``frame_drift`` block for FCPXML — the rational-NATIVE pin.

    FCPXML writes EVERY time as an exact whole-frame multiple of ``frameDuration``
    on the exact edit rate (an ``N/D``-seconds string) — for INT projects too
    (``'1/24s'``), never a float ms×fps value. So every exported video boundary is
    a whole frame on the exact grid and the ms→frame residual is 0 BY CONSTRUCTION
    on BOTH the int and the 1001-family path — the one NLE exit where our rational
    truth rides natively with NO drift row. (Contrast OTIO's int path, which
    writes fractional-frame RationalTime values; FCPXML never does.)
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
            timeline, _DRIFT_TRACKS["fcpxml"], edit_rate):
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
        "grid": "rational-native",
        "edit_rate": str(edit_rate),        # "24000/1001" or "24"
        "edit_fps": edit_rate.nominal_int,
        "boundaries_checked": count,
        "off_grid": off_grid,
        "cumulative_max_residual": str(max_residual),
        "all_zero": not off_grid,
        "all_zero_by_construction": True,
        "rate_mismatch": mismatch,
    }
    notes.append(
        f"frame_drift measured against the exact edit grid {edit_rate}: FCPXML "
        "carries every time as an exact whole-frame rational-seconds string "
        "(frameDuration-native) for BOTH int and 1001-family projects, so the "
        "ms→frame residual is 0 BY CONSTRUCTION — no drift row is needed even for "
        "an int project (the rational-native advantage; OTIO's int path can carry "
        "fractional frames, FCPXML never does).")
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

    # T2: FCPXML is rational-NATIVE — its drift is frame-native (residual 0 by
    # construction) for the INT path too, so it takes a dedicated branch that the
    # int/rational float logic below never touches (existing targets unchanged).
    if target == "fcpxml":
        return _fcpxml_frame_drift(timeline, source_rates)

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
        elif suffix in (".srt", ".vtt", ".ass", ".ttml", ".edl", ".fcpxml"):
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
