"""Branding overlays (round-Q §packaging): logo / watermark / badge / cta.

Two halves, mirroring test_packaging.py:
  (a) pure/fast — model defaults + validators, compiler emission and window
      math (cta at the film's end), the all-off byte-identical pin, and the
      render filtergraph strings (corner positioning, opacity, enable windows).
      No ffmpeg.
  (b) end-to-end (ffmpeg) — a tiny sample with a logo PNG burned onto the final:
      the render succeeds and the duration is unchanged (no OCR/pixel probe).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from manju.core.models import (
    BadgeSpec,
    CtaSpec,
    Dialogue,
    LogoSpec,
    OverlayClip,
    PackagingSpec,
    ProjectConfig,
    ShotSpec,
    TimelineRules,
    WatermarkSpec,
)
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline


# ============================================================ (a) pure / fast


def _shot(sid: str, *, dur: float = 2.0) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=sid, duration=dur, dialogue=Dialogue(speaker="linxia", text="台词。")),
        take_name="take_01",
        take_source=f"media/gen/{sid}/take_01.mp4",
        take_duration_ms=int(dur * 1000),
    )


def _input(*shots, packaging=None):
    return CompileInput(
        config=ProjectConfig(name="t"), rules=TimelineRules(),
        shots=list(shots), packaging=packaging,
    )


def _overlays(tl, kind):
    return [o for o in tl.tracks.overlay if o.kind == kind]


# ---- model defaults + validators


def test_branding_defaults_all_off():
    p = PackagingSpec()
    assert not (p.logo.enabled or p.watermark.enabled or p.badge.enabled or p.cta.enabled)
    assert p.logo.corner == "tr" and p.logo.size_pct == 12.0 and p.logo.opacity == 1.0
    assert p.logo.margin_pct == 2.5 and p.logo.duration_ms is None
    assert p.watermark.opacity == 0.35 and p.watermark.position == "center"
    assert p.badge.corner == "tl"
    assert p.cta.at_end_ms == 3000 and p.cta.position == "bottom"
    assert p.cta.text  # a placeholder call-to-action


@pytest.mark.parametrize("kwargs", [
    {"opacity": 1.5}, {"opacity": -0.01},
    {"size_pct": 0}, {"size_pct": 100.1}, {"size_pct": -5},
])
def test_logo_and_watermark_reject_out_of_range(kwargs):
    # opacity/size validation lives at the model (pydantic), not QC.
    with pytest.raises(Exception):
        LogoSpec(enabled=True, image="x.png", **kwargs)
    with pytest.raises(Exception):
        WatermarkSpec(enabled=True, text="w", **kwargs)


def test_in_range_values_are_accepted():
    LogoSpec(opacity=0.0, size_pct=100)  # boundaries are inclusive where sane
    LogoSpec(opacity=1.0, size_pct=0.001)
    WatermarkSpec(opacity=0.35, size_pct=30)


# ---- compiler emission + windows


def test_logo_full_film_window_and_geometry():
    pkg = PackagingSpec(logo=LogoSpec(
        enabled=True, image="media/imports/logo.png", corner="br",
        size_pct=10.0, margin_pct=3.0, opacity=0.8,
    ))
    tl = compile_timeline(_input(_shot("S001"), _shot("S002"), packaging=pkg))
    (logo,) = _overlays(tl, "logo")
    assert logo.start_ms == 0 and logo.duration_ms == tl.duration_ms  # full film
    assert logo.source == "media/imports/logo.png" and logo.corner == "br"
    assert logo.size_pct == 10.0 and logo.margin_pct == 3.0 and logo.opacity == 0.8


def test_logo_windowed_by_from_and_duration():
    pkg = PackagingSpec(logo=LogoSpec(
        enabled=True, image="media/imports/logo.png", from_ms=500, duration_ms=800,
    ))
    tl = compile_timeline(_input(_shot("S001"), packaging=pkg))
    (logo,) = _overlays(tl, "logo")
    assert logo.start_ms == 500 and logo.duration_ms == 800


def test_logo_enabled_without_image_emits_nothing():
    pkg = PackagingSpec(logo=LogoSpec(enabled=True, image=""))
    tl = compile_timeline(_input(_shot("S001"), packaging=pkg))
    assert _overlays(tl, "logo") == []


def test_watermark_spans_the_whole_film():
    pkg = PackagingSpec(watermark=WatermarkSpec(enabled=True, text="样片", opacity=0.4))
    tl = compile_timeline(_input(_shot("S001"), _shot("S002"), packaging=pkg))
    (wm,) = _overlays(tl, "watermark")
    assert wm.start_ms == 0 and wm.duration_ms == tl.duration_ms
    assert wm.text == "样片" and wm.opacity == 0.4


def test_badge_window_and_corner():
    pkg = PackagingSpec(badge=BadgeSpec(
        enabled=True, text="NEW", corner="tr", from_ms=200, duration_ms=1000,
    ))
    tl = compile_timeline(_input(_shot("S001"), packaging=pkg))
    (badge,) = _overlays(tl, "badge")
    assert badge.text == "NEW" and badge.corner == "tr"
    assert badge.start_ms == 200 and badge.duration_ms == 1000


def test_cta_window_is_the_closing_at_end_ms():
    pkg = PackagingSpec(cta=CtaSpec(enabled=True, text="关注", at_end_ms=1200))
    tl = compile_timeline(_input(_shot("S001"), _shot("S002"), packaging=pkg))
    (cta,) = _overlays(tl, "cta")
    # window = [total - at_end_ms, total]
    assert cta.start_ms == tl.duration_ms - 1200
    assert cta.start_ms + cta.duration_ms == tl.duration_ms
    assert cta.duration_ms == 1200


def test_cta_at_end_larger_than_film_clamps_to_start():
    # at_end_ms past the film start → window opens at 0, still ends at total
    pkg = PackagingSpec(cta=CtaSpec(enabled=True, text="关注", at_end_ms=10_000_000))
    tl = compile_timeline(_input(_shot("S001"), packaging=pkg))
    (cta,) = _overlays(tl, "cta")
    assert cta.start_ms == 0 and cta.duration_ms == tl.duration_ms


def test_all_branding_together_emits_four_overlays():
    pkg = PackagingSpec(
        logo=LogoSpec(enabled=True, image="media/imports/logo.png"),
        watermark=WatermarkSpec(enabled=True, text="WM"),
        badge=BadgeSpec(enabled=True, text="NEW"),
        cta=CtaSpec(enabled=True, text="FOLLOW", at_end_ms=1000),
    )
    tl = compile_timeline(_input(_shot("S001"), _shot("S002"), packaging=pkg))
    kinds = [o.kind for o in tl.tracks.overlay]
    assert kinds == ["logo", "watermark", "badge", "cta"]


# ---- fingerprint: branding folds only when effective


def test_fingerprint_changes_when_branding_enabled():
    off = _input(_shot("S001"), packaging=PackagingSpec())
    on = _input(_shot("S001"), packaging=PackagingSpec(
        badge=BadgeSpec(enabled=True, text="NEW")))
    assert off.fingerprint() != on.fingerprint()


def test_fingerprint_unchanged_by_disabled_branding():
    # an all-off branding block must not perturb the fingerprint (byte-identical)
    base = _input(_shot("S001")).fingerprint()
    disabled = _input(_shot("S001"), packaging=PackagingSpec(
        logo=LogoSpec(enabled=False, image="media/imports/logo.png"),
        watermark=WatermarkSpec(enabled=False, text="WM"),
    )).fingerprint()
    assert base == disabled


# ---- render burn path (filtergraph level, no ffmpeg)


def test_logo_burns_via_image_overlay_chain(tmp_path):
    from manju.media.render import _image_overlay_graph

    class _P:
        def resolve(self, s):
            return Path("/proj") / s

    logo = OverlayClip(kind="logo", source="media/imports/logo.png", corner="tr",
                       size_pct=12.0, margin_pct=2.5, opacity=0.8,
                       start_ms=0, duration_ms=4000)
    inputs, stmts, last = _image_overlay_graph(
        [logo], project=_P(), in_label="[vbase]", out_w=1080, base_idx=3)
    joined = ";".join(stmts)
    assert inputs == ["-i", "/proj/media/imports/logo.png"]
    assert "[3:v]scale=130:-1,format=rgba,colorchannelmixer=aa=0.8[br_img0]" in joined
    # tr corner with a 2.5%-of-width inset (27px @ 1080)
    assert "overlay=x=W-w-27:y=27" in joined
    assert "enable='between(t,0.000,4.000)'" in joined
    assert last == "[br_ov0]"


def test_logo_corners_map_to_the_right_position():
    from manju.media.render import _overlay_image_xy

    assert _overlay_image_xy(OverlayClip(kind="logo", corner="tl"), 10) == ("10", "10")
    assert _overlay_image_xy(OverlayClip(kind="logo", corner="tr"), 10) == ("W-w-10", "10")
    assert _overlay_image_xy(OverlayClip(kind="logo", corner="bl"), 10) == ("10", "H-h-10")
    assert _overlay_image_xy(OverlayClip(kind="logo", corner="br"), 10) == ("W-w-10", "H-h-10")
    # an image watermark is centred regardless of corner
    assert _overlay_image_xy(OverlayClip(kind="watermark", source="w.png"), 10) == (
        "(W-w)/2", "(H-h)/2")


def test_image_watermark_is_classified_as_image_and_text_as_text():
    from manju.media.render import _is_image_overlay, _is_text_overlay

    img_wm = OverlayClip(kind="watermark", source="media/imports/wm.png")
    txt_wm = OverlayClip(kind="watermark", text="WM")
    assert _is_image_overlay(img_wm) and not _is_text_overlay(img_wm)
    assert _is_text_overlay(txt_wm) and not _is_image_overlay(txt_wm)


def test_badge_cta_watermark_text_burn_via_drawtext(tmp_path):
    from manju.media.render import _title_card_filters

    ovs = [
        OverlayClip(kind="badge", text="NEW", corner="br", start_ms=0, duration_ms=4000),
        OverlayClip(kind="cta", text="关注", position="bottom", start_ms=2500, duration_ms=1500),
        OverlayClip(kind="watermark", text="样片", opacity=0.35, size_pct=30.0,
                    position="center", start_ms=0, duration_ms=4000),
    ]
    badge, cta, wm = _title_card_filters(ovs, out_w=1080, tmp_dir=tmp_path, font=None)
    # badge: small boxed chip anchored in the br corner (inset 3% = 32px @ 1080)
    assert badge.startswith("drawtext=") and "box=1" in badge
    assert "x=w-text_w-32:y=h-text_h-32" in badge
    assert "enable='between(t,0.000,4.000)'" in badge
    # cta: centred, closing-window chip in the lower band
    assert "x=(w-text_w)/2:y=h*0.86" in cta
    assert "enable='between(t,2.500,4.000)'" in cta
    # text watermark: large, centred, translucent (opacity in the fontcolor)
    assert "fontcolor=white@0.35" in wm and "x=(w-text_w)/2:y=(h-text_h)/2" in wm
    assert "box=1" not in wm  # a watermark is boxless


def test_title_card_filter_is_unchanged_by_round_q(tmp_path):
    # the historical title_card drawtext string must be byte-for-byte preserved
    from manju.media.render import _title_card_filters

    ov = OverlayClip(kind="title_card", template="chapter", text="标题",
                     start_ms=0, duration_ms=1500)
    (filt,) = _title_card_filters([ov], out_w=1080, tmp_dir=tmp_path, font=None)
    assert ("fontcolor=white:fontsize=108:x=(w-text_w)/2:y=h*0.28:"
            "box=1:boxcolor=black@0.45:boxborderw=24") in filt


# ---- QC: missing branding image


def test_qc_flags_missing_logo_image(tmp_project):
    from manju.qc.checks import run_qc

    tmp_project.save_packaging(PackagingSpec(logo=LogoSpec(
        enabled=True, image="media/imports/nope_logo.png")))
    report = run_qc(tmp_project, None, extract_frames=False)
    errs = [i for i in report.items
            if i.subject == "packaging" and i.level == "error"]
    assert errs and any("logo" in i.message and "missing" in i.message for i in errs)


def test_qc_flags_missing_image_watermark(tmp_project):
    from manju.qc.checks import run_qc

    tmp_project.save_packaging(PackagingSpec(watermark=WatermarkSpec(
        enabled=True, image="media/imports/nope_wm.png")))
    report = run_qc(tmp_project, None, extract_frames=False)
    errs = [i for i in report.items
            if i.subject == "packaging" and i.level == "error"]
    assert errs and any("watermark" in i.message for i in errs)


def test_qc_text_only_watermark_needs_no_image(tmp_project):
    from manju.qc.checks import run_qc

    tmp_project.save_packaging(PackagingSpec(watermark=WatermarkSpec(
        enabled=True, text="样片")))  # text watermark, no image → no existence error
    report = run_qc(tmp_project, None, extract_frames=False)
    errs = [i for i in report.items
            if i.subject == "packaging" and i.level == "error"]
    assert not errs


# =========================================================== (b) end-to-end

pytestmark_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required for the branding end-to-end tests",
)

FRAME_TOL_MS = 1000 / 24 + 5  # one frame at 24fps, plus probe rounding slack


def _make_logo_png(path: Path) -> None:
    """A tiny opaque logo PNG (pillow is a project dependency)."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (120, 120), (220, 40, 40, 255)).save(path)


