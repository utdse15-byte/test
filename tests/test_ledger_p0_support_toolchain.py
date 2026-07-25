"""Audit-wave P0 regressions — support bundle + toolchain manifest.

Group ``support_toolchain``: SUPPORT-P0-001..005, TOOLCHAIN-P0-001/002.

Red-first pins for the fixes landed in :mod:`manju.core.supportbundle` and
:mod:`manju.core.toolchain`. Each test fails on the pre-fix code and passes
after. The through-line is the honesty contract of a diagnostic artifact: it
carries STRUCTURED facts only, never follows a link off-project on input OR
output, never overwrites truth, and never executes project-supplied code while
probing versions.
"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from manju.core import supportbundle, toolchain
from manju.core.safeio import SafeOutError
from manju.core.supportbundle import BundleError, build_support_bundle


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _iso_providers(monkeypatch, tmp_path):
    """Hermetic provider-manifest dir (mirrors the fp_supportbundle fixture)."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_prov_iso"))


def _write_events(project, records) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (project.root / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _zip_blob(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return "\n".join(zf.read(n).decode("utf-8", "replace") for n in zf.namelist())


def _zip_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


# --------------------------------------------------------------------------- #
# SUPPORT-P0-001 — strict structured-field allowlist, free text dropped         #
# --------------------------------------------------------------------------- #

_PRIVATE_EVENTS = [
    # free text under innocuous keys — client identity, unreleased plot, prompt
    {"actor": "ai", "action": "generate",
     "detail": {"prompt": "CLIENT_CODENAME_RAVEN opens on UNRELEASED_PLOT_TWIST_ECHO",
                "shot": "S001", "n_takes": 3, "ok": True,
                "spec_hash": "sha256:" + "a" * 64}},
    # an uppercase codename parked under an enum-shaped key must STILL be dropped
    {"actor": "engine", "action": "note",
     "detail": {"code": "CLIENT_INTERNAL_INCIDENT_4DD2", "kind": "provider_timeout"}},
]


def test_support_p0_001_ledger_tail_is_structured_allowlist(tmp_project):
    _write_events(tmp_project, _PRIVATE_EVENTS)
    dest = tmp_project.root / "b.zip"
    build_support_bundle(tmp_project, dest)

    blob = _zip_blob(dest)
    for leak in ("CLIENT_CODENAME_RAVEN", "UNRELEASED_PLOT_TWIST_ECHO",
                 "CLIENT_INTERNAL_INCIDENT_4DD2", "prompt"):
        assert leak not in blob, f"free text leaked into bundle: {leak!r}"

    tail = _zip_members(dest)["events-tail.txt"].decode("utf-8")
    recs = [json.loads(line) for line in tail.splitlines()]
    # structured facts survive: enum verbs, counts, booleans, content hashes.
    assert recs[0]["action"] == "generate"
    assert recs[0]["detail"]["n_takes"] == 3
    assert recs[0]["detail"]["ok"] is True
    assert recs[0]["detail"]["spec_hash"].startswith("sha256:")
    # the machine-token enum survives; the uppercase codename under "code" does not.
    assert recs[1]["detail"] == {"kind": "provider_timeout"}
    # every surviving key is on the allowlist — no free-text field slips through.
    allowed = {"ts", "actor", "action", "level", "step", "kind", "code", "detail"}
    for rec in recs:
        assert set(rec) <= allowed


def test_support_p0_001_manifest_states_limited_guarantee(tmp_project):
    dest = tmp_project.root / "g.zip"
    summary = build_support_bundle(tmp_project, dest)
    # the "self-scan passed" claim is downgraded to an explicit LIMITED guarantee.
    assert "allowlist" in summary["guarantee"]
    assert "secondary tripwire" in summary["guarantee"]


# --------------------------------------------------------------------------- #
# SUPPORT-P0-002 — ledger symlink must not exfiltrate an off-project file        #
# --------------------------------------------------------------------------- #

def _skip_if_no_symlink(tmp_path):
    probe = tmp_path / "_lnk_probe"
    try:
        os.symlink(tmp_path / "_lnk_target", probe)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/user")
    probe.unlink()


def test_support_p0_002_events_symlink_is_not_followed(tmp_project, tmp_path):
    _skip_if_no_symlink(tmp_path)
    external = tmp_path / "outside_secret.jsonl"
    external.write_text(
        json.dumps({"actor": "ai", "action": "leak",
                    "detail": {"secret_body": "OFFPROJECT_EXFIL_MARKER_7Q"}}) + "\n",
        encoding="utf-8",
    )
    link = tmp_project.root / "events.jsonl"
    if link.exists():
        link.unlink()
    os.symlink(external, link)

    dest = tmp_project.root / "s.zip"
    summary = build_support_bundle(tmp_project, dest)          # must still build

    assert dest.exists()
    assert "OFFPROJECT_EXFIL_MARKER_7Q" not in _zip_blob(dest)
    # the refusal reason is recorded, and the link was NOT followed into a member.
    assert "events-tail.txt" not in _zip_members(dest)
    reasons = {s["what"]: s["reason"] for s in summary["skipped"]}
    assert "no-follow" in reasons["events-tail.txt"] or "symlink" in reasons["events-tail.txt"]


def test_support_p0_002_failures_symlink_is_not_followed(tmp_project, tmp_path):
    _skip_if_no_symlink(tmp_path)
    external = tmp_path / "outside_failures.jsonl"
    external.write_text("FAILURE_OFFPROJECT_MARKER_ZZ not even json\n", encoding="utf-8")
    reports = tmp_project.reports_dir
    reports.mkdir(parents=True, exist_ok=True)
    os.symlink(external, reports / "failures.jsonl")

    dest = tmp_project.root / "sf.zip"
    summary = build_support_bundle(tmp_project, dest)

    assert "FAILURE_OFFPROJECT_MARKER_ZZ" not in _zip_blob(dest)
    reasons = {s["what"]: s["reason"] for s in summary["skipped"]}
    assert "failures-tail.jsonl" in reasons


# --------------------------------------------------------------------------- #
# SUPPORT-P0-003 — --out may not overwrite truth / a source ledger              #
# --------------------------------------------------------------------------- #

def test_support_p0_003_refuses_to_overwrite_project_yaml(tmp_project):
    truth = tmp_project.root / "project.yaml"
    before = truth.read_bytes()
    with pytest.raises(BundleError):
        build_support_bundle(tmp_project, truth)
    assert truth.read_bytes() == before                       # truth untouched
    assert not truth.read_bytes().startswith(b"PK\x03\x04")    # not a zip


def test_support_p0_003_refuses_to_overwrite_events_ledger(tmp_project):
    _write_events(tmp_project, [{"actor": "ai", "action": "note"}])
    ledger = tmp_project.root / "events.jsonl"
    before = ledger.read_bytes()
    with pytest.raises(BundleError):
        build_support_bundle(tmp_project, ledger)
    assert ledger.read_bytes() == before


def test_support_p0_003_normal_out_still_works_inside_project(tmp_project):
    # a plain top-level zip and an exports/ zip are legal destinations.
    for rel in ("support-bundle.zip", "exports/diag.zip"):
        dest = tmp_project.root / rel
        summary = build_support_bundle(tmp_project, dest)
        assert dest.exists() and "MANIFEST.json" in summary["members"]


# --------------------------------------------------------------------------- #
# SUPPORT-P0-004 — predictable temp / symlink leaf can't capture the write       #
# --------------------------------------------------------------------------- #

def test_support_p0_004_predictable_temp_symlink_does_not_capture(tmp_project, tmp_path):
    _skip_if_no_symlink(tmp_path)
    external = tmp_path / "victim.bin"
    external.write_bytes(b"ORIGINAL-EXTERNAL-CONTENT")
    dest = tmp_project.root / "out.zip"
    # the pre-fix predictable temp name; plant a symlink to an off-project file.
    planted = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    os.symlink(external, planted)

    summary = build_support_bundle(tmp_project, dest)

    assert dest.exists() and not dest.is_symlink()             # real file, in place
    assert external.read_bytes() == b"ORIGINAL-EXTERNAL-CONTENT"  # never captured
    assert "MANIFEST.json" in summary["members"]


def test_support_p0_004_symlink_destination_is_refused(tmp_project, tmp_path):
    _skip_if_no_symlink(tmp_path)
    external = tmp_path / "target.zip"
    external.write_bytes(b"KEEP-ME")
    dest = tmp_project.root / "linked.zip"
    os.symlink(external, dest)
    with pytest.raises(BundleError):
        build_support_bundle(tmp_project, dest)
    assert external.read_bytes() == b"KEEP-ME"                 # not written through


# --------------------------------------------------------------------------- #
# SUPPORT-P0-005 / TOOLCHAIN-P0-001 — version probe never imports project code   #
# --------------------------------------------------------------------------- #

def _plant_malicious_module(dir_path: Path, name: str, marker: Path) -> None:
    (dir_path / f"{name}.py").write_text(
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('pwned', encoding='utf-8')\n"
        "__version__ = 'project-local-pwn'\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("dep_version", [supportbundle._dep_version, toolchain._dep_version])
def test_dep_version_never_imports_a_shadowing_module(dep_version, tmp_path, monkeypatch):
    marker = tmp_path / "MARKER_executed"
    _plant_malicious_module(tmp_path, "evilmod", marker)
    monkeypatch.syspath_prepend(str(tmp_path))          # project root at sys.path[0]
    monkeypatch.delitem(sys.modules, "evilmod", raising=False)

    result = dep_version("evilmod", "no-such-distribution-9f3a2b")

    assert result == "absent"                            # honest, mapped-by-metadata
    assert result != "project-local-pwn"
    assert not marker.exists(), "version probe executed project-supplied code"
    assert "evilmod" not in sys.modules


def test_support_p0_005_building_bundle_does_not_execute_project_module(
        tmp_project, tmp_path, monkeypatch):
    # OpenTimelineIO is absent here, so the old code would fall through to
    # __import__('opentimelineio') and run a project-root shim.
    marker = tmp_path / "otio_marker"
    _plant_malicious_module(tmp_project.root, "opentimelineio", marker)
    monkeypatch.syspath_prepend(str(tmp_project.root))
    monkeypatch.delitem(sys.modules, "opentimelineio", raising=False)
    monkeypatch.chdir(tmp_project.root)

    dest = tmp_path / "diag.zip"                          # outside project, allowed
    summary = build_support_bundle(tmp_project, dest)

    assert not marker.exists(), "support bundle executed a project-local module"
    otio = summary["environment"]["optional_deps"]["otio"]
    assert otio == "absent" and otio != "project-local-pwn"


def test_toolchain_p0_001_manifest_does_not_execute_project_module(
        tmp_project, tmp_path, monkeypatch):
    marker = tmp_path / "otio_marker_tc"
    _plant_malicious_module(tmp_project.root, "opentimelineio", marker)
    monkeypatch.syspath_prepend(str(tmp_project.root))
    monkeypatch.delitem(sys.modules, "opentimelineio", raising=False)
    monkeypatch.chdir(tmp_project.root)

    doc = toolchain.toolchain_manifest()

    assert not marker.exists(), "toolchain manifest executed a project-local module"
    assert doc["facts"]["deps"]["OpenTimelineIO"] == "absent"


# --------------------------------------------------------------------------- #
# TOOLCHAIN-P0-002 — a symlinked reports/toolchain must not write off-project    #
# --------------------------------------------------------------------------- #

def test_toolchain_p0_002_symlinked_dir_write_is_refused(tmp_project, tmp_path):
    _skip_if_no_symlink(tmp_path)
    external = tmp_path / "external_toolchain"
    external.mkdir()
    reports = tmp_project.reports_dir
    reports.mkdir(parents=True, exist_ok=True)
    os.symlink(external, reports / "toolchain")           # linked target dir

    doc = toolchain.toolchain_manifest()
    with pytest.raises(SafeOutError):
        toolchain.write_toolchain_manifest(tmp_project, doc)

    # nothing landed in the off-project directory.
    assert list(external.iterdir()) == []


def test_toolchain_p0_002_normal_write_lands_under_reports(tmp_project, tmp_path):
    doc = toolchain.toolchain_manifest()
    path = toolchain.write_toolchain_manifest(tmp_project, doc)
    assert path.is_file()
    assert path.relative_to(tmp_project.root).parts[:2] == ("reports", "toolchain")
    # deletable derived projection; re-derivable.
    path.unlink()
    assert toolchain.toolchain_manifest() is not None
