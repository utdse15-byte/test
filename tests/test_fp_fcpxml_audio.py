"""FP Loop V1 — FCPXML CONNECTED-AUDIO increment (the T2-deferred audio lanes).

Red-first (§20): this file pins the audio-lane extension of ``compile_fcpxml``
from FIRST PRINCIPLES — the connected-clip placement math, the fixed lane/role
maps, gain formatting, and the honest handling of loop / None-duration / ducking
/ fades — all derived inline from the frame math so the test encodes the SPEC.

It NEVER edits T2's ``test_fp_fcpxml.py`` (33 tests stay green, untouched) and
proves the empty-buses byte-identity against T2's own three-clip golden.

The placement contract (addendum): a connected audio clip at absolute timeline
frame ``F`` attaches to the spine clip ``i`` whose PULLED-BACK interval
``[offsets[i], offsets[i]+frames[i])`` contains ``F`` (ties → the earlier clip;
``F`` past the last clip end → the last clip), and

    child.offset = _secs(in_frames[i] + F − offsets[i], rate)

so that the recovered absolute position ``parent_offset + (child_offset −
parent_start)`` equals ``F``. lane map: voice −1 / music −2 / sfx −3 / ambient
−4; audioRole: dialogue / music / effects.sfx / effects.ambient.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

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
from manju.exporters.fcpxml import compile_fcpxml

R24 = Rate.from_fraction(24, 1)


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _vid(shot, take, start_ms, duration_ms, *, source_in_ms=0, transition=None):
    return VideoClip(
        shot=shot, take=take, source=f"media/gen/{shot}/{take}.mp4",
        start_ms=start_ms, duration_ms=duration_ms, source_in_ms=source_in_ms,
        transition_out=transition,
    )


def _aud(source, start_ms, duration_ms=None, **kw):
    return AudioClip(source=source, start_ms=start_ms, duration_ms=duration_ms, **kw)


def _tl(video, *, voice=(), music=(), sfx=(), ambient=(),
        fps=24, width=1080, height=1920):
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-fcpxml-audio"),
        fps=fps, width=width, height=height,
        duration_ms=sum(c.duration_ms for c in video),
        tracks=TimelineTracks(
            video=list(video), voice=list(voice), music=list(music),
            sfx=list(sfx), ambient=list(ambient),
        ),
    )


def _audio_timeline():
    """2 video clips (clip0 --xfade_fade 500ms--> clip1, clip1 in-point 250ms)
    + 3 audio clips exercising the hard cases:

      * A voice @500ms  → clip0 (simple)
      * B music @2500ms → clip1 (the PULLED-BACK second clip, across the dissolve)
      * C sfx  @3000ms  → clip1, extends PAST the parent's end (legal FCPXML)
    """
    return _tl(
        [
            _vid("S001", "TAKEA", 0, 2000,
                 transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
            _vid("S002", "TAKEB", 2000, 2000, source_in_ms=250),
        ],
        voice=[_aud("media/voice/line.wav", 500, 1000)],
        music=[_aud("media/music/bed.mp3", 2500, 1000, gain_db=-6.0)],
        sfx=[_aud("media/sfx/hit.wav", 3000, 1000, start_offset_ms=125)],
    )


# --------------------------------------------------------------------------- #
# 1. golden bytes — hand-computed, the whole document                          #
# --------------------------------------------------------------------------- #


def test_golden_audio_lanes_exact_bytes():
    got = compile_fcpxml(_audio_timeline(), rate=R24, name="AUD")

    # Frame math @24fps (frameDuration "1/24s"; F frames == "{F}/24s"):
    #   video: frames=[48,48]; in_frames=[0, 250ms->6]; dissolve 500ms->12
    #          (0<12<48 & 12<48 -> native); offsets=[0, 48-12=36]; seq=36+48=84
    #   video assets r2/r3 (avail 0+48=48, 6+48=54); audio assets r4/r5/r6
    #          (avail voice 0+24=24, music 0+24=24, sfx 3+24=27); effect r7
    #   A voice: F=500ms->12 in clip0[0,48) -> offset 0+12-0=12; start 0; dur 24
    #   B music: F=2500ms->60 in clip1[36,84) -> offset 6+60-36=30; start 0; dur 24; -6dB
    #   C sfx:   F=3000ms->72 in clip1[36,84) -> offset 6+72-36=42; start 125ms->3;
    #            dur 24; absolute [72,96) extends past parent end 84 (single clip)
    expected = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<fcpxml version="1.9">\n'
        '  <resources>\n'
        '    <format id="r1" name="ManjuFormat1080x1920p24" frameDuration="1/24s" '
        'width="1080" height="1920" />\n'
        '    <asset id="r2" name="S001" src="media/gen/S001/TAKEA.mp4" start="0s" '
        'duration="48/24s" hasVideo="1" format="r1" />\n'
        '    <asset id="r3" name="S002" src="media/gen/S002/TAKEB.mp4" start="0s" '
        'duration="54/24s" hasVideo="1" format="r1" />\n'
        '    <asset id="r4" name="line.wav" src="media/voice/line.wav" start="0s" '
        'duration="24/24s" hasAudio="1" />\n'
        '    <asset id="r5" name="bed.mp3" src="media/music/bed.mp3" start="0s" '
        'duration="24/24s" hasAudio="1" />\n'
        '    <asset id="r6" name="hit.wav" src="media/sfx/hit.wav" start="0s" '
        'duration="27/24s" hasAudio="1" />\n'
        '    <effect id="r7" name="Cross Dissolve" '
        'uid="FFVideoTransitionCrossDissolve" />\n'
        '  </resources>\n'
        '  <library>\n'
        '    <event name="AUD">\n'
        '      <project name="AUD">\n'
        '        <sequence format="r1" duration="84/24s" tcStart="0s" '
        'tcFormat="NDF">\n'
        '          <spine>\n'
        '            <asset-clip ref="r2" offset="0s" name="S001" start="0s" '
        'duration="48/24s" format="r1" tcFormat="NDF">\n'
        '              <asset-clip ref="r4" lane="-1" offset="12/24s" '
        'name="line.wav" start="0s" duration="24/24s" audioRole="dialogue" />\n'
        '            </asset-clip>\n'
        '            <transition name="Cross Dissolve" offset="36/24s" '
        'duration="12/24s">\n'
        '              <filter-video ref="r7" name="Cross Dissolve" />\n'
        '            </transition>\n'
        '            <asset-clip ref="r3" offset="36/24s" name="S002" '
        'start="6/24s" duration="48/24s" format="r1" tcFormat="NDF">\n'
        '              <asset-clip ref="r5" lane="-2" offset="30/24s" '
        'name="bed.mp3" start="0s" duration="24/24s" audioRole="music">\n'
        '                <adjust-volume amount="-6dB" />\n'
        '              </asset-clip>\n'
        '              <asset-clip ref="r6" lane="-3" offset="42/24s" '
        'name="hit.wav" start="3/24s" duration="24/24s" audioRole="effects.sfx" '
        '/>\n'
        '            </asset-clip>\n'
        '          </spine>\n'
        '        </sequence>\n'
        '      </project>\n'
        '    </event>\n'
        '  </library>\n'
        '</fcpxml>\n'
    )
    assert got == expected


def test_audio_golden_parses_dtd_less_and_nests_connected_clips():
    root = ET.fromstring(compile_fcpxml(_audio_timeline(), rate=R24, name="X"))
    spine = root.find("./library/event/project/sequence/spine")
    vclips = spine.findall("asset-clip")           # top-level (video) only
    assert [c.get("ref") for c in vclips] == ["r2", "r3"]
    # connected audio clips are NESTED inside their owning spine clip
    assert [c.get("ref") for c in vclips[0].findall("asset-clip")] == ["r4"]
    assert [c.get("ref") for c in vclips[1].findall("asset-clip")] == ["r5", "r6"]


# --------------------------------------------------------------------------- #
# 2. empty-buses byte-identity vs T2's frozen three-clip golden                 #
# --------------------------------------------------------------------------- #


def _t2_three_clip_timeline():
    """A byte-for-byte reconstruction of T2's _three_clip_timeline (video only)."""
    return _tl([
        _vid("S001", "TAKEA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
        _vid("S002", "TAKEB", 2000, 2000, source_in_ms=125,
             transition=TransitionSpec(type="fade", duration_ms=300)),
        _vid("S003", "TAKEC", 4000, 1000),
    ])


# T2's frozen golden (test_fp_fcpxml.py::test_golden_three_clip_fcpxml_exact_bytes).
# The audio increment MUST leave this byte-identical when all four buses are empty.
_T2_GOLDEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<fcpxml version="1.9">\n'
    '  <resources>\n'
    '    <format id="r1" name="ManjuFormat1080x1920p24" frameDuration="1/24s" '
    'width="1080" height="1920" />\n'
    '    <asset id="r2" name="S001" src="media/gen/S001/TAKEA.mp4" start="0s" '
    'duration="48/24s" hasVideo="1" format="r1" />\n'
    '    <asset id="r3" name="S002" src="media/gen/S002/TAKEB.mp4" start="0s" '
    'duration="51/24s" hasVideo="1" format="r1" />\n'
    '    <asset id="r4" name="S003" src="media/gen/S003/TAKEC.mp4" start="0s" '
    'duration="24/24s" hasVideo="1" format="r1" />\n'
    '    <effect id="r5" name="Cross Dissolve" '
    'uid="FFVideoTransitionCrossDissolve" />\n'
    '  </resources>\n'
    '  <library>\n'
    '    <event name="DEMO">\n'
    '      <project name="DEMO">\n'
    '        <sequence format="r1" duration="108/24s" tcStart="0s" '
    'tcFormat="NDF">\n'
    '          <spine>\n'
    '            <asset-clip ref="r2" offset="0s" name="S001" start="0s" '
    'duration="48/24s" format="r1" tcFormat="NDF" />\n'
    '            <transition name="Cross Dissolve" offset="36/24s" '
    'duration="12/24s">\n'
    '              <filter-video ref="r5" name="Cross Dissolve" />\n'
    '            </transition>\n'
    '            <asset-clip ref="r3" offset="36/24s" name="S002" '
    'start="3/24s" duration="48/24s" format="r1" tcFormat="NDF" />\n'
    "            <!-- MANJU: transition 'fade' (300ms) at S002 approximated "
    'as a hard cut (no clean FCPXML cross-dissolve mapping) -->\n'
    '            <asset-clip ref="r4" offset="84/24s" name="S003" start="0s" '
    'duration="24/24s" format="r1" tcFormat="NDF" />\n'
    '          </spine>\n'
    '        </sequence>\n'
    '      </project>\n'
    '    </event>\n'
    '  </library>\n'
    '</fcpxml>\n'
)


