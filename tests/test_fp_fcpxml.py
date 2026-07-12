"""FP Loop T2 — FCPXML writer (rational-native NLE exit + conform-classified).

Red-first (§20): this file pins the FCPXML writer's exact bytes and honesty
invariants from FIRST PRINCIPLES — every golden time string is derived inline
from the frame math (:mod:`manju.core.timebase`), so the test encodes the SPEC,
not a copy of the output.

What is pinned:

* golden bytes for a 3-clip INT timeline — format/asset/effect resources, the
  spine, a native cross-dissolve <transition>, a degraded transition (cut + an
  in-band <!-- MANJU --> note), exact rational-seconds times, 2-space indent,
  LF endings, trailing newline;
* the RATIONAL-NATIVE truth — frameDuration="1001/24000s" exact, int "1/24s";
  every offset/duration a whole-frame multiple of frameDuration (frame-aligned
  by inspection);
* the rational-native NO-DRIFT pin — an int project with an OFF-GRID millisecond
  boundary still reports residual 0 BY CONSTRUCTION (FCPXML snaps to a whole
  frame and carries it EXACTLY as N/D seconds — unlike OTIO's fractional int
  path), and a 1001-family project likewise;
* DTD-less XML validity + structure (library>event>project>sequence>spine);
* determinism (byte-identical recompiles), CJK names ride natively;
* transitions — clean cross-dissolve overlap geometry, degraded fallback,
  the never-a-wrong-dissolve guard (dissolve too long, or on the last clip);
* the conform "fcpxml" completeness meta-pin + honest classification;
* import is OUT OF SCOPE — no import / import-plan surface is claimed anywhere.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from manju.core.models import (
    AudioClip,
    CaptionLine,
    EditRate,
    OverlayClip,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from manju.core.spec import compute_spec_hash
from manju.core.timebase import Rate, ms_to_frames
from manju.exporters import conform
from manju.exporters.fcpxml import compile_fcpxml, export_fcpxml

R24 = Rate.from_fraction(24, 1)
NTSC = Rate.from_fraction(24000, 1001)  # 23.976


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _vid(shot, take, start_ms, duration_ms, *, source_in_ms=0, transition=None,
         duration_frames=None, source_gain_db=0.0):
    return VideoClip(
        shot=shot, take=take, source=f"media/gen/{shot}/{take}.mp4",
        start_ms=start_ms, duration_ms=duration_ms, source_in_ms=source_in_ms,
        transition_out=transition, duration_frames=duration_frames,
        source_gain_db=source_gain_db,
    )


def _timeline(clips, *, fps=24, width=1080, height=1920, rate_echo=None):
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-fcpxml"),
        fps=fps, width=width, height=height,
        duration_ms=sum(c.duration_ms for c in clips),
        rate_echo=rate_echo,
        tracks=TimelineTracks(video=clips),
    )


def _three_clip_timeline():
    """clip1 --xfade_fade(500ms clean)--> clip2 --fade(300ms degraded)--> clip3."""
    return _timeline([
        _vid("S001", "TAKEA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
        _vid("S002", "TAKEB", 2000, 2000, source_in_ms=125,
             transition=TransitionSpec(type="fade", duration_ms=300)),
        _vid("S003", "TAKEC", 4000, 1000),
    ])


# --------------------------------------------------------------------------- #
# 1. golden bytes — the whole document, derived from the frame math            #
# --------------------------------------------------------------------------- #


def test_golden_three_clip_fcpxml_exact_bytes():
    got = compile_fcpxml(_three_clip_timeline(), rate=R24, name="DEMO")

    # Frame math @24fps (frameDuration "1/24s"; F frames == "{F}/24s"):
    #   durations: 2000ms->48, 2000ms->48, 1000ms->24; source_in 125ms->3
    #   dissolve 500ms->12 (0<12<48 and 12<48 -> native); overlap pulls clip2/3 back
    #   clip1 offset 0, clip2 offset 48-12=36, clip3 offset 36+48=84; seq 84+24=108
    #   asset S002 available = in(3)+window(48)=51
    expected = (
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
    assert got == expected


def test_lf_endings_trailing_newline_and_xml_declaration():
    got = compile_fcpxml(_three_clip_timeline(), rate=R24, name="X")
    assert "\r" not in got
    assert got.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    assert got.endswith("\n") and not got.endswith("\n\n")


def test_dtd_less_parse_and_structure():
    got = compile_fcpxml(_three_clip_timeline(), rate=R24, name="X")
    root = ET.fromstring(got)  # DTD-less parse — no external entities fetched
    assert root.tag == "fcpxml" and root.get("version") == "1.9"
    # library > event > project > sequence > spine
    spine = root.find("./library/event/project/sequence/spine")
    assert spine is not None
    # ET.fromstring drops XML comments — the degraded-transition note is pinned in
    # the golden bytes + the degraded-transition tests. The tree carries the three
    # asset-clips and the one native cross-dissolve transition.
    assert [c.tag for c in spine] == [
        "asset-clip", "transition", "asset-clip", "asset-clip"]
    # every asset-clip ref resolves to a declared asset resource
    asset_ids = {a.get("id") for a in root.findall("./resources/asset")}
    for clip in spine.findall("asset-clip"):
        assert clip.get("ref") in asset_ids


# --------------------------------------------------------------------------- #
# 2. rational-native time — the star: frameDuration + frame-aligned strings     #
# --------------------------------------------------------------------------- #


def test_int_frame_duration_is_one_over_fps():
    got = compile_fcpxml(_timeline([_vid("A", "TA", 0, 1000)]), rate=R24, name="X")
    fmt = ET.fromstring(got).find("./resources/format")
    assert fmt.get("frameDuration") == "1/24s"          # int rate rides 1/24s


def test_rational_frame_duration_is_native_1001_over_24000():
    tl = _timeline(
        [_vid("A", "TA", 0, 2002, duration_frames=48),
         _vid("B", "TB", 2002, 2002, duration_frames=48)],
        rate_echo=EditRate(num=24000, den=1001),
    )
    got = compile_fcpxml(tl, rate=tl.frame_rate, name="RAT")
    root = ET.fromstring(got)
    # THE pin: the exact rational frameDuration rides natively
    assert root.find("./resources/format").get("frameDuration") == "1001/24000s"
    clips = root.findall(".//spine/asset-clip")
    # 48 frames == 48*1001/24000 == "48048/24000s"; offsets telescope exactly
    assert clips[0].get("duration") == "48048/24000s"
    assert clips[0].get("offset") == "0s"
    assert clips[1].get("offset") == "48048/24000s"     # exact, no drift
    assert root.find(".//sequence").get("duration") == "96096/24000s"


def test_all_times_are_whole_frame_multiples_of_frameduration():
    """Frame-alignment is verifiable by inspection: every N/D time has D == the
    frame-rate numerator and N an integer multiple of the frameDuration numerator
    (the rate denominator) — i.e. an exact whole-frame count."""
    import re

    tl = _timeline(
        [_vid("A", "TA", 0, 2002, source_in_ms=1001, duration_frames=48,
              transition=TransitionSpec(type="xfade_fade", duration_ms=250)),
         _vid("B", "TB", 2002, 2002, duration_frames=48)],
        rate_echo=EditRate(num=24000, den=1001),
    )
    got = compile_fcpxml(tl, rate=tl.frame_rate, name="X")
    fnum, fden = 1001, 24000  # frameDuration numerator/denominator (den/num)
    for num, den in re.findall(r'"(\d+)/(\d+)s"', got):
        num, den = int(num), int(den)
        assert den == fden, f"time not on the {fden} timescale: {num}/{den}"
        assert num % fnum == 0, f"time {num}/{den} is not a whole-frame multiple"


# --------------------------------------------------------------------------- #
# 3. THE rational-native no-drift pin (conform) — int AND 1001, residual 0      #
# --------------------------------------------------------------------------- #


def test_int_offgrid_boundary_has_zero_drift_by_construction():
    """An int project whose ms boundary is OFF the frame grid (1234ms @24fps ==
    29.616 frames) still reports residual 0 — FCPXML snaps to a whole frame and
    carries it EXACTLY as N/24 seconds (the advantage over OTIO's int path, which
    would carry the fractional-frame value)."""
    tl = _timeline([_vid("A", "TA", 0, 1234)])          # 1234ms is off-grid @24
    block, notes = conform._frame_drift("fcpxml", tl, None)
    assert block["checked"] is True
    assert block["grid"] == "rational-native"
    assert block["edit_rate"] == "24"
    assert block["all_zero"] is True
    assert block["all_zero_by_construction"] is True
    assert block["cumulative_max_residual"] == "0"
    assert any("0 BY CONSTRUCTION" in n for n in notes)


def test_rational_boundaries_have_zero_drift_by_construction():
    tl = _timeline(
        [_vid("A", "TA", 0, 2002, duration_frames=48),
         _vid("B", "TB", 2002, 2002, duration_frames=48)],
        rate_echo=EditRate(num=24000, den=1001),
    )
    block, _ = conform._frame_drift("fcpxml", tl, None)
    assert block["edit_rate"] == "24000/1001"
    assert block["all_zero"] is True and block["all_zero_by_construction"] is True
    assert block["boundaries_checked"] == 4              # 2 clips x start/end


def test_source_rate_drift_still_surfaces_against_the_edit_grid():
    # 23.976 material laid on the int 24 grid: exact grid drift must be reported
    tl = _timeline([_vid("A", "TA", 0, 2000)])
    block, _ = conform._frame_drift("fcpxml", tl, {"media/gen/A/TA.mp4": "23.976"})
    mism = block["rate_mismatch"]
    assert mism and mism[0]["source_rate"] == "24000/1001"
    assert Fraction(mism[0]["grid_drift_ms_over_clip"]) == Fraction(2, 1)  # 2000*0.1%


# --------------------------------------------------------------------------- #
# 4. determinism + CJK + empty timeline                                        #
# --------------------------------------------------------------------------- #


def test_determinism_byte_identical():
    tl = _three_clip_timeline()
    assert compile_fcpxml(tl, rate=R24, name="X") == compile_fcpxml(tl, rate=R24, name="X")


def test_cjk_names_ride_natively_and_parse():
    tl = _timeline([VideoClip(shot="镜头一", take="雨夜", source="media/gen/镜头一/雨夜.mp4",
                              start_ms=0, duration_ms=1000)])
    got = compile_fcpxml(tl, rate=R24, name="雨夜便利店")
    root = ET.fromstring(got)  # valid XML with CJK content
    assert root.find("./library/event").get("name") == "雨夜便利店"
    assert root.find(".//spine/asset-clip").get("name") == "镜头一"
    assert root.find("./resources/asset").get("src") == "media/gen/镜头一/雨夜.mp4"


def test_special_chars_in_source_are_xml_escaped():
    tl = _timeline([VideoClip(shot="A&B", take="T", source="media/gen/a & b<>.mp4",
                              start_ms=0, duration_ms=1000)])
    got = compile_fcpxml(tl, rate=R24, name="X")
    assert "&amp;" in got and "a & b" not in got        # escaped, not raw
    root = ET.fromstring(got)                            # round-trips verbatim
    assert root.find("./resources/asset").get("src") == "media/gen/a & b<>.mp4"


def test_empty_timeline_is_format_only_valid_doc():
    got = compile_fcpxml(_timeline([]), rate=R24, name="EMPTY")
    root = ET.fromstring(got)
    assert root.findall("./resources/asset") == []
    assert root.findall("./resources/effect") == []
    spine = root.find(".//spine")
    assert list(spine) == []
    assert root.find(".//sequence").get("duration") == "0s"


# --------------------------------------------------------------------------- #
# 5. transitions — native cross-dissolve, degraded fallback, never-wrong guard  #
# --------------------------------------------------------------------------- #


def test_clean_cross_dissolve_is_native_transition_with_overlap():
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=250)),
        _vid("B", "TB", 2000, 2000),
    ])
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    spine = root.find(".//spine")
    trans = spine.find("transition")
    assert trans is not None
    dt = ms_to_frames(250, R24)                          # 6 frames
    assert trans.get("duration") == f"{dt}/24s"          # "6/24s"
    assert trans.find("filter-video").get("name") == "Cross Dissolve"
    # overlap geometry: clip B (and the transition) pull back by dt frames
    clips = spine.findall("asset-clip")
    a_frames = ms_to_frames(2000, R24)                   # 48
    assert clips[1].get("offset") == f"{a_frames - dt}/24s"   # "42/24s"
    assert trans.get("offset") == clips[1].get("offset")
    # a cross-dissolve effect resource is declared exactly once
    effs = root.findall("./resources/effect")
    assert len(effs) == 1 and effs[0].get("uid") == "FFVideoTransitionCrossDissolve"


@pytest.mark.parametrize("ttype", ["fade", "xfade_wipeleft", "xfade_slideright", "bogus"])
def test_non_dissolve_degrades_to_cut_with_note_no_transition(ttype):
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type=ttype, duration_ms=400)),
        _vid("B", "TB", 2000, 2000),
    ])
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    assert root.findall(".//spine/transition") == []     # NEVER a wrong dissolve
    assert root.findall("./resources/effect") == []
    assert f"MANJU: transition '{ttype}' (400ms) at A" in got
    # clips stay abutting (no overlap pullback for a degraded transition)
    clips = root.findall(".//spine/asset-clip")
    assert clips[1].get("offset") == "48/24s"


def test_dissolve_too_long_for_clips_degrades_not_wrong():
    # dissolve 2000ms == 48 frames == the whole clip: cannot overlap -> degrade
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=2000)),
        _vid("B", "TB", 2000, 2000),
    ])
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    assert root.findall(".//spine/transition") == []     # guard: never a wrong dissolve
    assert "MANJU: transition 'xfade_fade' (2000ms) at A" in got


def test_clean_dissolve_on_last_clip_has_no_next_so_degrades():
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=250)),
    ])
    got = compile_fcpxml(tl, rate=R24, name="X")
    root = ET.fromstring(got)
    assert root.findall(".//spine/transition") == []
    assert "MANJU: transition 'xfade_fade' (250ms) at A" in got


def test_explicit_cut_and_none_emit_no_note_no_transition():
    tl = _timeline([
        _vid("A", "TA", 0, 1000, transition=TransitionSpec(type="cut", duration_ms=0)),
        _vid("B", "TB", 1000, 1000),                     # transition_out=None
    ])
    got = compile_fcpxml(tl, rate=R24, name="X")
    assert "MANJU" not in got
    assert ET.fromstring(got).findall(".//spine/transition") == []


# --------------------------------------------------------------------------- #
# 6. source in-points — asset-clip start + widened asset available range        #
# --------------------------------------------------------------------------- #


def test_source_in_point_is_asset_clip_start_and_widens_asset_duration():
    tl = _timeline([_vid("A", "TA", 0, 2000, source_in_ms=1000)])
    root = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X"))
    clip = root.find(".//spine/asset-clip")
    in_f = ms_to_frames(1000, R24)                       # 24
    win_f = ms_to_frames(2000, R24)                      # 48
    assert clip.get("start") == f"{in_f}/24s"            # "24/24s"
    assert clip.get("duration") == f"{win_f}/24s"        # "48/24s"
    # asset available range covers in-point + window (self-consistent)
    asset = root.find("./resources/asset")
    assert asset.get("start") == "0s"
    assert asset.get("duration") == f"{in_f + win_f}/24s"   # "72/24s"


def test_untrimmed_clip_start_is_zero():
    tl = _timeline([_vid("A", "TA", 0, 1000)])
    clip = ET.fromstring(compile_fcpxml(tl, rate=R24, name="X")).find(".//spine/asset-clip")
    assert clip.get("start") == "0s"


def test_shared_source_is_one_asset_referenced_twice():
    c1 = VideoClip(shot="A", take="T", source="media/gen/shared.mp4", start_ms=0, duration_ms=1000)
    c2 = VideoClip(shot="B", take="T", source="media/gen/shared.mp4", start_ms=1000, duration_ms=2000)
    root = ET.fromstring(compile_fcpxml(_timeline([c1, c2]), rate=R24, name="X"))
    assets = root.findall("./resources/asset")
    assert len(assets) == 1                              # deduped by src
    refs = {c.get("ref") for c in root.findall(".//spine/asset-clip")}
    assert refs == {assets[0].get("id")}
    # available range covers the LONGER use (2000ms window)
    assert assets[0].get("duration") == f"{ms_to_frames(2000, R24)}/24s"


# --------------------------------------------------------------------------- #
# 7. conform "fcpxml" target — completeness meta-pin + honest classification    #
# --------------------------------------------------------------------------- #


def _full_inventory_timeline():
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-fcpxml-conform"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(
            video=[
                _vid("S001", "TA", 0, 2000, source_in_ms=125, source_gain_db=-3.0,
                     transition=TransitionSpec(type="xfade_fade", duration_ms=250)),
                _vid("S002", "TB", 2000, 2000),
            ],
            overlay=[OverlayClip(kind="title_card", text="章", start_ms=0, duration_ms=1500)],
            music=[AudioClip(source="media/music/bed.mp3", start_ms=0, duration_ms=4000,
                             gain_db=-6.0, ducking=True, fade_in_ms=250, fade_out_ms=500,
                             start_offset_ms=250, loop=True)],
            captions=[CaptionLine(start_ms=0, end_ms=1000, text="你好")],
        ),
    )


def test_fcpxml_conform_every_feature_classified_exactly_once():
    tl = _full_inventory_timeline()
    inventory = {r["feature"] for r in conform.timeline_feature_inventory(tl)}
    categories = conform.classify_features("fcpxml", conform.timeline_feature_inventory(tl))
    union: set[str] = set()
    for cat in ("preserved", "approximated", "dropped", "unsupported"):
        feats = {r["feature"] for r in categories[cat]}
        assert not (union & feats), f"feature in >1 category: {union & feats}"
        union |= feats
    assert inventory - union == set(), f"unclassified: {sorted(inventory - union)}"
    assert union - inventory == set(), f"fabricated: {sorted(union - inventory)}"


def test_fcpxml_conform_video_preserved_captions_dropped_audio_classified():
    """EVOLVED with the V1 audio increment (orchestrator edit, teeth
    preserved): T2's original pin asserted audio_tracks/audio_gain
    "unsupported" while the writer carried no audio at all. V1 writes
    non-loop resolvable clips as connected role/lane asset-clips, so the
    honest static classification moved to "approximated" — with the
    loop/None omission stated in the detail (a loop-only timeline still
    gets no audio) — and audio_loops STAYS "unsupported" (one written
    pass of a fill-to-duration bed would be wrong audio). The original
    honesty tooth — writer-scope, not a format limit, nothing faked —
    remains asserted below."""
    tl = _full_inventory_timeline()
    cats = conform.classify_features("fcpxml", conform.timeline_feature_inventory(tl))
    preserved = {r["feature"] for r in cats["preserved"]}
    approx = {r["feature"] for r in cats["approximated"]}
    dropped = {r["feature"] for r in cats["dropped"]}
    unsupported = {r["feature"] for r in cats["unsupported"]}
    assert {"video_clips", "video_in_points"} <= preserved
    assert "transitions" in approx
    assert {"captions", "overlays", "clip_volume"} <= dropped
    assert "audio_tracks" in approx and "audio_gain" in approx
    assert "audio_loops" in unsupported
    audio_row = next(r for r in cats["approximated"] if r["feature"] == "audio_tracks")
    assert "role/lane" in audio_row["detail"] and "fcpxml.py" in audio_row["where"]
    assert "not a format limit" in audio_row["detail"]
    assert "OMITTED" in audio_row["detail"]  # the loop/None boundary is stated


def test_fcpxml_module_is_classified():
    """The exporter-module coverage pin (test_fp_conform) requires every module
    under exporters/ be classified — fcpxml.py must be in TARGET_CLASSIFIERS."""
    assert "fcpxml" in conform.TARGET_CLASSIFIERS
    assert "fcpxml" not in conform.UNSUPPORTED_TARGETS


# --------------------------------------------------------------------------- #
# 8. export_fcpxml integration (real project) + report + containment           #
# --------------------------------------------------------------------------- #


def _register(project, add_shot, make_take, shot_id):
    shot = add_shot(project, shot_id)
    take = make_take(project, shot_id, compute_spec_hash(shot, project.load_bible()))
    return take.name, f"media/gen/{shot_id}/{take.name}.mp4"


def test_export_fcpxml_writes_file_under_exports(tmp_project, add_shot, make_take):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    t2, s2 = _register(tmp_project, add_shot, make_take, "S002")
    tl = _timeline([
        VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000),
        VideoClip(shot="S002", take=t2, source=s2, start_ms=2000, duration_ms=2000),
    ])
    out = export_fcpxml(tmp_project, tl)
    assert out.exists()
    assert out.parent == tmp_project.exports_dir / "fcpxml"
    assert out.suffix == ".fcpxml"
    text = out.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    assert root.tag == "fcpxml"
    assert len(root.findall(".//spine/asset-clip")) == 2
    # deterministic: re-export is byte-identical
    assert export_fcpxml(tmp_project, tl).read_text(encoding="utf-8") == text


def test_export_fcpxml_dest_override(tmp_project, add_shot, make_take, tmp_path):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    tl = _timeline([VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=1000)])
    dest = tmp_path / "custom.fcpxml"
    out = export_fcpxml(tmp_project, tl, dest)
    assert out == dest and dest.exists()


def test_export_fcpxml_uses_project_rational_edit_rate(tmp_project, add_shot, make_take):
    config = tmp_project.load_config()
    config.fps = 24
    config.edit_rate = EditRate(num=24000, den=1001)
    tmp_project.save_config(config)
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    tl = _timeline(
        [VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2002,
                   duration_frames=48)],
        rate_echo=EditRate(num=24000, den=1001),
    )
    text = export_fcpxml(tmp_project, tl).read_text(encoding="utf-8")
    assert 'frameDuration="1001/24000s"' in text          # rational rides natively
    assert 'duration="48048/24000s"' in text


def test_export_fcpxml_refuses_source_outside_project(tmp_project):
    from manju.core.container import ProjectError

    tl = _timeline([VideoClip(shot="S001", take="T", source="../../etc/evil.mp4",
                              start_ms=0, duration_ms=1000)])
    with pytest.raises(ProjectError):
        export_fcpxml(tmp_project, tl)


def test_fcpxml_conform_report_drift_and_crosscheck(tmp_project, add_shot, make_take):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    t2, s2 = _register(tmp_project, add_shot, make_take, "S002")
    tl = _timeline([
        VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000),
        VideoClip(shot="S002", take=t2, source=s2, start_ms=2000, duration_ms=2000),
    ])
    out = export_fcpxml(tmp_project, tl)
    doc = conform.conform_loss_report(tmp_project, "fcpxml", tl, out)
    assert doc["target"] == "fcpxml"
    # rational-native: drift checked, all zero by construction
    assert doc["frame_drift"]["checked"] is True
    assert doc["frame_drift"]["all_zero"] is True
    assert doc["frame_drift"]["all_zero_by_construction"] is True
    # exported cross-check counts 2 asset-clips vs 2 clips (no MISMATCH)
    joined = " ".join(doc["notes"])
    assert "2 asset-clip(s) vs timeline 2 video clip(s)" in joined
    assert "MISMATCH" not in joined
    # the report never mutates the exported bytes
    assert out.read_text(encoding="utf-8") == export_fcpxml(tmp_project, tl).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 9. honest boundary — import is OUT OF SCOPE (no import / import-plan surface)  #
# --------------------------------------------------------------------------- #


def test_writer_exposes_no_import_surface():
    """WRITER only this loop: the module exports exactly the two writer entry
    points and claims no import / import-plan anywhere (honest boundary)."""
    import manju.exporters.fcpxml as m

    assert set(m.__all__) == {"compile_fcpxml", "export_fcpxml"}
    public = {n for n in dir(m) if not n.startswith("_")}
    assert not any("import" in n.lower() for n in public)
    assert not any("import" in n.lower() for n in m.__all__)
