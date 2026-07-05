"""Round D: property-based tests over the pure cores.

These pin INVARIANTS rather than examples: canonical hashing is key-order
blind, frame snapping is idempotent/monotone/on-grid, the caption splitter
never loses characters, SRT emit→parse is the identity on valid segments.
"""

from __future__ import annotations

import json
import re

from hypothesis import given, settings
from hypothesis import strategies as st

from manju.core.hashing import canonical_json, get_by_path, hash_value
from manju.providers.asr import TranscriptSegment, parse_srt, segments_to_srt
from manju.providers.jsonpath import extract
from manju.timeline.compiler import _split_caption, snap_to_frame_grid

# ------------------------------------------------------------------ hashing

_scalars = st.one_of(
    st.none(), st.booleans(), st.integers(-10**9, 10**9),
    st.text(max_size=20),  # includes CJK/emoji via general unicode
)
_json_values = st.recursive(
    _scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4),
    ),
    max_leaves=20,
)


def _shuffle_keys(value, rng_seed: int):
    """Rebuild dicts with reversed key insertion order, recursively."""
    if isinstance(value, dict):
        return {k: _shuffle_keys(value[k], rng_seed) for k in reversed(list(value))}
    if isinstance(value, list):
        return [_shuffle_keys(v, rng_seed) for v in value]
    return value


@given(_json_values)
@settings(max_examples=200)
def test_hash_value_is_key_order_blind(value):
    assert hash_value(value) == hash_value(_shuffle_keys(value, 0))


@given(_json_values)
@settings(max_examples=200)
def test_canonical_json_round_trips(value):
    assert json.loads(canonical_json(value)) == value


@given(st.dictionaries(
    st.text(min_size=1, max_size=6).filter(lambda s: "." not in s and "[" not in s and "]" not in s),
    st.integers(), min_size=1, max_size=5,
))
def test_get_by_path_finds_every_top_level_key(d):
    for key, expected in d.items():
        assert get_by_path(d, key) == expected


@given(st.lists(st.integers(0, 99), min_size=1, max_size=5))
def test_jsonpath_list_indexing(values):
    data = {"data": {"items": [{"v": v} for v in values]}}
    for i, v in enumerate(values):
        assert extract(data, f"$.data.items[{i}].v") == v


# --------------------------------------------------------------- frame grid


@given(st.integers(1, 10**7), st.sampled_from([24, 25, 30, 50, 60]))
def test_snap_is_idempotent(ms, fps):
    once = snap_to_frame_grid(ms, fps)
    assert snap_to_frame_grid(once, fps) == once


@given(st.integers(1, 10**7), st.sampled_from([24, 25, 30, 50, 60]))
def test_snap_lands_on_whole_frames(ms, fps):
    snapped = snap_to_frame_grid(ms, fps)
    frames = snapped * fps / 1000.0
    # within ms-quantization of a whole frame count, and never below 1 frame
    assert abs(frames - round(frames)) <= fps / 1000.0 + 1e-9
    assert round(frames) >= 1
    frame_ms = 1000.0 / fps
    if ms >= frame_ms / 2:
        # never further than half a frame (+1ms quantization) from the input
        assert abs(snapped - ms) <= frame_ms / 2 + 1.0
    else:
        # sub-half-frame inputs hit the one-frame floor (hypothesis found
        # ms=1 → 42ms @24fps on the first run — the floor is the point)
        assert snapped == snap_to_frame_grid(1, fps)


@given(st.integers(1, 10**6), st.integers(1, 10**6), st.sampled_from([24, 25, 30, 60]))
def test_snap_is_monotone(a, b, fps):
    lo, hi = sorted((a, b))
    assert snap_to_frame_grid(lo, fps) <= snap_to_frame_grid(hi, fps)


# ----------------------------------------------------------- caption split

_cjk_text = st.text(
    alphabet=st.sampled_from(list("雨夜便利店硬币年份林夏这不可能。!?,、abcdefg ")),
    min_size=0, max_size=200,
)


@given(_cjk_text, st.integers(4, 30), st.integers(1, 3))
@settings(max_examples=200)
def test_split_caption_loses_no_characters(text, max_chars, max_lines):
    pieces = _split_caption(text, max_chars, max_lines)
    strip = lambda s: re.sub(r"\s+", "", s)
    assert strip("".join(pieces)) == strip(text)
    assert all(p == p.strip() and p for p in pieces)


@given(_cjk_text, st.integers(4, 30), st.integers(1, 3))
@settings(max_examples=200)
def test_split_caption_respects_budget(text, max_chars, max_lines):
    budget = max_chars * max_lines
    for piece in _split_caption(text, max_chars, max_lines):
        assert len(piece) <= budget


# ------------------------------------------------------------ SRT roundtrip

_cue_text = st.lists(
    st.text(alphabet=st.sampled_from(list("雨夜台词abc,")), min_size=1, max_size=20)
      .map(str.strip).filter(bool),
    min_size=1, max_size=3,
).map("\n".join)


@st.composite
def _segments(draw):
    n = draw(st.integers(1, 6))
    segments, cursor = [], 0
    for _ in range(n):
        start = cursor + draw(st.integers(0, 5000))
        end = start + draw(st.integers(1, 60_000))
        segments.append(TranscriptSegment(start, end, draw(_cue_text)))
        cursor = end
    return segments


@given(_segments())
@settings(max_examples=100)
def test_srt_emit_parse_identity(segments):
    assert parse_srt(segments_to_srt(segments)) == segments
