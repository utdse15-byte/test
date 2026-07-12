"""FP Loop Y3 — FCPXML REAL audio fades (DTD-sourced native fade elements).

Red-first (§20): this file pins the fade extension of ``compile_fcpxml`` from
FIRST PRINCIPLES — the DTD containment chain, the linear-curve/parity ruling, the
exact ms→frame→rational duration math, the over-long clamp, and the loop-pass
no-fade ruling — all derived inline so the test encodes the SPEC.

The DTD source (Apple archived FCPXML v1.7 DTD, quoted verbatim in the loop
addendum):

    <!ELEMENT adjust-volume (param*)>
    <!ELEMENT param (fadeIn?, fadeOut?, keyframeAnimation?, param*)>
    <!ATTLIST param name CDATA #REQUIRED>
    <!ELEMENT fadeIn EMPTY>
    <!ATTLIST fadeIn type %fadeType; #IMPLIED>       (%fadeType; = linear|easeIn|easeOut|easeInOut)
    <!ATTLIST fadeIn duration %time; #REQUIRED>
    (fadeOut identical)

So a WRITTEN connected clip's fades ride:

    <asset-clip ...>
      <adjust-volume amount="{gain:g}dB">
        <param name="amount">
          <fadeIn  type="linear" duration="{frames}/{num}s"/>
          <fadeOut type="linear" duration="{frames}/{num}s"/>
        </param>
      </adjust-volume>
    </asset-clip>

``type="linear"`` is DELIBERATE (render afade default curve parity). The param
name string ``"amount"`` is a CONVENTION — the DTD requires a name ATTRIBUTE but
does NOT mandate that string.

It NEVER edits T2's ``test_fp_fcpxml.py``, V1's ``test_fp_fcpxml_audio.py`` or
W1's ``test_fp_fcpxml_loops.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from manju.core.models import (
    AudioClip,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)
from manju.core.timebase import Rate, Rounding, ms_to_frames
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
        meta=TimelineMeta(compiled_from="fp-fcpxml-fades"),
        fps=fps, width=width, height=height,
        duration_ms=sum(c.duration_ms for c in video),
        tracks=TimelineTracks(
            video=list(video), voice=list(voice), music=list(music),
            sfx=list(sfx), ambient=list(ambient),
        ),
    )


def _spine(root):
    return root.find("./library/event/project/sequence/spine")


def _frames(s: str) -> int:
    """'N/24s' or '0s' -> int N."""
    return 0 if s == "0s" else int(s.split("/")[0])


def _fade_timeline():
    """One 4000ms (96-frame) video clip hosting a music clip @0ms dur 2000ms
    (48 frames), gain -6dB, fade_in 250ms (->6 frames), fade_out 500ms
    (->12 frames)."""
    return _tl(
        [_vid("S001", "TAKEA", 0, 4000)],
        music=[_aud("media/music/bed.mp3", 0, 2000, gain_db=-6.0,
                    fade_in_ms=250, fade_out_ms=500)],
    )


# --------------------------------------------------------------------------- #
# 1. THE hand-computed byte golden — real fades, whole document                #
# --------------------------------------------------------------------------- #


# Frame math @24 (frameDuration "1/24s"; F frames == "F/24s"):
#   video frames=[96]; audio dur 2000ms->48; fade_in 250ms->6, fade_out 500ms->12
#   video asset r2 (avail 96); audio asset r3 (avail 0+48=48); no dissolve/effect
#   music F=0 in clip0[0,96) -> offset 0; start 0; dur 48; gain -6dB
_FADE_GOLDEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<fcpxml version="1.9">\n'
    '  <resources>\n'
    '    <format id="r1" name="ManjuFormat1080x1920p24" frameDuration="1/24s" '
    'width="1080" height="1920" />\n'
    '    <asset id="r2" name="S001" src="media/gen/S001/TAKEA.mp4" start="0s" '
    'duration="96/24s" hasVideo="1" format="r1" />\n'
    '    <asset id="r3" name="bed.mp3" src="media/music/bed.mp3" start="0s" '
    'duration="48/24s" hasAudio="1" />\n'
    '  </resources>\n'
    '  <library>\n'
    '    <event name="FADE">\n'
    '      <project name="FADE">\n'
    '        <sequence format="r1" duration="96/24s" tcStart="0s" '
    'tcFormat="NDF">\n'
    '          <spine>\n'
    '            <asset-clip ref="r2" offset="0s" name="S001" start="0s" '
    'duration="96/24s" format="r1" tcFormat="NDF">\n'
    '              <asset-clip ref="r3" lane="-2" offset="0s" name="bed.mp3" '
    'start="0s" duration="48/24s" audioRole="music">\n'
    '                <adjust-volume amount="-6dB">\n'
    '                  <param name="amount">\n'
    '                    <fadeIn type="linear" duration="6/24s" />\n'
    '                    <fadeOut type="linear" duration="12/24s" />\n'
    '                  </param>\n'
    '                </adjust-volume>\n'
    '              </asset-clip>\n'
    '            </asset-clip>\n'
    '          </spine>\n'
    '        </sequence>\n'
    '      </project>\n'
    '    </event>\n'
    '  </library>\n'
    '</fcpxml>\n'
)


def test_golden_real_fades_exact_bytes():
    got = compile_fcpxml(_fade_timeline(), rate=R24, name="FADE")
    assert got == _FADE_GOLDEN
    # the gain-only V1 MANJU fade note is GONE — the fade is now expressed
    assert "MANJU" not in got
    assert "gain-only" not in got


# --------------------------------------------------------------------------- #
# 2. DTD containment chain — asset-clip>adjust-volume>param>fadeIn/fadeOut      #
# --------------------------------------------------------------------------- #


def test_fade_containment_chain_matches_the_dtd():
    root = ET.fromstring(compile_fcpxml(_fade_timeline(), rate=R24, name="X"))
    clip = _spine(root).find("asset-clip").find("asset-clip[@lane='-2']")
    av = clip.find("adjust-volume")
    assert av is not None and av.get("amount") == "-6dB"
    param = av.find("param")
    assert param is not None                                  # param is the container
    # param name is a CONVENTION ("amount"); the DTD requires only a name attr
    assert param.get("name") == "amount"
    fi = param.find("fadeIn")
    fo = param.find("fadeOut")
    assert fi is not None and fo is not None
    # fadeIn/fadeOut are EMPTY elements (no children, no text)
    assert list(fi) == [] and (fi.text is None or fi.text.strip() == "")
    assert list(fo) == [] and (fo.text is None or fo.text.strip() == "")
    # the chain nowhere else: fadeIn/fadeOut live ONLY under param
    assert root.findall(".//adjust-volume/fadeIn") == []     # not a direct child


def test_fade_type_is_linear_render_parity():
    root = ET.fromstring(compile_fcpxml(_fade_timeline(), rate=R24, name="X"))
    param = root.find(".//adjust-volume/param")
    assert param.find("fadeIn").get("type") == "linear"
    assert param.find("fadeOut").get("type") == "linear"


def test_fade_durations_are_exact_rational_frames():
    """250ms->6 frames, 500ms->12 frames at 24fps (ms->frames ROUND_HALF_UP)."""
    assert ms_to_frames(250, R24, Rounding.ROUND_HALF_UP) == 6
    assert ms_to_frames(500, R24, Rounding.ROUND_HALF_UP) == 12
    root = ET.fromstring(compile_fcpxml(_fade_timeline(), rate=R24, name="X"))
    param = root.find(".//adjust-volume/param")
    assert param.find("fadeIn").get("duration") == "6/24s"
    assert param.find("fadeOut").get("duration") == "12/24s"


# --------------------------------------------------------------------------- #
# 3. zero-fade byte identity — drop-when-absent, unchanged V1 shape            #
# --------------------------------------------------------------------------- #


def _no_fade_timeline():
    return _tl(
        [_vid("S001", "TAKEA", 0, 4000)],
        music=[_aud("media/music/bed.mp3", 0, 2000, gain_db=-6.0)],
    )


# Byte-frozen: a zero-fade clip carries a bare gain <adjust-volume /> exactly as
# V1 wrote it — NO param/fadeIn/fadeOut leak. This is _FADE_GOLDEN with the fade
# container collapsed back to the self-closing gain element.
_NO_FADE_GOLDEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<fcpxml version="1.9">\n'
    '  <resources>\n'
    '    <format id="r1" name="ManjuFormat1080x1920p24" frameDuration="1/24s" '
    'width="1080" height="1920" />\n'
    '    <asset id="r2" name="S001" src="media/gen/S001/TAKEA.mp4" start="0s" '
    'duration="96/24s" hasVideo="1" format="r1" />\n'
    '    <asset id="r3" name="bed.mp3" src="media/music/bed.mp3" start="0s" '
    'duration="48/24s" hasAudio="1" />\n'
    '  </resources>\n'
    '  <library>\n'
    '    <event name="FADE">\n'
    '      <project name="FADE">\n'
    '        <sequence format="r1" duration="96/24s" tcStart="0s" '
    'tcFormat="NDF">\n'
    '          <spine>\n'
    '            <asset-clip ref="r2" offset="0s" name="S001" start="0s" '
    'duration="96/24s" format="r1" tcFormat="NDF">\n'
    '              <asset-clip ref="r3" lane="-2" offset="0s" name="bed.mp3" '
    'start="0s" duration="48/24s" audioRole="music">\n'
    '                <adjust-volume amount="-6dB" />\n'
    '              </asset-clip>\n'
    '            </asset-clip>\n'
    '          </spine>\n'
    '        </sequence>\n'
    '      </project>\n'
    '    </event>\n'
    '  </library>\n'
    '</fcpxml>\n'
)


def test_zero_fade_clip_is_byte_identical_drop_when_absent():
    got = compile_fcpxml(_no_fade_timeline(), rate=R24, name="FADE")
    assert got == _NO_FADE_GOLDEN
    for token in ("fadeIn", "fadeOut", "<param", "<fade"):
        assert token not in got, token


def test_zero_fade_and_fade_differ_only_in_the_volume_container():
    """The two documents are identical byte-for-byte except the adjust-volume
    subtree — the fade change touches NOTHING else."""
    no_fade = compile_fcpxml(_no_fade_timeline(), rate=R24, name="FADE")
    fade = compile_fcpxml(_fade_timeline(), rate=R24, name="FADE")
    bare = '                <adjust-volume amount="-6dB" />\n'
    container = (
        '                <adjust-volume amount="-6dB">\n'
        '                  <param name="amount">\n'
        '                    <fadeIn type="linear" duration="6/24s" />\n'
        '                    <fadeOut type="linear" duration="12/24s" />\n'
        '                  </param>\n'
        '                </adjust-volume>\n'
    )
    assert no_fade.replace(bare, container) == fade


# --------------------------------------------------------------------------- #
# 4. gain-zero + fades — adjust-volume becomes the REQUIRED fade container      #
# --------------------------------------------------------------------------- #


def test_zero_gain_with_fades_still_emits_0db_container():
    """DTD: fadeIn/fadeOut can only ride a param under adjust-volume, so a fade
    on a ZERO-gain clip MUST still open the container — amount defaults to '0dB'
    (the DTD's own default; harmless)."""
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             voice=[_aud("media/a/v.wav", 0, 1000, gain_db=0.0,
                         fade_in_ms=125, fade_out_ms=125)])
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    av = root.find(".//asset-clip[@lane='-1']/adjust-volume")
    assert av is not None and av.get("amount") == "0dB"
    assert av.find("param/fadeIn").get("duration") == "3/24s"   # 125ms -> 3
    assert av.find("param/fadeOut").get("duration") == "3/24s"


