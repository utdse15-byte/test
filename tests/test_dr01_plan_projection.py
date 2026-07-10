"""WP2/WP3 red-first probes — plan projection (p1..p7).

These assert the machine-facing plan surfaces (``build --dry-run`` /
``gui.plan.action_plan``) are DETERMINISTIC, EVIDENCE-INDEPENDENT, keyed by
stable shot identity, and honest about the provider/why/cost/spec_hash a build
will use. p1..p6 EXPECT PASS against current code; p7 EXPECTS RED first (the
dry-run branch leaks an ABSOLUTE ``timeline_path``) and goes green under the
single pre-approved graph.py fix.

Fake media only (no ffmpeg): every probe here rides the dry-run plan, which is
read-only and never renders.
"""

from __future__ import annotations

import os

from manju.build.explain import explain
from manju.build.graph import run_build
from manju.build.stale import ShotState, evaluate_all
from manju.core.models import TakeSidecar
from manju.core.spec import SPEC_VERSION, compute_spec_hash, spec_payload
from manju.gui.plan import action_plan
from manju.providers import routing


def _fresh_shot(project, add_shot, sid, tmp_path):
    """Add a shot AND a selected take whose spec_hash matches the current spec
    under SPEC_VERSION — so ``evaluate_shot`` reads it back as FRESH (cache
    hit). Editing a picture-affecting field afterwards flips it to STALE."""
    shot = add_shot(project, sid)
    bible = project.load_bible()
    h = compute_spec_hash(shot, bible, version=SPEC_VERSION, project_root=project.root)
    media = tmp_path / f"_{sid}.mp4"
    media.write_bytes(b"fakevideo-" + sid.encode("ascii"))
    info = project.register_take(
        sid, media,
        TakeSidecar(
            provider="test", spec_hash=h, spec_version=SPEC_VERSION,
            spec_snapshot=spec_payload(shot, bible, version=SPEC_VERSION,
                                       project_root=project.root),
        ),
    )
    project.update_shot_raw(
        sid, lambda d: d.setdefault("status", {}).__setitem__("selected_take", info.name)
    )
    return info


def _dry_plan(project, *, regen_stale=False):
    return run_build(project, target="final", dry_run=True, regen_stale=regen_stale).plan


# --------------------------------------------------------------------- p1


def test_dr01_p1_semantic_determinism(tmp_project, add_shot):
    """Same project, two dry-run projections → deep-equal (no timestamps /
    run ids / other evidence noise leaks into the machine plan)."""
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")

    env1 = action_plan(tmp_project, "build", {})
    env2 = action_plan(tmp_project, "build", {})
    assert env1 == env2, "action_plan envelope is not deterministic across two dry-runs"

    plan1 = _dry_plan(tmp_project)
    plan2 = _dry_plan(tmp_project)
    assert plan1 == plan2, "run_build dry-run plan is not deterministic"


# --------------------------------------------------------------------- p2


def test_dr01_p2_evidence_noise_independence(tmp_project, add_shot):
    """A spend row landing in the runs ledger between two dry-runs must not
    move the plan — the projection is derived from spec truth, not evidence."""
    from manju.runtime.state import RuntimeState

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")

    before = action_plan(tmp_project, "build", {})
    with RuntimeState(tmp_project.root) as state:
        state.record_run(shot="S001", provider="test", status="succeeded",
                         cost=99.0, currency="CNY")
    after = action_plan(tmp_project, "build", {})

    assert before["rows"] == after["rows"], "ledger noise perturbed the plan rows"
    assert before == after, "ledger noise perturbed the plan envelope"


# --------------------------------------------------------------------- p3


def test_dr01_p3_targeted_invalidation(tmp_project, add_shot, tmp_path):
    """Editing ONE shot's picture-affecting field invalidates only that shot:
    it enters the regen plan as stale; an unrelated planned row (missing S004)
    is byte-identical; the other fresh shots stay fresh (no cross leakage)."""
    for sid in ("S001", "S002", "S003"):
        _fresh_shot(tmp_project, add_shot, sid, tmp_path)
    add_shot(tmp_project, "S004")  # MISSING (no take) — a stable control row

    before = {r["shot"]: r for r in _dry_plan(tmp_project, regen_stale=True)}
    assert "S001" not in before  # fresh → not planned
    assert before.get("S004", {}).get("reason") == "missing"

    shot = tmp_project.load_shot("S001")
    shot.action.main = "她猛地转身撞翻了整排货架"  # picture-affecting (action text)
    tmp_project.save_shot(shot)

    after = {r["shot"]: r for r in _dry_plan(tmp_project, regen_stale=True)}

    # only S001's assessment changed — it is now planned with a stale reason
    assert after.get("S001", {}).get("reason") == "stale"
    # the unrelated planned row is byte-identical (no leakage)
    for k in ("reason", "provider", "estimated_cost", "currency", "duration_ms"):
        assert after["S004"][k] == before["S004"][k], f"S004.{k} changed after editing S001"

    states = {s.shot_id: s.state for s in evaluate_all(tmp_project)}
    assert states["S001"] == ShotState.STALE
    assert states["S002"] == ShotState.FRESH
    assert states["S003"] == ShotState.FRESH