def test_empty_buses_are_byte_identical_to_t2_video_only_golden():
    got = compile_fcpxml(_t2_three_clip_timeline(), rate=R24, name="DEMO")
    assert got == _T2_GOLDEN
    # no audio machinery leaks into a video-only document
    assert "lane=" not in got
    assert "hasAudio" not in got
    assert "audioRole" not in got
    assert "adjust-volume" not in got


# --------------------------------------------------------------------------- #
# 3. placement math — absolute position recovered (the hard case)              #
# --------------------------------------------------------------------------- #


def _spine(root):
    return root.find("./library/event/project/sequence/spine")


def _frames(s):
    """'N/24s' or '0s' → int N."""
    return 0 if s == "0s" else int(s.split("/")[0])


def test_placement_absolute_position_equals_F_for_every_connected_clip():
    root = ET.fromstring(compile_fcpxml(_audio_timeline(), rate=R24, name="X"))
    vclips = _spine(root).findall("asset-clip")
    # parent_offset + (child_offset − parent_start) MUST equal the clip's abs frame F
    expected_F = {"r4": ms_to_frames(500, R24),    # 12
                  "r5": ms_to_frames(2500, R24),   # 60
                  "r6": ms_to_frames(3000, R24)}   # 72
    for parent in vclips:
        p_off = _frames(parent.get("offset"))
        p_start = _frames(parent.get("start"))
        for child in parent.findall("asset-clip"):
            c_off = _frames(child.get("offset"))
            recovered = p_off + (c_off - p_start)
            assert recovered == expected_F[child.get("ref")], child.get("ref")


