"""timeline/ compiler robustness — a malformed ``<take>.timing.json`` must never
crash the compile/build (found by the hourly timeline scan).

``_load_voice_timing`` validated only ``isinstance(words, list) and words``, so a
JSON-valid but shape-malformed sidecar (entries missing ``start_ms``/``end_ms``,
or non-dict entries — a legacy/older-format, hand-edited, or partially-written
file) flowed unvalidated into ``_timed_captions``, which indexes
``word["text"]`` / ``group[0]["start_ms"]`` / ``group[-1]["end_ms"]`` directly
and raised an uncaught KeyError/TypeError that aborted ``manju build`` — against
the module's own stated contract that word-timing is "an enhancement, never a
gate". Malformed timing now degrades to the weighted-split fallback.
"""

from __future__ import annotations

from manju.core.models import ProjectConfig, ShotSpec, TimelineRules
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline


def _shot_input(voice_timing):
    shot = ShotSpec.model_validate(
        {"id": "S1", "dialogue": {"speaker": "x", "text": "雨夜便利店"}})
    return ShotInput(
        shot=shot, take_name="t", take_source="gen/S1.mp4",
        take_duration_ms=4000, voice_source="gen/v.wav",
        voice_duration_ms=3000, voice_timing=voice_timing)


def _compile(voice_timing):
    return compile_timeline(CompileInput(
        config=ProjectConfig(name="t"), rules=TimelineRules(),
        shots=[_shot_input(voice_timing)]))


def test_timing_entries_missing_start_ms_degrade_not_crash():
    # entries are dicts but lack start_ms/end_ms -> was KeyError at flush()
    tl = _compile([{"text": "雨夜"}, {"text": "便利店"}])
    assert [c.text for c in tl.tracks.captions] == ["雨夜便利店"]  # weighted-split fallback


def test_timing_non_dict_entries_degrade_not_crash():
    # non-dict entries -> was TypeError ('int' object is not subscriptable)
    tl = _compile([1, 2, 3])
    assert [c.text for c in tl.tracks.captions] == ["雨夜便利店"]


def test_valid_word_timing_still_takes_the_word_timed_path():
    # a well-formed timing list still drives word-timed cues (offset 200 =
    # padding_before; first cue starts at the first word's real start).
    valid = [{"text": "雨夜", "start_ms": 0, "end_ms": 1000},
             {"text": "便利店", "start_ms": 1000, "end_ms": 3000}]
    tl = _compile(valid)
    assert tl.tracks.captions
    assert tl.tracks.captions[0].start_ms == 200  # padding_before + word start 0


def test_is_valid_word_timing_predicate():
    from manju.timeline.compiler import _is_valid_word_timing

    assert _is_valid_word_timing(
        [{"text": "a", "start_ms": 0, "end_ms": 10}]) is True
    # malformed / degenerate forms all reject (-> weighted-split fallback)
    assert _is_valid_word_timing([{"text": "a"}]) is False        # no start/end
    assert _is_valid_word_timing([{"start_ms": 0, "end_ms": 10}]) is False  # no text
    assert _is_valid_word_timing([1, 2, 3]) is False              # non-dict
    assert _is_valid_word_timing([]) is False                     # empty
    assert _is_valid_word_timing(None) is False                   # absent
    assert _is_valid_word_timing(
        [{"text": "a", "start_ms": "0", "end_ms": 10}]) is False  # non-numeric
