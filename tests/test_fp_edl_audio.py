"""FP Loop Y1 — CMX3600 EDL declared A1/A2 audio subset (red-first).

S2 shipped a V-only cut list with Manju's four audio buses recorded as the
format's TOTALITY limit (audio ``unsupported`` in the conform audit). This loop
adds a DECLARED, honest two-channel subset — it contradicts nothing S2 said, it
narrows the loss:

* the VOICE bus becomes A1 events (CMX channel token ``A``);
* the MUSIC bus becomes A2 events (channel token ``A2``);
* SFX + AMBIENT are NOT squeezed into channels (classic CMX3600 has no third /
  fourth stereo pair) — every omitted clip gets an in-band ``* MANJU:`` note
  naming its bus / source / window, so nothing vanishes silently;
* audio source TC is zero-based from ``start_offset_ms`` (generated-media
  honesty, the exact S2 precedent for video ``source_in_ms``); record TC rides
  the SAME timebase machinery (``ms_to_frames`` ROUND_HALF_UP, offset by the
  broadcast-hour start frame);
* gain / fades are NOT expressible in bare CMX3600 → a per-clip ``* MANJU:``
  note whenever nonzero (never silent);
* a looped bed (``loop=True``) or an open-ended clip (``duration_ms is None``)
  is NEVER fabricated into a single cut event — it gets an omission note instead;
* event numbering is a SINGLE monotone sequence across V → A1 → A2 blocks
  (blocked-by-track — see the loop's ordering ruling); omitted clips consume no
  number.

Red-first (§20): every golden string below is derived from first principles
(the frame math is spelled out inline), so this file encodes the SPEC, not a
copy of the output. S2's ``test_fp_edl.py`` is NEVER edited — the V-only golden
there stays byte-identical because audio blocks only appear when an audio bus is
populated (see ``test_video_only_timeline_byte_identical_to_v_only``).
"""

from __future__ import annotations

import pytest

from manju.core.models import (
    AudioClip,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from manju.core.timebase import Rate, ms_to_frames
from manju.exporters.edl import compile_edl

R24 = Rate.from_fraction(24, 1)
R2397 = Rate.from_fraction(24000, 1001)  # 23.976 — NTSC but NON-drop (S2 rule)


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _vid(shot, take, start_ms, duration_ms, *, source_in_ms=0, transition=None):
    return VideoClip(
        shot=shot, take=take, source=f"media/gen/{shot}/{take}.mp4",
        start_ms=start_ms, duration_ms=duration_ms, source_in_ms=source_in_ms,
        transition_out=transition,
    )


def _av_timeline():
    """2 video clips + a populated voice/music/sfx/ambient bus set.

    voice: line1 (trimmed source, start_offset 500ms) + line2 (gain -3dB)
    music: bed (fade in/out) + loopbed (loop=True -> omission, no event)
    sfx:   whoosh (omission note only)
    ambient: rain (omission note only)
    """
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-edl-audio"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(
            video=[
                _vid("S001", "TAKEALPHA0001", 0, 2000),
                _vid("S002", "TAKEBETA0002", 2000, 2000),
            ],
            voice=[
                AudioClip(source="media/vo/line1.wav", start_ms=0,
                          duration_ms=1500, start_offset_ms=500),
                AudioClip(source="media/vo/line2.wav", start_ms=2000,
                          duration_ms=1000, gain_db=-3.0),
            ],
            music=[
                AudioClip(source="media/music/bed.mp3", start_ms=0,
                          duration_ms=4000, fade_in_ms=250, fade_out_ms=500),
                AudioClip(source="media/music/loopbed.mp3", start_ms=0,
                          duration_ms=8000, loop=True),
            ],
            sfx=[AudioClip(source="media/sfx/whoosh.wav",
                           start_ms=1000, duration_ms=300)],
            ambient=[AudioClip(source="media/amb/rain.wav",
                               start_ms=0, duration_ms=4000)],
        ),
    )


# --------------------------------------------------------------------------- #
# 1. golden bytes — the whole V + A1 + A2 document, derived from the math      #
# --------------------------------------------------------------------------- #