def test_zero_gain_zero_fades_still_emits_no_adjust_volume():
    """The drop-when-absent rule is untouched: no gain AND no fades = no element."""
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             voice=[_aud("media/a/v.wav", 0, 1000, gain_db=0.0)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    assert "adjust-volume" not in got


def test_only_fade_in_emits_only_fadein():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             music=[_aud("media/a/bed.mp3", 0, 2000, gain_db=-6.0,
                         fade_in_ms=250, fade_out_ms=0)])
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    param = root.find(".//adjust-volume/param")
    assert param is not None
    assert param.find("fadeIn") is not None
    assert param.find("fadeOut") is None


def test_only_fade_out_emits_only_fadeout():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             music=[_aud("media/a/bed.mp3", 0, 2000, gain_db=-6.0,
                         fade_in_ms=0, fade_out_ms=500)])
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    param = root.find(".//adjust-volume/param")
    assert param is not None
    assert param.find("fadeIn") is None
    assert param.find("fadeOut") is not None


# --------------------------------------------------------------------------- #
# 5. over-long fade clamps to the clip length + honest in-band note            #
# --------------------------------------------------------------------------- #


def test_over_long_fade_clamps_to_clip_length_with_note():
    """A fade longer than the clip would be an INVALID over-long fade — it clamps
    to the clip's whole-frame length and records an in-band MANJU note."""
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             music=[_aud("media/a/bed.mp3", 0, 1000, gain_db=0.0,   # 1000ms -> 24f
                         fade_in_ms=2000, fade_out_ms=0)])           # 2000ms -> 48f > 24
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    fi = root.find(".//adjust-volume/param/fadeIn")
    assert fi is not None
    assert fi.get("duration") == "24/24s"        # clamped to the 24-frame clip
    # the clamp is honest, not silent
    assert "MANJU" in got
    assert "clamp" in got.lower()
    assert "2000" in got                          # the original over-long ms named


