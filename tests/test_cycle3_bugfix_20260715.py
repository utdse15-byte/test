"""Cycle-3 continuous-goal regression pins."""

from __future__ import annotations

from pathlib import Path

from manju.build.locale_build import plan_locale_voice
from manju.gui import edit as edit_mod
from manju.gui import lab_page as lab_mod
from manju.mcp import tools as mcp_tools


def test_mcp_qc_schema_has_lang() -> None:
    qc = mcp_tools.TOOLS["qc"]
    props = qc["inputSchema"]["properties"]
    assert "lang" in props
    assert "final_path" in props


def test_mcp_qc_handler_mentions_final_path() -> None:
    import inspect

    src = inspect.getsource(mcp_tools._h_qc_locked)
    assert "final_path" in src
    assert "newest_locale_final" in src


def test_lab_scaffold_reloads() -> None:
    src = Path(lab_mod.__file__).read_text(encoding="utf-8")
    assert "reloadSoon" in src
    # applyScaffold path
    assert "已写入关键帧" in src
    idx = src.index("已写入关键帧")
    assert "reloadSoon" in src[idx:idx + 120]


def test_lab_generate_accepts_job_without_strict_202() -> None:
    src = Path(lab_mod.__file__).read_text(encoding="utf-8")
    assert "res.status === 202 || res.status === 200" in src


def test_edit_modal_keeps_open_on_fail() -> None:
    src = Path(edit_mod.__file__).read_text(encoding="utf-8")
    assert "keep plan modal open on failure" in src or "C3: keep plan modal" in src


def test_plan_locale_voice_signature_accepts_auto() -> None:
    import inspect

    sig = inspect.signature(plan_locale_voice)
    assert "gen" in sig.parameters
