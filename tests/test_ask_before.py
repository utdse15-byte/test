"""§8.3 ask_before — engine-enforced spend gate.

The design made the agent's discipline the first gate (SKILL.md §5); these
tests pin the engine-side backstop: a plan with estimated cost > 0 stops as
``waiting_user`` unless ``assume_yes`` is passed, uniformly for every actor.
Dry-run stays a pure estimate; zero-cost plans and projects that removed
``expensive_generation`` from ask_before are untouched.
"""

from __future__ import annotations

import pytest

from manju.build import graph
from manju.build.graph import run_build


@pytest.fixture
def priced(monkeypatch):
    """Every planned shot pretends to cost 5 CNY (as a real manifest would)."""
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (5.0, "CNY"))


def test_paid_plan_waits_for_user(tmp_project, add_shot, priced):
    add_shot(tmp_project, "S001")  # missing -> enters the generation plan
    result = run_build(tmp_project, target="qc", actor="ai")
    assert result.ok is False
    assert result.waiting_user is True
    assert any("waiting_user" in e and "5.0" in e for e in result.errors)
    assert not result.generated
    assert not tmp_project.takes("S001")  # nothing was spent or written


def test_assume_yes_proceeds(tmp_project, add_shot, priced):
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, target="qc", actor="ai", assume_yes=True)
    assert result.waiting_user is False
    assert result.generated  # offline fallback chain produced takes


def test_dry_run_is_never_gated(tmp_project, add_shot, priced):
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, dry_run=True, actor="ai")
    assert result.ok is True and result.waiting_user is False
    assert result.estimated_cost == 5.0


def test_per_build_budget_ignores_disposable_historical_ledger(
        tmp_project, add_shot, priced):
    """Historical ledger spend is not an input to the per-build breaker."""
    from manju.runtime.state import RuntimeState

    config = tmp_project.load_config()
    config.budget.limit = 10.0
    tmp_project.save_config(config)
    with RuntimeState(tmp_project.root) as state:
        state.record_run(shot="S000", provider="historical", status="succeeded",
                         cost=99.0, currency="CNY")
    add_shot(tmp_project, "S001")

    result = run_build(tmp_project, dry_run=True, actor="ai")

    assert result.ok is True
    assert result.waiting_user is False
    assert result.estimated_cost == 5.0


def test_zero_cost_plan_is_not_gated(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, target="qc", actor="ai")
    assert result.waiting_user is False


def test_config_opt_out(tmp_project, add_shot, priced):
    config = tmp_project.load_config()
    config.ask_before = ["final_export", "lock_change"]  # gate removed
    tmp_project.save_config(config)
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, target="qc", actor="ai")
    assert result.waiting_user is False


def test_redo_is_gated_too(tmp_project, add_shot, priced):
    from manju.build.graph import WaitingUser, redo_shot

    add_shot(tmp_project, "S001")
    with pytest.raises(WaitingUser) as exc:
        redo_shot(tmp_project, "S001", actor="ai")
    assert "waiting_user" in str(exc.value) and exc.value.estimated_cost == 5.0
    assert not tmp_project.takes("S001")  # nothing spent
    takes = redo_shot(tmp_project, "S001", actor="ai", assume_yes=True)
    assert takes  # explicit yes proceeds through the offline fallback chain


def test_spend_gate_helper(tmp_project):
    from manju.build.graph import WaitingUser, spend_gate

    spend_gate(tmp_project, 0.0, "CNY", assume_yes=False, hint="h")  # free: silent
    spend_gate(tmp_project, 9.0, "CNY", assume_yes=True, hint="h")  # approved
    with pytest.raises(WaitingUser):
        spend_gate(tmp_project, 9.0, "CNY", assume_yes=False, hint="用 --yes")


def test_on_phase_sequence(tmp_project, add_shot, make_take):
    """Coarse build progress for watchers: phases fire in build order."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    phases: list[str] = []
    result = run_build(tmp_project, target="qc", gen="off", on_phase=phases.append)
    assert result.ok is True
    assert phases[0] == "check"
    for expected in ("compile", "qc"):
        assert expected in phases
    assert phases.index("compile") < phases.index("qc")
    # a broken callback never breaks the build
    result = run_build(tmp_project, target="qc", gen="off",
                       on_phase=lambda ph: 1 / 0)
    assert result.ok is True
