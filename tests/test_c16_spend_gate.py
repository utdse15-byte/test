"""AI_IDE_16 §10 — the keyframe spend gate (the batch's teeth), proven at the
build dispatch site with a transport spy.

Pinned: under the UNATTENDED profile a paid video while a shot's keyframe is
UNADOPTED is a structured KEYFRAME_NOT_ADOPTED refusal BEFORE any provider
transport (transport=0) — it blocks the WHOLE paid build; assume_yes (a
confirmed proposal) does NOT bypass it. Under COLLABORATIVE it is only an
advisory warning (never blocks). A shot with NO keyframe candidates is
UNAFFECTED (opt-in per shot → old projects unchanged). A free/local plan is
never gated (no paid video to gate).

An unadopted keyframe leaves the shot NEEDS_SELECTION, so a SECOND, plain
MISSING shot (S002) is the paid work whose transport we watch — the gate on
S001 must stop S002's spend too under unattended, and must let it run under
collaborative.
"""

from __future__ import annotations

import pytest

from manju.build import graph
from manju.build.graph import run_build
from manju.providers import registry
from manju.qc import production as prod


@pytest.fixture
def priced(monkeypatch):
    """Every planned shot prices at 5 CNY (as a real cloud manifest would)."""
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (5.0, "CNY"))


@pytest.fixture
def transport_spy(monkeypatch):
    """Count real provider transport. The gate must return BEFORE any call."""
    calls = {"n": 0}
    real = registry.generate_with_fallback

    def spy(req, chain=None, **kw):
        calls["n"] += 1
        return real(req, chain, **kw)

    monkeypatch.setattr(registry, "generate_with_fallback", spy)
    return calls


# ------------------------------------------------------------ unattended teeth


def test_unattended_refuses_paid_build_transport_zero(
        tmp_project, add_shot, make_take, priced, transport_spy):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")  # unadopted keyframe
    add_shot(tmp_project, "S002")                              # plain paid video work
    result = run_build(tmp_project, target="qc", actor="ai",
                       assume_yes=True, agent_profile="unattended")
    assert result.ok is False
    assert any("KEYFRAME_NOT_ADOPTED" in e for e in result.errors)
    assert "S001" in " ".join(result.errors)
    # the teeth: NO transport at all — S002's paid video is stopped too
    assert transport_spy["n"] == 0
    assert not result.generated
    assert prod.video_takes(tmp_project, "S001") == []
    assert prod.video_takes(tmp_project, "S002") == []


def test_unattended_no_keyframe_candidates_is_unaffected(
        tmp_project, add_shot, priced, transport_spy):
    """Opt-in per shot: a project with NO keyframe candidates is never gated —
    the unattended build proceeds exactly as before (old projects unchanged)."""
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, target="qc", actor="ai",
                       assume_yes=True, agent_profile="unattended")
    assert result.selection_required == ["S001"]
    assert transport_spy["n"] >= 1
    assert not any("KEYFRAME_NOT_ADOPTED" in e for e in result.errors)


def test_unattended_adopted_keyframe_proceeds(
        tmp_project, add_shot, make_take, priced, transport_spy):
    add_shot(tmp_project, "S001")
    kf = make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = kf.name     # adopt it → gate cleared
    tmp_project.save_shot(shot)
    add_shot(tmp_project, "S002")
    result = run_build(tmp_project, target="qc", actor="ai",
                       assume_yes=True, agent_profile="unattended")
    assert result.selection_required == ["S002"]
    assert transport_spy["n"] >= 1
    assert not any("KEYFRAME_NOT_ADOPTED" in e for e in result.errors)


# ------------------------------------------------------------ collaborative warn


