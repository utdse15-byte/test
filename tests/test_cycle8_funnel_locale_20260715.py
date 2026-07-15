"""Cycle-8: funnel honesty for locale-only finals on plan stage."""

from manju.build.funnel import _plan_done, _produce_done
from manju.core.container import Project


def test_plan_done_true_when_only_locale_final(tmp_project: Project) -> None:
    loc = tmp_project.final_dir / "locales" / "ja"
    loc.mkdir(parents=True)
    (loc / "final_v1.mp4").write_bytes(b"x")
    ok, msg = _plan_done(tmp_project)
    assert ok is True
    assert "locale" in msg.lower() or "ja" in msg


def test_produce_done_still_false_without_base(tmp_project: Project) -> None:
    loc = tmp_project.final_dir / "locales" / "ja"
    loc.mkdir(parents=True)
    (loc / "final_v1.mp4").write_bytes(b"x")
    ok, msg = _produce_done(tmp_project)
    assert ok is False
    assert "locale" in msg.lower() or "locales" in msg or "ja" in msg
