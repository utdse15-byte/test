"""Packaging kit (round-N §13-14): packaging.yaml → intro/outro segments,
info-card overlays, cover + teaser exports, and the `manju package` command.

Two halves, mirroring test_overlay.py:
  (a) pure/fast — loader defaults, the scaffold, card content-addressing, the
      compiler (intro/outro insertion + shift, the packaging=None/disabled pin,
      info-card anchoring), the burn filter, and QC findings. No ffmpeg.
  (b) end-to-end (ffmpeg) — a tiny project with an intro card built to final:
      the card is a real segment, the second build is a content-key skip, an
      edited intro text re-renders; and `manju package` cuts a cover + teaser.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.models import (
    BadgeSpec,
    CoverSpec,
    CtaSpec,
    Dialogue,
    InfoCardSpec,
    LogoSpec,
    PackagingCard,
    PackagingSpec,
    ProjectConfig,
    ShotSpec,
    Timeline,
    TimelineRules,
    TimelineTracks,
    VideoClip,
    WatermarkSpec,
)
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline
from manju.timeline.packaging import packaging_card_relpath


# ============================================================ (a) pure / fast


def _shot(sid: str, *, text: str = "这是一句台词。", dur: float = 2.0,
          voice_ms: int = 1500) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=sid, duration=dur, dialogue=Dialogue(speaker="linxia", text=text)),
        take_name="take_01",
        take_source=f"media/gen/{sid}/take_01.mp4",
        take_duration_ms=int(dur * 1000),
        voice_source=f"media/gen/{sid}/voice_take_01.wav",
        voice_duration_ms=voice_ms,
    )


def _pkg(*, intro=False, outro=False, intro_ms=2000, outro_ms=1000, info_cards=None):
    return PackagingSpec(
        intro=PackagingCard(enabled=intro, text="片头", duration_ms=intro_ms),
        outro=PackagingCard(enabled=outro, text="片尾", duration_ms=outro_ms),
        info_cards=info_cards or [],
    )


def _input(*shots, packaging=None):
    return CompileInput(
        config=ProjectConfig(name="t"), rules=TimelineRules(),
        shots=list(shots), packaging=packaging,
    )


# ---- loader default + scaffold


def test_loader_defaults_when_absent(tmp_project):
    tmp_project.packaging_path.unlink()  # simulate a project without the file
    pkg = tmp_project.load_packaging()
    assert pkg.intro.enabled is False and pkg.outro.enabled is False
    assert pkg.teaser.enabled is False and pkg.info_cards == []
    assert pkg.cover.mode == "frame"


def test_new_scaffolds_packaging_disabled_with_hints(tmp_project):
    path = tmp_project.packaging_path
    assert path.exists() and path.parent.name == "timeline"  # next to rules.yaml
    text = path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("#")  # commented hints
    pkg = tmp_project.load_packaging()  # comments don't break the loader
    assert not (pkg.intro.enabled or pkg.outro.enabled or pkg.teaser.enabled)


# ---- card content addressing


def test_card_content_addressing():
    a = PackagingCard(enabled=True, text="片头")
    b = PackagingCard(enabled=True, text="片头")
    c = PackagingCard(enabled=True, text="改过的片头")
    pa = packaging_card_relpath("intro", a, 1080, 1920, 24)
    assert pa == packaging_card_relpath("intro", b, 1080, 1920, 24)  # same spec
    assert pa != packaging_card_relpath("intro", c, 1080, 1920, 24)  # edited text
    assert pa != packaging_card_relpath("intro", a, 1920, 1080, 24)  # resolution
    assert pa != packaging_card_relpath("intro", a, 1080, 1920, 30)  # fps
    assert pa.startswith("media/generated/_packaging/intro_") and pa.endswith(".mp4")


# ---- compiler: intro/outro insertion + shift, and the pin


def test_packaging_none_or_disabled_is_byte_identical():
    a = compile_timeline(_input(_shot("S001"), _shot("S002")))
    b = compile_timeline(_input(_shot("S001"), _shot("S002"), packaging=None))
    c = compile_timeline(_input(_shot("S001"), _shot("S002"), packaging=PackagingSpec()))
    # round-Q: a packaging.yaml that carries branding fields but leaves them all
    # OFF must still yield the byte-identical timeline (nothing folded/emitted).
    d = compile_timeline(_input(_shot("S001"), _shot("S002"), packaging=PackagingSpec(
        logo=LogoSpec(enabled=False, image="media/imports/logo.png"),
        watermark=WatermarkSpec(enabled=False, text="WM"),
        badge=BadgeSpec(enabled=False, text="NEW"),
        cta=CtaSpec(enabled=False, text="FOLLOW"),
    )))
    assert (a.model_dump_json() == b.model_dump_json()
            == c.model_dump_json() == d.model_dump_json())


def test_intro_outro_are_real_segments_that_shift_downstream():
    base = compile_timeline(_input(_shot("S001"), _shot("S002")))
    tl = compile_timeline(_input(_shot("S001"), _shot("S002"),
                                 packaging=_pkg(intro=True, outro=True)))
    vids = tl.tracks.video
    # leading/trailing packaging clips
    assert vids[0].shot == "__intro__" and vids[0].take == "packaging"
    assert vids[0].start_ms == 0 and vids[0].duration_ms == 2000
    assert vids[-1].shot == "__outro__" and vids[-1].duration_ms == 1000
    # the intro carries the content-addressed source path (pure, no fs)
    assert vids[0].source == packaging_card_relpath(
        "intro", PackagingCard(enabled=True, text="片头", duration_ms=2000), 1080, 1920, 24)
    # shots shifted by the intro duration
    assert vids[1].shot == "S001" and vids[1].start_ms == 2000
    # total = intro + shots + outro
    assert tl.duration_ms == base.duration_ms + 2000 + 1000
    # every downstream timing shifts by the intro duration, none was post-shifted
    assert tl.tracks.voice[0].start_ms == base.tracks.voice[0].start_ms + 2000
    assert tl.tracks.captions[0].start_ms == base.tracks.captions[0].start_ms + 2000
    # transitions: intro is not last (has one), the outro is last (none)
    assert vids[0].transition_out is not None
    assert vids[-1].transition_out is None


def test_intro_outro_get_no_captions_or_voice():
    base = compile_timeline(_input(_shot("S001"), _shot("S002")))
    tl = compile_timeline(_input(_shot("S001"), _shot("S002"),
                                 packaging=_pkg(intro=True, outro=True)))
    # exactly the shots' voices/captions — nothing was synthesised for the cards
    assert len(tl.tracks.voice) == len(base.tracks.voice)
    assert len(tl.tracks.captions) == len(base.tracks.captions)
    assert all(c.speaker == "linxia" for c in tl.tracks.captions)


def test_fingerprint_changes_when_intro_text_changes():
    a = _input(_shot("S001"), packaging=_pkg(intro=True))
    b = _input(_shot("S001"),
               packaging=PackagingSpec(intro=PackagingCard(enabled=True, text="别的片头")))
    assert a.fingerprint() != b.fingerprint()
    assert (compile_timeline(a).meta.compiled_from
            != compile_timeline(b).meta.compiled_from)


# ---- compiler: info cards (anchor + deterministic skip)


def test_info_cards_anchor_resolution_and_unknown_skip():
    cards = [
        InfoCardSpec(kind="chapter", text="第一章", at="shot:S001", duration_ms=1500),
        InfoCardSpec(kind="role", text="偏移", at="shot:S001:end", offset_ms=-200),
        InfoCardSpec(text="幽灵镜头", at="shot:GHOST", duration_ms=1500),
        # resolves past the film's end (2000 + 200 ≥ 2000) — could never be
        # seen, so it is skipped like the unknown shot (QC warns on both)
        InfoCardSpec(text="片尾之后", at="shot:S001:end", offset_ms=200),
    ]
    tl = compile_timeline(_input(_shot("S001", dur=2.0), packaging=_pkg(info_cards=cards)))
    infos = [o for o in tl.tracks.overlay if o.kind == "info_card"]
    assert [o.text for o in infos] == ["第一章", "偏移"]  # GHOST + past-end skipped
    assert infos[0].start_ms == 0                       # S001 starts at 0 (no intro)
    assert infos[1].start_ms == 2000 - 200              # shot end + negative offset
    # the semantic kind rides along and picks the burn band (round-N review)
    assert [o.subkind for o in infos] == ["chapter", "role"]


def test_info_card_anchor_shifts_with_intro():
    card = InfoCardSpec(text="第一章", at="shot:S001")
    tl = compile_timeline(_input(_shot("S001", dur=2.0),
                                 packaging=_pkg(intro=True, info_cards=[card])))
    info = next(o for o in tl.tracks.overlay if o.kind == "info_card")
    assert info.start_ms == 2000  # S001 now sits after the 2000ms intro


# ---- render burn path smoke (filtergraph level, no ffmpeg)


def test_info_card_rides_the_title_card_burn_path(tmp_path):
    from manju.core.models import OverlayClip
    from manju.media.render import _title_card_filters

    ov = OverlayClip(kind="info_card", text="第一章", start_ms=1000, duration_ms=1500)
    filters = _title_card_filters([ov], out_w=1080, tmp_dir=tmp_path, font=None)
    assert len(filters) == 1 and filters[0].startswith("drawtext=")
    assert "enable='between(t,1.000,2.500)'" in filters[0]
    # text is passed via a textfile (escaping-free path), so it lives on disk
    assert (tmp_path / "title_0.txt").read_text(encoding="utf-8") == "第一章"


# ---- QC findings


def test_qc_flags_missing_intro_asset(tmp_project):
    from manju.qc.checks import run_qc

    tmp_project.save_packaging(PackagingSpec(intro=PackagingCard(enabled=True, text="片头")))
    report = run_qc(tmp_project, None, extract_frames=False)
    errs = [i for i in report.items if i.subject == "packaging" and i.level == "error"]
    assert errs and any("intro" in i.message for i in errs)
    assert any("manju build" in i.suggestion for i in errs)


def test_qc_warns_on_info_card_unknown_shot(tmp_project):
    from manju.qc.checks import run_qc

    tl = Timeline(tracks=TimelineTracks(video=[
        VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                  start_ms=0, duration_ms=2000),
    ]))
    tmp_project.save_packaging(
        PackagingSpec(info_cards=[InfoCardSpec(text="幽灵", at="shot:GHOST")])
    )
    report = run_qc(tmp_project, tl, extract_frames=False)
    warns = [i for i in report.items if i.subject == "packaging" and i.level == "warn"]
    assert warns and any("GHOST" in i.message for i in warns)


def test_package_requires_a_final(tmp_project):
    from manju.media.packaging import PackagingError, make_package

    with pytest.raises(PackagingError):
        make_package(tmp_project)  # no final on disk → clean failure (no ffmpeg)


# ---- staleness (round-O advisory; round-W #56 gate): degrade-honestly paths
# (no ffmpeg). round-W: the bare advisory became `_stale_final_status`,
# returning (is_stale, message) — `is_stale=None` (unknown) never gates, but
# it no longer degrades to a silent `None` message either (§32's "never
# conflate 'cannot tell' with 'no problem'" ethos, applied here too).


def test_stale_status_unknown_when_no_timeline(tmp_project):
    """No compiled timeline on disk → honestly UNKNOWN (never crashes, never
    gates) even if handed a final path."""
    from manju.media.packaging import _stale_final_status

    assert tmp_project.load_timeline() is None
    fake_final = tmp_project.final_dir / "final_v1.mp4"
    is_stale, message = _stale_final_status(tmp_project, fake_final)
    assert is_stale is None
    assert message and "无法判断" in message


def test_stale_status_unknown_when_final_has_no_sidecar(tmp_project):
    """A timeline exists but the final carries no .key.json → cannot judge —
    honestly UNKNOWN (never a false positive, never a crash, never a gate)."""
    from manju.media.packaging import _stale_final_status

    tmp_project.save_timeline(Timeline(tracks=TimelineTracks(video=[
        VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                  start_ms=0, duration_ms=2000),
    ])))
    final = tmp_project.final_dir / "final_v1.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"not a real video, and no key sidecar next to it")
    is_stale, message = _stale_final_status(tmp_project, final)
    assert is_stale is None
    assert message and "无法判断" in message


def test_make_package_never_gates_on_unknown_staleness(tmp_project, monkeypatch):
    """round-W #56: an UNKNOWN staleness verdict must never refuse — only a
    PROVABLY stale final gates. This drives `make_package` far enough to reach
    the staleness check without needing a real ffmpeg build (the cover render
    is monkeypatched to a no-op)."""
    from manju.media import packaging as pkg_mod

    tmp_project.save_timeline(Timeline(tracks=TimelineTracks(video=[
        VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                  start_ms=0, duration_ms=2000),
    ])))
    final = tmp_project.final_dir / "final_v1.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"not a real video, and no key sidecar next to it")

    def _fake_cover(final, dest, *, frame_ms, width, height, fps, log):
        dest.write_bytes(b"fake-cover")  # default cover.mode == "frame"

    monkeypatch.setattr(pkg_mod, "_extract_cover_frame", _fake_cover)
    result = pkg_mod.make_package(tmp_project)  # force=False — must NOT raise
    assert result["cover"] is not None


# =========================================================== (b) end-to-end

pytestmark_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required for the packaging end-to-end tests",
)

FRAME_TOL_MS = 1000 / 24 + 5  # one frame at 24fps, plus probe rounding slack


def _finals(project) -> list[Path]:
    return sorted(project.final_dir.glob("final_v*.mp4"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A 2-shot vertical sample with an intro card, built to final once."""
    from manju.build.graph import run_build
    from manju.core.container import Project
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path_factory.mktemp("pkg") / "样片", shots=2)
    project = Project(root)
    pkg = project.load_packaging()
    pkg.intro.enabled = True
    pkg.intro.text = "片头"
    pkg.intro.duration_ms = 1000
    project.save_packaging(pkg)
    assert run_build(project, target="final").ok
    return project


