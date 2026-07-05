"""Tests for manju.timeline.compiler — a pure function (§6).

Same input, same output, always. Times are integer milliseconds; audio drives
picture duration; manual mode makes timeline.json human truth.
"""

from __future__ import annotations

import pytest

from manju.core.models import (
    Dialogue,
    MusicRules,
    ProjectConfig,
    ShotSpec,
    TimelineRules,
)
from manju.timeline.compiler import (
    CompileError,
    CompileInput,
    ShotInput,
    _resolve_duration_ms,
    build_timeline,
    compile_timeline,
)

CAPTION_BUDGET = TimelineRules().captions.max_chars_per_line * TimelineRules().captions.max_lines  # 36


def _shot_input(shot_id="S001", *, duration="auto", text="", take_source="gen/S001/take_01.mp4",
                take_duration_ms=4000, voice_source=None, voice_duration_ms=None) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=shot_id, duration=duration, dialogue=Dialogue(speaker="linxia", text=text)),
        take_name="take_01",
        take_source=take_source,
        take_duration_ms=take_duration_ms,
        voice_source=voice_source,
        voice_duration_ms=voice_duration_ms,
    )


def _input(*shots, rules=None, config=None) -> CompileInput:
    return CompileInput(
        config=config or ProjectConfig(name="t"),
        rules=rules or TimelineRules(),
        shots=list(shots),
    )


# ------------------------------------------------------------- determinism


def test_same_input_yields_byte_identical_json():
    inp = _input(_shot_input("S001"), _shot_input("S002"))
    a = compile_timeline(inp).model_dump_json()
    b = compile_timeline(inp).model_dump_json()
    assert a == b


def test_fingerprint_changes_when_take_source_changes():
    base = _input(_shot_input("S001", take_source="gen/S001/take_01.mp4"))
    changed = _input(_shot_input("S001", take_source="gen/S001/take_02.mp4"))
    assert base.fingerprint() != changed.fingerprint()
    assert compile_timeline(base).meta.compiled_from != compile_timeline(changed).meta.compiled_from


def test_empty_shots_raises_compile_error():
    with pytest.raises(CompileError):
        compile_timeline(_input())


# --------------------------------------------------------- duration resolution


def test_numeric_duration_wins_exactly_without_clamping():
    rules = TimelineRules()
    assert _resolve_duration_ms(_shot_input(duration=2.5), rules) == 2500
    # even below min_shot_ms (1200) an explicit number is honoured verbatim.
    assert _resolve_duration_ms(_shot_input(duration=0.5), rules) == 500


def test_auto_with_voice_uses_voice_plus_padding_clamped():
    rules = TimelineRules()  # padding 200 + 300 = 500, min 1200, max 10000
    assert _resolve_duration_ms(_shot_input(voice_duration_ms=2000), rules) == 2500
    assert _resolve_duration_ms(_shot_input(voice_duration_ms=100), rules) == 1200   # min clamp
    assert _resolve_duration_ms(_shot_input(voice_duration_ms=20000), rules) == 10000  # max clamp


def test_auto_without_voice_uses_take_duration_clamped():
    rules = TimelineRules()
    assert _resolve_duration_ms(_shot_input(voice_duration_ms=None, take_duration_ms=4000), rules) == 4000
    assert _resolve_duration_ms(_shot_input(voice_duration_ms=None, take_duration_ms=500), rules) == 1200


def test_auto_with_nothing_uses_default_shot_ms():
    rules = TimelineRules()
    got = _resolve_duration_ms(_shot_input(voice_duration_ms=None, take_duration_ms=None), rules)
    assert got == rules.timing.default_shot_ms == 3000


# --------------------------------------------------------------- transitions


def test_last_clip_has_no_transition_others_carry_default():
    tl = compile_timeline(_input(_shot_input("S001"), _shot_input("S002"), _shot_input("S003")))
    assert len(tl.tracks.video) == 3
    for clip in tl.tracks.video[:-1]:
        assert clip.transition_out is not None
        assert clip.transition_out.type == "fade"
    assert tl.tracks.video[-1].transition_out is None