@pytest.fixture(scope="module")
def built_with_logo(tmp_path_factory):
    """A 2-shot vertical sample with a corner logo burned into the final."""
    from manju.build.graph import run_build
    from manju.core.container import Project
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path_factory.mktemp("brand") / "样片", shots=2)
    project = Project(root)
    _make_logo_png(project.imports_dir / "logo.png")
    pkg = project.load_packaging()
    pkg.logo = LogoSpec(enabled=True, image="media/imports/logo.png",
                        corner="tr", size_pct=12.0, margin_pct=2.5, opacity=1.0)
    project.save_packaging(pkg)
    assert run_build(project, target="final").ok
    return project


@pytestmark_ffmpeg
def test_logo_burns_onto_final_without_changing_duration(built_with_logo):
    from manju.media.probe import probe_duration_ms

    project = built_with_logo
    tl = project.load_timeline()
    # the logo rides the overlay track as an image overlay (full film)
    (logo,) = [o for o in tl.tracks.overlay if o.kind == "logo"]
    assert logo.source == "media/imports/logo.png"
    assert logo.start_ms == 0 and logo.duration_ms == tl.duration_ms

    final = project.newest_final_path()
    assert final is not None and final.exists()
    dur = probe_duration_ms(final)
    # burning the overlay must not shift picture length (picture is the master)
    assert abs(dur - tl.duration_ms) <= FRAME_TOL_MS


@pytestmark_ffmpeg
def test_second_build_with_logo_is_a_content_key_skip(built_with_logo):
    from manju.build.graph import run_build

    finals = sorted(built_with_logo.final_dir.glob("final_v*.mp4"))
    assert run_build(built_with_logo, target="final").ok
    assert sorted(built_with_logo.final_dir.glob("final_v*.mp4")) == finals


@pytestmark_ffmpeg
def test_editing_logo_bytes_rerenders(built_with_logo):
    from PIL import Image

    from manju.build.graph import run_build

    project = built_with_logo
    before = len(list(project.final_dir.glob("final_v*.mp4")))
    # same path, different bytes → the content key must change (overlay image hash)
    Image.new("RGBA", (120, 120), (30, 30, 220, 255)).save(project.imports_dir / "logo.png")
    assert run_build(project, target="final").ok
    after = len(list(project.final_dir.glob("final_v*.mp4")))
    assert after == before + 1
