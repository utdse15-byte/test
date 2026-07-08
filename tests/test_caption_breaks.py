"""Caption line breaking at the declared budget (round-O, closes the round-N
review finding that max_chars_per_line relied on the renderer's auto-wrap).

- break_lines is pure and lossless: every character survives, no line exceeds
  the budget, breaks prefer punctuation in the tail of the window;
- author newlines are truth: text already containing a break is never re-broken
  (the manual-captions path counts on this);
- compile_srt / compile_ass apply the budget in compiled mode; the manual-mode
  ASS re-emit passes human cues through verbatim.
"""

from __future__ import annotations

from manju.core.models import CaptionLine, Timeline, TimelineTracks
from manju.exporters.srt_ass import break_lines, compile_ass, compile_srt, escape_ass_text


def _tl(text: str) -> Timeline:
    return Timeline(width=1080, height=1920, duration_ms=3000,
                    tracks=TimelineTracks(captions=[
                        CaptionLine(start_ms=0, end_ms=2000, text=text)]))


# ------------------------------------------------------------- break_lines


def test_break_lines_budget_and_losslessness():
    text = "深夜的便利店里,林夏握紧了那块旧手表,窗外霓虹在雨里融化。"
    out = break_lines(text, 12)
    lines = out.split("\n")
    assert all(len(l) <= 12 for l in lines)
    assert out.replace("\n", "") == text.replace(" ", "") or \
        out.replace("\n", "") == text  # lossless (spaces at breaks may drop)


def test_break_lines_prefers_punctuation():
    text = "第一句话,第二句话继续说下去"
    out = break_lines(text, 8)
    # the break lands after the comma (position 5), not hard at 8
    assert out.split("\n")[0] == "第一句话,"


def test_break_lines_hard_cut_without_punctuation():
    text = "一" * 30
    out = break_lines(text, 10)
    assert out.split("\n") == ["一" * 10, "一" * 10, "一" * 10]


def test_break_lines_respects_author_newlines():
    text = "作者自己断的行\n第二行很长很长很长很长很长很长很长很长"
    assert break_lines(text, 5) == text  # never re-broken


def test_break_lines_noop_cases():
    assert break_lines("短句", 10) == "短句"
    assert break_lines("随便什么", None) == "随便什么"
    assert break_lines("随便什么", 0) == "随便什么"


# ------------------------------------------------------- SRT / ASS emission


def test_compile_srt_breaks_at_budget():
    srt = compile_srt(_tl("一" * 20), max_chars_per_line=10)
    cue_lines = srt.splitlines()[2:4]
    assert cue_lines == ["一" * 10, "一" * 10]


def test_compile_srt_without_budget_is_unchanged():
    srt = compile_srt(_tl("一" * 20))
    assert "一" * 20 in srt


def test_compile_ass_breaks_to_hard_N():
    ass = compile_ass(_tl("一" * 20), width=1080, height=1920,
                      style={"max_chars_per_line": 10})
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "一" * 10 + "\\N" + "一" * 10 in dialogue


def test_compile_ass_manual_mode_passthrough():
    ass = compile_ass(_tl("一" * 20), width=1080, height=1920,
                      style={"max_chars_per_line": 10}, apply_line_breaks=False)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "\\N" not in dialogue and "一" * 20 in dialogue


# --------------------------------------------------- round-W #31: ASS injection


def test_escape_ass_text_defeats_override_block():
    text = "普通字幕{\\pos(0,0)}被劫持位置"
    out = escape_ass_text(text)
    assert "{" not in out and "}" not in out and "\\" not in out
    assert "｛＼pos(0,0)｝" in out  # visually near-identical, no longer parseable


def test_escape_ass_text_defeats_bare_hard_break_and_alpha():
    text = r"藏起来的\alpha 标签 {\alpha&HFF&}隐身字幕"
    out = escape_ass_text(text)
    assert "\\" not in out
    assert "{" not in out and "}" not in out


def test_escape_ass_text_noop_on_plain_text():
    text = "一句普通的中文字幕,没有任何花括号或反斜杠。"
    assert escape_ass_text(text) == text


def test_compile_ass_neutralizes_override_tag_in_cue():
    """round-W #31: a cue carrying an ASS override block (from ASR, manual SRT,
    or dialogue text) must not be able to control the burned subtitle's
    position/alpha/style — the override syntax is defeated, not preserved."""
    ass = compile_ass(_tl("台词{\\pos(0,0)\\alpha&HFF&}被注入"),
                      width=1080, height=1920, style={})
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    # no live ASS override syntax reaches the Dialogue Text field
    assert "{\\pos" not in dialogue and "{\\alpha" not in dialogue
    # the text is still legible (full-width lookalikes stand in for the guard)
    assert "台词" in dialogue and "被注入" in dialogue
    assert "｛" in dialogue and "｝" in dialogue


def test_compile_ass_injection_guard_survives_manual_passthrough():
    """The manual-captions re-burn path (apply_line_breaks=False) must also be
    guarded — a hand-typed or ASR-sourced SRT cue is exactly the untrusted
    text §31 is about."""
    ass = compile_ass(_tl("危险字幕{\\pos(0,0)}"), width=1080, height=1920,
                      style={}, apply_line_breaks=False)
    dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert "{\\pos" not in dialogue


def test_compile_srt_is_unaffected_by_injection_guard():
    """SRT has no override-tag grammar to defeat — the raw text (braces and
    all) is preserved verbatim, unlike the ASS path."""
    srt = compile_srt(_tl("台词{\\pos(0,0)}原样"))
    assert "{\\pos(0,0)}" in srt
