"""FP Loop C — manju.media-technical-profile/v1 honest technical facts.

Two layers, red-first:

* a PURE unit matrix over :func:`normalize_probe_document` driven entirely by
  hand-written ffprobe JSON dicts (roadmap §15.2/§15.3/§15.4 subset) — no media
  needed. Every axis is verbatim-or-``"unknown"``; nothing is ever guessed;
* a small real-ffmpeg integration subset (tiny lavfi clips) that proves the same
  facts survive a genuine encode→ffprobe round-trip, plus colour honesty on an
  untagged clip.

Plus: content-addressed write/read (tamper/malformed/delete → inert), the
grep-pin that the derived report is never a build/authorization input, and
byte-for-byte determinism of the document + digest.

Skips with evidence (env ffmpeg 6.1): a ffprobe-READABLE display-matrix rotation
could not be stamped by this ffmpeg (`-display_rotation` and `-metadata rotate=`
both round-trip to null side_data), so rotation is pinned at the UNIT level only
(``test_rotation_*``); the integration result is never faked.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.hashing import hash_file
from manju.media.technical_profile import (
    SCHEMA,
    normalize_probe_document as N,
    read_profile,
    report_path,
    technical_profile,
    validate_profile,
    write_profile,
)


# --------------------------------------------------------------- hand-written ffprobe JSON


def _vid(**over) -> dict:
    """A minimal, valid video stream dict with sensible defaults; override any key."""
    base = {
        "index": 0, "codec_type": "video", "codec_name": "h264",
        "profile": "High", "level": 40,
        "width": 1920, "height": 1080, "coded_width": 1920, "coded_height": 1080,
        "r_frame_rate": "24/1", "avg_frame_rate": "24/1",
        "pix_fmt": "yuv420p", "sample_aspect_ratio": "1:1",
        "display_aspect_ratio": "16:9", "field_order": "progressive",
        "nb_frames": "24", "duration": "1.0",
        "disposition": {"default": 1, "forced": 0}, "tags": {"language": "und"},
    }
    base.update(over)
    return base


def _aud(**over) -> dict:
    base = {
        "index": 1, "codec_type": "audio", "codec_name": "aac",
        "sample_rate": "48000", "sample_fmt": "fltp", "channels": 2,
        "channel_layout": "stereo", "disposition": {"default": 1, "forced": 0},
    }
    base.update(over)
    return base


def _probe(*streams, **fmt) -> dict:
    f = {"format_name": "mov,mp4,m4a,3gp", "bit_rate": "2000000", "nb_streams": len(streams)}
    f.update(fmt)
    return {"streams": list(streams), "format": f}


def _codes(doc) -> set[str]:
    return {d["code"] for d in doc["diagnostics"]}


# ============================================================ §15.2 picture


def test_portrait_geometry():
    doc = N(_probe(_vid(width=1080, height=1920, coded_width=1080, coded_height=1920,
                        display_aspect_ratio="9:16")))
    p = doc["facts"]["picture"]
    assert p["coded_width"] == 1080 and p["coded_height"] == 1920
    assert p["display_aspect_ratio"]["normalized"] == {"num": 9, "den": 16}
    assert doc["schema"] == SCHEMA


def test_square_geometry():
    doc = N(_probe(_vid(width=512, height=512, coded_width=512, coded_height=512,
                        display_aspect_ratio="1:1")))
    p = doc["facts"]["picture"]
    assert p["coded_width"] == p["coded_height"] == 512


def test_rotation_90_via_display_matrix():
    doc = N(_probe(_vid(side_data_list=[
        {"side_data_type": "Display Matrix", "rotation": 90}])))
    assert doc["facts"]["picture"]["rotation"] == 90


def test_rotation_via_rotate_tag():
    doc = N(_probe(_vid(tags={"rotate": "270"})))
    assert doc["facts"]["picture"]["rotation"] == 270


def test_rotation_absent_is_zero_not_guessed():
    # no display matrix, no rotate tag → displayed as-is → 0 (an honest fact, not a guess)
    doc = N(_probe(_vid()))
    assert doc["facts"]["picture"]["rotation"] == 0


def test_anamorphic_sar_dar_consistent_no_diag():
    # SAR 4:3 on a 640x480 coded frame → expected DAR = (640/480)*(4/3) = 16/9.
    doc = N(_probe(_vid(width=640, height=480, coded_width=640, coded_height=480,
                        sample_aspect_ratio="4:3", display_aspect_ratio="16:9")))
    p = doc["facts"]["picture"]
    assert p["sample_aspect_ratio"] == {"raw": "4:3", "normalized": {"num": 4, "den": 3}}
    assert p["display_aspect_ratio"]["normalized"] == {"num": 16, "den": 9}
    assert "DAR_SAR_GEOMETRY_MISMATCH" not in _codes(doc)


def test_anamorphic_dar_mismatch_diag_exact():
    # same 640x480 + SAR 4:3 but DAR mislabelled 4:3 → exact (tol 0) mismatch.
    doc = N(_probe(_vid(width=640, height=480, coded_width=640, coded_height=480,
                        sample_aspect_ratio="4:3", display_aspect_ratio="4:3")))
    mm = [d for d in doc["diagnostics"] if d["code"] == "DAR_SAR_GEOMETRY_MISMATCH"]
    assert len(mm) == 1
    assert mm[0]["expected_display_aspect_ratio"] == {"num": 16, "den": 9}
    assert mm[0]["display_aspect_ratio"] == {"num": 4, "den": 3}
    assert mm[0]["sample_aspect_ratio"] == {"num": 4, "den": 3}


def test_yuv420p_8bit():
    p = N(_probe(_vid(pix_fmt="yuv420p")))["facts"]["picture"]
    assert p["pix_fmt"] == "yuv420p"
    assert p["bit_depth"] == 8
    assert p["chroma_subsampling"] == "4:2:0"
    assert p["has_alpha"] is False


def test_yuv420p10le_10bit():
    p = N(_probe(_vid(pix_fmt="yuv420p10le")))["facts"]["picture"]
    assert p["bit_depth"] == 10
    assert p["chroma_subsampling"] == "4:2:0"


def test_yuv422p_8bit_422():
    p = N(_probe(_vid(pix_fmt="yuv422p")))["facts"]["picture"]
    assert p["bit_depth"] == 8
    assert p["chroma_subsampling"] == "4:2:2"


def test_bits_per_raw_sample_overrides_pixfmt_table():
    # an explicit bits_per_raw_sample is verbatim truth; it wins over the table.
    p = N(_probe(_vid(pix_fmt="yuv420p", bits_per_raw_sample="10")))["facts"]["picture"]
    assert p["bit_depth"] == 10


def test_unknown_pixfmt_bit_depth_and_chroma_unknown():
    p = N(_probe(_vid(pix_fmt="p010le")))["facts"]["picture"]  # not in the COMMON table
    assert p["bit_depth"] == "unknown"
    assert p["chroma_subsampling"] == "unknown"
    assert p["has_alpha"] == "unknown"


def test_alpha_detected_for_rgba():
    p = N(_probe(_vid(pix_fmt="rgba")))["facts"]["picture"]
    assert p["has_alpha"] is True


def test_field_order_verbatim_or_unknown():
    assert N(_probe(_vid(field_order="tt")))["facts"]["picture"]["field_order"] == "tt"
    # a bogus value is NOT trusted
    assert N(_probe(_vid(field_order="weird")))["facts"]["picture"]["field_order"] == "unknown"
    v = _vid()
    v.pop("field_order")
    assert N(_probe(v))["facts"]["picture"]["field_order"] == "unknown"


# ============================================================ §15.3 colour


def test_missing_color_tags_all_unknown_and_color_unknown_diag():
    v = _vid()  # base has no colour keys at all
    doc = N(_probe(v))
    c = doc["facts"]["color"]
    assert c["primaries"] == c["transfer"] == c["matrix"] == c["range"] == "unknown"
    assert c["color_known"] is False
    diag = [d for d in doc["diagnostics"] if d["code"] == "COLOR_UNKNOWN"]
    assert len(diag) == 1
    assert diag[0]["severity"] == "advisory"  # advisory, never blocking
    assert set(diag[0]["missing"]) == {"primaries", "transfer", "matrix", "range"}


def test_full_color_tags_known_no_diag():
    doc = N(_probe(_vid(color_primaries="bt709", color_transfer="bt709",
                        color_space="bt709", color_range="tv")))
    c = doc["facts"]["color"]
    assert c["color_known"] is True
    assert (c["primaries"], c["transfer"], c["matrix"], c["range"]) == \
        ("bt709", "bt709", "bt709", "tv")
    assert "COLOR_UNKNOWN" not in _codes(doc)


def test_full_vs_limited_range_recorded_verbatim():
    assert N(_probe(_vid(color_range="pc")))["facts"]["color"]["range"] == "pc"    # full
    assert N(_probe(_vid(color_range="tv")))["facts"]["color"]["range"] == "tv"    # limited


def test_partial_color_tags_still_unknown_gate():
    # three of four present → color_known must still be False (no guessing the 4th).
    doc = N(_probe(_vid(color_primaries="bt2020", color_transfer="smpte2084",
                        color_space="bt2020nc")))  # range absent
    c = doc["facts"]["color"]
    assert c["color_known"] is False
    assert c["range"] == "unknown"
    assert [d for d in doc["diagnostics"] if d["code"] == "COLOR_UNKNOWN"][0]["missing"] == ["range"]


# ============================================================ §15.4 audio


def test_no_audio_stream_is_null():
    doc = N(_probe(_vid()))  # video only
    assert doc["facts"]["audio"] is None


def test_mono_layout():
    a = N(_probe(_vid(), _aud(channels=1, channel_layout="mono")))["facts"]["audio"]
    assert a["channels"] == 1 and a["channel_layout"] == "mono"


def test_stereo_layout():
    a = N(_probe(_vid(), _aud(channels=2, channel_layout="stereo")))["facts"]["audio"]
    assert a["channels"] == 2 and a["channel_layout"] == "stereo"


def test_sample_rate_44100_vs_48000():
    a44 = N(_probe(_vid(), _aud(sample_rate="44100")))["facts"]["audio"]
    a48 = N(_probe(_vid(), _aud(sample_rate="48000")))["facts"]["audio"]
    assert a44["sample_rate"] == 44100 and a48["sample_rate"] == 48000


def test_audio_bit_depth_from_sample_fmt_and_verbatim():
    # s16 → 16 via the sample-fmt table
    assert N(_probe(_vid(), _aud(sample_fmt="s16")))["facts"]["audio"]["bit_depth"] == 16
    # fltp float storage → 32 (honest storage width)
    assert N(_probe(_vid(), _aud(sample_fmt="fltp")))["facts"]["audio"]["bit_depth"] == 32
    # explicit bits_per_raw_sample is verbatim truth
    assert N(_probe(_vid(), _aud(sample_fmt="s32", bits_per_raw_sample="24")))[
        "facts"]["audio"]["bit_depth"] == 24


def test_unknown_channel_layout_is_unknown():
    v = _aud()
    v.pop("channel_layout")
    assert N(_probe(_vid(), v))["facts"]["audio"]["channel_layout"] == "unknown"


# ============================================================ §15.1 time / rate


def test_ntsc_rate_exact_rational_and_edit_grid_drift():
    doc = N(_probe(_vid(r_frame_rate="30000/1001", avg_frame_rate="30000/1001",
                        duration="10.01")), edit_fps=30)
    t = doc["facts"]["time"]
    assert t["rate"] == {"num": 30000, "den": 1001}   # EXACT rational, never a float
    assert t["rate_mode"] == "cfr"
    assert t["duration_ms"] == 10010
    assert t["range_semantics"] == "start_inclusive_end_exclusive"
    d = [x for x in doc["diagnostics"] if x["code"] == "RATE_UNREPRESENTABLE_ON_EDIT_GRID"]
    assert len(d) == 1
    diag = d[0]
    assert diag["edit_fps"] == 30
    assert diag["true_rate"] == {"num": 30000, "den": 1001}
    # one edit-frame of drift first accrues at exactly 1_000_000/30 ms ≈ 33.333 s
    assert diag["one_frame_drift_at_ms"] == {"num": 100000, "den": 3}
    # over a 10.01 s clip the int-30 grid drifts exactly 1001/100 ms = 10.01 ms (0.1%)
    assert diag["grid_drift_ms_over_clip"] == {"num": 1001, "den": 100}
    assert diag["grid_drift_ms_over_clip_approx"] == 10.01


def test_matching_integer_rate_has_no_drift_diag():
    doc = N(_probe(_vid(r_frame_rate="30/1", avg_frame_rate="30/1")), edit_fps=30)
    assert "RATE_UNREPRESENTABLE_ON_EDIT_GRID" not in _codes(doc)


def test_non_ntsc_rate_mismatch_still_drifts():
    # 24 fps source on a 30 fps edit grid is representable by neither — drift reported.
    doc = N(_probe(_vid(r_frame_rate="24/1", avg_frame_rate="24/1")), edit_fps=30)
    assert "RATE_UNREPRESENTABLE_ON_EDIT_GRID" in _codes(doc)


def test_no_edit_fps_no_drift_diag():
    doc = N(_probe(_vid(r_frame_rate="30000/1001", avg_frame_rate="30000/1001")))
    assert "RATE_UNREPRESENTABLE_ON_EDIT_GRID" not in _codes(doc)


def test_vfr_suspected_when_r_and_avg_differ():
    doc = N(_probe(_vid(r_frame_rate="30/1", avg_frame_rate="24000/1001")))
    assert doc["facts"]["time"]["rate_mode"] == "vfr_suspected"
    v = [d for d in doc["diagnostics"] if d["code"] == "VFR_SUSPECTED"]
    assert len(v) == 1
    assert v[0]["r_frame_rate"] == "30/1" and v[0]["avg_frame_rate"] == "24000/1001"


def test_odd_decimal_rate_is_unknown_never_coerced():
    # a decimal that matches no standard rate is REFUSED, never snapped to 24.
    doc = N(_probe(_vid(r_frame_rate="23.5", avg_frame_rate="23.5")))
    assert doc["facts"]["time"]["rate"] == "unknown"
    assert doc["facts"]["time"]["r_frame_rate"] == "23.5"   # raw kept verbatim


def test_degenerate_rate_zero_is_unknown():
    doc = N(_probe(_vid(r_frame_rate="0/0", avg_frame_rate="0/0")))
    assert doc["facts"]["time"]["rate"] == "unknown"
    assert doc["facts"]["time"]["rate_mode"] == "unknown"


# ============================================================ §5.6 container


def test_container_per_stream_disposition_and_language():
    doc = N(_probe(
        _vid(tags={"language": "eng"}, disposition={"default": 1, "forced": 0}),
        _aud(tags={"language": "jpn"}, disposition={"default": 1, "forced": 1}),
    ))
    con = doc["facts"]["container"]
    assert con["format_name"] == "mov,mp4,m4a,3gp"
    assert con["bit_rate"] == 2000000
    assert con["nb_streams"] == 2
    assert con["streams"][0] == {"index": 0, "codec_type": "video",
                                 "disposition": {"default": 1, "forced": 0}, "language": "eng"}
    assert con["streams"][1]["disposition"] == {"default": 1, "forced": 1}
    assert con["streams"][1]["language"] == "jpn"


# ============================================================ empty / robustness


def test_empty_probe_document_is_all_unknown_not_crash():
    doc = N({})
    t = doc["facts"]["time"]
    assert t["rate"] == "unknown" and t["duration_ms"] == "unknown"
    assert doc["facts"]["picture"]["pix_fmt"] == "unknown"
    assert doc["facts"]["audio"] is None
    assert doc["schema"] == SCHEMA


# ============================================================ determinism


def test_deterministic_same_input_byte_identical():
    probe = _probe(_vid(r_frame_rate="30000/1001", avg_frame_rate="30000/1001"), _aud())
    a = N(probe, edit_fps=30)
    b = N(probe, edit_fps=30)
    assert json.dumps(a) == json.dumps(b)          # byte-identical document
    assert a["profile_digest"] == b["profile_digest"]


def test_digest_covers_facts_only_not_header():
    probe = _probe(_vid())
    a = N(probe, source_media_hash="sha256:" + "a" * 64, source_ref="x")
    b = N(probe, source_media_hash="sha256:" + "b" * 64, source_ref="y")
    assert a["profile_digest"] == b["profile_digest"]   # header is NOT in the digest
    # but any real fact change moves the digest
    c = N(_probe(_vid(pix_fmt="yuv422p")))
    assert c["profile_digest"] != a["profile_digest"]


# ============================================================ write / read / delete (derived)


def test_profile_round_trips_content_addressed(tmp_project, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"exact-bytes-A")
    h = hash_file(media)
    doc = N(_probe(_vid()), source_media_hash=h, source_ref="media/imports/clip.mp4")
    path = write_profile(tmp_project, doc)
    assert path.exists()
    assert h.split(":")[-1] in path.name           # content-addressed filename
    got = read_profile(tmp_project, h)
    assert got is not None
    assert got["header"]["source_media_hash"] == h


def test_tampered_misfiled_profile_reads_none(tmp_project):
    h = "sha256:" + "a" * 64
    other = "sha256:" + "b" * 64
    doc = N(_probe(_vid()), source_media_hash=h)
    write_profile(tmp_project, doc)                 # stored under h
    # copy the doc (still binding h) into the file named for a DIFFERENT hash
    misfiled = report_path(tmp_project, other)
    misfiled.parent.mkdir(parents=True, exist_ok=True)
    misfiled.write_text(json.dumps(doc), encoding="utf-8")
    assert read_profile(tmp_project, other) is None  # content-address mismatch → rejected
    # validation names the mismatch as a blocking diagnostic
    assert any(d["code"] == "PROFILE_SOURCE_HASH_MISMATCH"
               for d in validate_profile(doc, other))


def test_malformed_profile_reads_none(tmp_project):
    h = "sha256:" + "c" * 64
    p = report_path(tmp_project, h)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert read_profile(tmp_project, h) is None
    p.write_text('{"schema": "manju.wrong/v1", "header": {}, "facts": {}}', encoding="utf-8")
    assert read_profile(tmp_project, h) is None


def test_deleting_profile_is_inert(tmp_project, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"bytes-A")
    h = hash_file(media)
    doc = N(_probe(_vid()), source_media_hash=h)
    path = write_profile(tmp_project, doc)
    before = media.read_bytes()
    path.unlink()                                   # delete the derived projection
    assert media.read_bytes() == before             # source untouched
    assert read_profile(tmp_project, h) is None     # gone, and re-derivable


def test_technical_reports_not_a_build_or_auth_input():
    """The derived report dir must never be read by build/core/providers/runtime."""
    from manju.media import technical_profile as tp
    root = Path(tp.__file__).resolve().parents[1]   # src/manju
    hits: list[str] = []
    for sub in ("build", "core", "providers", "runtime"):
        out = subprocess.run(
            ["grep", "-rn", "reports/technical", str(root / sub),
             "--include=*.py", "--exclude-dir=__pycache__"],
            capture_output=True, text=True).stdout
        hits += [ln for ln in out.splitlines() if ln.strip()]
    assert hits == [], f"a build/authorization path reads the derived profile: {hits}"


# ============================================================ real-ffmpeg integration subset


_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe required")
class TestRealFfmpegIntegration:
    """Tiny lavfi clips prove the pure facts survive a genuine encode→ffprobe
    round-trip. See module docstring for the rotation SKIPPED_WITH_EVIDENCE note."""

    @staticmethod
    def _gen(args: list[str], dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        *args, str(dest)], check=True, capture_output=True, timeout=120)
        return dest

    def test_portrait_420_8bit_with_color_tags(self, tmp_project):
        dest = self._gen(
            ["-f", "lavfi", "-i", "testsrc=duration=0.3:size=108x192:rate=24",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3:sample_rate=48000",
             "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
             "-colorspace", "bt709", "-color_range", "tv",
             "-c:v", "libx264", "-c:a", "aac", "-ac", "2"],
            tmp_project.root / "media" / "imports" / "tagged.mp4")
        doc = technical_profile(dest, source_ref="media/imports/tagged.mp4")
        f = doc["facts"]
        # picture: portrait 4:2:0 8-bit, honestly read back
        assert f["picture"]["coded_width"] == 108 and f["picture"]["coded_height"] == 192
        assert f["picture"]["pix_fmt"] == "yuv420p"
        assert f["picture"]["bit_depth"] == 8
        assert f["picture"]["chroma_subsampling"] == "4:2:0"
        # colour: all four tags present → color_known true, recorded verbatim
        assert f["color"]["color_known"] is True
        assert f["color"]["primaries"] == "bt709"
        assert f["color"]["range"] in ("tv", "limited")
        assert "COLOR_UNKNOWN" not in _codes(doc)
        # audio stereo 48k
        assert f["audio"] is not None
        assert f["audio"]["sample_rate"] == 48000
        assert f["audio"]["channel_layout"] == "stereo"
        # header binds the exact bytes; source_ref is project-relative (no abs path)
        assert doc["header"]["source_media_hash"] == hash_file(dest)
        assert doc["header"]["source_ref"] == "media/imports/tagged.mp4"
        assert not doc["header"]["source_ref"].startswith("/")

    def test_untagged_clip_color_unknown_honesty(self, tmp_project):
        dest = self._gen(
            ["-f", "lavfi", "-i", "testsrc=duration=0.3:size=128x72:rate=24",
             "-pix_fmt", "yuv420p", "-c:v", "libx264"],
            tmp_project.root / "media" / "imports" / "untagged.mp4")
        doc = technical_profile(dest, source_ref="media/imports/untagged.mp4")
        c = doc["facts"]["color"]
        assert c["color_known"] is False
        assert c["primaries"] == "unknown" and c["transfer"] == "unknown"
        assert c["matrix"] == "unknown" and c["range"] == "unknown"
        assert "COLOR_UNKNOWN" in _codes(doc)
        assert doc["facts"]["audio"] is None       # video-only clip

    def test_write_read_round_trip_real_media(self, tmp_project):
        dest = self._gen(
            ["-f", "lavfi", "-i", "testsrc=duration=0.2:size=64x64:rate=25",
             "-pix_fmt", "yuv420p", "-c:v", "libx264"],
            tmp_project.root / "media" / "imports" / "sq.mp4")
        doc = technical_profile(dest, source_ref="media/imports/sq.mp4")
        write_profile(tmp_project, doc)
        got = read_profile(tmp_project, hash_file(dest))
        assert got is not None
        assert got["profile_digest"] == doc["profile_digest"]

    def test_unreadable_media_raises_media_error(self, tmp_project):
        from manju.media.ffmpeg import MediaError
        bad = tmp_project.root / "media" / "imports" / "bad.mp4"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"not a real media file at all")
        with pytest.raises(MediaError):
            technical_profile(bad)
