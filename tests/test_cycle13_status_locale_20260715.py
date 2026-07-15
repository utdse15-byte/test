"""Cycle-13: project_status reports locale finals."""

from manju.build.status import project_status
from manju.core.container import Project


def test_status_locale_finals_and_note(tmp_project: Project, add_shot) -> None:
    add_shot(tmp_project, "S001")
    loc = tmp_project.final_dir / "locales" / "de"
    loc.mkdir(parents=True)
    (loc / "final_v3.mp4").write_bytes(b"x")
    info = project_status(tmp_project)
    assert "de" in info.get("locale_finals", {})
    assert info["locale_finals"]["de"].endswith("final_v3.mp4")
    assert info.get("latest_final_note") and "locale" in info["latest_final_note"].lower()
