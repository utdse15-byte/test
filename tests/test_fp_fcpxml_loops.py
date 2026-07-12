"""FP Loop W1 — FCPXML loop-bed MATERIALIZATION (render-parity, pure/impure split).

Red-first (§20): this file pins the loop-bed extension of ``compile_fcpxml`` from
FIRST PRINCIPLES — the CUMULATIVE-boundary pass math, the whole-source-repeat
placement (render parity with ``media/render.py``'s ``-stream_loop -1`` + atrim),
the pure/impure split (``compile_fcpxml`` gains ``loop_lengths``; ``export_fcpxml``
probes), and the honest omission fallback when no probed length is available.

The purity ruling (addendum, BINDING):
  * ``compile_fcpxml`` STAYS PURE — it gains ``loop_lengths: dict[str, int] | None``
    (source → probed natural ms). ``None`` (default) is today's behaviour
    BYTE-IDENTICAL: loop clips are omitted with the honest in-band note.
  * A loop clip whose source has a positive entry MATERIALIZES: pass boundaries
    ``b_k = ms_to_frames(min(k·N_ms, D_ms), ROUND_HALF_UP)`` for k=0..K
    (K = ceil(D_ms/N_ms)); pass k occupies frames ``[F+b_k, F+b_{k+1})`` with
    source start 0 and duration ``b_{k+1}−b_k``. The telescoping sum equals the
    clip's total frames EXACTLY (no per-pass rounding drift). Each pass is a
    connected clip through the SAME parent-selection + offset helpers as V1; the
    first pass carries the materialization note.
  * ``export_fcpxml`` (IO) probes loop sources via the EXISTING media probe
    machinery (``media.probe.probe_duration_ms``); a probe failure / zero / None
    duration leaves the source OUT of the dict → the pure omission path (never a
    fabricated length).

HAND-COMPUTED GOLDEN (addendum): D=5000ms N=1900ms @24fps
  K = ceil(5000/1900) = 3
  b_0 = ms_to_frames(min(0,5000))    = ms_to_frames(0)    = 0
  b_1 = ms_to_frames(min(1900,5000)) = ms_to_frames(1900) = 45.6 -> HALF_UP -> 46
  b_2 = ms_to_frames(min(3800,5000)) = ms_to_frames(3800) = 91.2 -> HALF_UP -> 91
  b_3 = ms_to_frames(min(5700,5000)) = ms_to_frames(5000) = 120.0          -> 120
  boundaries 0/46/91/120 -> passes 46 + 45 + 29 = 120 EXACTLY (= clip frames).

It NEVER edits T2's ``test_fp_fcpxml.py`` or V1's ``test_fp_fcpxml_audio.py``
(both stay green, untouched) and proves the None-path byte-identity against a
frozen omit-path golden.
"""

from __future__ import annotations

import math
import shutil
import subprocess
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
from manju.core.spec import compute_spec_hash
from manju.core.timebase import Rate, Rounding, ms_to_frames
from manju.exporters.fcpxml import compile_fcpxml, export_fcpxml

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
        meta=TimelineMeta(compiled_from="fp-fcpxml-loops"),
        fps=fps, width=width, height=height,
        duration_ms=sum(c.duration_ms for c in video),
        tracks=TimelineTracks(
            video=list(video), voice=list(voice), music=list(music),
            sfx=list(sfx), ambient=list(ambient),
        ),
    )


def _boundaries(d_ms: int, n_ms: int, rate: Rate = R24) -> list[int]:
    """The cumulative pass boundaries the spec derives, computed independently
    from the exporter so the test encodes the math, not the implementation."""
    k = math.ceil(d_ms / n_ms)
    return [ms_to_frames(min(i * n_ms, d_ms), rate, Rounding.ROUND_HALF_UP)
            for i in range(k + 1)]


def _spine(root):
    return root.find("./library/event/project/sequence/spine")


def _frames(s: str) -> int:
    """'N/24s' or '0s' -> int N."""
    return 0 if s == "0s" else int(s.split("/")[0])


# --------------------------------------------------------------------------- #
# 1. THE hand-computed byte golden — D=5000ms N=1900ms @24 (whole document)     #
# --------------------------------------------------------------------------- #


def _golden_loop_timeline():
    """One 5000ms (120-frame) video clip hosting a 5000ms ambient loop of a
    1900ms-natural source (gain 0 for a clean minimal golden)."""
    return _tl(
        [_vid("S001", "TAKEA", 0, 5000)],
        ambient=[_aud("media/a/room.wav", 0, 5000, loop=True)],
    )


