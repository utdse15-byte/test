"""Regression pins for PROJECT_BUG_SCAN_2026-07-15 (P0/P1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.build.graph import GEN_MODES, _plan_generation
from manju.core.container import Project, ProjectError
from manju.core.library import Library
from manju.core.locale import validate_lang
from manju.core.models import VoiceTakeSidecar
from manju.gui.jobs import RETRYABLE_KINDS, Job


# ---------------------------------------------------------------------------
# P0-3: --lang validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["..", "../x", "en/..", "a/b", r"a\\b", ".", "", "CON"])
def test_validate_lang_rejects_traversal_and_junk(bad: str) -> None:
    with pytest.raises(ProjectError):
        validate_lang(bad)


def test_validate_lang_accepts_bcp47() -> None:
    assert validate_lang("en") == "en"
    assert validate_lang("en-US") == "en-US"
    assert validate_lang("zh-Hans") == "zh-Hans"


def test_voice_takes_dir_refuses_dotdot(tmp_project: Project) -> None:
    with pytest.raises(ProjectError):
        tmp_project._voice_takes_dir("S001", lang="..")


def test_locale_captions_dir_refuses_dotdot(tmp_project: Project) -> None:
    from manju.build.locale_build import locale_captions_dir

    with pytest.raises(ProjectError):
        locale_captions_dir(tmp_project, "..")


# ---------------------------------------------------------------------------
# P1-6: --gen auto means missing + stale
# ---------------------------------------------------------------------------

def test_gen_auto_in_modes() -> None:
    assert "auto" in GEN_MODES


def test_plan_generation_auto_includes_stale(
    tmp_project: Project, add_shot, monkeypatch
) -> None:
    """auto must treat STALE like --regen-stale (not as silent missing)."""
    from manju.build.stale import ShotBuildStatus, ShotState

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    add_shot(tmp_project, "S003")

    statuses = [
        ShotBuildStatus("S001", ShotState.MISSING, "sha256:a", None, None, "no take"),
        ShotBuildStatus("S002", ShotState.STALE, "sha256:b", "take_01", None, "spec changed"),
        ShotBuildStatus("S003", ShotState.FRESH, "sha256:c", "take_01", None, "ok"),
    ]

    monkeypatch.setattr(
        "manju.build.graph._estimate_shot_cost",
        lambda *a, **k: (0.0, "CNY"),
    )
    monkeypatch.setattr(
        "manju.build.graph._routed_shot_for_pricing",
        lambda project, shot, **k: shot,
    )

    plan_missing = _plan_generation(
        tmp_project, statuses, gen="missing", regen_stale=False
    )
    plan_auto = _plan_generation(
        tmp_project, statuses, gen="auto", regen_stale=False
    )
    plan_stale_flag = _plan_generation(
        tmp_project, statuses, gen="missing", regen_stale=True
    )

    shots_missing = {p["shot"] for p in plan_missing}
    shots_auto = {p["shot"] for p in plan_auto}
    shots_flag = {p["shot"] for p in plan_stale_flag}

    assert "S001" in shots_missing
    assert "S002" not in shots_missing
    assert "S001" in shots_auto and "S002" in shots_auto
    assert shots_auto == shots_flag


# ---------------------------------------------------------------------------
# P1-9: retryable only for supported kinds
# ---------------------------------------------------------------------------

def test_retryable_kinds_match_server_allowlist() -> None:
    assert RETRYABLE_KINDS == frozenset({
        "build", "redo", "voice", "redo_batch", "voice_batch",
    })


@pytest.mark.parametrize("kind,expect", [
    ("build", True),
    ("voice", True),
    ("qc", False),
    ("export", False),
    ("repair", False),
    ("ingest", False),
])
def test_job_retryable_flag(kind: str, expect: bool) -> None:
    j = Job(id="t", kind=kind, params={}, project_id="p")
    j.state = "failed"
    assert j.to_dict()["retryable"] is expect
    j.state = "canceled"
    assert j.to_dict()["retryable"] is expect
    j.state = "done"
    assert j.to_dict()["retryable"] is False


# ---------------------------------------------------------------------------
# P1-5: exclusive voice take create
# ---------------------------------------------------------------------------

def test_register_voice_take_exclusive(
    tmp_project: Project, add_shot, tmp_path: Path
) -> None:
    add_shot(tmp_project, "S001")
    sid = "S001"
    src = tmp_path / "v.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 12)
    sc = VoiceTakeSidecar(provider="test", voice_hash="sha256:x")
    dest1 = tmp_project.register_voice_take(sid, src, sc)
    assert dest1.exists()
    dest2 = tmp_project.register_voice_take(
        sid, src, VoiceTakeSidecar(provider="test", voice_hash="sha256:y")
    )
    assert dest2 != dest1
    assert dest2.exists()


# ---------------------------------------------------------------------------
# P1-2: library set_tags / set_note under lock
# ---------------------------------------------------------------------------

def test_library_set_tags_and_note(tmp_path: Path) -> None:
    lib = Library(tmp_path / "lib")
    blob = tmp_path / "a.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    result = lib.add(blob, tags=["t1"], note="n1")
    h = result["entry"]["hash"]
    entry = lib.set_tags(h[:12], ["t2", "t3"])
    assert entry["tags"] == ["t2", "t3"]
    entry = lib.set_note(h[:12], "hello")
    assert entry["note"] == "hello"


# ---------------------------------------------------------------------------
# P1-4: checked_shot_write mutates CAS'd bytes
# ---------------------------------------------------------------------------

def test_checked_shot_write_uses_cas_snapshot(tmp_project: Project, add_shot) -> None:
    from manju.core.writes import checked_shot_write, shot_text_hash

    add_shot(tmp_project, "S001")
    sid = "S001"
    rev = shot_text_hash(tmp_project, sid)

    def mutate(d: dict) -> None:
        d.setdefault("status", {})["review"] = "approved"

    checked_shot_write(tmp_project, sid, mutate, expected_text_hash=rev)
    raw = tmp_project.load_shot_raw(sid)
    assert (raw.get("status") or {}).get("review") == "approved"
