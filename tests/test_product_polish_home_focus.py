"""Product-polish gates for a calm, action-first home cockpit.

The cockpit already owns the fresh-project progress summary and the primary
next action.  Auto-opening the older full onboarding panel rendered the same
six steps a second time before the build controls, while the diagnostic
evaluation report occupied another full screen in beginner mode.  These tests
pin the intended information hierarchy without weakening either feature:
both remain available on demand, and evaluation remains visible to pro users.
"""

from __future__ import annotations

from manju.gui.page import render_js
from manju.gui.state import build_state


JS = render_js()


def _function(name: str, next_name: str) -> str:
    start = JS.index(f"function {name}(")
    end = JS.index(f"function {next_name}(", start)
    return JS[start:end]


def test_state_refresh_does_not_auto_open_a_second_onboarding_checklist() -> None:
    """The cockpit summary is the automatic guide; full help is explicit."""
    render_state = _function("render", "renderCockpit")
    assert "maybeOnboarding" not in render_state
    assert "openOnboarding" in JS  # header/cockpit help still opens it
    assert '"/api/onboarding"' in JS


def test_beginner_mode_does_not_fetch_the_diagnostic_evaluation_report() -> None:
    """Hidden diagnostics should not consume startup work in beginner mode."""
    maybe_evaluate = _function("maybeEvaluate", "fetchEvaluate")
    assert 'classList.contains("mj-mode-pro")' in maybe_evaluate


def test_evaluation_is_a_collapsed_pro_detail_not_an_always_open_panel() -> None:
    block = _function("renderEvaluateBlock", "ckBlock")
    assert 'el("details", "ck-block wide mj-pro-only ck-eval")' in block
    assert 'el("summary", "ck-eval-title"' in block
    assert "评估与质量观察" in block


def test_empty_ish_cockpit_stops_after_progress_instead_of_listing_empty_noise() -> None:
    """Before the first take, idle/none/missing support cards add no decision."""
    grid = _function("renderCockGrid", "renderEvaluateBlock")
    first_support = grid.index("/* deliverables strip")
    assert "if (fresh) return grid;" in grid[:first_support]


def test_project_header_does_not_repeat_cockpit_spend_and_next_action() -> None:
    cockpit = _function("renderCockpit", "renderHeroCTA")
    assert 'document.querySelectorAll("#header .spend, #header .next-step")' in cockpit
    assert "node.remove()" in cockpit


class _Runner:
    def list(self):
        return []

    def interrupted(self):
        return []


def test_gui_state_carries_the_current_execution_policy(tmp_project, monkeypatch) -> None:
    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict_zero_cost")
    state = build_state(tmp_project, _Runner())
    assert state["execution_policy"]["mode"] == "strict_zero_cost"
    assert state["execution_policy"]["status"] == "strict"


def test_header_keeps_execution_mode_visible() -> None:
    header = _function("renderHeader", "workspaceChip")
    assert "严格零成本" in header
    assert "标准执行" in header
    assert "执行模式无效" in header
