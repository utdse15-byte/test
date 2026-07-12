"""FP Loop S2 — CMX3600 EDL writer (real SMPTE timecode + conform-classified).

Red-first (§20): this file is written to pin the EDL writer's exact bytes and
honesty invariants BEFORE the implementation is trusted — every golden string
here is derived from first principles (the frame math is spelled out inline via
:mod:`manju.core.timebase`), so the test encodes the SPEC, not a copy of the
output.

What is pinned:

* golden bytes for a 3-clip timeline — TITLE + FCM header, cut events, a clean
  cross-dissolve (two-line C+D), a degraded transition (cut + `* MANJU:` note),
  FROM/TO CLIP NAME comments, exact column layout, LF endings;
* record-TC continuity — event N's record-out == event N+1's record-in;
* source-TC honesty — 00:00:00:00-based from source_in_ms, source length ==
  record length;
* reel truncation to 8 ASCII chars + deterministic collision disambiguation;
* CJK take names — ASCII reel column, full UTF-8 name in the comment;
* drop-frame — 29.97 yields FCM: DROP FRAME + ';' frames separator;
* determinism (byte-identical across repeated compiles);
* the conform "edl" completeness meta-pin (every feature classified once).
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from manju.core.models import (
    AudioClip,
    CaptionLine,
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
from manju.exporters.edl import compile_edl, export_edl

R24 = Rate.from_fraction(24, 1)
R30DF = Rate.from_fraction(30000, 1001)  # 29.97 drop-frame-legal


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _vid(shot, take, start_ms, duration_ms, *, source_in_ms=0, transition=None):
    return VideoClip(
        shot=shot, take=take, source=f"media/gen/{shot}/{take}.mp4",
        start_ms=start_ms, duration_ms=duration_ms, source_in_ms=source_in_ms,
        transition_out=transition,
    )


def _timeline(clips, *, fps=24):
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-edl"),
        fps=fps, width=1080, height=1920,
        duration_ms=sum(c.duration_ms for c in clips),
        tracks=TimelineTracks(video=clips),
    )


def _three_clip_timeline():
    """clip1 --xfade_fade(500ms clean dissolve)--> clip2 --fade(300ms degraded)--> clip3."""
    return _timeline([
        _vid("S001", "TAKEALPHA0001", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=500)),
        _vid("S002", "TAKEBETA0002", 2000, 2000, source_in_ms=125,
             transition=TransitionSpec(type="fade", duration_ms=300)),
        _vid("S003", "TAKEGAMMA0003", 4000, 1000),
    ])


# --------------------------------------------------------------------------- #
# 1. golden bytes — the whole document, derived from the frame math            #
# --------------------------------------------------------------------------- #


def test_golden_three_clip_edl_exact_bytes():
    tl = _three_clip_timeline()
    got = compile_edl(tl, rate=R24, start_timecode="01:00:00:00", title="DEMO EDL")

    # Frame math @24fps NDF, start 01:00:00:00 == frame 86400:
    #   ms_to_frames: 0->0, 2000->48, 4000->96, 5000->120, 125->3, 500->12
    #   clip1 rec 86400..86448 (01:00:00:00..01:00:02:00), src 0..48 (..00:00:02:00)
    #   clip2 rec 86448..86496 (01:00:02:00..01:00:04:00), src 3..51 (00:00:00:03..00:00:02:03)
    #   clip3 rec 86496..86520 (01:00:04:00..01:00:05:00), src 0..24 (..00:00:01:00)
    #   dissolve 500ms -> 012 frames
    expected = (
        "TITLE:   DEMO EDL\n"
        "FCM: NON-DROP FRAME\n"
        "001  TAKEALPH V    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00\n"
        "* FROM CLIP NAME: TAKEALPHA0001\n"
        "002  TAKEALPH V    C        00:00:02:00 00:00:02:00 01:00:02:00 01:00:02:00\n"
        "002  TAKEBETA V    D    012 00:00:00:03 00:00:02:03 01:00:02:00 01:00:04:00\n"
        "* FROM CLIP NAME: TAKEALPHA0001\n"
        "* TO CLIP NAME: TAKEBETA0002\n"
        "* MANJU: transition 'fade' (300ms) at S002 approximated as hard cut "
        "(no clean CMX3600 dissolve mapping)\n"
        "003  TAKEGAMM V    C        00:00:00:00 00:00:01:00 01:00:04:00 01:00:05:00\n"
        "* FROM CLIP NAME: TAKEGAMMA0003\n"
    )
    assert got == expected


def test_lf_endings_and_trailing_newline():
    got = compile_edl(_three_clip_timeline(), rate=R24, title="X")
    assert "\r" not in got
    assert got.endswith("\n")
    assert not got.endswith("\n\n")


# --------------------------------------------------------------------------- #
# 2. record-TC continuity — event N out == event N+1 in                        #
# --------------------------------------------------------------------------- #


def test_record_tc_continuity_no_gaps():
    tl = _timeline([
        _vid("A", "TA", 0, 1000),
        _vid("B", "TB", 1000, 1500),
        _vid("C", "TC", 2500, 800),
    ])
    got = compile_edl(tl, rate=R24, title="X")
    # collect (rec_in, rec_out) from each single-cut event row
    rows = [ln for ln in got.splitlines() if ln[:3].isdigit()]
    rec = [(ln.split()[-2], ln.split()[-1]) for ln in rows]
    # event N's record-out is event N+1's record-in, exactly
    for (_, out_n), (in_n1, _) in zip(rec, rec[1:]):
        assert out_n == in_n1, f"discontinuity: {out_n} != {in_n1}"


# --------------------------------------------------------------------------- #
# 3. source-TC honesty — zero-based, length matches record                     #
# --------------------------------------------------------------------------- #


def test_source_tc_zero_based_and_length_matches_record():
    # a trimmed clip: source_in_ms=1000 -> source starts at 00:00:01:00, and the
    # source window length equals the record window length (a cut list invariant)
    tl = _timeline([_vid("A", "TA", 0, 2000, source_in_ms=1000)])
    got = compile_edl(tl, rate=R24, title="X")
    row = [ln for ln in got.splitlines() if ln.startswith("001")][0]
    src_in, src_out, rec_in, rec_out = row.split()[-4:]
    assert src_in == "00:00:01:00"          # zero-based from source_in_ms=1000ms
    assert src_out == "00:00:03:00"          # + 2000ms window
    assert rec_in == "01:00:00:00" and rec_out == "01:00:02:00"
    # source length == record length: both spans are 2000ms == 48 frames
    src_span = ms_to_frames(3000, R24) - ms_to_frames(1000, R24)
    rec_span = ms_to_frames(2000, R24) - ms_to_frames(0, R24)
    assert src_span == rec_span == 48


def test_untrimmed_clip_source_starts_at_zero():
    tl = _timeline([_vid("A", "TA", 0, 1000)])
    got = compile_edl(tl, rate=R24, title="X")
    row = [ln for ln in got.splitlines() if ln.startswith("001")][0]
    src_in = row.split()[-4]
    assert src_in == "00:00:00:00"


# --------------------------------------------------------------------------- #
# 4. reels — 8-char truncation + deterministic collision disambiguation        #
# --------------------------------------------------------------------------- #


def test_reel_truncated_to_eight_ascii_chars():
    tl = _timeline([_vid("A", "SUPERCALIFRAGILISTIC", 0, 1000)])
    got = compile_edl(tl, rate=R24, title="X")
    row = [ln for ln in got.splitlines() if ln.startswith("001")][0]
    reel = row.split()[1]
    assert reel == "SUPERCAL"                 # first 8 uppercase ASCII alnum
    assert "* FROM CLIP NAME: SUPERCALIFRAGILISTIC" in got  # full name in comment


def test_reel_collision_disambiguated_deterministically():
    # two DISTINCT takes that fold to the same 8-char stem "TAKEALPH"
    tl = _timeline([
        _vid("A", "TAKEALPHA0001", 0, 1000),
        _vid("B", "TAKEALPHA0002", 1000, 1000),
    ])
    got = compile_edl(tl, rate=R24, title="X")
    reels = [ln.split()[1] for ln in got.splitlines() if ln[:3].isdigit()]
    assert reels[0] == "TAKEALPH"
    assert reels[1] == "TAKEALP2"             # deterministic numeric suffix
    assert reels[0] != reels[1]
    # deterministic across recompiles
    assert compile_edl(tl, rate=R24, title="X") == got


def test_same_take_reuses_same_reel():
    tl = _timeline([
        _vid("A", "SHAREDTAKE", 0, 1000),
        _vid("B", "OTHERTAKE", 1000, 1000),
        _vid("C", "SHAREDTAKE", 2000, 1000),  # same take as clip A
    ])
    got = compile_edl(tl, rate=R24, title="X")
    reels = [ln.split()[1] for ln in got.splitlines() if ln[:3].isdigit()]
    assert reels[0] == reels[2], "identical takes must map to the same reel"


# --------------------------------------------------------------------------- #
# 5. CJK take names — ASCII reel column, UTF-8 name in the comment             #
# --------------------------------------------------------------------------- #


def test_cjk_take_name_ascii_reel_utf8_comment():
    tl = _timeline([_vid("S001", "镜头一_雨夜", 0, 1000)])
    got = compile_edl(tl, rate=R24, title="片名")
    row = [ln for ln in got.splitlines() if ln.startswith("001")][0]
    reel = row.split()[1]
    # the reel column is pure ASCII (hash-derived; the fold keeps ASCII alnum)
    assert reel.isascii() and reel.isalnum() and len(reel) <= 8
    # the full CJK name rides the comment verbatim (UTF-8)
    assert "* FROM CLIP NAME: 镜头一_雨夜" in got
    # the TITLE column is ASCII-folded (no raw CJK in the header)
    title_line = got.splitlines()[0]
    assert title_line.isascii()


def test_all_cjk_take_name_still_ascii_reel():
    tl = _timeline([_vid("S001", "镜头", 0, 1000)])   # no ASCII alnum at all
    got = compile_edl(tl, rate=R24, title="X")
    reel = [ln for ln in got.splitlines() if ln.startswith("001")][0].split()[1]
    assert reel.isascii() and reel and len(reel) <= 8


# --------------------------------------------------------------------------- #
# 6. transitions — clean dissolve vs degraded fallback                         #
# --------------------------------------------------------------------------- #


def test_clean_dissolve_becomes_two_line_D_event():
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type="xfade_fade", duration_ms=250)),
        _vid("B", "TB", 2000, 2000),
    ])
    got = compile_edl(tl, rate=R24, title="X")
    lines = got.splitlines()
    d_rows = [ln for ln in lines if ln[:3].isdigit() and ln.split()[3] == "D"]
    assert len(d_rows) == 1
    d = d_rows[0]
    assert d.split()[3] == "D"
    assert d.split()[4] == "006"              # 250ms @24fps -> 6 frames
    assert "* TO CLIP NAME: TB" in got
    # the incoming event has TWO lines sharing event number 002
    ev2 = [ln for ln in lines if ln.startswith("002")]
    assert len(ev2) == 2
    assert ev2[0].split()[3] == "C" and ev2[1].split()[3] == "D"


@pytest.mark.parametrize("ttype", ["fade", "xfade_wipeleft", "xfade_slideright", "bogus_type"])
def test_non_dissolve_transition_degrades_to_cut_with_note(ttype):
    tl = _timeline([
        _vid("A", "TA", 0, 2000,
             transition=TransitionSpec(type=ttype, duration_ms=400)),
        _vid("B", "TB", 2000, 2000),
    ])
    got = compile_edl(tl, rate=R24, title="X")
    assert " D " not in " ".join(
        ln for ln in got.splitlines() if ln[:3].isdigit()
    ), "a non-dissolve transition must NEVER emit a D event"
    assert f"* MANJU: transition '{ttype}' (400ms) at A" in got


def test_explicit_cut_and_none_emit_no_note():
    tl = _timeline([
        _vid("A", "TA", 0, 1000, transition=TransitionSpec(type="cut", duration_ms=0)),
        _vid("B", "TB", 1000, 1000),  # transition_out=None
    ])
    got = compile_edl(tl, rate=R24, title="X")
    assert "* MANJU:" not in got
    assert "D" not in [ln.split()[3] for ln in got.splitlines() if ln[:3].isdigit()]


# --------------------------------------------------------------------------- #
# 7. drop-frame — 29.97 => FCM: DROP FRAME + ';' separator                      #
# --------------------------------------------------------------------------- #


def test_drop_frame_ntsc_header_and_separator():
    tl = _timeline([_vid("A", "TA", 0, 2000)], fps=30)
    got = compile_edl(tl, rate=R30DF, start_timecode="01:00:00:00", title="X")
    assert "FCM: DROP FRAME" in got
    row = [ln for ln in got.splitlines() if ln.startswith("001")][0]
    src_in, src_out, rec_in, rec_out = row.split()[-4:]
    # drop-frame renders ';' before the frames field (CMX-legal DF convention)
    assert rec_in == "01:00:00;00"
    assert src_in == "00:00:00;00"
    assert src_out == "00:00:02;00"           # 2000ms @29.97 -> 60 frames == 2s
    assert ";" in rec_out


def test_auto_ndf_for_integer_rate():
    got = compile_edl(_timeline([_vid("A", "TA", 0, 1000)]), rate=R24, title="X")
    assert "FCM: NON-DROP FRAME" in got
    assert ";" not in got  # colon-only NDF rendering


def test_drop_frame_refused_on_non_df_legal_rate():
    # 23.976 is NTSC but NOT drop-frame-legal
    r2397 = Rate.from_fraction(24000, 1001)
    with pytest.raises(ValueError):
        compile_edl(_timeline([_vid("A", "TA", 0, 1000)]),
                    rate=r2397, drop_frame=True, title="X")


# --------------------------------------------------------------------------- #
# 8. determinism + empty timeline                                              #
# --------------------------------------------------------------------------- #


def test_determinism_byte_identical():
    tl = _three_clip_timeline()
    a = compile_edl(tl, rate=R24, title="X")
    b = compile_edl(tl, rate=R24, title="X")
    assert a == b


def test_empty_video_timeline_is_header_only():
    got = compile_edl(_timeline([]), rate=R24, title="EMPTY")
    assert got == "TITLE:   EMPTY\nFCM: NON-DROP FRAME\n"


# --------------------------------------------------------------------------- #
# 9. export_edl integration (real project) + rate resolution                   #
# --------------------------------------------------------------------------- #


def _register(project, add_shot, make_take, shot_id):
    shot = add_shot(project, shot_id)
    take = make_take(project, shot_id, compute_spec_hash(shot, project.load_bible()))
    return take.name, f"media/gen/{shot_id}/{take.name}.mp4"


def test_export_edl_writes_file_under_exports(tmp_project, add_shot, make_take):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    t2, s2 = _register(tmp_project, add_shot, make_take, "S002")
    tl = _timeline([
        VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000),
        VideoClip(shot="S002", take=t2, source=s2, start_ms=2000, duration_ms=2000),
    ])
    out = export_edl(tmp_project, tl)
    assert out.exists()
    assert out.parent == tmp_project.exports_dir / "edl"
    assert out.suffix == ".edl"
    text = out.read_text(encoding="utf-8")
    assert text.startswith("TITLE:")
    assert "FCM: NON-DROP FRAME" in text
    # two cut events for two clips
    assert len([ln for ln in text.splitlines() if ln[:3].isdigit()]) == 2
    # deterministic: re-export is byte-identical
    out2 = export_edl(tmp_project, tl)
    assert out2.read_text(encoding="utf-8") == text


def test_export_edl_dest_override(tmp_project, add_shot, make_take, tmp_path):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    tl = _timeline([VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=1000)])
    dest = tmp_path / "custom.edl"
    out = export_edl(tmp_project, tl, dest)
    assert out == dest and dest.exists()


def test_export_edl_honors_project_declared_ntsc_df_rate(tmp_project, add_shot, make_take):
    # declare a drop-frame-legal rational edit_rate that mirrors fps=30
    from manju.core.models import EditRate

    config = tmp_project.load_config()
    config.fps = 30
    config.edit_rate = EditRate(num=30000, den=1001)
    tmp_project.save_config(config)

    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    tl = _timeline([VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000)], fps=30)
    out = export_edl(tmp_project, tl)
    text = out.read_text(encoding="utf-8")
    assert "FCM: DROP FRAME" in text
    assert "01:00:00;00" in text  # drop-frame record TC


# --------------------------------------------------------------------------- #
# 10. conform "edl" target — completeness meta-pin + honest classification     #
# --------------------------------------------------------------------------- #


def _full_inventory_timeline():
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-edl-conform"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(
            video=[
                _vid("S001", "TA", 0, 2000, source_in_ms=125,
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


def test_edl_conform_every_feature_classified_exactly_once():
    tl = _full_inventory_timeline()
    inventory = {r["feature"] for r in conform.timeline_feature_inventory(tl)}
    categories = conform.classify_features("edl", conform.timeline_feature_inventory(tl))
    union: set[str] = set()
    for cat in ("preserved", "approximated", "dropped", "unsupported"):
        feats = {r["feature"] for r in categories[cat]}
        assert not (union & feats), f"feature in >1 category: {union & feats}"
        union |= feats
    assert inventory - union == set(), f"unclassified: {sorted(inventory - union)}"
    assert union - inventory == set(), f"fabricated: {sorted(union - inventory)}"


def test_edl_conform_audio_is_unsupported_video_is_preserved():
    """EVOLVED with Y1 (orchestrator edit, teeth preserved): S2's original
    pin asserted audio_tracks unsupported while the writer was V-only. Y1
    landed the DECLARED A1/A2 subset (voice->A1, music->A2; sfx/ambient
    omitted with per-clip notes), so the honest classification moved to
    approximated — with the never-squeezed boundary asserted. loop/ducking
    stay unsupported (no CMX primitive; no fabricated events)."""
    tl = _full_inventory_timeline()
    cats = conform.classify_features("edl", conform.timeline_feature_inventory(tl))
    preserved = {r["feature"] for r in cats["preserved"]}
    unsupported = {r["feature"] for r in cats["unsupported"]}
    approx = {r["feature"] for r in cats["approximated"]}
    dropped = {r["feature"] for r in cats["dropped"]}
    assert "video_clips" in preserved and "video_in_points" in preserved
    assert "audio_tracks" in approx  # Y1: declared A1/A2 subset
    assert {"audio_loops", "ducking"} <= unsupported  # still no primitive
    assert "transitions" in approx
    assert "captions" in dropped and "overlays" in dropped
    row = next(r for r in cats["approximated"] if r["feature"] == "audio_tracks")
    assert "A1" in row["detail"] and "A2" in row["detail"]
    assert "never squeezed" in row["detail"]  # sfx/ambient honesty stated


def test_edl_conform_report_and_drift_and_crosscheck(tmp_project, add_shot, make_take):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    t2, s2 = _register(tmp_project, add_shot, make_take, "S002")
    tl = _timeline([
        VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000),
        VideoClip(shot="S002", take=t2, source=s2, start_ms=2000, duration_ms=2000),
    ])
    out = export_edl(tmp_project, tl)
    doc = conform.conform_loss_report(tmp_project, "edl", tl, out)
    assert doc["target"] == "edl"
    # video-only drift is checked (grid-snapped -> all zero on this timeline)
    assert doc["frame_drift"]["checked"] is True
    assert doc["frame_drift"]["all_zero"] is True
    # exported cross-check counts 2 events vs 2 clips (no MISMATCH)
    joined = " ".join(doc["notes"])
    assert "2 event(s) vs timeline 2 video clip(s)" in joined
    assert "MISMATCH" not in joined


def test_ntsc_source_rate_drift_surfaces_on_edl(tmp_project, add_shot, make_take):
    t1, s1 = _register(tmp_project, add_shot, make_take, "S001")
    tl = _timeline([VideoClip(shot="S001", take=t1, source=s1, start_ms=0, duration_ms=2000)])
    out = export_edl(tmp_project, tl)
    # 23.976 material laid on a 24 grid: exact grid drift must be reported
    doc = conform.conform_loss_report(tmp_project, "edl", tl, out,
                                      source_rates={s1: "23.976"})
    mism = doc["frame_drift"]["rate_mismatch"]
    assert mism and mism[0]["source_rate"] == "24000/1001"
    assert Fraction(mism[0]["grid_drift_ms_over_clip"]) == Fraction(2, 1)  # 2000ms*0.1%
