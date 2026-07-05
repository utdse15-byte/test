"""Round M: word-timed captions — cues snap to the TTS engine's real word
boundaries; the weighted split stays as the fallback for timing-less voices."""

from __future__ import annotations

import json
import shutil

import pytest

from manju.core.models import ProjectConfig, TimelineRules
from manju.timeline.compiler import (
    CompileInput,
    ShotInput,
    _timed_captions,
    compile_timeline,
)
from manju.core.models import ShotSpec

WAV = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
       b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

WORDS = [
    {"start_ms": 0, "end_ms": 400, "text": "凌晨"},
    {"start_ms": 400, "end_ms": 800, "text": "三点"},
    {"start_ms": 820, "end_ms": 900, "text": ","},
    {"start_ms": 950, "end_ms": 1500, "text": "还有人"},
    {"start_ms": 1500, "end_ms": 2000, "text": "醒着吗"},
    {"start_ms": 2000, "end_ms": 2100, "text": "?"},
    {"start_ms": 2400, "end_ms": 3000, "text": "别挂断"},
    {"start_ms": 3000, "end_ms": 3100, "text": "。"},
]


def test_timed_captions_group_and_offset():
    cues = _timed_captions(WORDS, offset_ms=1000, max_chars=18, max_lines=2,
                           speaker="chen")
    # sentence enders break eagerly: 2 cues
    assert [c.text for c in cues] == ["凌晨三点,还有人醒着吗?", "别挂断。"]
    assert cues[0].start_ms == 1000 + 0 and cues[0].end_ms == 1000 + 2100
    assert cues[1].start_ms == 1000 + 2400 and cues[1].end_ms == 1000 + 3100
    assert all(c.speaker == "chen" for c in cues)


def test_timed_captions_respect_budget():
    long_words = [{"start_ms": i * 100, "end_ms": i * 100 + 90, "text": "字"}
                  for i in range(50)]
    cues = _timed_captions(long_words, offset_ms=0, max_chars=5, max_lines=2,
                           speaker="")
    assert all(len(c.text) <= 10 for c in cues)
    assert sum(len(c.text) for c in cues) == 50  # no characters lost
    starts = [c.start_ms for c in cues]
    assert starts == sorted(starts)


def _shot_input(**overrides):
    base = dict(
        shot=ShotSpec.model_validate({
            "id": "S001",
            "dialogue": {"speaker": "chen", "text": "凌晨三点,还有人醒着吗?别挂断。"},
        }),
        take_name="take_01", take_source="media/gen/S001/take_01.mp4",
        take_duration_ms=4000, voice_source="media/gen/S001/voice_take_01.mp3",
        voice_duration_ms=3100,
    )
    base.update(overrides)
    return ShotInput(**base)


def test_compiler_prefers_word_timing_over_weighted_split():
    inp = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                       shots=[_shot_input(voice_timing=WORDS)])
    timeline = compile_timeline(inp)
    texts = [c.text for c in timeline.tracks.captions]
    assert texts == ["凌晨三点,还有人醒着吗?", "别挂断。"]
    # cue 1 starts exactly at voice start (padding_before) + word 0 start
    assert timeline.tracks.captions[0].start_ms == 200 + 0

    # without timing: the weighted split produces a single distributed cue
    inp2 = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                        shots=[_shot_input(voice_timing=None)])
    timeline2 = compile_timeline(inp2)
    assert [c.text for c in timeline2.tracks.captions] != texts or \
        timeline2.tracks.captions[0].end_ms != timeline.tracks.captions[0].end_ms


def test_fingerprint_includes_voice_timing():
    a = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                     shots=[_shot_input(voice_timing=WORDS)])
    b = CompileInput(config=ProjectConfig(name="t"), rules=TimelineRules(),
                     shots=[_shot_input(voice_timing=None)])
    assert a.fingerprint() != b.fingerprint()


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe required")
def test_gather_loads_timing_sidecar(tmp_project, add_shot, make_take):
    """gather_compile_input picks up <voice_take>.timing.json when present."""
    from manju.core.spec import compute_spec_hash
    from manju.timeline.compiler import gather_compile_input

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "chen", "text": "凌晨三点。"})
    take = make_take(tmp_project, "S001",
                     compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    tdir = tmp_project.takes_dir("S001")
    (tdir / "voice_take_01.wav").write_bytes(WAV)
    (tdir / "voice_take_01.timing.json").write_text(
        json.dumps(WORDS, ensure_ascii=False), encoding="utf-8"
    )
    inp = gather_compile_input(tmp_project, lambda p: 3100)
    [entry] = inp.shots
    assert entry.voice_timing == WORDS


def test_alignment_reinserts_dropped_punctuation():
    """Edge zh word boundaries carry no punctuation; alignment against the
    original dialogue glues it back onto the preceding word."""
    from manju.timeline.compiler import _align_words_to_text, _timed_captions

    bare = [
        {"start_ms": 0, "end_ms": 400, "text": "凌晨"},
        {"start_ms": 400, "end_ms": 800, "text": "三点"},
        {"start_ms": 950, "end_ms": 2000, "text": "还有人醒着吗"},
        {"start_ms": 2400, "end_ms": 3000, "text": "别挂断"},
    ]
    text = "凌晨三点,还有人醒着吗?别挂断。"
    enriched = _align_words_to_text(bare, text)
    assert [w["text"] for w in enriched] == ["凌晨", "三点,", "还有人醒着吗?", "别挂断。"]

    cues = _timed_captions(bare, offset_ms=0, max_chars=18, max_lines=2,
                           speaker="chen", original_text=text)
    assert [c.text for c in cues] == ["凌晨三点,还有人醒着吗?", "别挂断。"]
    assert cues[1].start_ms == 2400  # the sentence break came from reinserted "?"


def test_alignment_mismatch_falls_back_to_raw():
    from manju.timeline.compiler import _align_words_to_text

    words = [{"start_ms": 0, "end_ms": 1, "text": "完全不同"}]
    assert _align_words_to_text(words, "对不上的台词") == words