@pytestmark_ffmpeg
def test_intro_is_a_real_rendered_segment(built):
    from manju.media.probe import probe_duration_ms

    tl = built.load_timeline()
    intro = tl.tracks.video[0]
    assert intro.shot == "__intro__" and intro.duration_ms == 1000
    # the content-addressed card asset exists on disk (rendered by the build phase)
    rel = packaging_card_relpath("intro", built.load_packaging().intro, 1080, 1920, 24)
    assert built.resolve(rel).exists()
    # the final includes the card duration, within one frame of the timeline total
    dur = probe_duration_ms(_finals(built)[-1])
    assert tl.duration_ms > 1000  # intro + shots
    assert abs(dur - tl.duration_ms) <= FRAME_TOL_MS


@pytestmark_ffmpeg
def test_second_build_is_a_content_key_skip(built):
    from manju.build.graph import run_build

    before = len(_finals(built))
    assert run_build(built, target="final").ok
    assert len(_finals(built)) == before  # nothing changed → no new final


@pytestmark_ffmpeg
def test_editing_intro_text_rerenders(built):
    from manju.build.graph import run_build

    before = len(_finals(built))
    pkg = built.load_packaging()
    pkg.intro.text = "全新片头"  # changes the card hash → new source → new key
    built.save_packaging(pkg)
    assert run_build(built, target="final").ok
    assert len(_finals(built)) == before + 1
    # the new content-addressed asset was rendered (append-only; old one stays)
    rel = packaging_card_relpath("intro", built.load_packaging().intro, 1080, 1920, 24)
    assert built.resolve(rel).exists()


