"""Product trust pins for candidate generation and human selection."""

from __future__ import annotations


def test_build_stops_before_compile_when_candidates_are_unselected(
    tmp_project, add_shot, make_take
):
    """A generated candidate is reviewable material, never an implicit pick."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001")
    candidate = make_take(tmp_project, "S001", "candidate")

    result = run_build(tmp_project, target="final", gen="off")

    assert result.ok is False
    assert result.selection_required == ["S001"]
    assert "manual selection required" in result.errors[0]
    assert tmp_project.load_shot("S001").status.selected_take is None
    assert candidate.name in {t.name for t in tmp_project.takes("S001")}
    assert result.timeline_path is None