def test_connected_clip_across_dissolve_uses_pulled_back_parent_geometry():
    """B (music @2500ms) attaches to the SECOND clip, whose offset was pulled
    back 12 frames by the dissolve and whose start carries a 6-frame in-point —
    so its child.offset (30) is neither F (60) nor F−pullback; it is
    in_frames[1] + F − offsets[1] = 6 + 60 − 36 = 30."""
    root = ET.fromstring(compile_fcpxml(_audio_timeline(), rate=R24, name="X"))
    v1 = _spine(root).findall("asset-clip")[1]
    assert v1.get("offset") == "36/24s" and v1.get("start") == "6/24s"
    music = v1.find("asset-clip[@lane='-2']")
    assert music.get("offset") == "30/24s"           # 6 + 60 − 36
    # and it is a child of clip1, NOT clip0
    v0 = _spine(root).findall("asset-clip")[0]
    assert v0.find("asset-clip[@lane='-2']") is None


def test_connected_clip_may_extend_past_parent_end_as_single_clip():
    root = ET.fromstring(compile_fcpxml(_audio_timeline(), rate=R24, name="X"))
    v1 = _spine(root).findall("asset-clip")[1]
    sfx = v1.find("asset-clip[@lane='-3']")
    # abs span [72, 96); parent clip1 abs span [36, 84) → extends 12 frames past.
    p_off, p_start = _frames(v1.get("offset")), _frames(v1.get("start"))
    c_off, c_dur = _frames(sfx.get("offset")), _frames(sfx.get("duration"))
    abs_start = p_off + (c_off - p_start)
    abs_end = abs_start + c_dur
    parent_end = p_off + (_frames(v1.get("duration")))
    assert abs_end > parent_end                       # legal: not split
    # exactly ONE sfx clip on the lane (no split into two)
    assert len(_spine(root).findall(".//asset-clip[@lane='-3']")) == 1