# Hand-derived from the boundary math above (0/46/91/120 -> 46/45/29):
_LOOP_GOLDEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<fcpxml version="1.9">\n'
    '  <resources>\n'
    '    <format id="r1" name="ManjuFormat1080x1920p24" frameDuration="1/24s" '
    'width="1080" height="1920" />\n'
    '    <asset id="r2" name="S001" src="media/gen/S001/TAKEA.mp4" start="0s" '
    'duration="120/24s" hasVideo="1" format="r1" />\n'
    '    <asset id="r3" name="room.wav" src="media/a/room.wav" start="0s" '
    'duration="46/24s" hasAudio="1" />\n'
    '  </resources>\n'
    '  <library>\n'
    '    <event name="LOOP">\n'
    '      <project name="LOOP">\n'
    '        <sequence format="r1" duration="120/24s" tcStart="0s" '
    'tcFormat="NDF">\n'
    '          <spine>\n'
    '            <asset-clip ref="r2" offset="0s" name="S001" start="0s" '
    'duration="120/24s" format="r1" tcFormat="NDF">\n'
    '              <asset-clip ref="r3" lane="-4" offset="0s" name="room.wav" '
    'start="0s" duration="46/24s" audioRole="effects.ambient" />\n'
    "              <!-- MANJU: audio 'room.wav' (ambient) materialized loop "
    '(3 passes, natural 1900ms, render parity: -stream_loop) -->\n'
    '              <asset-clip ref="r3" lane="-4" offset="46/24s" '
    'name="room.wav" start="0s" duration="45/24s" audioRole="effects.ambient" '
    '/>\n'
    '              <asset-clip ref="r3" lane="-4" offset="91/24s" '
    'name="room.wav" start="0s" duration="29/24s" audioRole="effects.ambient" '
    '/>\n'
    '            </asset-clip>\n'
    '          </spine>\n'
    '        </sequence>\n'
    '      </project>\n'
    '    </event>\n'
    '  </library>\n'
    '</fcpxml>\n'
)


def test_golden_materialized_loop_exact_bytes():
    got = compile_fcpxml(
        _golden_loop_timeline(), rate=R24, name="LOOP",
        loop_lengths={"media/a/room.wav": 1900},
    )
    assert got == _LOOP_GOLDEN


def test_golden_boundaries_and_passes_are_the_hand_computed_values():
    """Independently re-derive the spec math and assert the emitted durations."""
    assert _boundaries(5000, 1900) == [0, 46, 91, 120]
    got = compile_fcpxml(
        _golden_loop_timeline(), rate=R24, name="LOOP",
        loop_lengths={"media/a/room.wav": 1900},
    )
    root = ET.fromstring(got)
    passes = _spine(root).find("asset-clip").findall("asset-clip[@lane='-4']")
    offsets = [_frames(p.get("offset")) for p in passes]
    durs = [_frames(p.get("duration")) for p in passes]
    assert offsets == [0, 46, 91]          # cumulative boundaries F+b_k, F=0
    assert durs == [46, 45, 29]            # b_{k+1} - b_k
    assert sum(durs) == 120                # telescopes to the clip total EXACTLY
    # every pass repeats the WHOLE source from 0 (render parity: -stream_loop)
    assert all(p.get("start") == "0s" for p in passes)


def test_materialization_note_on_first_pass_only():
    got = compile_fcpxml(
        _golden_loop_timeline(), rate=R24, name="LOOP",
        loop_lengths={"media/a/room.wav": 1900},
    )
    assert got.count("materialized loop") == 1          # first pass only
    assert "3 passes" in got and "natural 1900ms" in got
    assert "render parity: -stream_loop" in got


def test_tail_pass_is_shorter_than_a_full_pass():
    got = compile_fcpxml(
        _golden_loop_timeline(), rate=R24, name="LOOP",
        loop_lengths={"media/a/room.wav": 1900},
    )
    root = ET.fromstring(got)
    passes = _spine(root).find("asset-clip").findall("asset-clip[@lane='-4']")
    full = ms_to_frames(1900, R24)                       # 46
    assert _frames(passes[0].get("duration")) == full
    assert _frames(passes[-1].get("duration")) < full    # trimmed tail (29 < 46)


