"""Tests for manju.core.hashing — the single source of truth for spec_hash,
value locks, and cache keys (§4.3, §5)."""

from __future__ import annotations

import hashlib

import pytest

from manju.core.hashing import (
    HASH_PREFIX,
    cache_key,
    canonical_json,
    get_by_path,
    hash_file,
    hash_value,
    short_hash,
)


def test_canonical_json_is_key_order_independent():
    a = canonical_json({"b": 1, "a": 2, "c": 3})
    b = canonical_json({"c": 3, "a": 2, "b": 1})
    assert a == b == '{"a":2,"b":1,"c":3}'


def test_canonical_json_preserves_unicode_literally():
    # ensure_ascii=False: CJK stays literal, never \uXXXX-escaped.
    out = canonical_json({"city": "雨夜", "char": "林夏"})
    assert "雨夜" in out
    assert "林夏" in out
    assert "\\u" not in out


def test_canonical_json_is_compact():
    # compact separators, no incidental whitespace.
    assert canonical_json({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'


def test_hash_value_stable_regardless_of_insertion_order():
    left = hash_value({"a": {"x": 1, "y": 2}, "b": [1, 2, 3]})
    right = hash_value({"b": [1, 2, 3], "a": {"y": 2, "x": 1}})
    assert left == right
    assert left.startswith(HASH_PREFIX)


def test_hash_value_differs_on_content_change():
    assert hash_value({"a": 1}) != hash_value({"a": 2})
    # list order IS significant (it changes the picture / value).
    assert hash_value([1, 2]) != hash_value([2, 1])


def test_get_by_path_nested_dict():
    data = {"dialogue": {"text": "这不可能。", "speaker": "linxia"}}
    assert get_by_path(data, "dialogue.text") == "这不可能。"


def test_get_by_path_into_list():
    data = {"characters": ["linxia", "old_zhou"], "quality": {"avoid": ["黑屏", "多余手指"]}}
    assert get_by_path(data, "characters.1") == "old_zhou"
    assert get_by_path(data, "quality.avoid.0") == "黑屏"


def test_get_by_path_missing_key_raises_keyerror():
    with pytest.raises(KeyError):
        get_by_path({"dialogue": {}}, "dialogue.text")


def test_get_by_path_through_non_container_raises_keyerror():
    with pytest.raises(KeyError):
        get_by_path({"dialogue": "flat"}, "dialogue.text")


def test_cache_key_is_deterministic_and_sensitive():
    k1 = cache_key("src", 1, {"model": "x"})
    k2 = cache_key("src", 1, {"model": "x"})
    k3 = cache_key("src", 2, {"model": "x"})
    assert k1 == k2
    assert k1 != k3
    assert k1.startswith(HASH_PREFIX)


def test_short_hash_strips_prefix_and_truncates():
    full = hash_value({"a": 1})
    short = short_hash(full)
    assert not short.startswith(HASH_PREFIX)
    assert len(short) == 16
    assert full.removeprefix(HASH_PREFIX).startswith(short)
    assert len(short_hash(full, 8)) == 8


def test_hash_file_matches_hashlib(tmp_path):
    payload = "雨夜便利店\n".encode("utf-8") + b"\x00\x01binary"
    f = tmp_path / "clip.bin"
    f.write_bytes(payload)
    expected = HASH_PREFIX + hashlib.sha256(payload).hexdigest()
    assert hash_file(f) == expected
    # stable across calls
    assert hash_file(f) == hash_file(f)