@pytestmark_ffmpeg
def test_package_cover_frame_and_teaser(built):
    from manju.media.packaging import make_package
    from manju.media.probe import probe

    pkg = built.load_packaging()
    pkg.cover = CoverSpec(mode="frame", frame_ms=0)
    pkg.teaser.enabled = True
    pkg.teaser.from_ms = 0
    pkg.teaser.duration_ms = 1000
    built.save_packaging(pkg)

    result = make_package(built, force=True)
    cover = built.resolve(result["cover"])
    assert cover.exists() and cover.name == "cover.png"
    cinfo = probe(cover)
    assert (cinfo.width, cinfo.height) == (1080, 1920)  # project resolution

    teaser = built.resolve(result["teaser"])
    assert teaser.exists()
    tinfo = probe(teaser)
    assert abs(tinfo.duration_ms - 1000) <= FRAME_TOL_MS  # within one frame of the ask

    # second run skips both via the content-key sidecars; --force recuts
    assert set(make_package(built)["skipped"]) == {"cover", "teaser"}
    assert make_package(built, force=True)["skipped"] == []


@pytestmark_ffmpeg
def test_cover_crash_mid_write_leaves_old_file_and_key_intact(built, monkeypatch):
    """Round W (#76): a crash mid-ffmpeg-write must not corrupt the trusted
    cover.png IN PLACE, nor leave a key.json that matches a half-written
    file. Before this round ffmpeg wrote directly to cover.png; now it lands
    via atomic_output (temp sibling + verified swap), so a "crash" that
    manages to write garbage only ever touches the temp file — the last
    KNOWN-GOOD cover and its key must survive byte-for-byte."""
    import manju.media.packaging as pkg_mod
    from manju.media.ffmpeg import MediaError
    from manju.media.packaging import make_package

    pkg = built.load_packaging()
    pkg.cover = CoverSpec(mode="frame", frame_ms=0)
    built.save_packaging(pkg)

    # establish a known-good cover + key first
    result = make_package(built, force=True)
    cover_path = built.resolve(result["cover"])
    good_bytes = cover_path.read_bytes()
    key_path = cover_path.with_suffix(".key.json")
    good_key = key_path.read_text(encoding="utf-8")

    # a different spec so the next call must actually re-render, not skip
    pkg2 = built.load_packaging()
    pkg2.cover = CoverSpec(mode="frame", frame_ms=200)
    built.save_packaging(pkg2)

    def crashing_run_ffmpeg(args, **kw):
        # simulate ffmpeg having written SOME garbage bytes to its declared
        # output before the process is killed / errors out.
        Path(str(args[-1])).write_bytes(b"GARBAGE-NOT-A-REAL-PNG")
        raise MediaError("simulated crash mid-encode")

    monkeypatch.setattr(pkg_mod, "run_ffmpeg", crashing_run_ffmpeg)
    with pytest.raises(MediaError):
        make_package(built, force=True)
    monkeypatch.undo()

    # the OLD file and key are untouched — never corrupted in place
    assert cover_path.read_bytes() == good_bytes
    assert key_path.read_text(encoding="utf-8") == good_key
    # and no stray temp file leaked into the packaging dir
    assert list(cover_path.parent.glob(".cover.png.tmp-*")) == []

    # a real (non-crashing) retry succeeds and produces a NEW, different key
    retried = make_package(built, force=True)
    assert built.resolve(retried["cover"]).read_bytes() != good_bytes


