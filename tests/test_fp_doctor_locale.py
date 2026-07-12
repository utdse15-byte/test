"""FP Loop Y2 — `manju doctor` locale-meta + interchange-exit advisory rows.

Red-first (§20): this pins the NEW doctor rows BEFORE the implementation in
``manju.build.doctor.run_doctor`` is trusted. The rows are ADVISORY — they
surface health as ✓/•/⚠ exactly like the existing toolbelt/disk probes and
NEVER change doctor's exit code (the aggregate ``ok``).

What is pinned:
  * healthy project — one ✓ ``locale:<lang>`` row per locale dir (the declared
    ``direction`` echoed ONLY when set) + one ✓ ``export:<fmt>`` row per
    interchange exit that exists and is well-formed;
  * a broken ``locales/<lang>/meta.yaml`` → the locale row carries
    ``core.locale.load_locale_meta``'s message VERBATIM (consult, never
    re-implement the validation);
  * a corrupt ``exports/<name>.fcpxml`` → the ``export:fcpxml`` row is FLAGGED
    (⚠, row ``ok`` False) — a stdlib well-formedness probe only; semantics stay
    ``exporters.conform``'s job, and the row points there;
  * absent exports → ``•`` absent-is-not-an-error rows (never ✗ failures);
  * EXIT-CODE POLICY UNCHANGED — the aggregate ``ok`` is decided by the SAME
    documented gating set as before (env tools + provider manifests +
    project_check); the new advisory rows, even broken ones, never gate it;
  * the frozen doctor row grammar (``{name, ok, detail, line}`` + glyph prefix)
    holds for every row, new and old.
"""

from __future__ import annotations

import pytest