def test_tie_in_dissolve_overlap_attaches_to_earlier_clip():
    """F=38 lands in the overlap [36,48) — inside BOTH clip0[0,48) and
    clip1[36,84). Ties break to the EARLIER clip (clip0)."""
    tl = _tl(
        [
            _vid("S001", "TAKEA", 0, 2000,
                 transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
            _vid("S002", "TAKEB", 2000, 2000, source_in_ms=250),
        ],
        voice=[_aud("media/voice/v.wav", 1600, 500)],   # 1600ms -> F=38
    )
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    v0, v1 = _spine(root).findall("asset-clip")
    assert v0.find("asset-clip[@lane='-1']") is not None    # earlier clip wins
    assert v1.find("asset-clip[@lane='-1']") is None
    # offset via clip0 geometry: in_frames[0] + 38 − offsets[0] = 0 + 38 − 0 = 38
    assert v0.find("asset-clip[@lane='-1']").get("offset") == "38/24s"


def test_clip_past_last_clip_end_attaches_to_last_clip():
    tl = _tl(
        [_vid("S001", "TAKEA", 0, 2000), _vid("S002", "TAKEB", 2000, 2000)],
        sfx=[_aud("media/sfx/late.wav", 5000, 500)],    # F=120 ≥ seq end 96
    )
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    v0, v1 = _spine(root).findall("asset-clip")
    assert v0.find("asset-clip[@lane='-3']") is None
    assert v1.find("asset-clip[@lane='-3']") is not None


# --------------------------------------------------------------------------- #
# 4. fixed lane + role maps                                                     #
# --------------------------------------------------------------------------- #


def test_lane_and_role_map_is_fixed_and_deterministic():
    tl = _tl(
        [_vid("S001", "TAKEA", 0, 4000)],
        voice=[_aud("media/a/voice.wav", 0, 1000)],
        music=[_aud("media/a/music.wav", 0, 1000)],
        sfx=[_aud("media/a/sfx.wav", 0, 1000)],
        ambient=[_aud("media/a/amb.wav", 0, 1000)],     # non-loop ambient
    )
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    parent = _spine(root).find("asset-clip")
    got = {c.get("lane"): c.get("audioRole") for c in parent.findall("asset-clip")}
    assert got == {
        "-1": "dialogue",
        "-2": "music",
        "-3": "effects.sfx",
        "-4": "effects.ambient",
    }


# --------------------------------------------------------------------------- #
# 5. gain formatting — adjust-volume only when non-zero, :g format             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("gain_db,expected", [(-6.0, "-6dB"), (-6.5, "-6.5dB"),
                                              (3.0, "3dB")])
def test_gain_becomes_adjust_volume_with_g_formatting(gain_db, expected):
    tl = _tl([_vid("S001", "TAKEA", 0, 2000)],
             voice=[_aud("media/a/v.wav", 0, 1000, gain_db=gain_db)])
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    av = _spine(root).find(".//asset-clip[@lane='-1']/adjust-volume")
    assert av is not None and av.get("amount") == expected