def test_golden_av_edl_exact_bytes():
    tl = _av_timeline()
    got = compile_edl(tl, rate=R24, start_timecode="01:00:00:00", title="AV DEMO")

    # Frame math @24fps NDF, start 01:00:00:00 == frame 86400:
    #   ms_to_frames: 0->0, 500->12, 1000->24, 1500->36, 2000->48, 3000->72,
    #                 4000->96
    #   V: clip1 rec 86400..86448 (01:00:00:00..02:00) src 0..48
    #      clip2 rec 86448..86496 (01:00:02:00..04:00) src 0..48
    #   A1(voice): line1 dur 36f, rec 86400..86436 (..01:00:01:12),
    #                 src 12..48 (00:00:00:12..02:00) <- zero-based from 500ms
    #              line2 dur 24f, rec 86448..86472 (01:00:02:00..03:00),
    #                 src 0..24 (..00:00:01:00); gain -3dB -> note
    #   A2(music): bed dur 96f, rec 86400..86496 (01:00:00:00..04:00),
    #                 src 0..96; fades -> note
    #              loopbed loop=True -> omission note (no event)
    #   sfx/ambient -> omission notes (no events)
    #   event numbering: single monotone 001..005 across V(1,2) A1(3,4) A2(5)
    expected = (
        "TITLE:   AV DEMO\n"
        "FCM: NON-DROP FRAME\n"
        "001  TAKEALPH V    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00\n"
        "* FROM CLIP NAME: TAKEALPHA0001\n"
        "002  TAKEBETA V    C        00:00:00:00 00:00:02:00 01:00:02:00 01:00:04:00\n"
        "* FROM CLIP NAME: TAKEBETA0002\n"
        "003  LINE1    A    C        00:00:00:12 00:00:02:00 01:00:00:00 01:00:01:12\n"
        "* FROM CLIP NAME: media/vo/line1.wav\n"
        "004  LINE2    A    C        00:00:00:00 00:00:01:00 01:00:02:00 01:00:03:00\n"
        "* FROM CLIP NAME: media/vo/line2.wav\n"
        "* MANJU: voice clip 'media/vo/line2.wav' gain -3dB not expressible in "
        "bare CMX3600 (a mix level, not a cut-list primitive)\n"
        "005  BED      A2   C        00:00:00:00 00:00:04:00 01:00:00:00 01:00:04:00\n"
        "* FROM CLIP NAME: media/music/bed.mp3\n"
        "* MANJU: music clip 'media/music/bed.mp3' fades (in 250ms / out 500ms) "
        "not expressible in bare CMX3600\n"
        "* MANJU: music clip 'media/music/loopbed.mp3' [0..8000ms) loop=True "
        "omitted - CMX3600 has no loop primitive; the render materializes the "
        "bed by repetition (no honest single-event source window)\n"
        "* MANJU: sfx clip 'media/sfx/whoosh.wav' [1000..1300ms) omitted - "
        "CMX3600 classic form carries A1/A2 only (voice->A1, music->A2); "
        "sfx/ambient have no channel\n"
        "* MANJU: ambient clip 'media/amb/rain.wav' [0..4000ms) omitted - "
        "CMX3600 classic form carries A1/A2 only (voice->A1, music->A2); "
        "sfx/ambient have no channel\n"
    )
    assert got == expected


# --------------------------------------------------------------------------- #
# 2. the V-only regression guard — S2's world is byte-identical                #
# --------------------------------------------------------------------------- #


