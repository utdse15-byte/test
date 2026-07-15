"""Cycle-1 continuous-goal regression pins (2026-07-15)."""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.build.graph import run_build
from manju.core.container import Project
from manju.core.locale import add_locale, locale_dir
from manju.core.yamlio import write_yaml
from manju.gui import edit as edit_mod
from manju.gui import page as page_mod
from manju.gui import pages as pages_mod
from manju.gui import storyboard as sb_mod


def test_locale_build_hard_fails_when_text_without_voice(
    tmp_project: Project, add_shot, monkeypatch
) -> None:
    """C1-P0: filled locale lines + no TTS/plan must not ship 母语+外语字幕 film."""
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "中文台词"})
    add_locale(tmp_project, "en")
    write_yaml(
        locale_dir(tmp_project, "en") / "lines.yaml",
        {"S001": {"text": "English line", "base_hash": "x"}},
    )
    # No TTS providers → empty plan
    monkeypatch.setattr(
        "manju.providers.tts.tts_providers", lambda: {},
    )
    res = run_build(
        tmp_project, target="final", gen="missing", dry_run=False,
        force=False, actor="test", assume_yes=True, lang="en",
    )
    assert res.ok is False
    joined = " ".join(res.errors)
    assert "有译文无配音" in joined or "English" in joined or "S001" in joined


def test_lang_exports_refused(tmp_project: Project, add_shot) -> None:
    add_shot(tmp_project, "S001")
    res = run_build(
        tmp_project, target="exports", gen="off", dry_run=False,
        force=False, actor="test", assume_yes=True, lang="en",
    )
    assert res.ok is False
    assert any("exports" in e for e in res.errors)


def test_review_redo_sends_assume_yes() -> None:
    src = Path(pages_mod.__file__).read_text(encoding="utf-8")
    assert "assume_yes: true" in src
    assert "确认即批准花费" in src or "确认即批准" in src


def test_storyboard_batch_assume_yes() -> None:
    src = Path(sb_mod.__file__).read_text(encoding="utf-8")
    assert "assume_yes: true" in src


def test_edit_trim_treats_canceled_as_failure() -> None:
    src = Path(edit_mod.__file__).read_text(encoding="utf-8")
    assert 'job.state === "canceled"' in src
    assert "已取消" in src


def test_kbd_hint_includes_n() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert '["n", " 备注' in src or '["n", " 备注 (note)"]' in src


def test_job_badge_waiting_user_label() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "待确认花费" in src
