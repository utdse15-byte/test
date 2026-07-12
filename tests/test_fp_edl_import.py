"""FP Loop Y4 — CMX3600 EDL IMPORT-PLAN (the analysis half S2 declined).

Red-first (§20): S2 shipped an EDL WRITER only ("there is no EDL import path
this loop"). This suite pins the READ-ONLY analysis counterpart BEFORE the
implementation is trusted, on the same house import-plan pattern as
`exporters/fcpxml_import.py` (W2) / `exporters/openclap/import_plan.py` — PLAN,
never apply.

Every golden EDL string below is the COMMITTED S2 writer's exact output (the
bytes pinned in `tests/test_fp_edl.py` at HEAD) OR a hand-built CMX3600 document
whose frame math is spelled out from first principles via
:mod:`manju.core.timebase` — so the test encodes the SPEC, not a copy of the
importer's output. The importer module (`exporters/edl_import.py`) is never
allowed to write, and `edl.py` (a parallel loop owns it) is never imported here.

What is pinned:

* round-trip floor vs the committed S2 goldens — the 3-clip NDF document parses
  back to exactly the writer's windows / dissolve candidate / record+source TC,
  frames derived through the document's FCM (NON-DROP);
* the drop-frame document — FCM: DROP FRAME + ';' frames separator → DF frame
  math (30000/1001), rate inferred honestly;
* MANJU notes surfaced verbatim; the degraded-transition note rides through;
* generic channel tokens — V-only, and A1/A2/AA/B audio events parsed without a
  crash (the Y1 audio extension landing in parallel);
* unknown lines counted, never a crash;
* needs_relink for every (unresolvable) CMX reel/clip source;
* determinism, digest tamper-evidence, CJK clip names, and a no-apply/no-write
  surface + no-writes tree-hash proof.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from manju.core.timebase import Rate, Timecode
from manju.exporters.edl_import import (
    PLAN_SCHEMA,
    EdlImportError,
    parse_edl,
    plan_edl_import,
    verify_plan_digest,
)

R24 = Rate.from_fraction(24, 1)
R30DF = Rate.from_fraction(30000, 1001)


# --------------------------------------------------------------------------- #
# committed S2 goldens (verbatim bytes; see tests/test_fp_edl.py at HEAD)      #
# --------------------------------------------------------------------------- #

# test_golden_three_clip_edl_exact_bytes: clip1 --xfade_fade(500ms clean
# dissolve)--> clip2 --fade(300ms degraded)--> clip3, @24 NDF, start 01:00:00:00.
GOLDEN_NDF = (
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

# test_drop_frame_ntsc_header_and_separator: a single TA clip @29.97 DF, 2000ms.
GOLDEN_DF = (
    "TITLE:   X\n"
    "FCM: DROP FRAME\n"
    "001  TA       V    C        00:00:00;00 00:00:02;00 01:00:00;00 01:00:02;00\n"
    "* FROM CLIP NAME: TA\n"
)


# --------------------------------------------------------------------------- #
# 1. round-trip floor — the committed 3-clip NDF golden                        #
# --------------------------------------------------------------------------- #


def test_roundtrip_ndf_windows_recovered_exactly():
    parsed = parse_edl(GOLDEN_NDF)
    plan = plan_edl_import(parsed, rate=R24)

    assert plan["schema"] == PLAN_SCHEMA
    assert plan["title"] == "DEMO EDL"
    assert plan["frame_code_mode"] == "NON-DROP FRAME"
    assert plan["drop_frame"] is False

    windows = plan["windows"]
    # three clips: the zero-record-duration "C" dissolve-freeze row is NOT a window
    assert len(windows) == 3

    # frame math @24 NDF, start 01:00:00:00 == frame 86400 (spelled from timebase):
    base = Timecode(1, 0, 0, 0, R24, False).to_frames()
    assert base == 86400
    # clip1: src 0..48, rec 86400..86448
    w1 = windows[0]
    assert (w1["src_in_frames"], w1["src_out_frames"]) == (0, 48)
    assert (w1["rec_in_frames"], w1["rec_out_frames"]) == (86400, 86448)
    assert w1["duration_frames"] == 48
    assert w1["clip_name"] == "TAKEALPHA0001"
    # clip2 (the dissolve incoming): src 3..51, rec 86448..86496
    w2 = windows[1]
    assert (w2["src_in_frames"], w2["src_out_frames"]) == (3, 51)
    assert (w2["rec_in_frames"], w2["rec_out_frames"]) == (86448, 86496)
    assert w2["clip_name"] == "TAKEBETA0002"
    # clip3: src 0..24, rec 86496..86520
    w3 = windows[2]
    assert (w3["src_in_frames"], w3["src_out_frames"]) == (0, 24)
    assert (w3["rec_in_frames"], w3["rec_out_frames"]) == (86496, 86520)
    assert w3["clip_name"] == "TAKEGAMMA0003"


def test_roundtrip_ndf_record_continuity_no_gaps():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    recs = [(w["rec_in_frames"], w["rec_out_frames"]) for w in plan["windows"]]
    for (_, out_n), (in_n1, _) in zip(recs, recs[1:]):
        assert out_n == in_n1, f"discontinuity {out_n} != {in_n1}"


def test_roundtrip_ndf_source_length_matches_record():
    # a cut-list invariant the writer guarantees: source span == record span.
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    for w in plan["windows"]:
        assert w["source_duration_frames"] == w["duration_frames"]


def test_roundtrip_ndf_dissolve_is_xfade_fade_candidate():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    dissolves = [t for t in plan["transitions"]
                 if t["disposition"] == "xfade_fade_candidate"]
    assert len(dissolves) == 1
    d = dissolves[0]
    assert d["proposed_type"] == "xfade_fade"
    assert d["duration_frames"] == 12          # 500ms @24 -> 12 frames
    assert d["at_event"] == 2
    assert d["from_clip"] == "TAKEALPHA0001"    # outgoing
    assert d["to_clip"] == "TAKEBETA0002"       # incoming


def test_manju_note_surfaced_verbatim():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    notes = plan["manju_notes"]
    assert len(notes) == 1
    assert notes[0] == (
        "* MANJU: transition 'fade' (300ms) at S002 approximated as hard cut "
        "(no clean CMX3600 dissolve mapping)")


# --------------------------------------------------------------------------- #
# 2. drop-frame document                                                       #
# --------------------------------------------------------------------------- #


def test_drop_frame_document_math_and_inference():
    parsed = parse_edl(GOLDEN_DF)
    # rate not supplied: DF ⇒ inferred 30000/1001, flagged honestly
    plan = plan_edl_import(parsed)
    assert plan["frame_code_mode"] == "DROP FRAME"
    assert plan["drop_frame"] is True
    assert plan["edit_rate"] == "30000/1001"
    assert plan["rate_assumed"] is True

    w = plan["windows"][0]
    # record 01:00:00;00 == frame 107892 at 30000/1001 DF (from timebase):
    assert Timecode(1, 0, 0, 0, R30DF, True).to_frames() == 107892
    assert w["rec_in_frames"] == 107892
    # 2000ms == 60 frames record duration; source zero-based
    assert w["duration_frames"] == 60
    assert w["src_in_frames"] == 0
    assert w["src_out_frames"] == 60
    # TCs echo their drop separator verbatim
    assert w["rec_in_tc"] == "01:00:00;00"


def test_drop_frame_explicit_rate_agrees_with_inference():
    got_inferred = plan_edl_import(parse_edl(GOLDEN_DF))
    got_explicit = plan_edl_import(parse_edl(GOLDEN_DF), rate=R30DF)
    assert got_inferred["windows"][0]["rec_in_frames"] == \
        got_explicit["windows"][0]["rec_in_frames"]
    assert got_explicit["rate_assumed"] is False


# --------------------------------------------------------------------------- #
# 3. generic channel tokens — V-only + A1/A2/AA/B audio (Y1 lands in parallel) #
# --------------------------------------------------------------------------- #


_AUDIO_EDL = (
    "TITLE:   AUD\n"
    "FCM: NON-DROP FRAME\n"
    "001  REELV    V    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00\n"
    "* FROM CLIP NAME: PICTURE\n"
    "002  REELA    A    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00\n"
    "* FROM CLIP NAME: DIALOG\n"
    "003  REELA2   A2   C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00\n"
    "* FROM CLIP NAME: MUSIC\n"
    "004  REELAA   AA   C        00:00:00:00 00:00:02:00 01:00:02:00 01:00:04:00\n"
    "005  REELB    B    C        00:00:00:00 00:00:02:00 01:00:04:00 01:00:06:00\n"
)


def test_generic_channels_video_and_audio_split():
    plan = plan_edl_import(parse_edl(_AUDIO_EDL), rate=R24)
    # V and B events are picture windows; A / A2 / AA are audio-only events.
    win_channels = [w["channel"] for w in plan["windows"]]
    assert "V" in win_channels and "B" in win_channels
    aud_channels = [a["channel"] for a in plan["audio_events"]]
    assert set(aud_channels) == {"A", "A2", "AA"}
    # audio is surfaced verbatim, no Manju bus fabricated (CMX flat A-channels)
    for a in plan["audio_events"]:
        assert "bus" not in a
        assert a["disposition"] == "cmx_audio_channel"


def test_v_only_document_has_no_audio_events():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    assert plan["audio_events"] == []


# --------------------------------------------------------------------------- #
# 4. unknown lines counted, never a crash                                      #
# --------------------------------------------------------------------------- #


def test_unknown_lines_counted_never_crash():
    doc = (
        "TITLE:   MESS\n"
        "FCM: NON-DROP FRAME\n"
        "AUD  3   AA    V    C\n"                       # split-audio header junk
        "SOME GARBAGE THAT IS NOT A CMX ROW\n"
        "001  REEL1    V    C        00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00\n"
        ">>> SOURCE FILE: whatever.mov\n"
        "* FROM CLIP NAME: CLIP1\n"
    )
    plan = plan_edl_import(parse_edl(doc), rate=R24)
    assert len(plan["windows"]) == 1
    assert plan["unknown_rows"] >= 2            # the two non-CMX garbage lines
    # comments beginning with '*' are recognized, never counted as unknown
    assert plan["windows"][0]["clip_name"] == "CLIP1"


def test_malformed_event_row_does_not_crash():
    doc = (
        "TITLE:   BAD\n"
        "FCM: NON-DROP FRAME\n"
        "001  REEL1    V    C        NOT:A:TIME:CODE 00:00:01:00\n"   # too few / bad TCs
    )
    plan = plan_edl_import(parse_edl(doc), rate=R24)
    # not parseable as an event → counted, no window, no exception
    assert plan["windows"] == []
    assert plan["unknown_rows"] >= 1


# --------------------------------------------------------------------------- #
# 5. needs_relink — every CMX reel/clip source is unresolvable                 #
# --------------------------------------------------------------------------- #


def test_every_source_is_needs_relink():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    relinks = {r["source"] for r in plan["needs_relink"]}
    # three distinct clip sources, each pointing at the relink machinery
    assert relinks == {"TAKEALPHA0001", "TAKEBETA0002", "TAKEGAMMA0003"}
    for r in plan["needs_relink"]:
        assert r["op"] == "needs_relink"
        assert "manju relink" in r["relink_via"]
    for w in plan["windows"]:
        assert w["media_status"] == "needs_relink"


def test_needs_relink_deduped_by_source():
    doc = (
        "TITLE:   DUP\n"
        "FCM: NON-DROP FRAME\n"
        "001  REEL1    V    C        00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00\n"
        "* FROM CLIP NAME: SAME\n"
        "002  REEL1    V    C        00:00:01:00 00:00:02:00 01:00:01:00 01:00:02:00\n"
        "* FROM CLIP NAME: SAME\n"
    )
    plan = plan_edl_import(parse_edl(doc), rate=R24)
    assert len(plan["windows"]) == 2
    assert len(plan["needs_relink"]) == 1       # deduped


# --------------------------------------------------------------------------- #
# 6. unsupported transitions (W / K) degrade honestly, never faked             #
# --------------------------------------------------------------------------- #


def test_wipe_event_is_unsupported_not_faked():
    doc = (
        "TITLE:   WIPE\n"
        "FCM: NON-DROP FRAME\n"
        "001  REEL1    V    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00\n"
        "002  REEL1    V    C        00:00:02:00 00:00:02:00 01:00:02:00 01:00:02:00\n"
        "002  REEL2    V    W001 025 00:00:00:00 00:00:02:00 01:00:02:00 01:00:04:00\n"
    )
    plan = plan_edl_import(parse_edl(doc), rate=R24)
    unsupported = [t for t in plan["transitions"]
                   if t["disposition"] == "unsupported_transition"]
    assert len(unsupported) == 1
    assert unsupported[0]["edit_type"] == "W001"
    assert "proposed_type" not in unsupported[0]   # never faked into an xfade
    # no xfade candidate fabricated from a wipe
    assert not any(t["disposition"] == "xfade_fade_candidate"
                   for t in plan["transitions"])


# --------------------------------------------------------------------------- #
# 7. CJK clip name — surfaced verbatim                                         #
# --------------------------------------------------------------------------- #


def test_cjk_clip_name_verbatim():
    doc = (
        "TITLE:   PIAN\n"
        "FCM: NON-DROP FRAME\n"
        "001  JINGTOU   V    C        00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00\n"
        "* FROM CLIP NAME: 镜头一_雨夜\n"
    )
    plan = plan_edl_import(parse_edl(doc), rate=R24)
    assert plan["windows"][0]["clip_name"] == "镜头一_雨夜"
    assert plan["needs_relink"][0]["source"] == "镜头一_雨夜"


# --------------------------------------------------------------------------- #
# 8. digest tamper-evidence (conform-loss precedent, W2 idiom)                 #
# --------------------------------------------------------------------------- #


def test_digest_present_and_verifies():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    assert isinstance(plan["digest"], str) and plan["digest"].startswith("sha256:")
    assert verify_plan_digest(plan) is plan


def test_tampered_plan_rejected():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    plan["windows"][0]["rec_in_frames"] = 999999
    with pytest.raises(EdlImportError):
        verify_plan_digest(plan)


def test_dropped_digest_rejected():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    del plan["digest"]
    with pytest.raises(EdlImportError):
        verify_plan_digest(plan)


def test_wrong_schema_rejected():
    plan = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    from manju.core.hashing import hash_value
    facts = {k: v for k, v in plan.items() if k != "digest"}
    facts["schema"] = "manju.not-edl/v1"
    facts["digest"] = hash_value({k: v for k, v in facts.items() if k != "digest"})
    with pytest.raises(EdlImportError):
        verify_plan_digest(facts)


# --------------------------------------------------------------------------- #
# 9. determinism                                                               #
# --------------------------------------------------------------------------- #


def test_determinism_byte_identical():
    a = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    b = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    assert a == b


# --------------------------------------------------------------------------- #
# 10. no-apply / no-write surface + no-writes tree hash                        #
# --------------------------------------------------------------------------- #


def test_module_exposes_no_apply_or_write_surface():
    import manju.exporters.edl_import as mod

    for name in mod.__all__:
        low = name.lower()
        assert "apply" not in low
        assert "write" not in low
        assert "export" not in low


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        st = p.stat()
        h.update(str(p.relative_to(root)).encode())
        h.update(str(st.st_mtime_ns).encode())
        if p.is_file():
            h.update(str(st.st_size).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def test_import_plan_never_writes(tmp_path):
    edl_path = tmp_path / "cut.edl"
    edl_path.write_text(GOLDEN_NDF, encoding="utf-8")
    before = _tree_hash(tmp_path)
    for _ in range(3):
        parsed = parse_edl(edl_path)
        plan = plan_edl_import(parsed, rate=R24)
        verify_plan_digest(plan)
    after = _tree_hash(tmp_path)
    assert before == after, "import-plan must not write a single byte"


def test_parse_accepts_path_and_string_equivalently(tmp_path):
    edl_path = tmp_path / "cut.edl"
    edl_path.write_text(GOLDEN_NDF, encoding="utf-8")
    from_path = plan_edl_import(parse_edl(edl_path), rate=R24)
    from_str = plan_edl_import(parse_edl(GOLDEN_NDF), rate=R24)
    # source-independent fields agree (only the caller's source bytes differ)
    for key in ("windows", "transitions", "manju_notes", "needs_relink"):
        assert from_path[key] == from_str[key]