def test_audio_asset_available_range_is_the_natural_source_length():
    """The shared <asset> duration widens to the longest pass = the natural
    source length in frames (ms_to_frames(N_ms)), self-consistent like V1."""
    root = ET.fromstring(compile_fcpxml(
        _golden_loop_timeline(), rate=R24, name="LOOP",
        loop_lengths={"media/a/room.wav": 1900}))
    asset = next(a for a in root.findall("./resources/asset")
                 if a.get("hasAudio") == "1")
    assert asset.get("duration") == "46/24s"             # = ms_to_frames(1900)


def test_gain_lands_on_every_materialized_pass():
    tl = _tl(
        [_vid("S001", "TAKEA", 0, 5000)],
        ambient=[_aud("media/a/room.wav", 0, 5000, loop=True, gain_db=-24.0)],
    )
    root = ET.fromstring(compile_fcpxml(
        tl, rate=R24, name="X", loop_lengths={"media/a/room.wav": 1900}))
    passes = _spine(root).find("asset-clip").findall("asset-clip[@lane='-4']")
    assert len(passes) == 3
    for p in passes:
        av = p.find("adjust-volume")
        assert av is not None and av.get("amount") == "-24dB"


# --------------------------------------------------------------------------- #
# 2. None-path byte identity (the PURE default is today's behaviour)           #
# --------------------------------------------------------------------------- #


def _none_path_loop_timeline():
    return _tl(
        [_vid("S001", "TAKEA", 0, 4000)],
        ambient=[_aud("media/a/room.wav", 0, 4000, loop=True, gain_db=-24.0)],
    )


# Frozen from the PRE-CHANGE exporter (captured before implementation): the
# default/None path MUST reproduce this byte-for-byte — the loop bed omitted
# with the verbatim honest note, no audio asset, no lane.
_NONE_PATH_GOLDEN = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<fcpxml version="1.9">\n'
    '  <resources>\n'
    '    <format id="r1" name="ManjuFormat1080x1920p24" frameDuration="1/24s" '
    'width="1080" height="1920" />\n'
    '    <asset id="r2" name="S001" src="media/gen/S001/TAKEA.mp4" start="0s" '
    'duration="96/24s" hasVideo="1" format="r1" />\n'
    '  </resources>\n'
    '  <library>\n'
    '    <event name="X">\n'
    '      <project name="X">\n'
    '        <sequence format="r1" duration="96/24s" tcStart="0s" '
    'tcFormat="NDF">\n'
    '          <spine>\n'
    '            <asset-clip ref="r2" offset="0s" name="S001" start="0s" '
    'duration="96/24s" format="r1" tcFormat="NDF">\n'
    "              <!-- MANJU: audio 'room.wav' (ambient) is a loop bed "
    "(fill-to-duration) — a single pass would be wrong audio; omitted this "
    'increment (a future loop may materialize repeats) -->\n'
    '            </asset-clip>\n'
    '          </spine>\n'
    '        </sequence>\n'
    '      </project>\n'
    '    </event>\n'
    '  </library>\n'
    '</fcpxml>\n'
)


def test_none_default_is_byte_identical_to_frozen_omit_golden():
    got = compile_fcpxml(_none_path_loop_timeline(), rate=R24, name="X")
    assert got == _NONE_PATH_GOLDEN
    assert "lane=" not in got and "hasAudio" not in got   # nothing materialized


def test_explicit_none_equals_default_and_omits():
    tl = _none_path_loop_timeline()
    default = compile_fcpxml(tl, rate=R24, name="X")
    explicit = compile_fcpxml(tl, rate=R24, name="X", loop_lengths=None)
    assert default == explicit == _NONE_PATH_GOLDEN


def test_empty_loop_lengths_dict_omits_exactly_like_none():
    """A source absent from a PROVIDED dict (probe failure / zero duration) takes
    the SAME honest omission path — never a fabricated length."""
    tl = _none_path_loop_timeline()
    assert compile_fcpxml(tl, rate=R24, name="X", loop_lengths={}) == _NONE_PATH_GOLDEN
    # a dict that only carries an UNRELATED source is equally an omission
    other = compile_fcpxml(tl, rate=R24, name="X",
                           loop_lengths={"media/a/other.wav": 1000})
    assert other == _NONE_PATH_GOLDEN