@pytestmark_ffmpeg
def test_package_cover_card_mode(built):
    from manju.media.packaging import make_package
    from manju.media.probe import probe

    pkg = built.load_packaging()
    pkg.cover = CoverSpec(mode="card", text="封面标题", template="chapter")
    built.save_packaging(pkg)
    result = make_package(built, force=True)
    cover = built.resolve(result["cover"])
    assert probe(cover).width == 1080 and probe(cover).height == 1920


# ------------------------------------- round-N review: teaser window safety


@pytestmark_ffmpeg
def test_teaser_from_ms_past_end_is_a_clean_failure(built):
    from manju.media.packaging import PackagingError, make_package

    pkg = built.load_packaging()
    pkg.teaser.enabled = True
    pkg.teaser.from_ms = 10_000_000  # far past any final
    built.save_packaging(pkg)
    with pytest.raises(PackagingError, match="at/past the end"):
        make_package(built, force=True)
    # the broken window must NOT have been key-cached: rerunning with the same
    # spec must fail again, not skip via a stale sidecar
    with pytest.raises(PackagingError, match="at/past the end"):
        make_package(built)


@pytestmark_ffmpeg
def test_teaser_overrun_is_clamped_with_warning(built):
    from manju.media.packaging import make_package
    from manju.media.probe import probe, probe_duration_ms

    final_ms = probe_duration_ms(_finals(built)[-1])
    pkg = built.load_packaging()
    pkg.teaser.enabled = True
    pkg.teaser.from_ms = max(0, final_ms - 500)
    pkg.teaser.duration_ms = 2000  # overruns the end by ~1500ms
    built.save_packaging(pkg)

    result = make_package(built, force=True)
    assert any("clamped" in w for w in result["warnings"]), result["warnings"]
    teaser = built.resolve(result["teaser"])
    assert probe(teaser).duration_ms <= 500 + FRAME_TOL_MS  # clamped, not silent