def test_zero_gain_emits_no_adjust_volume():
    tl = _tl([_vid("S001", "TAKEA", 0, 2000)],
             voice=[_aud("media/a/v.wav", 0, 1000, gain_db=0.0)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    assert "adjust-volume" not in got


# --------------------------------------------------------------------------- #
# 6. honest handling — loop omitted, None-duration skipped, ducking + fades    #
# --------------------------------------------------------------------------- #


def test_loop_bed_is_omitted_with_honest_note_not_written():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             ambient=[_aud("media/a/room.wav", 0, 4000, loop=True, gain_db=-24.0)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    assert root.findall(".//asset-clip[@lane='-4']") == []   # NOT written
    assert "hasAudio" not in got                              # no asset either
    assert "MANJU" in got and "loop" in got and "room.wav" in got


def test_none_duration_clip_is_skipped_with_honest_note():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             sfx=[_aud("media/a/hit.wav", 1000, duration_ms=None)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    assert root.findall(".//asset-clip[@lane='-3']") == []    # skipped
    assert "MANJU" in got and "hit.wav" in got                # honest note
    assert "duration" in got.lower()


def test_ducking_clip_is_written_but_ducking_relationship_noted():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             voice=[_aud("media/a/v.wav", 0, 1000)],
             music=[_aud("media/a/bed.mp3", 0, 4000, gain_db=-6.0, ducking=True)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    music = root.find(".//asset-clip[@lane='-2']")
    assert music is not None                                  # the clip STILL plays
    assert music.find("adjust-volume").get("amount") == "-6dB"
    # ducking relationship is a render-time sidechain — noted, never claimed as mix
    assert "MANJU" in got and "ducking" in got


def test_fades_take_gain_only_branch_no_fade_elements():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             music=[_aud("media/a/bed.mp3", 0, 4000, gain_db=-6.0,
                         fade_in_ms=250, fade_out_ms=500)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    # gain-only branch: NO fade element shapes are emitted
    for token in ("fadeIn", "fadeOut", "fade-in", "fade-out", "<fade"):
        assert token not in got, token
    # but the omission is honest (in-band note names the fade values)
    assert "MANJU" in got and "250" in got and "500" in got
    # gain still lands
    root = ET.fromstring(got)
    assert root.find(".//asset-clip[@lane='-2']/adjust-volume").get("amount") == "-6dB"


# --------------------------------------------------------------------------- #
# 7. resources — hasAudio assets, dedup + widening                             #
# --------------------------------------------------------------------------- #


def test_audio_assets_declared_hasaudio_after_video_assets():
    root = ET.fromstring(compile_fcpxml(_audio_timeline(), rate=R24, name="X"))
    assets = root.findall("./resources/asset")
    kinds = [(a.get("id"), a.get("hasVideo"), a.get("hasAudio")) for a in assets]
    assert kinds == [
        ("r2", "1", None), ("r3", "1", None),        # video first
        ("r4", None, "1"), ("r5", None, "1"), ("r6", None, "1"),  # audio after
    ]
    # audio assets carry NO video format ref
    for a in assets[2:]:
        assert a.get("format") is None


def test_shared_audio_source_is_one_asset_widened_to_longest_use():
    tl = _tl(
        [_vid("S001", "TAKEA", 0, 6000)],
        sfx=[
            _aud("media/a/hit.wav", 0, 1000, start_offset_ms=125),   # need 3+24=27
            _aud("media/a/hit.wav", 2000, 2000),                     # need 0+48=48
        ],
    )
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    audio_assets = [a for a in root.findall("./resources/asset")
                    if a.get("hasAudio") == "1"]
    assert len(audio_assets) == 1                                    # deduped
    assert audio_assets[0].get("duration") == "48/24s"              # widened to max
    refs = {c.get("ref") for c in root.findall(".//asset-clip[@lane='-3']")}
    assert refs == {audio_assets[0].get("id")}                       # both reference it


# --------------------------------------------------------------------------- #
# 8. determinism, CJK, and the no-video boundary                               #
# --------------------------------------------------------------------------- #


def test_determinism_byte_identical_with_audio():
    tl = _audio_timeline()
    assert compile_fcpxml(tl, rate=R24, name="X") == compile_fcpxml(tl, rate=R24, name="X")


def test_cjk_audio_source_names_ride_natively():
    tl = _tl([_vid("S001", "TAKEA", 0, 2000)],
             music=[_aud("media/音乐/主题曲.mp3", 0, 1000, gain_db=-6.0)])
    got = compile_fcpxml(tl, rate=R24, name="雨夜")
    root = ET.fromstring(got)                                        # valid XML
    asset = next(a for a in root.findall("./resources/asset")
                 if a.get("hasAudio") == "1")
    assert asset.get("src") == "media/音乐/主题曲.mp3"
    assert asset.get("name") == "主题曲.mp3"
    assert root.find(".//asset-clip[@lane='-2']").get("name") == "主题曲.mp3"


def test_audio_without_video_spine_is_not_attachable_and_does_not_crash():
    """No spine clip to connect to → audio is honestly not emitted (no parent),
    and the document stays valid (the connected-clip model needs a spine host)."""
    tl = _tl([], music=[_aud("media/a/bed.mp3", 0, 1000, gain_db=-6.0)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    assert root.findall(".//spine/asset-clip") == []
    assert "lane=" not in got