def test_clips_are_laid_out_sequentially():
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0), _shot_input("S002", duration=3.0)))
    v0, v1 = tl.tracks.video
    assert v0.start_ms == 0 and v0.duration_ms == 2000
    assert v1.start_ms == 2000 and v1.duration_ms == 3000
    assert tl.duration_ms == 5000


# ------------------------------------------------------------------ captions


def test_captions_follow_voice_window_and_split_long_cjk():
    text = ("雨夜便利店的秘密" * 20)[:80]  # 80 CJK chars, no spaces
    assert len(text) == 80
    voice_ms = 8000
    inp = _input(
        _shot_input("S001", text=text, voice_source="gen/S001/voice.wav", voice_duration_ms=voice_ms)
    )
    tl = compile_timeline(inp)
    caps = tl.tracks.captions

    assert len(caps) > 1  # long text was split
    # no caption line exceeds the on-screen budget (max_chars * max_lines).
    assert all(len(c.text) <= CAPTION_BUDGET for c in caps)

    voice_clip = tl.tracks.voice[0]
    window_start = voice_clip.start_ms
    window_end = window_start + voice_ms
    # captions follow the voice window, not the raw shot start.
    assert caps[0].start_ms == window_start
    assert caps[-1].end_ms <= window_end
    # sequential and non-overlapping.
    for a, b in zip(caps, caps[1:]):
        assert a.start_ms < a.end_ms
        assert a.end_ms <= b.start_ms
    assert all(c.speaker == "linxia" for c in caps)


def test_captions_span_shot_when_no_voice():
    inp = _input(_shot_input("S001", duration=3.0, text="短句一行足够。"))
    tl = compile_timeline(inp)
    caps = tl.tracks.captions
    assert len(caps) == 1
    assert caps[0].start_ms == 0  # no voice: caption spans the shot from its start


def test_no_captions_when_dialogue_empty():
    inp = _input(_shot_input("S001", text=""))
    assert compile_timeline(inp).tracks.captions == []


# --------------------------------------------------------------------- music


def test_music_clip_present_only_when_source_set_and_spans_full_duration():
    rules = TimelineRules(music=MusicRules(source="media/imports/bgm.wav"))
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0), _shot_input("S002", duration=3.0), rules=rules))
    assert len(tl.tracks.music) == 1
    music = tl.tracks.music[0]
    assert music.source == "media/imports/bgm.wav"
    assert music.start_ms == 0
    assert music.duration_ms == tl.duration_ms == 5000  # trimmed to picture length
    assert music.fade_out_ms == rules.music.fade_out_ms == 1500


def test_no_music_clip_when_source_unset():
    tl = compile_timeline(_input(_shot_input("S001")))
    assert tl.tracks.music == []


# ------------------------------------------------ manual takeover (real project)


def test_build_timeline_compiled_then_manual_takeover(tmp_project, add_shot, make_take):
    project = tmp_project
    shot = add_shot(project, "S001")
    take = make_take(project, "S001", "manual")
    shot.status.selected_take = take.name
    project.save_shot(shot)

    probe_fn = lambda p: 2000  # noqa: E731 — stub, no real ffprobe

    # mode=compiled (default): timeline.json is written and truth is overwritten.
    timeline, path, overwrote = build_timeline(project, probe_fn)
    assert overwrote is True
    assert path == project.timeline_path
    assert project.timeline_path.exists()
    assert timeline.meta.mode == "compiled"

    # flip to manual: timeline.json becomes human truth.
    rules = project.load_rules()
    rules.mode = "manual"
    project.save_rules(rules)

    sentinel = '{"human": "hand-edited timeline, do not clobber"}\n'
    project.timeline_path.write_text(sentinel, encoding="utf-8")

    _, gen_path, overwrote2 = build_timeline(project, probe_fn)
    assert overwrote2 is False
    assert gen_path == project.timeline_generated_path
    assert project.timeline_generated_path.exists()
    # the human's timeline.json is untouched (§6).
    assert project.timeline_path.read_text(encoding="utf-8") == sentinel
