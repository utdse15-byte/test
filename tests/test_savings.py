"""Cache savings + spend delta (REPORTS/COMPETITIVE-UX-STUDY.md, Nx
replayed-hits pattern): skipped work and its avoided cost must be visible in
the BuildResult.

- FRESH / MANUAL shots are cache hits -> ``result.skipped``.
- ``result.saved_cost`` prices what regenerating the FRESH ones would have
  cost (manual imports were never paid work, so they contribute 0).
- Pricing is advisory: it rides ``_estimate_shot_cost`` and never fails a
  build.
"""

from __future__ import annotations

from manju.build import graph
from manju.build.graph import run_build
from manju.core.spec import compute_spec_hash


def _select_fresh_take(tmp_project, add_shot, make_take, shot_id: str) -> None:
    """Create a shot with a selected take whose spec_hash matches -> FRESH."""
    shot = add_shot(tmp_project, shot_id)
    spec_hash = compute_spec_hash(shot, tmp_project.load_bible())
    take = make_take(tmp_project, shot_id, spec_hash)
    tmp_project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )


def test_fresh_take_is_reported_as_skipped(tmp_project, add_shot, make_take):
    _select_fresh_take(tmp_project, add_shot, make_take, "S001")
    result = run_build(tmp_project, target="qc", gen="off")
    assert result.ok is True
    assert "S001" in result.skipped


def test_saved_cost_prices_the_fresh_cache_hit(tmp_project, add_shot, make_take,
                                               monkeypatch):
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (3.0, "CNY"))
    _select_fresh_take(tmp_project, add_shot, make_take, "S001")
    result = run_build(tmp_project, target="qc", gen="off")
    assert result.ok is True
    assert result.skipped == ["S001"]
    assert result.saved_cost == 3.0
    # the GUI reads result dicts as-is — to_dict must carry the new field
    assert result.to_dict()["saved_cost"] == 3.0


def test_manual_take_is_skipped_but_contributes_zero(tmp_project, add_shot,
                                                     make_take, monkeypatch):
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (3.0, "CNY"))
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")  # MANUAL_HASH import
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )
    result = run_build(tmp_project, target="qc", gen="off")
    assert result.ok is True
    assert "S001" in result.skipped
    assert result.saved_cost == 0.0


def test_missing_shot_is_not_a_cache_hit(tmp_project, add_shot, make_take):
    _select_fresh_take(tmp_project, add_shot, make_take, "S001")
    add_shot(tmp_project, "S002")  # no takes at all -> MISSING
    result = run_build(tmp_project, dry_run=True)  # read-only plan, never gated
    assert "S001" in result.skipped
    assert "S002" not in result.skipped
