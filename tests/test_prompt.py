"""Tests for manju.providers.prompt — prompt compilation (§8.5).

Verbatim override pass-through; deterministic default layout with Bible
excerpts and natural camera phrasing; custom templates tolerant of missing
placeholders; locked bookkeeping never leaks into the prompt.
"""

from __future__ import annotations

from manju.core.models import ShotSpec
from manju.providers.prompt import compile_prompt

# A Bible with `locked` bookkeeping on both entries — it must never surface in
# a compiled prompt (§4.3, §8.5).
BIBLE = {
    "convenience_store": {
        "name": "便利店",
        "description": "雨夜街角的二十四小时便利店,霓虹灯在水洼里融化",
        "lighting": "冷白灯管,窗外霓虹泛蓝",
        "locked": {"lighting": "sha256:deadbeefcafe"},
    },
    "linxia": {
        "name": "林夏",
        "appearance": "短发,黑色风衣,左手戴旧手表",
        "voice": "冷静、克制、略带沙哑",
        "locked": {"appearance": "sha256:0011223344"},
    },
}


def _shot(**overrides) -> ShotSpec:
    data = {
        "id": "S001",
        "scene": "convenience_store",
        "characters": ["linxia"],
        "camera": {"shot_size": "close_up", "movement": "slow_push_in", "angle": "eye_level"},
        "action": {"main": "林夏接过硬币,镜头推进到硬币年份", "emotion": "震惊、克制"},
        "quality": {"must_show": ["硬币年份 2036 清晰可读"], "avoid": ["多余手指", "黑屏"]},
        "dialogue": {"speaker": "linxia", "text": "这不可能。"},
    }
    data.update(overrides)
    return ShotSpec.model_validate(data)


def test_override_returned_verbatim():
    exact = "  Agent-authored prompt for model-X.\nKeep {braces} and spacing.  "
    shot = _shot(generation={"prompt_override": exact})
    # Verbatim: not stripped, not reformatted, braces untouched.
    assert compile_prompt(shot, BIBLE) == exact
    # An override also wins over a custom template.
    assert compile_prompt(shot, BIBLE, template="{scene}") == exact


def test_empty_override_falls_through_to_compilation():
    shot = _shot(generation={"prompt_override": "   "})  # whitespace = not a prompt
    out = compile_prompt(shot, BIBLE)
    assert "便利店" in out  # compiled, not returned verbatim


def test_default_template_contains_key_fields():
    out = compile_prompt(_shot(), BIBLE)
    # scene Bible excerpt values
    assert "便利店" in out
    assert "冷白灯管,窗外霓虹泛蓝" in out
    # character name + Bible excerpt
    assert "林夏" in out
    assert "短发,黑色风衣,左手戴旧手表" in out
    # camera phrases from the fixed mapping
    assert "close-up shot" in out
    assert "slow push-in" in out
    assert "eye-level angle" in out
    # action / emotion / dialogue
    assert "林夏接过硬币,镜头推进到硬币年份" in out
    assert "震惊、克制" in out
    assert "这不可能。" in out
    # quality prefixes + items
    assert "must clearly show: 硬币年份 2036 清晰可读" in out
    assert "avoid: 多余手指, 黑屏" in out


def test_locked_keys_excluded_from_excerpts():
    out = compile_prompt(_shot(), BIBLE)
    assert "locked" not in out
    assert "sha256" not in out


def test_default_layout_skips_empty_sections():
    # a bare shot: no scene/characters/quality/dialogue -> those lines are gone.
    out = compile_prompt(ShotSpec.model_validate({"id": "S002"}), {})
    assert "Scene:" not in out
    assert "Characters:" not in out
    assert "must clearly show" not in out
    assert "Dialogue:" not in out
    # camera has non-empty defaults, so it is present.
    assert "Camera:" in out


def test_custom_template_missing_placeholder_is_blank_not_keyerror():
    tmpl = "{scene} || {camera} || {nonexistent}"
    out = compile_prompt(_shot(), BIBLE, template=tmpl)
    assert "close-up shot" in out
    assert out.endswith("|| ")  # {nonexistent} rendered as empty string
    assert "{" not in out and "}" not in out


def test_custom_template_uses_prefixed_quality_fields():
    out = compile_prompt(_shot(), BIBLE, template="[{must_show}] [{avoid}]")
    assert out == "[must clearly show: 硬币年份 2036 清晰可读] [avoid: 多余手指, 黑屏]"


def test_determinism_independent_of_bible_key_order():
    # Same content as BIBLE, but every dict's keys inserted in a different order.
    shuffled = {
        "linxia": {
            "voice": "冷静、克制、略带沙哑",
            "locked": {"appearance": "sha256:0011223344"},
            "appearance": "短发,黑色风衣,左手戴旧手表",
            "name": "林夏",
        },
        "convenience_store": {
            "lighting": "冷白灯管,窗外霓虹泛蓝",
            "name": "便利店",
            "locked": {"lighting": "sha256:deadbeefcafe"},
            "description": "雨夜街角的二十四小时便利店,霓虹灯在水洼里融化",
        },
    }
    assert compile_prompt(_shot(), BIBLE) == compile_prompt(_shot(), shuffled)