def test_collaborative_warns_but_never_blocks(
        tmp_project, add_shot, make_take, priced, transport_spy):
    """Collaborative NEVER hard-refuses on the gate: the build proceeds PAST the
    gate into generation (transport runs for the plain shot), only an advisory
    warning is added, and no KEYFRAME_NOT_ADOPTED refusal is emitted. (An
    unadopted-keyframe shot has no video take, so the build may still fail LATER
    at compile — that is a separate, legitimate 'needs video' failure, not the
    gate.)"""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")  # unadopted keyframe
    add_shot(tmp_project, "S002")                              # plain paid work
    result = run_build(tmp_project, target="qc", actor="human",
                       assume_yes=True, agent_profile="collaborative")
    # the gate did NOT block: generation ran (unlike the unattended transport=0)
    assert transport_spy["n"] >= 1
    assert not any("KEYFRAME_NOT_ADOPTED" in e for e in result.errors)
    assert any("keyframe ladder" in w for w in result.warnings)


def test_default_profile_is_collaborative(
        tmp_project, add_shot, make_take, priced):
    """No agent_profile passed (CLI/human default) == collaborative: a dry-run
    surfaces the advisory warning and never refuses."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    result = run_build(tmp_project, dry_run=True, actor="human")
    assert result.ok is True
    assert any("keyframe ladder" in w for w in result.warnings)
    assert not any("KEYFRAME_NOT_ADOPTED" in e for e in result.errors)


def test_dry_run_surfaces_the_gate_warning(
        tmp_project, add_shot, make_take, priced):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    result = run_build(tmp_project, dry_run=True, actor="ai")
    assert result.ok is True                # dry-run never blocks
    assert any("keyframe ladder" in w for w in result.warnings)


def test_free_local_plan_is_not_a_paid_video_gate(
        tmp_project, add_shot, make_take, transport_spy):
    """Without a price (free local fallback) there is no §10 paid video to gate —
    the unattended build proceeds even with an unadopted keyframe."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    result = run_build(tmp_project, target="qc", actor="ai",
                       assume_yes=True, agent_profile="unattended")
    # not gated (free) → no keyframe refusal, and generation is attempted
    assert not any("KEYFRAME_NOT_ADOPTED" in e for e in result.errors)


def test_gen_off_never_gates(
        tmp_project, add_shot, make_take, priced, transport_spy):
    """gen=off produces nothing → there is no paid video request to gate."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    result = run_build(tmp_project, target="qc", actor="ai", gen="off",
                       assume_yes=True, agent_profile="unattended")
    assert not any("KEYFRAME_NOT_ADOPTED" in e for e in result.errors)
    assert transport_spy["n"] == 0


# ----------------------------------------- director_execute wiring (unattended)


def test_director_execute_unattended_refuses_paid_build(
        tmp_project, add_shot, make_take, priced, transport_spy):
    """The one unattended path to paid video is a human-confirmed proposal run
    by director_execute — it must honor the §10 gate under the unattended
    profile (transport 0). Collaborative execute proceeds (warn only)."""
    from manju.build import director as d

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")  # unadopted keyframe

    prop = d.propose(tmp_project, [{"type": "build", "target": "qc", "gen": "missing"}])
    d.confirm(tmp_project, prop.id, actor="human")
    outcome = d.execute(tmp_project, prop.id, agent_profile="unattended")
    assert outcome.ok is False
    assert transport_spy["n"] == 0
    assert "KEYFRAME_NOT_ADOPTED" in (outcome.to_dict().get("diff", "") or "") or \
        any("KEYFRAME_NOT_ADOPTED" in str(r) for r in outcome.to_dict().get("results", []))


def test_director_execute_collaborative_proceeds(
        tmp_project, add_shot, make_take, priced, transport_spy):
    from manju.build import director as d

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    add_shot(tmp_project, "S002")
    prop = d.propose(tmp_project, [{"type": "build", "target": "qc", "gen": "missing"}])
    d.confirm(tmp_project, prop.id, actor="human")
    outcome = d.execute(tmp_project, prop.id)  # default collaborative
    # collaborative does NOT refuse on the gate — generation runs (transport>=1)
    # and no KEYFRAME_NOT_ADOPTED is raised (unlike the unattended path).
    assert transport_spy["n"] >= 1
    assert "KEYFRAME_NOT_ADOPTED" not in str(outcome.to_dict())