@pytestmark_ffmpeg
def test_package_warns_when_cover_frame_is_inside_intro(built):
    from manju.media.packaging import make_package

    pkg = built.load_packaging()
    assert pkg.intro.enabled  # the module fixture built with an intro card
    pkg.teaser.enabled = False
    pkg.cover = CoverSpec(mode="frame", frame_ms=pkg.intro.duration_ms // 2)
    built.save_packaging(pkg)
    result = make_package(built, force=True)
    assert any("inside the" in w and "intro" in w for w in result["warnings"]), (
        result["warnings"])


# ------------------------------ round-O: `manju package` staleness advisory

# A dedicated freshly-built project so the caption-rule edit below can't leak
# into the shared `built` module fixture (which other tests reuse).
@pytest.fixture(scope="module")
def stale_project(tmp_path_factory):
    from manju.build.graph import run_build
    from manju.core.container import Project
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path_factory.mktemp("stale") / "样片", shots=2)
    project = Project(root)
    assert run_build(project, target="final").ok
    return project


@pytestmark_ffmpeg
def test_package_no_stale_warning_right_after_build(stale_project):
    """Nothing changed since the build → the final matches current specs, so no
    staleness advisory (the recompiled key equals the final's sidecar)."""
    from manju.media.packaging import make_package

    result = make_package(stale_project, force=True)
    assert not any("stale" in w for w in result["warnings"]), result["warnings"]


