"""Cycle-9: director suggestions honest about locale-only finals."""

from manju.build.director import suggest_next
from manju.core.container import Project


def test_director_locale_only_not_raw_missing_final(
    tmp_project: Project, add_shot, make_take
) -> None:
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:test")  # not MISSING so final gate runs
    loc = tmp_project.final_dir / "locales" / "en"
    loc.mkdir(parents=True)
    (loc / "final_v1.mp4").write_bytes(b"x")
    tips = suggest_next(tmp_project)
    texts = [t.text for t in tips]
    joined = " ".join(texts)
    assert "locale" in joined.lower() or "en" in joined
    assert not any(t == "还没有成片 final — 建议构建" for t in texts)
