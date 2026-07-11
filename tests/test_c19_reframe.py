"""AI_IDE_19 WP4 — Smart Reframe: ROI tracks → crop keyframes.

§11 rows 7-10: crop keyframes are rate-limited (stable/smooth) · safe areas
(subtitle/logo/key-object) stay inside the crop · an infeasible crop falls back
to blanking/pillarbox · the FORMAT_ONLY invariant holds (reframe never changes
duration/segment-selection/audio/subtitle — pinned via 13C's semantic digest).
Multi-subject conflict → UNKNOWN/needs_manual. The crop is EXECUTABLE (13C had it
declared-only) and the keyframe artifact is human-editable (adopted via proposal).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.media import reframe


SOURCE_WH = (1920, 1080)
TARGET_WH = (1080, 1920)   # landscape → portrait (the classic vertical reframe)


def _track(*centers):
    """A single-subject ROI track: centers are (t_ms, cx, cy) normalised."""
    return {"subject_id": "linxia", "priority": 1.0,
            "keyframes": [{"t_ms": t, "cx": cx, "cy": cy, "w": 0.2, "h": 0.3}
                          for t, cx, cy in centers]}


# --------------------------------------------------------------------- row 7

def test_crop_keyframes_are_rate_limited():
    # subject teleports left→right between two frames 100ms apart; the compiled
    # crop centre may move at most max_px_per_s * 0.1s, never the raw jump.
    track = _track((0, 0.1, 0.5), (100, 0.9, 0.5))
    out = reframe.compile_crop_keyframes([track], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=1000)
    assert out["status"] == reframe.OK
    kfs = out["keyframes"]
    assert len(kfs) == 2
    dx = abs(kfs[1]["x"] - kfs[0]["x"])
    assert dx <= 1000 * 0.1 + 1, f"crop jumped {dx}px, exceeds the rate limit"
    # without limiting, the raw centre delta would be ~0.8*1920 = 1536px
    assert dx < 1536


def test_crop_window_matches_target_aspect_and_stays_in_source():
    track = _track((0, 0.5, 0.5))
    out = reframe.compile_crop_keyframes([track], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=5000)
    k = out["keyframes"][0]
    # portrait crop of a landscape source: full height, narrowed width
    assert k["h"] == 1080
    assert abs(k["w"] / k["h"] - TARGET_WH[0] / TARGET_WH[1]) < 0.01
    assert 0 <= k["x"] and k["x"] + k["w"] <= SOURCE_WH[0]
    assert 0 <= k["y"] and k["y"] + k["h"] <= SOURCE_WH[1]


# --------------------------------------------------------------------- row 8

def test_safe_area_stays_inside_crop():
    # a subtitle/logo safe box on the RIGHT edge; the subject is on the LEFT.
    # the crop must be nudged so the safe box is never cropped out.
    safe = [{"x": 0.80, "y": 0.85, "w": 0.15, "h": 0.10, "role": "subtitle"}]
    track = _track((0, 0.15, 0.5))
    out = reframe.compile_crop_keyframes([track], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=5000,
                                         safe_areas=safe)
    assert out["status"] == reframe.OK
    k = out["keyframes"][0]
    box_l, box_r = 0.80 * 1920, (0.80 + 0.15) * 1920
    assert k["x"] <= box_l + 1 and k["x"] + k["w"] >= box_r - 1, "safe box fell outside crop"


# --------------------------------------------------------------------- row 9

def test_infeasible_crop_falls_back_to_blanking():
    # a safe box WIDER than any portrait crop window can be → impossible to keep
    # it while cropping → blanking/pillarbox fallback (nothing fabricated).
    wide_safe = [{"x": 0.05, "y": 0.4, "w": 0.9, "h": 0.2, "role": "lower_third"}]
    track = _track((0, 0.5, 0.5))
    out = reframe.compile_crop_keyframes([track], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=5000,
                                         safe_areas=wide_safe)
    assert out["status"] == reframe.BLANKING
    assert out["strategy"] in ("pillarbox", "letterbox")


# --------------------------------------------------------------------- row 10

def test_format_only_invariant_preserved():
    from manju.build import delivery
    # a fake compiled timeline; its semantic digest is the format-only surface
    base = _FakeTimeline()
    base_digest = delivery.timeline_semantic_digest(base)
    track = _track((0, 0.5, 0.5), (1000, 0.5, 0.5))
    out = reframe.compile_crop_keyframes([track], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=5000)
    # reframe declares it changes NONE of the semantic axes
    assert out["changes"] == {"duration": False, "selection": False,
                              "audio": False, "subtitle": False}
    art = reframe.reframe_artifact(out, base_timeline_digest=base_digest,
                                   source_hash="sha256:v")
    # the artifact binds the base digest; the format-only proof is that it equals
    # the base's — reframe is pure geometry, excluded from the digest.
    assert reframe.reframe_preserves_semantics(base_digest, art) == []
    # geometry (a 9:16 crop) does NOT move the semantic digest (w/h excluded)
    assert delivery.timeline_semantic_digest(base) == base_digest


def test_reframe_artifact_inert_until_adopted():
    out = reframe.compile_crop_keyframes([_track((0, 0.5, 0.5))],
                                         source_wh=SOURCE_WH, target_wh=TARGET_WH,
                                         max_px_per_s=5000)
    art = reframe.reframe_artifact(out, base_timeline_digest="sha256:b",
                                   source_hash="sha256:v")
    # 13C rule reused: an un-adopted framing artifact never drives render
    assert art["adopted"] is False and art["drives_render"] is False
    adopted = reframe.adopt_reframe_artifact(art)
    assert adopted["adopted"] is True and adopted["drives_render"] is True


# ------------------------------------------------------------- multi-subject

def test_multi_subject_conflict_is_needs_manual():
    # two subjects at opposite edges cannot both fit a narrow portrait crop.
    left = {"subject_id": "a", "keyframes": [{"t_ms": 0, "cx": 0.1, "cy": 0.5, "w": 0.2, "h": 0.3}]}
    right = {"subject_id": "b", "keyframes": [{"t_ms": 0, "cx": 0.9, "cy": 0.5, "w": 0.2, "h": 0.3}]}
    out = reframe.compile_crop_keyframes([left, right], source_wh=SOURCE_WH,
                                         target_wh=TARGET_WH, max_px_per_s=5000)
    assert out["status"] == reframe.NEEDS_MANUAL
    assert "conflict" in out["reason"].lower() or "manual" in out["reason"].lower()


# ------------------------------------------------------------- executable crop

def test_ffmpeg_crop_expr_is_deterministic():
    out = reframe.compile_crop_keyframes([_track((0, 0.3, 0.5), (1000, 0.6, 0.5))],
                                         source_wh=SOURCE_WH, target_wh=TARGET_WH,
                                         max_px_per_s=5000)
    e1 = reframe.ffmpeg_crop_expr(out, fps=25)
    e2 = reframe.ffmpeg_crop_expr(out, fps=25)
    assert e1 == e2 and e1.startswith("crop=")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_execute_reframe_preserves_duration(tmp_project, tmp_path):
    src = tmp_path / "land.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=1",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    from manju.core.models import TakeSidecar
    take = tmp_project.register_take("S001", src, TakeSidecar(provider="test", spec_hash="h"))
    out = reframe.compile_crop_keyframes([_track((0, 0.5, 0.5))],
                                         source_wh=(320, 180), target_wh=(180, 320),
                                         max_px_per_s=5000)
    new = reframe.execute_reframe(tmp_project, "S001", take.name, out, target_wh=(180, 320))
    from manju.media.probe import probe
    src_ms = probe(src).duration_ms
    new_ms = probe(new.media_path).duration_ms
    assert abs(new_ms - src_ms) <= 80, "reframe changed duration — not format-only"
    assert new.sidecar.params.get("op") == "reframe"


# ------------------------------------------------------------- test double

class _FakeClip:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeTracks:
    video = [_FakeClip(shot="S001", take="take_01", source="a.mp4",
                       start_ms=0, duration_ms=1000, source_in_ms=0)]
    voice = []
    music = []
    sfx = []
    ambient = []
    captions = []
    overlay = []


class _FakeTimeline:
    tracks = _FakeTracks()