@pytestmark_ffmpeg
def test_package_warns_when_final_is_stale_after_rule_edit(stale_project):
    """Bump a caption rule WITHOUT rebuilding: the newest final now predates the
    current specs (its caption cues re-chunk), so package advises a rebuild.
    --force keeps working exactly as before (bypasses the round-W #56 gate)."""
    from manju.media.packaging import make_package

    rules = stale_project.load_rules()
    rules.captions.max_chars_per_line = 3  # re-chunks the caption track → new key
    stale_project.save_rules(rules)
    # deliberately NO run_build here — the final is intentionally left stale
    result = make_package(stale_project, force=True)
    assert any("stale" in w and "manju build" in w for w in result["warnings"]), (
        result["warnings"])


@pytestmark_ffmpeg
def test_package_refuses_provably_stale_final_by_default(stale_project):
    """round-W #56: without --force, a PROVABLY stale final now REFUSES —
    package no longer silently cuts cover/teaser from an old build."""
    from manju.media.packaging import PackagingError, make_package

    rules = stale_project.load_rules()
    rules.captions.max_chars_per_line = 5  # re-chunks the caption track → new key
    stale_project.save_rules(rules)
    with pytest.raises(PackagingError) as exc:
        make_package(stale_project)  # force=False (default) — must refuse
    assert "manju build" in str(exc.value)
    # --force still works (the escape hatch is unchanged)
    result = make_package(stale_project, force=True)
    assert result["cover"] is not None


def test_cover_key_rekeys_with_card_look_but_frame_mode_is_untouched(monkeypatch):
    """The cover freshness key must fold in the card TEMPLATE's actual CSS for
    mode="card": after a shipped template fix, an old cover.png must read 过期
    in the export center, not fresh-with-the-pre-fix-look. mode="frame" never
    renders through the template, so its key stays byte-identical (no spurious
    staleness on upgrade)."""
    from manju.core.models import CoverSpec, ProjectConfig
    from manju.media import html_card as hc
    from manju.media.packaging import cover_cache_key

    config = ProjectConfig(name="p")
    card = CoverSpec(mode="card", text="封面", template="chapter")
    frame = CoverSpec(mode="frame", frame_ms=1200)

    card_before = cover_cache_key(config, "sha256:f" * 8, card)
    frame_before = cover_cache_key(config, "sha256:f" * 8, frame)

    monkeypatch.setitem(hc.CARD_TEMPLATES, "chapter",
                        hc.CARD_TEMPLATES["chapter"] + "/*look-v2*/")

    assert cover_cache_key(config, "sha256:f" * 8, card) != card_before, \
        "card-mode cover key ignored the template content"
    assert cover_cache_key(config, "sha256:f" * 8, frame) == frame_before, \
        "frame-mode cover key must not depend on card templates"