def test_video_only_timeline_byte_identical_to_v_only():
    """A timeline with NO audio bus must render exactly the V-only document S2
    pinned — audio blocks appear ONLY when a bus is populated."""
    tl = Timeline(
        meta=TimelineMeta(compiled_from="v-only"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(video=[_vid("S001", "TA", 0, 2000)]),
    )
    got = compile_edl(tl, rate=R24, start_timecode="01:00:00:00", title="X")
    assert got == (
        "TITLE:   X\n"
        "FCM: NON-DROP FRAME\n"
        "001  TA       V    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00\n"
        "* FROM CLIP NAME: TA\n"
    )
    # no audio channel tokens, no audio notes leaked in
    assert " A  " not in got and " A2 " not in got
    assert "* MANJU:" not in got


# --------------------------------------------------------------------------- #
# 3. audio source TC — zero-based from start_offset_ms (S2 honesty precedent)  #
# --------------------------------------------------------------------------- #


def test_audio_source_tc_zero_based_from_start_offset():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=1500,
        tracks=TimelineTracks(voice=[
            AudioClip(source="media/vo/a.wav", start_ms=0, duration_ms=1500,
                      start_offset_ms=500),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    row = [ln for ln in got.splitlines() if ln[:3].isdigit()][0]
    src_in, src_out, rec_in, rec_out = row.split()[-4:]
    assert src_in == "00:00:00:12"   # 500ms -> 12f, zero-based (not real tape TC)
    assert src_out == "00:00:02:00"  # + 1500ms window (36f)
    assert rec_in == "01:00:00:00" and rec_out == "01:00:01:12"
    # source length == record length (a cut-list invariant, S2 precedent)
    src_span = ms_to_frames(2000, R24) - ms_to_frames(500, R24)
    rec_span = ms_to_frames(1500, R24) - ms_to_frames(0, R24)
    assert src_span == rec_span == 36


def test_untrimmed_audio_source_starts_at_zero():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=1000,
        tracks=TimelineTracks(voice=[
            AudioClip(source="media/vo/a.wav", start_ms=0, duration_ms=1000),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    row = [ln for ln in got.splitlines() if ln[:3].isdigit()][0]
    assert row.split()[-4] == "00:00:00:00"


# --------------------------------------------------------------------------- #
# 4. channel tokens — voice -> A (A1), music -> A2                             #
# --------------------------------------------------------------------------- #


def test_voice_is_A1_music_is_A2_channel_tokens():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=2000,
        tracks=TimelineTracks(
            voice=[AudioClip(source="v.wav", start_ms=0, duration_ms=1000)],
            music=[AudioClip(source="m.mp3", start_ms=0, duration_ms=2000)],
        ),
    )
    got = compile_edl(tl, rate=R24, title="X")
    rows = [ln for ln in got.splitlines() if ln[:3].isdigit()]
    channels = [ln.split()[2] for ln in rows]
    assert channels == ["A", "A2"], "voice=A1 (token 'A'), music=A2"


# --------------------------------------------------------------------------- #
# 5. event numbering — a single monotone run across V -> A1 -> A2 blocks       #
# --------------------------------------------------------------------------- #


def test_event_numbering_single_monotone_across_tracks():
    got = compile_edl(_av_timeline(), rate=R24, title="X")
    nums = [int(ln.split()[0]) for ln in got.splitlines() if ln[:3].isdigit()]
    # 5 emitted events (2 V + 2 voice + 1 music); loopbed/sfx/ambient omitted
    assert nums == [1, 2, 3, 4, 5]
    # strictly increasing, no restart-at-001 per track
    assert all(b == a + 1 for a, b in zip(nums, nums[1:]))
    # the V events keep S2's numbers (1,2) — blocked-by-track puts audio AFTER
    v_rows = [ln for ln in got.splitlines()
              if ln[:3].isdigit() and ln.split()[2] == "V"]
    assert [int(r.split()[0]) for r in v_rows] == [1, 2]


# --------------------------------------------------------------------------- #
# 6. omitted clips never fabricate an event — loop / None-duration            #
# --------------------------------------------------------------------------- #


def test_looped_bed_is_omission_note_not_an_event():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=8000,
        tracks=TimelineTracks(music=[
            AudioClip(source="media/music/loop.mp3", start_ms=0,
                      duration_ms=8000, loop=True),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    assert [ln for ln in got.splitlines() if ln[:3].isdigit()] == []  # no event
    assert ("* MANJU: music clip 'media/music/loop.mp3' [0..8000ms) loop=True "
            "omitted - CMX3600 has no loop primitive") in got


def test_open_ended_none_duration_clip_is_omission_note():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=0,
        tracks=TimelineTracks(music=[
            AudioClip(source="media/music/open.mp3", start_ms=0,
                      duration_ms=None),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    assert [ln for ln in got.splitlines() if ln[:3].isdigit()] == []
    assert ("* MANJU: music clip 'media/music/open.mp3' start 0ms omitted - "
            "open-ended (no duration_ms); CMX3600 needs a frame-exact "
            "out-point") in got


# --------------------------------------------------------------------------- #
# 7. gain / fades on a PLACED clip -> notes, never silent                      #
# --------------------------------------------------------------------------- #


def test_gain_on_placed_clip_gets_note():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=1000,
        tracks=TimelineTracks(voice=[
            AudioClip(source="v.wav", start_ms=0, duration_ms=1000, gain_db=2.5),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    assert len([ln for ln in got.splitlines() if ln[:3].isdigit()]) == 1  # placed
    assert ("* MANJU: voice clip 'v.wav' gain +2.5dB not expressible in bare "
            "CMX3600 (a mix level, not a cut-list primitive)") in got


def test_fades_on_placed_clip_get_note():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=4000,
        tracks=TimelineTracks(music=[
            AudioClip(source="m.mp3", start_ms=0, duration_ms=4000,
                      fade_in_ms=250, fade_out_ms=500),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    assert len([ln for ln in got.splitlines() if ln[:3].isdigit()]) == 1
    assert ("* MANJU: music clip 'm.mp3' fades (in 250ms / out 500ms) not "
            "expressible in bare CMX3600") in got


def test_clean_placed_clip_emits_no_loss_note():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=1000,
        tracks=TimelineTracks(voice=[
            AudioClip(source="v.wav", start_ms=0, duration_ms=1000),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    assert "* MANJU:" not in got  # no gain, no fades, no loop -> nothing to note


# --------------------------------------------------------------------------- #
# 8. sfx / ambient are omitted with per-clip bus/source/window notes           #
# --------------------------------------------------------------------------- #


def test_sfx_and_ambient_omitted_with_notes_never_squeezed():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=2000,
        tracks=TimelineTracks(
            sfx=[AudioClip(source="s.wav", start_ms=500, duration_ms=300)],
            ambient=[AudioClip(source="a.wav", start_ms=0, duration_ms=2000)],
        ),
    )
    got = compile_edl(tl, rate=R24, title="X")
    # NOT squeezed into channels: no A3/A4, no events at all here
    assert [ln for ln in got.splitlines() if ln[:3].isdigit()] == []
    assert "A3" not in got and "A4" not in got
    assert ("* MANJU: sfx clip 's.wav' [500..800ms) omitted - CMX3600 classic "
            "form carries A1/A2 only (voice->A1, music->A2); sfx/ambient have "
            "no channel") in got
    assert ("* MANJU: ambient clip 'a.wav' [0..2000ms) omitted - CMX3600 "
            "classic form carries A1/A2 only (voice->A1, music->A2); "
            "sfx/ambient have no channel") in got


# --------------------------------------------------------------------------- #
# 9. audio reels — 8-char ASCII, deterministic, no collision with V reels      #
# --------------------------------------------------------------------------- #


def test_audio_reel_disambiguated_against_video_reels():
    # a video take and an audio source that fold to the SAME 8-char stem
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=2000,
        tracks=TimelineTracks(
            video=[_vid("S001", "SHAREDID", 0, 1000)],
            voice=[AudioClip(source="media/vo/sharedid.wav",
                             start_ms=0, duration_ms=1000)],
        ),
    )
    got = compile_edl(tl, rate=R24, title="X")
    reels = [ln.split()[1] for ln in got.splitlines() if ln[:3].isdigit()]
    assert reels[0] == "SHAREDID"       # V reel keeps the stem
    assert reels[1] == "SHAREDI2"       # audio reel disambiguated (single namespace)
    assert reels[1].isascii() and len(reels[1]) <= 8


def test_cjk_audio_source_ascii_reel_utf8_comment():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=1000,
        tracks=TimelineTracks(voice=[
            AudioClip(source="media/vo/旁白.wav", start_ms=0, duration_ms=1000),
        ]),
    )
    got = compile_edl(tl, rate=R24, title="X")
    reel = [ln for ln in got.splitlines() if ln[:3].isdigit()][0].split()[1]
    assert reel.isascii() and reel and len(reel) <= 8
    assert "* FROM CLIP NAME: media/vo/旁白.wav" in got  # full UTF-8 name kept


# --------------------------------------------------------------------------- #
# 10. determinism + rational 23.976 stays NON-drop (S2 rule)                    #
# --------------------------------------------------------------------------- #


def test_determinism_byte_identical_with_audio():
    tl = _av_timeline()
    assert compile_edl(tl, rate=R24, title="X") == compile_edl(tl, rate=R24, title="X")


def test_rational_2397_audio_is_non_drop():
    tl = Timeline(
        meta=TimelineMeta(), fps=24, duration_ms=1000,
        tracks=TimelineTracks(voice=[
            AudioClip(source="v.wav", start_ms=0, duration_ms=1000),
        ]),
    )
    got = compile_edl(tl, rate=R2397, title="X")
    assert "FCM: NON-DROP FRAME" in got  # 23.976 is NTSC but NON-drop
    assert ";" not in got                # colon-only NDF rendering on audio rows
    row = [ln for ln in got.splitlines() if ln[:3].isdigit()][0]
    assert row.split()[2] == "A"         # voice channel token still A1


def test_drop_frame_audio_uses_semicolon_separator():
    r30df = Rate.from_fraction(30000, 1001)
    tl = Timeline(
        meta=TimelineMeta(), fps=30, duration_ms=2000,
        tracks=TimelineTracks(music=[
            AudioClip(source="m.mp3", start_ms=0, duration_ms=2000),
        ]),
    )
    got = compile_edl(tl, rate=r30df, start_timecode="01:00:00:00", title="X")
    assert "FCM: DROP FRAME" in got
    row = [ln for ln in got.splitlines() if ln[:3].isdigit()][0]
    src_in, src_out, rec_in, rec_out = row.split()[-4:]
    assert rec_in == "01:00:00;00" and src_in == "00:00:00;00"
    assert ";" in rec_out
    assert row.split()[2] == "A2"  # music channel token