from manju.build.doctor import run_doctor
from manju.core.container import ProjectError
from manju.core.locale import add_locale, load_locale_meta
from manju.core.models import (
    CaptionLine,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml
from manju.exporters.edl import export_edl
from manju.exporters.fcpxml import export_fcpxml
from manju.exporters.otio import export_otio
from manju.exporters.srt_ass import export_captions
from manju.exporters.ttml import export_ttml

CHECK_KEYS = {"name", "ok", "detail", "line"}
GLYPHS = ("✓", "✗", "•", "⚠")
INTERCHANGE = ("otio", "edl", "fcpxml", "ttml", "vtt")

# The DOCUMENTED doctor gating rule (identical to tests/test_doctor.py's
# ``expected_ok``): the aggregate ``ok`` is decided ONLY by env tools
# (ffmpeg/ffprobe), provider manifests, and the project check. The new
# ``locale:*`` / ``export:*`` advisory names are deliberately absent from it —
# asserting ``result["ok"] is gating_ok(...)`` is the exit-code-policy pin.
GATING = ("ffmpeg", "ffprobe", "project_check", "provider_manifest")


def gating_ok(checks: list[dict]) -> bool:
    ok = True
    for c in checks:
        if c["name"] in GATING or c["name"].startswith("provider:"):
            ok = ok and c["ok"]
    return ok


def by_name(result: dict) -> dict[str, dict]:
    return {c["name"]: c for c in result["checks"]}


@pytest.fixture
def no_manifests(monkeypatch, tmp_path):
    """Hermetic provider scan (mirrors test_doctor.py): an empty manifest dir so
    a developer's real ~/.manju/providers never leaks in and moves ``ok``."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))


def _build_timeline() -> Timeline:
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-y2"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="take_01",
                             source="media/gen/S001/take_01.mp4",
                             start_ms=0, duration_ms=2000)],
            captions=[CaptionLine(start_ms=0, end_ms=2000, text="测试字幕")],
        ),
    )


def _export_all(project) -> None:
    """Produce GENUINE interchange artifacts with the real exporters, so the
    doctor probes are validated against real bytes (not hand-rolled samples)."""
    tl = _build_timeline()
    export_otio(project, tl)          # exports/otio/<name>.otio (JSON)
    export_edl(project, tl)           # exports/edl/<name>.edl   (CMX3600)
    export_fcpxml(project, tl)        # exports/fcpxml/<name>.fcpxml (XML)
    export_ttml(project, tl)          # captions/captions.ttml   (XML)
    export_captions(project, tl)      # captions/captions.{srt,ass,vtt}


# --------------------------------------------------------------- grammar/shape


def test_new_rows_obey_the_frozen_doctor_grammar(no_manifests, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_locale(tmp_project, "en")
    _export_all(tmp_project)
    result = run_doctor(tmp_project)

    assert set(result) == {"checks", "ok"}
    for c in result["checks"]:
        assert set(c) == CHECK_KEYS
        assert isinstance(c["ok"], bool)
        assert isinstance(c["detail"], str) and isinstance(c["line"], str)
        assert c["line"].startswith(GLYPHS)


# ------------------------------------------------------------------- healthy


def test_healthy_locale_and_interchange_rows(no_manifests, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # en declares an explicit LTR direction; ja declares no meta.yaml at all.
    add_locale(tmp_project, "en")
    write_yaml(tmp_project.root / "locales" / "en" / "meta.yaml", {"direction": "ltr"})
    add_locale(tmp_project, "ja")
    _export_all(tmp_project)

    checks = by_name(run_doctor(tmp_project))

    # locale rows: ✓, direction echoed ONLY when declared.
    assert checks["locale:en"]["ok"] is True
    assert checks["locale:en"]["line"].startswith("✓ locale en:")
    assert "direction: ltr" in checks["locale:en"]["detail"]
    assert checks["locale:ja"]["ok"] is True
    assert checks["locale:ja"]["line"].startswith("✓ locale ja:")
    assert "direction: " not in checks["locale:ja"]["detail"]  # none declared

    # interchange rows: present + well-formed → ✓.
    for fmt in INTERCHANGE:
        row = checks[f"export:{fmt}"]
        assert row["ok"] is True, row
        assert row["line"].startswith("✓ ")
    # a well-formed exit still POINTS at conform for semantics (well-formed only).
    assert "conform" in checks["export:fcpxml"]["line"]

    # exit-code policy: aggregate ok is the gating-only verdict.
    assert run_doctor(tmp_project)["ok"] is gating_ok(run_doctor(tmp_project)["checks"])


# ---------------------------------------------------- broken meta (verbatim)


def test_broken_meta_row_carries_message_verbatim(no_manifests, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_locale(tmp_project, "ar")
    write_yaml(tmp_project.root / "locales" / "ar" / "meta.yaml", {"direction": "sideways"})

    # Capture load_locale_meta's EXACT rejection (consult, don't copy).
    with pytest.raises(ProjectError) as ei:
        load_locale_meta(tmp_project, "ar")
    expected = str(ei.value)

    result = run_doctor(tmp_project)
    row = by_name(result)["locale:ar"]
    assert row["ok"] is False
    assert row["line"].startswith("⚠ locale ar:")
    assert expected in row["detail"]      # verbatim, not paraphrased
    assert expected in row["line"]

    # advisory: the broken meta never changes the exit code.
    assert result["ok"] is gating_ok(result["checks"])


# ----------------------------------------------------- corrupt interchange


def test_corrupt_fcpxml_is_flagged_but_advisory(no_manifests, tmp_project):
    name = tmp_project.load_config().name
    p = tmp_project.exports_dir / "fcpxml" / f"{name}.fcpxml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("<fcpxml><unclosed>", encoding="utf-8")  # not well-formed XML

    result = run_doctor(tmp_project)
    row = by_name(result)["export:fcpxml"]
    assert row["ok"] is False
    assert row["line"].startswith("⚠ ")
    assert "conform" in row["line"]  # the row points at the semantics owner

    # a corrupt file is flagged on its OWN row; the exit code is unchanged.
    assert result["ok"] is gating_ok(result["checks"])


# --------------------------------------------------------- absent-not-error


def test_absent_exports_are_absent_not_errors(no_manifests, tmp_project):
    result = run_doctor(tmp_project)  # fresh project: no exports, no locales
    checks = by_name(result)
    for fmt in INTERCHANGE:
        row = checks[f"export:{fmt}"]
        assert row["ok"] is True            # absence is NOT a failure
        assert row["line"].startswith("• ")  # the "absence is a fact" glyph
    assert not any(n.startswith("locale:") for n in checks)  # no locales dir
    assert result["ok"] is gating_ok(result["checks"])


# ----------------------------------------------- exit-code policy (the pin)


def test_exit_code_policy_unchanged_even_with_broken_advisory(
    no_manifests, tmp_project, add_shot
):
    add_shot(tmp_project, "S001")
    # a broken locale meta AND a corrupt interchange file, at once.
    add_locale(tmp_project, "ar")
    write_yaml(tmp_project.root / "locales" / "ar" / "meta.yaml", {"direction": "sideways"})
    name = tmp_project.load_config().name
    otio = tmp_project.exports_dir / "otio" / f"{name}.otio"
    otio.parent.mkdir(parents=True, exist_ok=True)
    otio.write_text("{not valid json", encoding="utf-8")

    result = run_doctor(tmp_project)
    checks = by_name(result)
    # both advisory rows report trouble on their OWN row...
    assert checks["locale:ar"]["ok"] is False
    assert checks["export:otio"]["ok"] is False
    # ...yet the aggregate exit code is still the gating-only verdict.
    assert result["ok"] is gating_ok(result["checks"])
