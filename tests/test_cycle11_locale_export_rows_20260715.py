"""Cycle-11: export center lists locale finals as rows."""

from manju.build.exportstatus import deliverables
from manju.core.container import Project


def test_deliverables_includes_locale_final_row(tmp_project: Project) -> None:
    loc = tmp_project.final_dir / "locales" / "fr"
    loc.mkdir(parents=True)
    (loc / "final_v2.mp4").write_bytes(b"x")
    rows = deliverables(tmp_project)
    kinds = [r.kind for r in rows]
    assert "final_locale_fr" in kinds
    fr = next(r for r in rows if r.kind == "final_locale_fr")
    assert fr.openable is True
    assert "fr" in fr.label