def test_fade_exactly_clip_length_is_not_clamped_no_note():
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             music=[_aud("media/a/bed.mp3", 0, 1000, gain_db=-6.0,   # 24 frames
                         fade_in_ms=1000, fade_out_ms=0)])           # 1000ms -> 24f == clip
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    assert root.find(".//adjust-volume/param/fadeIn").get("duration") == "24/24s"
    assert "clamp" not in got.lower()             # equal, not over-long


# --------------------------------------------------------------------------- #
# 6. loop passes carry NO fades (whole-source repeats)                         #
# --------------------------------------------------------------------------- #


def test_materialized_loop_passes_carry_no_fade_elements():
    """A loop bed materializes into whole-source passes; each pass repeats the
    WHOLE source, so a fade handle would be wrong on a per-pass basis — the
    passes carry NO fadeIn/fadeOut. The un-expressed fade rides an honest note."""
    tl = _tl(
        [_vid("S001", "TAKEA", 0, 5000)],
        ambient=[_aud("media/a/room.wav", 0, 5000, loop=True, gain_db=-24.0,
                      fade_in_ms=500, fade_out_ms=500)],
    )
    got = compile_fcpxml(tl, rate=R24, name="X",
                         loop_lengths={"media/a/room.wav": 1900})
    root = ET.fromstring(got)
    passes = _spine(root).find("asset-clip").findall("asset-clip[@lane='-4']")
    assert len(passes) == 3                        # materialized (render parity)
    for p in passes:
        assert p.find(".//fadeIn") is None
        assert p.find(".//fadeOut") is None
        # gain still lands on every pass (V1/W1 behaviour unchanged)
        assert p.find("adjust-volume").get("amount") == "-24dB"
    assert "fadeIn" not in got and "fadeOut" not in got
    # the un-expressed fade is stated honestly, not silently dropped
    assert "MANJU" in got and "fade" in got.lower()


# --------------------------------------------------------------------------- #
# 7. determinism                                                               #
# --------------------------------------------------------------------------- #


def test_determinism_byte_identical_with_fades():
    tl = _fade_timeline()
    assert compile_fcpxml(tl, rate=R24, name="X") == compile_fcpxml(tl, rate=R24, name="X")


@pytest.mark.parametrize("fi_ms,fo_ms,fi_f,fo_f", [
    (250, 500, 6, 12), (125, 125, 3, 3), (1000, 0, 24, 0), (0, 2000, 0, 48),
])
def test_fade_frame_math_many_shapes(fi_ms, fo_ms, fi_f, fo_f):
    tl = _tl([_vid("S001", "TAKEA", 0, 8000)],
             music=[_aud("media/a/bed.mp3", 0, 4000, gain_db=-6.0,
                         fade_in_ms=fi_ms, fade_out_ms=fo_ms)])
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    param = root.find(".//adjust-volume/param")
    fi = param.find("fadeIn")
    fo = param.find("fadeOut")
    assert (0 if fi is None else _frames(fi.get("duration"))) == fi_f
    assert (0 if fo is None else _frames(fo.get("duration"))) == fo_f
