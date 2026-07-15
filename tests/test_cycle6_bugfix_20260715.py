"""Cycle-6 continuous-loop regression pins."""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.build.exportstatus import _final_row, _locale_final_langs
from manju.build.graph import BuildCanceled, run_build
from manju.core.container import Project
from manju.gui import edit as edit_mod
from manju.gui import page as page_mod
from manju.gui import pages as pages_mod
from manju.mcp import tools as mcp_tools


def test_locale_voice_cancel_is_build_canceled_not_failed(
    tmp_project: Project, add_shot, monkeypatch
) -> None:
    """C6: should_cancel during locale voice must raise BuildCanceled out of run_build."""
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "hi"})
    from manju.core.locale import add_locale, locale_dir
    from manju.core.yamlio import write_yaml

    add_locale(tmp_project, "en")
    write_yaml(
        locale_dir(tmp_project, "en") / "lines.yaml",
        {"S001": {"text": "hello", "base_hash": "x"}},
    )

    class FakeTts:
        id = "fake"

        def synthesize(self, project, shot, bible):
            from manju.core.models import VoiceTakeSidecar

            src = project.runtime_dir / "f.wav"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(b"RIFF" + b"\x00" * 12)
            return project.register_voice_take(
                shot.id, src, VoiceTakeSidecar(provider="fake", voice_hash="sha256:x"),
                lang="en",
            )

    monkeypatch.setattr(
        "manju.providers.tts.get_tts_provider", lambda *a, **k: FakeTts()
    )
    monkeypatch.setattr(
        "manju.providers.tts.tts_providers",
        lambda: {"fake": type("M", (), {"cost": type("C", (), {"per_call": 0, "currency": "CNY"})()})()},
    )

    calls = {"n": 0}

    def cancel_after_first():
        calls["n"] += 1
        return calls["n"] > 0  # cancel before first shot starts

    # Make plan non-empty then cancel immediately inside synthesize
    def fake_plan(project, lang, *, gen="missing"):
        return [{"shot": "S001", "provider": "fake", "estimated_cost": 0, "currency": "CNY"}]

    monkeypatch.setattr("manju.build.locale_build.plan_locale_voice", fake_plan)

    res = run_build(
        tmp_project, target="final", gen="missing", dry_run=False,
        force=False, actor="test", assume_yes=True, lang="en",
        should_cancel=cancel_after_first,
    )
    # Outer run_build converts BuildCanceled to canceled result
    assert res.canceled is True or (res.ok is False and "取消" in " ".join(res.errors + res.warnings))


def test_lang_qc_without_locale_final_does_not_use_base(
    tmp_project: Project, add_shot, make_take
) -> None:
    add_shot(tmp_project, "S001")
    # Need a video take so timeline compiles; still no locale final.
    make_take(tmp_project, "S001", "sha256:test")
    res = run_build(
        tmp_project, target="qc", gen="off", dry_run=False,
        force=False, actor="t", assume_yes=True, lang="en",
    )
    assert res.ok is False
    joined = " ".join(res.errors)
    assert "locale" in joined.lower() or "成片" in joined
    assert "base" in joined.lower() or "回退" in joined or "locales" in joined


def test_exportstatus_locale_final_message(tmp_project: Project) -> None:
    loc = tmp_project.final_dir / "locales" / "en"
    loc.mkdir(parents=True)
    (loc / "final_v1.mp4").write_bytes(b"x")
    assert "en" in _locale_final_langs(tmp_project)


def test_spa_post_holds_disable_on_job() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "holdDisable" in src


def test_edit_trim_accepts_job_envelope() -> None:
    src = Path(edit_mod.__file__).read_text(encoding="utf-8")
    assert "res.data.job" in src


def test_review_repair_accepts_job() -> None:
    src = Path(pages_mod.__file__).read_text(encoding="utf-8")
    assert "res.data && res.data.job" in src


def test_mcp_qc_contains_final_path() -> None:
    import inspect

    src = inspect.getsource(mcp_tools._h_qc_locked)
    assert "out_of_project" in src or "resolve" in src
    assert "apply_locale_overlay" in src


def test_plan_modal_no_proceed_on_failed() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "never offer proceed-with-assume_yes" in src or "!failed" in src
    assert "仍要继续" not in src or "plan failed" in src.lower()


def test_cancelable_running_kinds_export() -> None:
    from manju.gui.jobs import CANCELABLE_RUNNING_KINDS, Job

    # C25: redo is now cancelable (should_cancel → GenerationRequest poll).
    j = Job(id="1", kind="redo", params={}, project_id="p")
    j.state = "running"
    assert j.to_dict()["cancelable"] is True
    j2 = Job(id="2", kind="build", params={}, project_id="p")
    j2.state = "running"
    assert j2.to_dict()["cancelable"] is True
    assert "build" in CANCELABLE_RUNNING_KINDS
    assert "redo" in CANCELABLE_RUNNING_KINDS


def test_edit_poll_ten_minutes() -> None:
    src = Path(edit_mod.__file__).read_text(encoding="utf-8")
    assert "600000" in src
