"""Round-N review response — regression pins for the confirmed findings.

- newest-final resolution is NUMERIC everywhere (final_v10 beats final_v9);
- timeline/rules.yaml and timeline/packaging.yaml are inside the `manju
  check` safety net (a malformed one is a one-line finding, never a
  traceback — FIX-D);
- the title_card overlay starts at the first CONTENT frame, past an enabled
  packaging intro (no stacked cards over the intro);
- SFX anchors that resolve at/past the film's end are skipped
  deterministically and QC warns (an inaudible hit must not vanish silently);
- the auto ASS font size honors rules.captions.max_chars_per_line (the
  declared budget fits one rendered line); an explicit size stays user truth.
"""

from __future__ import annotations

from manju.core.check import run_check
from manju.core.models import (
    AudioMixRules,
    Dialogue,
    PackagingCard,
    PackagingSpec,
    ProjectConfig,
    SfxClipSpec,
    ShotSpec,
    Timeline,
    TimelineRules,
    TimelineTracks,
    TitleCardRules,
    VideoClip,
)
from manju.exporters.srt_ass import compile_ass
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline

WAV_HEADER = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80\xbb\x00\x00\x00w\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


def _shot(sid: str, *, dur: float = 2.0) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=sid, duration=dur,
                      dialogue=Dialogue(speaker="linxia", text="一句台词。")),
        take_name="take_01",
        take_source=f"media/gen/{sid}/take_01.mp4",
        take_duration_ms=int(dur * 1000),
    )


# ------------------------------------------------- numeric newest-final


def test_newest_final_path_is_numeric_past_v9(tmp_project):
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 9, 10, 11):
        (tmp_project.final_dir / f"final_v{n}.mp4").write_bytes(b"x")
    newest = tmp_project.newest_final_path()
    # lexicographic sort would say final_v9; the resolver must say v11
    assert newest is not None and newest.name == "final_v11.mp4"


def test_newest_final_path_none_when_empty(tmp_project):
    assert tmp_project.newest_final_path() is None


# ----------------------------------- check covers rules + packaging (FIX-D)


def test_check_flags_malformed_packaging_yaml(tmp_project):
    tmp_project.packaging_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_project.packaging_path.write_text(
        "intro:\n  enabled: true\n  duration_ms: not_a_number\n",
        encoding="utf-8",
    )
    report = run_check(tmp_project)  # a finding, never a traceback
    assert any("timeline/packaging.yaml" in e for e in report.errors), report.errors


def test_check_flags_malformed_rules_yaml(tmp_project):
    tmp_project.rules_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_project.rules_path.write_text(
        "timing:\n  min_shot_ms: [broken\n", encoding="utf-8"
    )
    report = run_check(tmp_project)
    assert any("timeline/rules.yaml" in e for e in report.errors), report.errors


# ------------------------------------------- title card shifts past intro


def _compile(rules: TimelineRules, packaging: PackagingSpec | None = None) -> Timeline:
    return compile_timeline(CompileInput(
        config=ProjectConfig(name="t"), rules=rules,
        shots=[_shot("S001"), _shot("S002")], packaging=packaging,
    ))


def test_title_card_starts_after_intro():
    rules = TimelineRules(
        title_card=TitleCardRules(enabled=True, text="影片标题", duration_ms=1500))
    pkg = PackagingSpec(intro=PackagingCard(enabled=True, text="片头", duration_ms=2000))
    tl = _compile(rules, pkg)
    title = next(o for o in tl.tracks.overlay if o.kind == "title_card")
    intro = tl.tracks.video[0]
    assert intro.shot == "__intro__"
    assert title.start_ms == intro.duration_ms  # first CONTENT frame, not 0


def test_title_card_still_at_zero_without_intro():
    rules = TimelineRules(
        title_card=TitleCardRules(enabled=True, text="影片标题", duration_ms=1500))
    tl = _compile(rules)
    title = next(o for o in tl.tracks.overlay if o.kind == "title_card")
    assert title.start_ms == 0


# ------------------------------------- past-the-end SFX: skip + QC warning


def test_sfx_past_film_end_is_skipped():
    rules = TimelineRules(audio=AudioMixRules(sfx=[
        SfxClipSpec(source="media/imports/hit.wav", at="shot:S002:end", offset_ms=100),
        SfxClipSpec(source="media/imports/hit.wav", at="shot:S002:end", offset_ms=-100),
    ]))
    tl = _compile(rules)
    # the +100 lands past total (4000) and is skipped; the −100 stays
    assert [c.start_ms for c in tl.tracks.sfx] == [tl.duration_ms - 100]


def test_qc_warns_on_past_end_sfx_anchor(tmp_project, add_shot):
    from manju.qc.checks import run_qc

    add_shot(tmp_project, "S001")
    sfx = tmp_project.imports_dir / "hit.wav"
    sfx.parent.mkdir(parents=True, exist_ok=True)
    sfx.write_bytes(WAV_HEADER)

    rules = tmp_project.load_rules()
    rules.audio = AudioMixRules(sfx=[
        SfxClipSpec(source="media/imports/hit.wav", at="shot:S001:end", offset_ms=500)])
    tmp_project.save_rules(rules)

    timeline = Timeline(duration_ms=2000, tracks=TimelineTracks(video=[
        VideoClip(shot="S001", take="take_01",
                  source="media/gen/S001/take_01.mp4", start_ms=0, duration_ms=2000),
    ]))
    report = run_qc(tmp_project, timeline, extract_frames=False)
    warns = [i for i in report.items
             if i.level == "warn" and "past the end" in i.message]
    assert warns, [i.message for i in report.items]


# --------------------------------- ASS font size honors max_chars_per_line


def _fontsize(ass: str) -> int:
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    return int(style_line.split(",")[2])


def _tl_with_caption() -> Timeline:
    from manju.core.models import CaptionLine

    return Timeline(width=1080, height=1920, duration_ms=2000,
                    tracks=TimelineTracks(captions=[
                        CaptionLine(start_ms=0, end_ms=1000, text="你好")]))


def test_ass_fontsize_fits_declared_chars():
    tl = _tl_with_caption()
    default = _fontsize(compile_ass(tl, width=1080, height=1920, style={}))
    dense = _fontsize(compile_ass(
        tl, width=1080, height=1920, style={"max_chars_per_line": 24}))
    usable = 1080 - 2 * max(20, 1080 // 20)
    assert dense * 24 <= usable  # 24 declared chars fit one rendered line
    assert dense < default


def test_ass_explicit_size_beats_the_fit():
    tl = _tl_with_caption()
    ass = compile_ass(tl, width=1080, height=1920,
                      style={"size": 90, "max_chars_per_line": 24})
    assert _fontsize(ass) == 90  # user truth, never overridden
