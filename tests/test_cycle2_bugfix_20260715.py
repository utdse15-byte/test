"""Cycle-2 continuous-goal regression pins."""

from __future__ import annotations

from pathlib import Path

from manju.build import funnel as funnel_mod
from manju.build.locale_build import synthesize_locale_voices
from manju.core.container import Project
from manju.core.models import RemoteJobInfo, VoiceTakeSidecar
from manju.gui import create_page as create_mod
from manju.gui import series_page as series_mod
from manju.runtime.state import RuntimeState, _iter_voice_media


def test_iter_voice_media_includes_locale(
    tmp_project: Project, add_shot, tmp_path: Path
) -> None:
    add_shot(tmp_project, "S001")
    src = tmp_path / "v.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 12)
    sc = VoiceTakeSidecar(
        provider="tts",
        voice_hash="sha256:x",
        remote=RemoteJobInfo(job_id="j1", cost=1.5, currency="CNY"),
    )
    tmp_project.register_voice_take("S001", src, sc, lang="en")
    labels = [lab for _, _, lab in _iter_voice_media(tmp_project, "S001")]
    assert any(lab.startswith("locales/en/") for lab in labels)


def test_rebuild_counts_voice_spend(
    tmp_project: Project, add_shot, tmp_path: Path
) -> None:
    add_shot(tmp_project, "S001")
    src = tmp_path / "v.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 12)
    sc = VoiceTakeSidecar(
        provider="tts",
        voice_hash="sha256:x",
        remote=RemoteJobInfo(job_id="j1", cost=2.0, currency="CNY"),
    )
    tmp_project.register_voice_take("S001", src, sc)
    # TRISURFACE F-02 surfaced a latent misuse here: RuntimeState takes the
    # PROJECT ROOT (it appends .manju/state.sqlite itself). Passing the db
    # file path only ever "worked" because no real state.sqlite existed at
    # register time — live voice recording now creates it, and the bogus
    # nested path (<db-file>/.manju/state.sqlite) fails loudly. Assertions
    # below are unchanged: rebuild still derives the row and the spend.
    state = RuntimeState(tmp_project.root)
    info = state.rebuild(tmp_project)
    assert info["runs"] >= 1
    rows = state.list_runs() if hasattr(state, "list_runs") else None
    # Direct SQL via cost sum
    total = state._conn.execute("SELECT SUM(cost) AS c FROM runs").fetchone()["c"]
    assert float(total or 0) >= 2.0


def test_synthesize_locale_voices_accepts_should_cancel() -> None:
    import inspect

    sig = inspect.signature(synthesize_locale_voices)
    assert "should_cancel" in sig.parameters


def test_create_skill_has_catch() -> None:
    src = Path(create_mod.__file__).read_text(encoding="utf-8")
    assert ".catch(function" in src
    assert "无法连接本地服务，技能内容没有加载" in src


def test_series_timeout_clears_sticky_status() -> None:
    src = Path(series_mod.__file__).read_text(encoding="utf-8")
    assert "仍在排队/运行(超时)" in src


def test_funnel_locale_honesty_message(
    tmp_project: Project, add_shot, tmp_path: Path
) -> None:
    add_shot(tmp_project, "S001")
    loc = tmp_project.final_dir / "locales" / "en"
    loc.mkdir(parents=True)
    (loc / "final_v1.mp4").write_bytes(b"x")
    ok, msg = funnel_mod._produce_done(tmp_project)
    assert ok is False
    assert "locale" in msg.lower() or "locales" in msg