def test_non_loop_audio_is_unaffected_by_loop_lengths():
    """A non-loop clip is written the same whether or not loop_lengths is given —
    loop_lengths gates ONLY loop beds."""
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             voice=[_aud("media/a/v.wav", 0, 1000, gain_db=-6.0)])
    a = compile_fcpxml(tl, rate=R24, name="X")
    b = compile_fcpxml(tl, rate=R24, name="X", loop_lengths={"media/a/v.wav": 500})
    assert a == b                                          # v.wav is not a loop


# --------------------------------------------------------------------------- #
# 3. pass parenting across a dissolve boundary                                 #
# --------------------------------------------------------------------------- #


def test_passes_parent_across_a_dissolve_boundary():
    """N=1000ms D=3000ms @24, loop @0ms, over two clips with a 500ms dissolve.
      clip0 [0,48) (xfade 500ms->12), clip1 pulled back to [36,84).
      boundaries 0/24/48/72 -> passes [0,24)[24,48)[48,72), all 24 frames.
      pass0,pass1 land in clip0 (f<48); pass2 lands at f=48 -> clip1 (tie/end
      rule: 48 is the exclusive end of clip0, inside clip1). child offsets via
      the SAME formula in_frames[i]+abs-offsets[i]: 0, 24, and 0+48-36=12."""
    assert _boundaries(3000, 1000) == [0, 24, 48, 72]
    tl = _tl(
        [
            _vid("S001", "TAKEA", 0, 2000,
                 transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
            _vid("S002", "TAKEB", 2000, 2000),
        ],
        ambient=[_aud("media/a/room.wav", 0, 3000, loop=True)],
    )
    got = compile_fcpxml(tl, rate=R24, name="X", loop_lengths={"media/a/room.wav": 1000})
    root = ET.fromstring(got)
    v0, v1 = _spine(root).findall("asset-clip")
    p0 = v0.findall("asset-clip[@lane='-4']")
    p1 = v1.findall("asset-clip[@lane='-4']")
    assert [_frames(p.get("offset")) for p in p0] == [0, 24]     # clip0 hosts 2
    assert [_frames(p.get("offset")) for p in p1] == [12]        # clip1 hosts 1
    # absolute position recovered through each parent's geometry == F+b_k
    for parent in (v0, v1):
        p_off, p_start = _frames(parent.get("offset")), _frames(parent.get("start"))
        for child in parent.findall("asset-clip[@lane='-4']"):
            recovered = p_off + (_frames(child.get("offset")) - p_start)
            assert recovered in (0, 24, 48)
    # note rides the FIRST pass's parent (clip0) only — it sits inside clip0's
    # block, BEFORE the <transition> that precedes clip1 (comments are dropped by
    # ET.fromstring, so assert on the raw document text).
    assert got.count("materialized loop") == 1
    assert got.index("materialized loop") < got.index("<transition")
    assert "3 passes" in got and "natural 1000ms" in got


# --------------------------------------------------------------------------- #
# 4. probe-failure omission + determinism                                      #
# --------------------------------------------------------------------------- #


def test_zero_or_negative_probed_length_is_omitted_not_materialized():
    tl = _none_path_loop_timeline()
    for bad in (0, -5):
        got = compile_fcpxml(tl, rate=R24, name="X",
                             loop_lengths={"media/a/room.wav": bad})
        assert got == _NONE_PATH_GOLDEN                   # honest omission


def test_loop_with_none_duration_cannot_materialize_and_is_omitted():
    """A loop bed with no resolvable duration_ms has no fill target, so even a
    valid probed natural length cannot materialize it — honest omission."""
    tl = _tl([_vid("S001", "TAKEA", 0, 4000)],
             ambient=[_aud("media/a/room.wav", 0, duration_ms=None, loop=True)])
    got = compile_fcpxml(tl, rate=R24, name="X",
                         loop_lengths={"media/a/room.wav": 1900})
    root = ET.fromstring(got)
    assert root.findall(".//asset-clip[@lane='-4']") == []
    assert "MANJU" in got and "room.wav" in got


def test_determinism_byte_identical_materialized():
    tl = _golden_loop_timeline()
    a = compile_fcpxml(tl, rate=R24, name="LOOP", loop_lengths={"media/a/room.wav": 1900})
    b = compile_fcpxml(tl, rate=R24, name="LOOP", loop_lengths={"media/a/room.wav": 1900})
    assert a == b


@pytest.mark.parametrize("d_ms,n_ms", [
    (5000, 1900), (3000, 1000), (10000, 3333), (2000, 700), (1500, 1500),
    (4321, 617), (2002, 1001),
])
def test_telescoping_sum_equals_clip_total_for_many_shapes(d_ms, n_ms):
    """The cumulative-boundary property: the pass durations sum EXACTLY to the
    clip's whole-frame total for every shape (no per-pass rounding drift)."""
    tl = _tl([_vid("S001", "TAKEA", 0, d_ms)],
             ambient=[_aud("media/a/room.wav", 0, d_ms, loop=True)])
    root = ET.fromstring(compile_fcpxml(
        tl, rate=R24, name="X", loop_lengths={"media/a/room.wav": n_ms}))
    passes = _spine(root).find("asset-clip").findall("asset-clip[@lane='-4']")
    total = sum(_frames(p.get("duration")) for p in passes)
    assert total == ms_to_frames(d_ms, R24)
    # offsets are strictly increasing cumulative boundaries
    offs = [_frames(p.get("offset")) for p in passes]
    assert offs == sorted(offs) and len(set(offs)) == len(offs)


# --------------------------------------------------------------------------- #
# 5. real ffprobe e2e through export_fcpxml (the IO/probe layer)               #
# --------------------------------------------------------------------------- #


def _register_video(project, add_shot, make_take, shot_id):
    shot = add_shot(project, shot_id)
    take = make_take(project, shot_id, compute_spec_hash(shot, project.load_bible()))
    return take.name, f"media/gen/{shot_id}/{take.name}.mp4"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe required for the real-probe e2e")
def test_export_fcpxml_probes_real_wav_and_materializes(tmp_project, add_shot, make_take):
    """End-to-end: export_fcpxml PROBES a real generated wav via the existing
    media probe machinery, builds loop_lengths, and materializes whole passes +
    a trimmed tail — the impure IO layer feeding the pure compiler."""
    t1, s1 = _register_video(tmp_project, add_shot, make_take, "S001")

    # a real 1.9s mono wav INSIDE the project (containment-safe project-relative src)
    wav_abs = tmp_project.root / "media" / "audio" / "room.wav"
    wav_abs.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.9",
         "-ac", "1", str(wav_abs)],
        check=True, capture_output=True,
    )
    from manju.media.probe import probe_duration_ms
    natural_ms = probe_duration_ms(wav_abs)
    assert natural_ms is not None and 1800 <= natural_ms <= 2000   # ~1900ms

    tl = _tl(
        [VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=5000)],
        ambient=[_aud("media/audio/room.wav", 0, 5000, loop=True, gain_db=-24.0)],
    )
    out = export_fcpxml(tmp_project, tl)
    text = out.read_text(encoding="utf-8")
    root = ET.fromstring(text)

    passes = _spine(root).find("asset-clip").findall("asset-clip[@lane='-4']")
    # K = ceil(5000/natural_ms) whole+tail passes, telescoping to the clip total
    expected_k = math.ceil(5000 / natural_ms)
    assert len(passes) == expected_k
    assert sum(_frames(p.get("duration")) for p in passes) == ms_to_frames(5000, R24)
    assert all(p.get("start") == "0s" for p in passes)           # whole-source repeats
    assert "materialized loop" in text and "render parity: -stream_loop" in text
    # the probed asset available range equals the natural source length in frames
    asset = next(a for a in root.findall("./resources/asset")
                 if a.get("hasAudio") == "1")
    assert _frames(asset.get("duration")) == ms_to_frames(natural_ms, R24)
    # deterministic re-export
    assert export_fcpxml(tmp_project, tl).read_text(encoding="utf-8") == text


@pytest.mark.skipif(shutil.which("ffprobe") is None,
                    reason="ffprobe required")
def test_export_fcpxml_missing_loop_source_omits_not_crashes(tmp_project, add_shot, make_take):
    """A loop source that cannot be probed (missing file) leaves the source OUT
    of loop_lengths -> the honest omission path; export never crashes."""
    t1, s1 = _register_video(tmp_project, add_shot, make_take, "S001")
    tl = _tl(
        [VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=4000)],
        ambient=[_aud("media/audio/nonexistent.wav", 0, 4000, loop=True)],
    )
    out = export_fcpxml(tmp_project, tl)
    got = out.read_text(encoding="utf-8")
    root = ET.fromstring(got)
    assert root.findall(".//asset-clip[@lane='-4']") == []       # omitted
    assert "MANJU" in got and "loop bed" in got                  # honest note
