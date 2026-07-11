"""AI_IDE_16 §8 — transition / shot-connection preview (VERIFY + PIN only).

Per the addendum ruling 6, THIS batch does not add a J-L cut / sound-bridge audio
model (AI_IDE_18 extends audio). It verifies + pins that a transition CHOICE is a
TIMELINE SOURCE fact (rules.yaml transition_overrides) that the compiler reads
into the compiled clip, and that a generative bridge is proposal-only / inert —
it triggers ZERO transport in this batch (the real bridge execution is
AI_IDE_19's controlled Provider path).
"""

from __future__ import annotations

from manju.core.models import TransitionSpec
from manju.media.probe import probe_duration_ms
from manju.timeline.compiler import compile_timeline, gather_compile_input


def _compile(project):
    return compile_timeline(gather_compile_input(
        project, probe_duration_ms, allow_missing_takes=True))


def _clip(timeline, shot_id):
    return next(c for c in timeline.tracks.video if c.shot == shot_id)


def test_transition_choice_is_a_timeline_source_fact(tmp_project, add_shot):
    """A transition override in rules.yaml (SOURCE) flows into the compiled
    clip's transition_out — the choice is timeline source, not a report."""
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")            # an interior boundary to carry it
    rules = tmp_project.load_rules()
    rules.transition_overrides = {"S001": TransitionSpec(type="xfade_fade", duration_ms=500)}
    tmp_project.save_rules(rules)

    tl = _compile(tmp_project)
    assert _clip(tl, "S001").transition_out.type == "xfade_fade"
    assert _clip(tl, "S001").transition_out.duration_ms == 500
    # it lives in the timeline SOURCE (rules.yaml), surviving any report deletion
    import shutil
    shutil.rmtree(tmp_project.root / "reports", ignore_errors=True)
    assert tmp_project.load_rules().transition_overrides["S001"].type == "xfade_fade"


def test_changing_source_transition_moves_the_compiled_fact(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    rules = tmp_project.load_rules()
    rules.transition_overrides = {"S001": TransitionSpec(type="cut")}
    tmp_project.save_rules(rules)
    assert _clip(_compile(tmp_project), "S001").transition_out.type == "cut"
    # edit the SOURCE → the compiled fact follows (source authority, not a report)
    rules.transition_overrides = {"S001": TransitionSpec(type="xfade_slideleft")}
    tmp_project.save_rules(rules)
    assert _clip(_compile(tmp_project), "S001").transition_out.type == "xfade_slideleft"


def test_generative_bridge_is_proposal_only_transport_zero(
        tmp_project, add_shot, monkeypatch):
    """A generative-bridge transition choice is INERT this batch: it compiles as
    a normal (source) transition and triggers NO provider transport — the actual
    bridge generation is AI_IDE_19's, never this batch's."""
    from manju.build import graph
    from manju.build.graph import run_build
    from manju.providers import registry

    calls = {"n": 0}
    real = registry.generate_with_fallback

    def spy(req, chain=None, **kw):
        calls["n"] += 1
        return real(req, chain, **kw)

    monkeypatch.setattr(registry, "generate_with_fallback", spy)

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    rules = tmp_project.load_rules()
    # a hypothetical generative bridge named as a transition type — lenient
    # validation keeps it as source; the compiler never spawns a request for it.
    rules.transition_overrides = {"S001": TransitionSpec(type="generative_bridge")}
    tmp_project.save_rules(rules)

    tl = _compile(tmp_project)                       # compiles, no crash
    assert _clip(tl, "S001").transition_out.type == "generative_bridge"
    # a dry-run build sees the transition but never plans a bridge generation
    result = run_build(tmp_project, dry_run=True, actor="ai")
    assert result.ok is True
    assert calls["n"] == 0                           # transport 0 — proposal only