# --------------------------------------------------------------------- p4


def test_dr01_p4_explainability_union(tmp_project, add_shot, tmp_path):
    """For a shot routed by the fallback chain, the union of dry-run row +
    explain() + routing.resolve gives: chosen provider, why, estimated cost,
    and a non-empty input spec_hash (with a take_spec_hash for the take)."""
    _fresh_shot(tmp_project, add_shot, "S001", tmp_path)
    shot = tmp_project.load_shot("S001")
    shot.action.main = "她转身冲向便利店的门口"
    tmp_project.save_shot(shot)  # → STALE, but the take (+ its spec_hash) remain

    plan = _dry_plan(tmp_project, regen_stale=True)
    row = next(r for r in plan if r["shot"] == "S001")
    assert row["reason"] == "stale"
    assert row["provider"]                 # a provider label
    assert "estimated_cost" in row and "currency" in row

    exp = explain(tmp_project)
    ent = next(e for e in exp["shots"] if e["shot"] == "S001")
    assert ent["video"]["state"] == "stale"
    assert ent["video"]["spec_hash"]           # non-empty
    assert ent["video"].get("take_spec_hash")  # present because a take exists
    assert ent["video"].get("why")             # the stale reason

    res = routing.resolve(tmp_project, tmp_project.load_shot("S001"))
    assert res.order and res.chosen            # a concrete provider a build would try


# --------------------------------------------------------------------- p5


def test_dr01_p5_stable_node_identity(tmp_project, add_shot):
    """Plan rows are keyed by shot id: reordering shots/index.yaml changes row
    ORDER but not row CONTENT — a shot's row is identical regardless of index
    position."""
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    add_shot(tmp_project, "S003")

    p1 = _dry_plan(tmp_project)
    order1 = [r["shot"] for r in p1]

    index = tmp_project.load_index()
    index.order = list(reversed(index.order))
    tmp_project.save_index(index)

    p2 = _dry_plan(tmp_project)
    order2 = [r["shot"] for r in p2]

    assert order1 == ["S001", "S002", "S003"]
    assert order2 == ["S003", "S002", "S001"]        # ORDER followed the index
    by1 = {r["shot"]: r for r in p1}
    by2 = {r["shot"]: r for r in p2}
    for sid in ("S001", "S002", "S003"):
        assert by1[sid] == by2[sid], f"row for {sid} changed with its position"


# --------------------------------------------------------------------- p6


def test_dr01_p6_plan_build_provider_parity(tmp_project, add_shot):
    """The provider named in the dry-run row equals the provider a real build
    would try FIRST — checked offline against routing.resolve (the same
    resolution generate_with_fallback performs), mirroring test_director's
    cost-parity pin."""
    add_shot(tmp_project, "S001", generation={"provider": "ffmpeg_kenburns"})

    row = next(r for r in _dry_plan(tmp_project) if r["shot"] == "S001")
    assert row["provider"] == "ffmpeg_kenburns"

    res = routing.resolve(tmp_project, tmp_project.load_shot("S001"))
    assert res.order[0] == "ffmpeg_kenburns"          # what a real build tries first
    assert row["provider"] == res.order[0]            # plan/build parity


# --------------------------------------------------------------------- p7 (RED)


def test_dr01_p7_project_relative_timeline_path(tmp_project, add_shot):
    """EXPECT RED first: the dry-run machine output leaks an ABSOLUTE
    timeline_path (graph.py:791 ``str(project.timeline_path)``). The build
    branch (graph.py:1236) stores ``project.relpath(...)`` — the dry-run must
    match. Goes green under the pre-approved relpath fix."""
    add_shot(tmp_project, "S001")

    tp = run_build(tmp_project, target="final", dry_run=True).to_dict()["timeline_path"]
    assert tp is not None
    assert not os.path.isabs(tp), (
        f"dry-run timeline_path must be project-relative, got absolute {tp!r}"
    )
    assert tp == "timeline/timeline.json"
