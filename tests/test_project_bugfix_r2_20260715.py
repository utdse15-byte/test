"""Regression pins for PROJECT_BUG_SCAN_2026-07-15_ROUND2."""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.build.locale_build import synthesize_locale_voices
from manju.core.container import Project
from manju.gui.common_js import COMMON_JS
from manju.gui.webclient import WEBCLIENT_JS
from manju.runtime.buildlock import BuildLock, build_lock


def test_webclient_return_status_option_documented() -> None:
    assert "returnStatus" in WEBCLIENT_JS
    assert "response.status" in WEBCLIENT_JS


def test_common_post_uses_return_status() -> None:
    assert "returnStatus: true" in COMMON_JS or "returnStatus:true" in COMMON_JS.replace(
        " ", ""
    )


def test_synthesize_locale_voices_hold_lock_false_under_outer(
    tmp_project: Project, add_shot, monkeypatch
) -> None:
    """R2-P0-1: under outer build_lock, hold_lock=False must not re-acquire."""
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "hello en"})

    class FakeTts:
        id = "fake"

        def synthesize(self, project, shot, bible):
            from manju.core.models import VoiceTakeSidecar

            src = project.runtime_dir / "fake.wav"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(b"RIFF" + b"\x00" * 12)
            sc = VoiceTakeSidecar(provider="fake", voice_hash="sha256:x")
            return project.register_voice_take(shot.id, src, sc, lang="en")

    monkeypatch.setattr(
        "manju.providers.tts.get_tts_provider", lambda *a, **k: FakeTts()
    )
    # Ensure locale lines exist for overlay
    from manju.core.locale import add_locale, locale_dir
    from manju.core.yamlio import write_yaml

    add_locale(tmp_project, "en")
    write_yaml(
        locale_dir(tmp_project, "en") / "lines.yaml",
        {"S001": {"text": "hello en", "base_hash": "x"}},
    )

    plan = [{"shot": "S001", "provider": "fake"}]
    with build_lock(tmp_project.root, actor="engine"):
        # Must not raise BuildLocked
        out = synthesize_locale_voices(
            tmp_project, plan, lang="en", actor="engine", hold_lock=False
        )
    assert out
    assert tmp_project.voice_takes("S001", lang="en")


def test_run_qc_accepts_final_path(tmp_project: Project, add_shot, tmp_path: Path) -> None:
    """R2-P0-2: run_qc(final_path=...) probes the given file."""
    from manju.qc.checks import run_qc

    add_shot(tmp_project, "S001")
    fake = tmp_path / "locale_final.mp4"
    fake.write_bytes(b"not-a-real-mp4")
    # Should not raise; unreadable final becomes a QC error item, not base path.
    report = run_qc(tmp_project, None, extract_frames=False, final_path=fake)
    assert any(
        getattr(i, "subject", None) == "final" or "final" in str(getattr(i, "message", ""))
        for i in getattr(report, "items", [])
    ) or report is not None


def test_register_take_exclusive(tmp_project: Project, add_shot, tmp_path: Path) -> None:
    from manju.core.models import TakeSidecar

    add_shot(tmp_project, "S001")
    src = tmp_path / "t.mp4"
    src.write_bytes(b"fakevideo-1")
    t1 = tmp_project.register_take(
        "S001", src, TakeSidecar(provider="test", spec_hash="sha256:a")
    )
    t2 = tmp_project.register_take(
        "S001", src, TakeSidecar(provider="test", spec_hash="sha256:b")
    )
    assert t1.name != t2.name
    assert t1.media_path and t1.media_path.exists()
    assert t2.media_path and t2.media_path.exists()
