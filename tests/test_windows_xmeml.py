"""Wave 3 (MANJU_WINDOWS_ONLY_LEAN_V3 §5.2) — XMEML (FCP7 XML v4) writer.

Red-first (§20): this file is written BEFORE ``src/manju/exporters/xmeml.py``
exists — every test errors at import until the module lands.

What is pinned here:

* the timebase+ntsc rate carrier — a 24000/1001 project rides
  ``<timebase>24</timebase><ntsc>TRUE</ntsc>`` and every ``<start>/<end>/
  <in>/<out>`` is an EXACT whole-frame integer (zero drift: 1001 ms → 24
  frames, 2002 ms → 48, 4004 ms → 96 — asserted as literal document numbers);
  an integer-rate project rides ``ntsc FALSE``;
* ``_file_url`` — ``file://localhost/...`` URIs: forward slashes, percent-
  encoded spaces/CJK, the drive-letter colon kept literal, BOTH input
  separators accepted;
* linked A/V — a video clip and a voice clip sharing one source emit paired
  ``<link>`` elements tying the two clipitems;
* native Cross Dissolve ``<transitionitem>`` between adjacent clipitems, and
  the degrade-to-cut honesty for every other transition kind;
* ET.fromstring well-formedness, byte-determinism, containment refusal;
* the conform ``"xmeml"`` target: complete classification (zero unclassified),
  honest categories, and the frame-native zero-drift block.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from manju.core.container import ProjectError
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
from manju.exporters.xmeml import _file_url, compile_xmeml, export_xmeml

R24 = Rate.from_fraction(24, 1)
NTSC = Rate.from_fraction(24000, 1001)


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


def _timeline(clips, *, fps=24, width=1080, height=1920, rate_echo=None,
              voice=(), music=(), sfx=(), ambient=(), overlay=(), captions=()):
    return Timeline(
        meta=TimelineMeta(compiled_from="w3-xmeml"),
        fps=fps, width=width, height=height,
        duration_ms=sum(int(c.duration_ms) for c in clips),
        rate_echo=rate_echo,
        tracks=TimelineTracks(video=list(clips), voice=list(voice),
                              music=list(music), sfx=list(sfx),
                              ambient=list(ambient), overlay=list(overlay),
                              captions=list(captions)),
    )


def _ntsc_timeline():
    """Two stamped 1001-family clips: 2002 ms == exactly 48 frames, 4004 ms ==
    exactly 96 frames, source_in 1001 ms == exactly 24 frames at 24000/1001."""
    return _timeline(
        [_vid("S001", "TA", 0, 2002, source_in_ms=1001, duration_frames=48),
         _vid("S002", "TB", 2002, 4004, duration_frames=96)],
        rate_echo=EditRate(num=24000, den=1001),
    )


def _clipitems(root):
    return root.findall("./sequence/media/video/track/clipitem")


# --------------------------------------------------------------------------- #
# 1. rate carrier — ntsc TRUE + timebase 24 + exact whole-frame numbers        #
# --------------------------------------------------------------------------- #


def test_ntsc_project_rides_timebase_24_ntsc_true(tmp_project):
    got = compile_xmeml(tmp_project, _ntsc_timeline(), name="NTSC")
    root = ET.fromstring(got)
    assert root.tag == "xmeml" and root.get("version") == "4"
    seq = root.find("sequence")
    assert seq.find("rate/timebase").text == "24"
    assert seq.find("rate/ntsc").text == "TRUE"
    # clipitems carry the same rate
    c0 = _clipitems(root)[0]
    assert c0.find("rate/timebase").text == "24"
    assert c0.find("rate/ntsc").text == "TRUE"


def test_ntsc_frames_are_exact_zero_drift(tmp_project):
    """THE pin: known 1001-family ms values land as EXACT frame integers —
    1001 ms = 24 frames, 2002 ms = 48, 4004 ms = 96 — and the record spine
    telescopes with no drift (clip 2 starts exactly where clip 1 ends)."""
    root = ET.fromstring(compile_xmeml(tmp_project, _ntsc_timeline(), name="X"))
    c0, c1 = _clipitems(root)
    assert c0.find("start").text == "0"
    assert c0.find("end").text == "48"      # 2002 ms == 48 whole frames exactly
    assert c0.find("in").text == "24"       # 1001 ms == 24 whole frames exactly
    assert c0.find("out").text == "72"      # in + window, real numbers (no -1)
    assert c1.find("start").text == "48"    # telescoped — zero drift
    assert c1.find("end").text == "144"     # + 4004 ms == 96 whole frames
    assert c1.find("in").text == "0"
    assert c1.find("out").text == "96"
    assert root.find("sequence/duration").text == "144"


def test_ntsc_frame_stamp_wins_over_rounded_ms(tmp_project):
    """A compiler-stamped clip (duration_frames=25, ms rounded to 1043) emits
    the FRAME truth: 25, never a re-derived ms approximation."""
    tl = _timeline(
        [_vid("A", "TA", 0, 1043, duration_frames=25),
         _vid("B", "TB", 1043, 2002, duration_frames=48)],
        rate_echo=EditRate(num=24000, den=1001),
    )
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    c0, c1 = _clipitems(root)
    assert c0.find("end").text == "25"
    assert c1.find("start").text == "25"    # continuity from the stamp
    assert c1.find("end").text == "73"


def test_integer_rate_project_rides_ntsc_false(tmp_project):
    tl = _timeline([_vid("A", "TA", 0, 2000), _vid("B", "TB", 2000, 1000)])
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    seq = root.find("sequence")
    assert seq.find("rate/timebase").text == "24"
    assert seq.find("rate/ntsc").text == "FALSE"
    c0, c1 = _clipitems(root)
    assert (c0.find("start").text, c0.find("end").text) == ("0", "48")
    assert (c1.find("start").text, c1.find("end").text) == ("48", "72")
    assert seq.find("duration").text == "72"


def test_integer_offgrid_ms_snaps_to_whole_frame(tmp_project):
    # 1234 ms @ 24 fps = 29.616 frames -> ROUND_HALF_UP -> 30 (a whole frame;
    # the emitted integer IS the grid — no fractional frame can ride XMEML)
    tl = _timeline([_vid("A", "TA", 0, 1234)])
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    assert _clipitems(root)[0].find("end").text == "30"
    assert ms_to_frames(1234, R24) == 30    # the same timebase math, by name


# --------------------------------------------------------------------------- #
# 2. _file_url — file://localhost URIs, both separators, CJK, drive colon      #
# --------------------------------------------------------------------------- #


def test_file_url_windows_backslash_cjk_space():
    assert _file_url("C:\\Users\\晓 明\\f.mov") == (
        "file://localhost/C:/Users/%E6%99%93%20%E6%98%8E/f.mov")


def test_file_url_windows_forward_slash():
    assert _file_url("D:/a b/f.mov") == "file://localhost/D:/a%20b/f.mov"


def test_file_url_posix_cjk():
    assert _file_url("/home/x/雨夜.mov") == (
        "file://localhost/home/x/%E9%9B%A8%E5%A4%9C.mov")


def test_file_url_accepts_mixed_separators():
    assert _file_url("C:/Users\\晓 明/f.mov") == _file_url("C:\\Users\\晓 明\\f.mov")


def test_file_url_percent_encodes_reserved_chars_keeps_drive_colon():
    url = _file_url("C:\\a&b#c%d\\f.mov")
    assert url.startswith("file://localhost/C:/")   # drive colon literal
    assert "&" not in url and "#" not in url
    assert "%26" in url and "%23" in url and "%25" in url


def test_pathurl_in_document_matches_file_url(tmp_project):
    tl = _timeline([_vid("S001", "TA", 0, 1000)])
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    pathurl = root.find(".//clipitem/file/pathurl").text
    expected = _file_url(str(tmp_project.resolve("media/gen/S001/TA.mp4")))
    assert pathurl == expected
    assert pathurl.startswith("file://localhost/")
    # the CJK project dir (雨夜便利店.manju) is percent-encoded in the URI
    assert "%E9%9B%A8" in pathurl and " " not in pathurl


# --------------------------------------------------------------------------- #
# 3. linked A/V — shared-source video+voice clipitems carry <link> pairs       #
# --------------------------------------------------------------------------- #


def test_shared_source_video_and_voice_are_linked(tmp_project):
    shared = "media/gen/S001/TA.mp4"
    tl = _timeline(
        [_vid("S001", "TA", 0, 2000)],
        voice=[AudioClip(source=shared, start_ms=0, duration_ms=2000)],
    )
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    vclip = _clipitems(root)[0]
    atracks = root.findall("./sequence/media/audio/track")
    assert len(atracks) == 2                # voice track + music track, always
    aclip = atracks[0].find("clipitem")
    assert aclip is not None
    v_id, a_id = vclip.get("id"), aclip.get("id")
    v_refs = [ln.find("linkclipref").text for ln in vclip.findall("link")]
    a_refs = [ln.find("linkclipref").text for ln in aclip.findall("link")]
    assert a_id in v_refs, "video clipitem must link to its audio clipitem"
    assert v_id in a_refs, "audio clipitem must link back to the video clipitem"
    # one shared <file>: the pathurl is defined once, then referenced by id
    assert vclip.find("file").get("id") == aclip.find("file").get("id")
    assert len(root.findall(".//pathurl")) == 1


def test_unshared_voice_clip_has_no_link(tmp_project):
    tl = _timeline(
        [_vid("S001", "TA", 0, 2000)],
        voice=[AudioClip(source="media/voice/v1.wav", start_ms=0, duration_ms=2000)],
    )
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    assert _clipitems(root)[0].findall("link") == []
    aclip = root.find("./sequence/media/audio/track/clipitem")
    assert aclip.findall("link") == []


def test_music_bus_is_second_audio_track_with_in_point(tmp_project):
    tl = _timeline(
        [_vid("S001", "TA", 0, 4000)],
        music=[AudioClip(source="media/music/bed.mp3", start_ms=0,
                         duration_ms=4000, start_offset_ms=250)],
    )
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    tracks = root.findall("./sequence/media/audio/track")
    assert tracks[0].findall("clipitem") == []      # empty voice track
    mclip = tracks[1].find("clipitem")
    assert mclip.find("start").text == "0"
    assert mclip.find("end").text == "96"           # 4000 ms @ 24
    assert mclip.find("in").text == "6"             # 250 ms in-point
    assert mclip.find("out").text == "102"


def test_loop_bed_and_sfx_are_omitted_with_inband_note(tmp_project):
    tl = _timeline(
        [_vid("S001", "TA", 0, 4000)],
        ambient=[AudioClip(source="media/music/rain.wav", start_ms=0,
                           duration_ms=4000, loop=True)],
        sfx=[AudioClip(source="media/sfx/hit.wav", start_ms=1000, duration_ms=500)],
    )
    got = compile_xmeml(tmp_project, tl, name="X")
    assert "MANJU" in got                            # honest omission, in-band
    assert "rain.wav" in got and "hit.wav" in got
    root = ET.fromstring(got)
    # nothing fabricated: only the (empty) voice+music tracks exist
    assert all(t.findall("clipitem") == []
               for t in root.findall("./sequence/media/audio/track"))


# --------------------------------------------------------------------------- #
# 4. transitions — native Cross Dissolve transitionitem, degrade honesty       #
# --------------------------------------------------------------------------- #


def test_clean_cross_dissolve_is_native_transitionitem(tmp_project):
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
        _vid("B", "TB", 2000, 2000),
    ])
    root = ET.fromstring(compile_xmeml(tmp_project, tl, name="X"))
    track = root.find("./sequence/media/video/track")
    assert [c.tag for c in track] == ["clipitem", "transitionitem", "clipitem"]
    trans = track.find("transitionitem")
    assert trans.find("effect/effectid").text == "Cross Dissolve"
    # 500 ms @ 24 = 12 frames straddling the cut at 48, centre-aligned
    assert trans.find("alignment").text == "center"
    assert trans.find("start").text == "42"
    assert trans.find("end").text == "54"
    assert int(trans.find("end").text) - int(trans.find("start").text) == 12


@pytest.mark.parametrize("ttype", ["fade", "xfade_wipeleft", "bogus"])
def test_non_dissolve_degrades_to_cut_with_note(tmp_project, ttype):
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type=ttype, duration_ms=400)),
        _vid("B", "TB", 2000, 2000),
    ])
    got = compile_xmeml(tmp_project, tl, name="X")
    root = ET.fromstring(got)
    assert root.findall(".//transitionitem") == []   # NEVER a wrong dissolve
    assert f"MANJU: transition '{ttype}'" in got


def test_dissolve_too_long_for_clips_degrades(tmp_project):
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=2000)),
        _vid("B", "TB", 2000, 2000),
    ])
    got = compile_xmeml(tmp_project, tl, name="X")
    assert ET.fromstring(got).findall(".//transitionitem") == []
    assert "MANJU: transition 'xfade_fade'" in got


def test_cut_and_none_emit_nothing(tmp_project):
    tl = _timeline([
        _vid("A", "TA", 0, 1000, transition=TransitionSpec(type="cut", duration_ms=0)),
        _vid("B", "TB", 1000, 1000),
    ])
    got = compile_xmeml(tmp_project, tl, name="X")
    assert "MANJU" not in got
    assert ET.fromstring(got).findall(".//transitionitem") == []


# --------------------------------------------------------------------------- #
# 5. well-formedness, determinism, CJK, empty timeline                         #
# --------------------------------------------------------------------------- #


def test_document_is_well_formed_xml(tmp_project):
    got = compile_xmeml(tmp_project, _ntsc_timeline(), name="雨夜便利店")
    root = ET.fromstring(got)                        # raises if malformed
    assert root.find("sequence/name").text == "雨夜便利店"
    assert got.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    assert "\r" not in got
    assert got.endswith("\n") and not got.endswith("\n\n")


def test_byte_determinism_two_compiles_identical(tmp_project):
    tl = _timeline(
        [_vid("S001", "TA", 0, 2000,
              transition=TransitionSpec(type="xfade_fade", duration_ms=250)),
         _vid("S002", "TB", 2000, 2000)],
        voice=[AudioClip(source="media/voice/v1.wav", start_ms=0, duration_ms=2000)],
    )
    a = compile_xmeml(tmp_project, tl, name="X")
    b = compile_xmeml(tmp_project, tl, name="X")
    assert a == b


def test_empty_timeline_is_valid_zero_duration_doc(tmp_project):
    got = compile_xmeml(tmp_project, _timeline([]), name="EMPTY")
    root = ET.fromstring(got)
    assert root.find("sequence/duration").text == "0"
    assert _clipitems(root) == []


# --------------------------------------------------------------------------- #
# 6. export_xmeml — naming, dirs, determinism, containment                     #
# --------------------------------------------------------------------------- #


def _register(project, add_shot, make_take, shot_id):
    shot = add_shot(project, shot_id)
    take = make_take(project, shot_id, compute_spec_hash(shot, project.load_bible()))
    return take.name, f"media/gen/{shot_id}/{take.name}.mp4"


def test_export_xmeml_writes_file_under_exports(tmp_project, add_shot, make_take):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    t2, s2 = _register(tmp_project, add_shot, make_take, "S002")
    tl = _timeline([
        VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000),
        VideoClip(shot="S002", take=t2, source=s2, start_ms=2000, duration_ms=2000),
    ])
    out = export_xmeml(tmp_project, tl)
    assert out.exists()
    assert out.parent == tmp_project.exports_dir / "xmeml"
    assert out.name == "雨夜便利店.xml"               # <project-name>.xml
    text = out.read_text(encoding="utf-8")
    assert len(ET.fromstring(text).findall(".//video/track/clipitem")) == 2
    # deterministic: re-export is byte-identical
    assert export_xmeml(tmp_project, tl).read_text(encoding="utf-8") == text


def test_export_xmeml_dest_override(tmp_project, add_shot, make_take, tmp_path):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    tl = _timeline([VideoClip(shot="S001", take=t1, source=s1,
                              start_ms=0, duration_ms=1000)])
    dest = tmp_path / "custom.xml"
    assert export_xmeml(tmp_project, tl, dest) == dest and dest.exists()


def test_export_refuses_source_outside_project(tmp_project):
    tl = _timeline([VideoClip(shot="S001", take="T", source="../../etc/evil.mp4",
                              start_ms=0, duration_ms=1000)])
    with pytest.raises(ProjectError):
        export_xmeml(tmp_project, tl)


def test_compile_refuses_outside_audio_source_too(tmp_project):
    tl = _timeline(
        [_vid("S001", "TA", 0, 2000)],
        music=[AudioClip(source="../../etc/evil.wav", start_ms=0, duration_ms=1000)],
    )
    with pytest.raises(ProjectError):
        compile_xmeml(tmp_project, tl, name="X")


# --------------------------------------------------------------------------- #
# 7. conform "xmeml" — completeness, honest categories, frame-native drift     #
# --------------------------------------------------------------------------- #


def _full_inventory_timeline():
    return _timeline(
        [_vid("S001", "TA", 0, 2000, source_in_ms=125, source_gain_db=-3.0,
              transition=TransitionSpec(type="xfade_fade", duration_ms=250)),
         _vid("S002", "TB", 2000, 2000)],
        overlay=[OverlayClip(kind="title_card", text="章", start_ms=0, duration_ms=1500)],
        voice=[AudioClip(source="media/voice/v1.wav", start_ms=0, duration_ms=2000)],
        music=[AudioClip(source="media/music/bed.mp3", start_ms=0, duration_ms=4000,
                         gain_db=-6.0, ducking=True, fade_in_ms=250, fade_out_ms=500,
                         start_offset_ms=250)],
        ambient=[AudioClip(source="media/music/rain.wav", start_ms=0,
                           duration_ms=4000, loop=True)],
        captions=[CaptionLine(start_ms=0, end_ms=1000, text="你好",
                              speaker="林夏", role="sdh")],
    )


def test_xmeml_module_is_classified():
    assert "xmeml" in conform.TARGET_CLASSIFIERS
    assert "xmeml" not in conform.UNSUPPORTED_TARGETS


def test_xmeml_conform_every_feature_classified_exactly_once():
    tl = _full_inventory_timeline()
    inventory = {r["feature"] for r in conform.timeline_feature_inventory(tl)}
    cats = conform.classify_features("xmeml", conform.timeline_feature_inventory(tl))
    union: set[str] = set()
    for cat in ("preserved", "approximated", "dropped", "unsupported"):
        feats = {r["feature"] for r in cats[cat]}
        assert not (union & feats), f"feature in >1 category: {union & feats}"
        union |= feats
    assert inventory - union == set(), f"unclassified: {sorted(inventory - union)}"
    assert union - inventory == set(), f"fabricated: {sorted(union - inventory)}"


def test_xmeml_conform_categories_are_honest():
    tl = _full_inventory_timeline()
    cats = conform.classify_features("xmeml", conform.timeline_feature_inventory(tl))
    got = {r["feature"]: c
           for c in ("preserved", "approximated", "dropped", "unsupported")
           for r in cats[c]}
    assert got["video_clips"] == "preserved"
    assert got["video_in_points"] == "preserved"
    assert got["transitions"] == "approximated"
    assert got["audio_tracks"] == "approximated"     # declared voice/music subset
    assert got["audio_in_points"] == "approximated"
    assert got["captions"] == "dropped"              # ride the SRT/TTML exits
    assert got["caption_roles"] == "dropped"
    assert got["caption_speakers"] == "dropped"
    assert got["overlays"] == "dropped"
    assert got["clip_volume"] == "dropped"
    assert got["audio_gain"] == "dropped"
    assert got["audio_loops"] == "dropped"           # omitted + note, never faked
    row = next(r for r in cats["approximated"] if r["feature"] == "audio_tracks")
    assert "xmeml.py" in row["where"]
    assert "voice" in row["detail"].lower() and "music" in row["detail"].lower()


def test_xmeml_conform_report_zero_unclassified_and_drift_free(
        tmp_project, add_shot, make_take):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    t2, s2 = _register(tmp_project, add_shot, make_take, "S002")
    tl = _timeline(
        [VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000),
         VideoClip(shot="S002", take=t2, source=s2, start_ms=2000, duration_ms=2000)],
        voice=[AudioClip(source="media/voice/v1.wav", start_ms=0, duration_ms=2000)],
        music=[AudioClip(source="media/music/bed.mp3", start_ms=0, duration_ms=4000)],
    )
    out = export_xmeml(tmp_project, tl)
    doc = conform.conform_loss_report(tmp_project, "xmeml", tl, out)
    assert doc["target"] == "xmeml"
    inventory = {r["feature"] for r in conform.timeline_feature_inventory(tl)}
    union = {r["feature"]
             for c in ("preserved", "approximated", "dropped", "unsupported")
             for r in doc[c]}
    assert union == inventory                        # zero unclassified errors
    fd = doc["frame_drift"]
    assert fd["checked"] is True
    assert fd["grid"] == "frame-native"
    assert fd["all_zero"] is True
    assert fd["all_zero_by_construction"] is True
    # video 2x2 + voice 1x2 + music 1x2 boundaries on the emitted frame grid
    assert fd["boundaries_checked"] == 8
    joined = " ".join(doc["notes"])
    assert "2 clipitem(s) vs timeline 2 video clip(s)" in joined
    assert "MISMATCH" not in joined
    # the report never mutates the exported artifact
    assert out.read_text(encoding="utf-8") == export_xmeml(
        tmp_project, tl).read_text(encoding="utf-8")


def test_xmeml_ntsc_drift_block_zero_by_construction():
    block, notes = conform._frame_drift("xmeml", _ntsc_timeline(), None)
    assert block["checked"] is True
    assert block["grid"] == "frame-native"
    assert block["edit_rate"] == "24000/1001"
    assert block["edit_fps"] == 24
    assert block["all_zero"] is True
    assert block["all_zero_by_construction"] is True
    assert block["boundaries_checked"] == 4          # 2 clips x start/end
    assert any("0 BY CONSTRUCTION" in n for n in notes)


def test_xmeml_int_offgrid_boundary_zero_by_construction():
    # 1234 ms @ 24 is OFF the ms grid (29.616 frames) — XMEML emits the whole
    # frame integer, so the residual against the emitted grid is 0.
    tl = _timeline([_vid("A", "TA", 0, 1234)])
    block, _ = conform._frame_drift("xmeml", tl, None)
    assert block["edit_rate"] == "24"
    assert block["all_zero"] is True and block["all_zero_by_construction"] is True


def test_xmeml_source_rate_mismatch_still_surfaces():
    from fractions import Fraction

    tl = _timeline([_vid("A", "TA", 0, 2000)])
    block, _ = conform._frame_drift("xmeml", tl, {"media/gen/A/TA.mp4": "23.976"})
    mism = block["rate_mismatch"]
    assert mism and mism[0]["source_rate"] == "24000/1001"
    assert Fraction(mism[0]["grid_drift_ms_over_clip"]) == Fraction(2, 1)


def test_markers_are_omitted_and_documented():
    """The Timeline model has no marker truth — the writer must say so instead
    of inventing markers, and the document must carry none."""
    import manju.exporters.xmeml as m

    assert "marker" in (m.__doc__ or "").lower()
    assert set(m.__all__) == {"compile_xmeml", "export_xmeml"}
