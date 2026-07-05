"""Content QC (§9, v2.2): machine checks first, agent last, engine-driven.

The M3 acceptance case lives here: a shot deliberately violating must_show is
caught by OCR with NO agent in the loop.
"""

from __future__ import annotations

import shutil

import pytest

from manju.qc.content import (
    Verdict,
    check_ocr_assertion,
    mcp_video_gate,
    parse_assertions,
    sample_frames,
    tesseract_available,
)

needs_tools = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not tesseract_available(),
    reason="ffmpeg + tesseract required for OCR assertion tests",
)


# ------------------------------------------------------------------ parser


def test_parse_assertions_tiers():
    assertions = parse_assertions([
        "硬币年份 2036 清晰可读",       # digit run -> machine
        "「这不可能」字样出现在画面",     # quoted -> machine
        "角色林夏在画面中",             # prose -> agent tier
    ])
    kinds = [(a.kind, a.token) for a in assertions]
    assert kinds[0] == ("ocr_text", "2036")
    assert kinds[1] == ("ocr_text", "这不可能")
    assert kinds[2] == ("agent", None)


def test_parse_assertions_empty():
    assert parse_assertions([]) == []


# --------------------------------------------------------- OCR end-to-end


@pytest.fixture(scope="module")
def card_take(tmp_path_factory):
    """A caption card that really contains 'ID-2036' — rendered by the same
    provider chain a degraded shot would use."""
    from manju.media.card import caption_card

    dest = tmp_path_factory.mktemp("card") / "card.mp4"
    caption_card("硬币年份 2036", dest, width=1080, height=1920, fps=24,
                 duration_ms=1500)
    return dest


@needs_tools
def test_ocr_assertion_pass(card_take, tmp_path):
    frames = sample_frames(card_take, tmp_path)
    assert frames, "no frames sampled"
    [assertion] = parse_assertions(["2036 清晰可读"])
    assert check_ocr_assertion(assertion, frames) is Verdict.PASS


@needs_tools
def test_ocr_assertion_fail(card_take, tmp_path):
    frames = sample_frames(card_take, tmp_path)
    [assertion] = parse_assertions(["9999 清晰可读"])
    assert check_ocr_assertion(assertion, frames) is Verdict.FAIL


def test_ocr_unknown_without_frames():
    [assertion] = parse_assertions(["2036 可读"])
    assert check_ocr_assertion(assertion, []) is Verdict.UNKNOWN


# ------------------------------------------- M3 acceptance: no agent needed


@needs_tools
def test_must_show_violation_caught_by_machine(tmp_project, add_shot):
    """§13 M3: 故意让某镜头违背 must_show,机检在 agent 不在场时抓住它。"""
    from manju.build.graph import run_build
    from manju.qc.checks import run_qc

    # a shot whose fallback lands on caption_card (no media, no cloud): the
    # card will contain the dialogue text, NOT the required token 7777
    add_shot(
        tmp_project, "S001",
        dialogue={"speaker": "linxia", "text": "这不可能。"},
        quality={"must_show": ["7777 清晰可读"]},
        duration=1.5,
    )
    result = run_build(tmp_project, target="qc")
    assert result.ok, result.errors  # build itself succeeds (card generated)

    report = run_qc(tmp_project, tmp_project.load_timeline())
    violations = [i for i in report.items
                  if i.level == "error" and i.area == "content" and "7777" in i.message]
    assert violations, "machine OCR did not catch the must_show violation"
    assert violations[0].auto_safe  # repair plan may redo it automatically
    assert not report.ok


@needs_tools
def test_must_show_satisfied_passes(tmp_project, add_shot):
    from manju.build.graph import run_build
    from manju.qc.checks import run_qc

    # dialogue contains 2036 -> the caption card renders it -> OCR finds it
    add_shot(
        tmp_project, "S001",
        dialogue={"speaker": "linxia", "text": "硬币年份 2036"},
        quality={"must_show": ["2036 清晰可读"]},
        duration=1.5,
    )
    assert run_build(tmp_project, target="qc").ok
    report = run_qc(tmp_project, tmp_project.load_timeline())
    assert report.ok, [i.message for i in report.items if i.level == "error"]
    assert any("must_show OK" in i.message for i in report.items)


def test_agent_tier_recorded(tmp_project, add_shot, make_take):
    """Prose assertions the machine cannot check are escalated, not dropped."""
    from manju.core.spec import compute_spec_hash
    from manju.qc.checks import run_qc

    shot = add_shot(tmp_project, "S001",
                    quality={"must_show": ["角色林夏的神态是震惊而克制的"]})
    take = make_take(tmp_project, "S001",
                     compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    report = run_qc(tmp_project, None, extract_frames=False)
    escalations = [i for i in report.items
                   if i.area == "content" and "待 agent 终审" in i.message]
    assert escalations and escalations[0].level == "warn"
    assert "qc_vision" in escalations[0].suggestion  # points at the §8.6 slot


# ------------------------------------------------------------- mcp-video


def test_mcp_video_gate_degrades_not_crashes(tmp_path):
    """Adapter wall (§2.5): whatever mcp-video does on a bogus file, our gate
    returns a tri-state verdict instead of raising."""
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"junk")
    verdict, note = mcp_video_gate(bogus)
    assert verdict in (Verdict.PASS, Verdict.FAIL, Verdict.UNKNOWN)
    assert isinstance(note, str)
