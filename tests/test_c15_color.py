"""AI_IDE_15 WP6 — deterministic color analysis & shot matching (contract §10,
addendum ruling 7). Red-first, PIL-only (numpy is absent in this environment).

Proves:
* color stats (histogram / luma / white balance) are a PURE, deterministic
  function of the exact image bytes, bound to the input content hash;
* ffprobe color metadata (space / transfer / matrix / range) is recorded, and
  anything unidentifiable is UNKNOWN — never guessed;
* a reference comparison is bound to the reference's content hash and yields
  READ-ONLY single-variable suggestions (LUT / exposure / WB / curve);
* the histogram preview changes NO source/request digest (append-only, §10),
  and an adopted correction routes to the existing append-only repair-op path.
"""

from __future__ import annotations

import pytest

from manju.core.hashing import hash_file
from manju.qc import colorstats

from tests.fixtures.golden import GOLDEN_DIR
from tests.fixtures.golden.fake_reviewer import load_manifest

MANIFEST = load_manifest()
CASES = {c["id"]: c for c in MANIFEST["cases"]}


def _img(case_id):
    return GOLDEN_DIR / CASES[case_id]["path"]


# ------------------------------------------------------------- color stats


def test_color_stats_are_pure_and_hash_bound():
    p = _img("visual.scene.base")
    a = colorstats.color_stats(p)
    b = colorstats.color_stats(p)
    assert a == b  # deterministic
    assert a["input_sha256"] == hash_file(p)  # bound to the exact bytes
    # the histogram / luma / white-balance facts are present and well-shaped.
    assert len(a["histogram"]["r"]) == 256
    assert 0.0 <= a["luma_mean"] <= 255.0
    assert set(a["channel_mean"]) == {"r", "g", "b"}
    assert "white_balance" in a


def test_color_stats_differ_for_a_warm_vs_cool_frame():
    cool = colorstats.color_stats(_img("visual.scene.base"))          # cold light
    warm = colorstats.color_stats(_img("visual.scene.drift.lighting"))  # warm relight
    # the deterministic stats actually separate the two lighting states.
    assert cool["channel_mean"] != warm["channel_mean"]


# ----------------------------------------------------- color metadata (probe)


def test_color_metadata_untagged_is_unknown_never_guessed(tmp_path):
    # a PNG has no video color tags -> every field honestly UNKNOWN.
    meta = colorstats.color_metadata(_img("visual.scene.base"))
    assert meta["space"] == "UNKNOWN"
    assert meta["transfer"] == "UNKNOWN"
    assert meta["matrix"] == "UNKNOWN"
    assert meta["range"] == "UNKNOWN"


def test_color_metadata_reads_tagged_video(tmp_path):
    import subprocess

    clip = tmp_path / "tagged.mp4"
    # The fixture has to actually BE tagged. ffmpeg >= 7.1 no longer honours the
    # -color_trc/-color_primaries output options for libx264 (7.0.2 writes all
    # four axes; 7.1 and master write only colorspace+range), so building the
    # fixture that way made this test fail on a current ffmpeg as though the
    # PARSER were broken. The setparams filter stamps the frames themselves and
    # is honoured by every build tested — and by the pinned 6.1.1, since the
    # filter has existed since ffmpeg 4.3.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=blue:s=64x64:d=1:r=24",
         "-vf", "setparams=color_primaries=bt709:color_trc=bt709"
                ":colorspace=bt709:range=tv",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-color_range", "tv", str(clip)], check=True)
    meta = colorstats.color_metadata(clip)
    assert meta["transfer"] == "bt709"
    assert meta["matrix"] == "bt709"      # ffprobe color_space == matrix coefficients
    assert meta["range"] == "tv"
    assert meta["space"] == "bt709"       # primaries


def test_color_metadata_unreadable_is_unknown(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a media file")
    meta = colorstats.color_metadata(junk)
    assert set(meta.values()) == {"UNKNOWN"}


# ----------------------------------------------- reference comparison / suggest


def test_reference_comparison_is_hash_bound_and_read_only():
    ref = _img("visual.scene.base")          # canonical cool style frame
    cur = _img("visual.scene.drift.lighting")  # warm-drifted current frame
    result = colorstats.compare_to_reference(cur, ref)
    assert result["input_sha256"] == hash_file(cur)
    assert result["reference_sha256"] == hash_file(ref)  # bound to the ref bytes
    # read-only single-variable suggestions from a known vocabulary.
    assert result["suggestions"]
    allowed = {"lut", "exposure", "white_balance", "curve", "shot_match"}
    for s in result["suggestions"]:
        assert s["variable"] in allowed
        assert s["single_variable"] is True
    assert result["do_not_execute_automatically"] is True
    assert result["adopt_via"]["path"] == "repair_op"  # existing append-only path


def test_identical_frames_need_no_correction():
    ref = _img("visual.scene.base")
    result = colorstats.compare_to_reference(ref, ref)
    assert result["match"] is True
    assert result["suggestions"] == []


def test_preview_does_not_change_source_or_request_digest():
    # §10: 自动 histogram match 只允许作为 preview/建议,不能偷偷改变下游 request。
    cur = _img("visual.scene.drift.lighting")
    ref = _img("visual.scene.base")
    before = hash_file(cur)
    preview = colorstats.histogram_match_preview(cur, ref)
    after = hash_file(cur)
    assert before == after  # the source bytes are untouched by the preview
    assert preview["source_sha256"] == before
    assert preview["reference_sha256"] == hash_file(ref)
    assert preview["is_preview"] is True
    assert preview["mutates_source"] is False
    assert preview["mutates_downstream_request"] is False


def test_adoption_is_append_only_and_binds_input_and_reference(tmp_path):
    # §10: 校色输出 append-only 且绑定输入/参考 hash — the adoption descriptor points
    # at the existing repair-op path and carries BOTH bound hashes; it never
    # overwrites the take (proposal only).
    cur = _img("visual.scene.drift.lighting")
    ref = _img("visual.scene.base")
    result = colorstats.compare_to_reference(cur, ref)
    adopt = result["adopt_via"]
    assert adopt["append_only"] is True
    assert adopt["input_sha256"] == hash_file(cur)
    assert adopt["reference_sha256"] == hash_file(ref)
    assert adopt["overwrites_source"] is False
