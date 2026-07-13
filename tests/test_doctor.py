"""Tests for the doctor engine (``manju.build.doctor.run_doctor``) — the same
checks ``manju doctor`` prints, returned as plain data so the GUI can serve
them as JSON over HTTP.

Provider-manifest scenarios never touch ``~/.manju``: the environment tests
point ``MANJU_PROVIDERS_DIR`` at an empty temp dir, and the manifest tests
monkeypatch ``load_manifests`` / ``manifest_errors`` with fakes of the real
shapes (``load_manifests() -> (dict[id, manifest], errors)``, manifests with
``.type`` and ``.validate_for_generic() -> list[str]``).
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest

import manju.providers.manifest as manifest_mod
import manju.providers.registry as registry_mod
from manju.build.doctor import run_doctor
from manju.core.check import run_check

CHECK_KEYS = {"name", "ok", "detail", "line"}
GLYPHS = ("✓", "✗", "•", "⚠")


def expected_ok(checks: list[dict[str, Any]]) -> bool:
    """The documented aggregation rule: ffmpeg/ffprobe required (git optional),
    provider manifests gate, project check gates; everything else (cjk_font,
    toolbelt, disk_free, informational entries) never gates."""
    gate = True
    for c in checks:
        if (c["name"] in ("ffmpeg", "ffprobe", "project_check", "provider_manifest")
                or c["name"].startswith("provider:")):
            gate = gate and c["ok"]
    return gate


def by_name(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """First check per name (only provider_manifest can repeat)."""
    out: dict[str, dict[str, Any]] = {}
    for c in result["checks"]:
        out.setdefault(c["name"], c)
    return out


@pytest.fixture
def no_manifests(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Hermetic provider scan: an empty (nonexistent) manifest dir, so a
    developer's real ~/.manju/providers never leaks into these tests."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))


class FakeManifest:
    """The shape run_doctor consumes: ``.type`` plus ``validate_for_generic()``
    (mirrors ``manju.providers.manifest.ProviderManifest``)."""

    type = "video"

    def __init__(self, pid: str, problems: list[str] | None = None) -> None:
        self.id = pid
        self._problems = list(problems or [])

    def validate_for_generic(self) -> list[str]:
        return list(self._problems)


def _patch_providers(monkeypatch: pytest.MonkeyPatch,
                     manifests: dict[str, FakeManifest],
                     load_errors: list[str] | None = None,
                     registry_errors: list[str] | None = None) -> None:
    monkeypatch.setattr(manifest_mod, "load_manifests",
                        lambda: (manifests, list(load_errors or [])))
    monkeypatch.setattr(registry_mod, "manifest_errors",
                        lambda: list(registry_errors or []))


# ------------------------------------------------------------ environment


def test_run_doctor_none_shape_and_env_tools(no_manifests):
    result = run_doctor(None)

    assert set(result) == {"checks", "ok"}
    assert isinstance(result["ok"], bool)
    assert isinstance(result["checks"], list) and result["checks"]
    for c in result["checks"]:
        assert set(c) == CHECK_KEYS
        assert isinstance(c["name"], str)
        assert isinstance(c["ok"], bool)
        assert isinstance(c["detail"], str)
        assert isinstance(c["line"], str)
        assert c["line"].startswith(GLYPHS)

    checks = by_name(result)
    for tool in ("ffmpeg", "ffprobe", "git"):
        found = shutil.which(tool)
        assert checks[tool]["ok"] is (found is not None)
        # W2 §4.5: rows pass the narrow redaction at the add() choke point —
        # a /usr/bin path is identity, but on Windows C:\Users\<name>\...
        # collapses to its basename (no username in a doctor paste).
        from manju.core.supportbundle import redact_private_text
        assert checks[tool]["detail"] == redact_private_text(found or "NOT FOUND")

    # no project given -> environment-only entry, no project-specific checks
    assert checks["project"]["ok"] is True
    assert checks["project"]["detail"] == "no project in cwd (environment checks only)"
    assert checks["project"]["line"] == "• no project in cwd (environment checks only)"
    assert "disk_free" not in checks
    assert "project_check" not in checks

    # empty manifest dir -> the informational providers entry
    assert checks["providers"]["ok"] is True
    assert checks["providers"]["line"] == (
        "• no cloud provider manifests configured (~/.manju/providers, §8.6)")

    assert result["ok"] is expected_ok(result["checks"])


def test_present_tool_lines_start_with_check_glyph(no_manifests):
    checks = by_name(run_doctor(None))
    for tool in ("ffmpeg", "ffprobe", "git"):
        if shutil.which(tool):
            assert checks[tool]["line"].startswith(f"✓ {tool}: ")
        else:
            assert checks[tool]["line"].startswith(f"✗ {tool}: ")
    for binary in ("capcut-cli", "tesseract"):  # toolbelt: absence is a dot, not a cross
        assert checks[binary]["line"].startswith("✓" if shutil.which(binary) else "•")
        assert checks[binary]["ok"] is True


# ---------------------------------------------------------------- project


def test_run_doctor_with_project(no_manifests, tmp_project):
    result = run_doctor(tmp_project)
    checks = by_name(result)

    assert "project" not in checks  # replaced by the real project checks
    assert "disk_free" in checks
    assert checks["disk_free"]["detail"].endswith(" GB")

    report = run_check(tmp_project)
    assert report.ok, report.errors  # the fixture project passes its own check
    assert checks["project_check"]["ok"] is report.ok
    assert checks["project_check"]["detail"] == "ok"
    assert checks["project_check"]["line"] == "✓ project check: ok"
    assert result["ok"] is expected_ok(result["checks"])


def test_failing_project_check_gates_ok(no_manifests, tmp_project, add_shot):
    add_shot(tmp_project, "S01", scene="dark_alley")  # scene not in the bible
    report = run_check(tmp_project)
    assert not report.ok

    result = run_doctor(tmp_project)
    entry = by_name(result)["project_check"]
    assert entry["ok"] is False
    assert entry["detail"] == f"{len(report.errors)} errors"
    assert entry["line"] == f"✗ project check: {len(report.errors)} errors"
    assert result["ok"] is False


# -------------------------------------------------------- provider manifests


def test_provider_manifest_entry_when_manifests_exist(monkeypatch):
    _patch_providers(monkeypatch, {"acme": FakeManifest("acme")})
    result = run_doctor(None)
    checks = by_name(result)

    assert "providers" not in checks  # informational no-manifests entry replaced
    entry = checks["provider:acme"]
    assert entry["ok"] is True
    assert entry["detail"] == "ok"
    assert entry["line"] == "✓ provider acme (video): ok"
    assert result["ok"] is expected_ok(result["checks"])


def test_failing_manifest_validation_flips_ok(monkeypatch):
    _patch_providers(monkeypatch, {"acme": FakeManifest("acme", ["missing url"])})
    result = run_doctor(None)

    entry = by_name(result)["provider:acme"]
    assert entry["ok"] is False
    assert entry["detail"] == "missing url"
    assert entry["line"] == "✗ provider acme (video): missing url"
    assert result["ok"] is False


def test_manifest_load_errors_gate_ok_and_dedupe(monkeypatch):
    _patch_providers(
        monkeypatch, {},
        load_errors=["acme/provider.yaml: bad yaml"],
        registry_errors=["acme/provider.yaml: bad yaml", "acme: no adapter"],
    )
    result = run_doctor(None)

    entries = [c for c in result["checks"] if c["name"] == "provider_manifest"]
    assert [e["detail"] for e in entries] == [
        "acme/provider.yaml: bad yaml", "acme: no adapter"]  # registry dup dropped
    for e in entries:
        assert e["ok"] is False
        assert e["line"] == f"✗ provider manifest: {e['detail']}"
    assert result["ok"] is False


def test_doctor_reports_build_lock_and_caches(tmp_project):
    """doctor reads .manju/build.lock as a plain JSON file (§3: no lock
    machinery needed to REPORT one) and prices the reclaimable render caches.
    The lock is written directly here — the probe only ever reads it."""
    import json

    from manju.build.doctor import run_doctor

    # no lock, no caches -> neither entry
    names = [c["name"] for c in run_doctor(tmp_project)["checks"]]
    assert "build_lock" not in names and "gc_reclaimable" not in names

    (tmp_project.segments_dir).mkdir(parents=True, exist_ok=True)
    (tmp_project.segments_dir / "seg.mp4").write_bytes(b"x" * 2_000_000)
    lock_path = tmp_project.runtime_dir / "build.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps({"pid": 4242, "actor": "human", "started": "2026-07-06T00:00:00+00:00"}),
        encoding="utf-8",
    )
    try:
        info = run_doctor(tmp_project)
        by_name = {c["name"]: c for c in info["checks"]}
        assert "actor=human" in by_name["build_lock"]["detail"]
        assert by_name["build_lock"]["ok"] is True  # informational, never gates
        assert "2.0 MB" in by_name["gc_reclaimable"]["detail"]
        assert info["ok"] is True
    finally:
        lock_path.unlink()
