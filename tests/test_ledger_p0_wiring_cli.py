"""Ledger P0/P1 group ``wiring_cli`` — the CLI actually reaches the hardened owners.

The service/core layer already grew the safe seams (``core.safeio``,
``Library.use_into`` / ``find_verified_blob``, ``media.relink.write_relink_plan``,
``build.bridge.write_bridge_plan``, ``build.pullsheet``, ``qc.report``,
``core.toolchain``), but ``cli.py`` was still calling the old bare-write paths,
so none of it was reachable from the command line. Every test here drives the
COMMAND (not the module) and pins two things:

1. a refusal surfaces through the stable ``--json`` envelope with a non-zero
   exit — never a traceback, never a silent success;
2. bytes that failed verification are never used, and the pre-existing file at a
   refused destination is left intact.

Entries: LIBRARY-P0-002 (`lib use`), INGEST-P0-002 (`import --on-duplicate
link`), RELINK-P0-001 (`relink plan --out`), BRIDGE-P0-001 (`bridge plan
--out`), STATE-P0-001 (`tasks retry|attach-remote-job|abandon`), QC-P0-001
(`qc`), PULLSHEET-P0-001 (`export --pullsheet`), TOOLCHAIN-P0-002 (`toolchain
--write`), WINCLI-P1-021 (`director run` exit code), CLI-P1-004 (`new`).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.library import Library, _hex

runner = CliRunner()

POSIX = os.name == "posix"
needs_symlink = pytest.mark.skipif(not POSIX, reason="symlink escape shapes need POSIX links")

# a genuine 1x1 PNG — bridge endpoint intake checks real magic bytes.
_MIN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """cwd-anchor a fresh project so ``_project()`` discovers it from cwd."""
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


@pytest.fixture
def iso_lib(tmp_path, monkeypatch):
    """Point MANJU_LIBRARY at a tmp shelf so the real ~/.manju is untouched."""
    root = tmp_path / "user_lib"
    monkeypatch.setenv("MANJU_LIBRARY", str(root))
    return root


def _envelope(result) -> dict:
    """The ``--json`` error object — proves the refusal is structured, not a
    traceback leaking through Typer's exception hook."""
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"refusal escaped as a traceback: {result.exception!r}")
    return json.loads(result.stdout)


def _truth_bytes(project) -> bytes:
    return (project.root / "project.yaml").read_bytes()


# ==================================================== LIBRARY-P0-002 (lib use)


def test_lib_use_refuses_substituted_blob_and_writes_nothing(in_project, iso_lib, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"real-library-bytes")
    lib = Library()
    entry = lib.add(src)["entry"]
    hash8 = _hex(entry["hash"])[:8]
    # the blob path stays contained, but its bytes no longer hash to the index row
    (lib.root / entry["blob"]).write_bytes(b"SUBSTITUTED-BYTES")

    result = runner.invoke(app, ["lib", "use", hash8, "--json"])
    assert result.exit_code != 0, result.stdout
    env = _envelope(result)
    assert "error" in env and "code" in env
    # nothing of the unverified content landed in the project
    landed = list((in_project.root / "media" / "refs").glob("*"))
    assert landed == [], landed
    assert b"SUBSTITUTED-BYTES" not in b"".join(
        p.read_bytes() for p in in_project.root.rglob("*") if p.is_file())


@needs_symlink
def test_lib_use_refuses_blob_row_pointing_outside_the_shelf(in_project, iso_lib, tmp_path):
    secret = tmp_path / "outside-secret.txt"
    secret.write_bytes(b"TOP-SECRET-OUTSIDE")
    src = tmp_path / "seed.mp4"
    src.write_bytes(b"seed-bytes")
    lib = Library()
    entry = lib.add(src)["entry"]
    hash8 = _hex(entry["hash"])[:8]
    # forge the row's blob into a link that escapes the shelf
    blob = lib.root / entry["blob"]
    blob.unlink()
    blob.symlink_to(secret)

    result = runner.invoke(app, ["lib", "use", hash8, "--json"])
    assert result.exit_code != 0, result.stdout
    _envelope(result)
    assert list((in_project.root / "media" / "refs").glob("*")) == []


def test_lib_use_still_copies_a_good_blob(in_project, iso_lib, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"good-library-bytes")
    entry = Library().add(src)["entry"]
    hash8 = _hex(entry["hash"])[:8]

    result = runner.invoke(app, ["lib", "use", hash8, "--json"])
    assert result.exit_code == 0, result.stdout
    dest = in_project.root / "media" / "refs" / "clip.mp4"
    assert dest.read_bytes() == b"good-library-bytes"


# ============================================ INGEST-P0-002 (import --on-duplicate link)


def test_import_link_falls_back_to_dropped_file_when_blob_is_forged(
        in_project, iso_lib, tmp_path):
    drop = tmp_path / "shot.mp4"
    drop.write_bytes(b"dropped-honest-bytes")
    lib = Library()
    entry = lib.add(drop)["entry"]
    # forge the shelf: same recorded hash, different actual bytes
    (lib.root / entry["blob"]).write_bytes(b"FORGED-LIBRARY-BYTES")

    result = runner.invoke(
        app, ["import", str(drop), "--on-duplicate", "link", "--json"])
    assert result.exit_code == 0, result.stdout
    landed = in_project.root / "media" / "imports" / "shot.mp4"
    # the dropped bytes were used, NOT the substituted shelf bytes
    assert landed.read_bytes() == b"dropped-honest-bytes"
    payload = json.loads(result.stdout)
    assert any("provenance: dropped" in n for n in payload["library_duplicates"])


def test_import_link_uses_the_shelf_when_the_blob_verifies(in_project, iso_lib, tmp_path):
    drop = tmp_path / "shot.mp4"
    drop.write_bytes(b"identical-bytes")
    Library().add(drop)

    result = runner.invoke(
        app, ["import", str(drop), "--on-duplicate", "link", "--json"])
    assert result.exit_code == 0, result.stdout
    landed = in_project.root / "media" / "imports" / "shot.mp4"
    assert landed.read_bytes() == b"identical-bytes"
    payload = json.loads(result.stdout)
    assert any("provenance: library" in n for n in payload["library_duplicates"])


# ================================================== RELINK-P0-001 (relink plan --out)


def test_relink_plan_out_refuses_project_truth_via_cli(in_project, tmp_path):
    before = _truth_bytes(in_project)
    scan = tmp_path / "backup"
    scan.mkdir()
    result = runner.invoke(app, ["relink", "plan", "--root", str(scan),
                                 "--out", "project.yaml", "--json"])
    assert result.exit_code != 0, result.stdout
    env = _envelope(result)
    assert env["code"] == "bad_out", env
    assert _truth_bytes(in_project) == before


def test_relink_plan_out_into_exports_is_written(in_project, tmp_path):
    scan = tmp_path / "backup"
    scan.mkdir()
    result = runner.invoke(app, ["relink", "plan", "--root", str(scan),
                                 "--out", "exports/relink.json", "--json"])
    assert result.exit_code == 0, result.stdout
    written = in_project.root / "exports" / "relink.json"
    assert json.loads(written.read_text(encoding="utf-8"))["summary"] is not None


# ================================================== BRIDGE-P0-001 (bridge plan --out)


def _bridge_frames(project) -> tuple[Path, Path]:
    d = project.root / "media" / "refs"
    d.mkdir(parents=True, exist_ok=True)
    a, b = d / "a.png", d / "b.png"
    a.write_bytes(_MIN_PNG)
    b.write_bytes(_MIN_PNG + b"\x00")
    return a, b


def test_bridge_plan_out_refuses_project_truth_via_cli(in_project):
    a, b = _bridge_frames(in_project)
    before = _truth_bytes(in_project)
    result = runner.invoke(app, ["bridge", "plan", "--prev", str(a), "--next", str(b),
                                 "--duration-ms", "400", "--out", "project.yaml",
                                 "--json"])
    assert result.exit_code != 0, result.stdout
    env = _envelope(result)
    assert env["code"] == "bad_out", env
    assert _truth_bytes(in_project) == before


def test_bridge_plan_out_into_exports_is_written(in_project):
    a, b = _bridge_frames(in_project)
    result = runner.invoke(app, ["bridge", "plan", "--prev", str(a), "--next", str(b),
                                 "--duration-ms", "400",
                                 "--out", "exports/bridge.json", "--json"])
    assert result.exit_code == 0, result.stdout
    written = in_project.root / "exports" / "bridge.json"
    assert "request_digest" in json.loads(written.read_text(encoding="utf-8"))


# ============================================ STATE-P0-001 (tasks * ledger open)


def _poison_ledger(project, tmp_path) -> None:
    """Aim ``.manju/state.sqlite`` at an external file — RuntimeState refuses."""
    external = tmp_path / "external.sqlite"
    (project.root / ".manju").mkdir(parents=True, exist_ok=True)
    db = project.root / ".manju" / "state.sqlite"
    if db.exists():
        db.unlink()
    db.symlink_to(external)


@needs_symlink
@pytest.mark.parametrize("argv", [
    ["tasks", "retry", "1", "--json"],
    ["tasks", "attach-remote-job", "sub-1", "job-1", "--json"],
    ["tasks", "abandon", "sub-1", "--reason", "gone", "--json"],
])
def test_tasks_commands_refuse_a_poisoned_ledger_with_an_envelope(
        in_project, tmp_path, argv):
    _poison_ledger(in_project, tmp_path)
    result = runner.invoke(app, argv)
    assert result.exit_code != 0, result.stdout
    env = _envelope(result)
    assert env["code"] == "bad_out", env
    assert not (tmp_path / "external.sqlite").exists()  # never initialized outside


# =========================================================== QC-P0-001 (qc)


@needs_symlink
def test_qc_refuses_a_symlinked_reports_dir_with_an_envelope(in_project, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    reports = in_project.root / "reports"
    shutil.rmtree(reports)
    reports.symlink_to(outside, target_is_directory=True)

    result = runner.invoke(app, ["qc", "--json"])
    assert result.exit_code != 0, result.stdout
    env = _envelope(result)
    assert env["code"] == "bad_out", env
    assert list(outside.iterdir()) == []  # nothing published through the link


# ============================================ PULLSHEET-P0-001 (export --pullsheet)


@needs_symlink
def test_export_pullsheet_refuses_a_symlinked_exports_dir_with_an_envelope(
        in_project, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    exports = in_project.root / "exports"
    shutil.rmtree(exports)
    exports.symlink_to(outside, target_is_directory=True)

    result = runner.invoke(app, ["export", "--pullsheet", "--yes", "--json"])
    assert result.exit_code != 0, result.stdout
    env = _envelope(result)
    assert env["code"] == "bad_out", env
    assert not list(outside.rglob("*.csv"))


# ==================================================== TOOLCHAIN-P0-002 (--write)


@needs_symlink
def test_toolchain_write_refuses_a_symlinked_reports_dir_with_an_envelope(
        in_project, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    reports = in_project.root / "reports"
    shutil.rmtree(reports)
    reports.symlink_to(outside, target_is_directory=True)

    result = runner.invoke(app, ["toolchain", "--write", "--json"])
    assert result.exit_code != 0, result.stdout
    env = _envelope(result)
    assert env["code"] == "bad_out", env
    assert not list(outside.rglob("*.json"))


# ================================================ WINCLI-P1-021 (director run)


def _failing_outcome():
    from manju.build.director import Outcome

    return Outcome(
        proposal_id="p-1", ok=False, state="failed",
        results=[{"index": 0, "type": "build", "ok": False, "error": "boom"}],
        failure={"id": "f-1", "cause": "boom"},
        diff={"outputs": {}}, suggestions=[])


def _ok_outcome():
    from manju.build.director import Outcome

    return Outcome(proposal_id="p-1", ok=True, state="done",
                   results=[{"index": 0, "type": "build", "ok": True}],
                   diff={"outputs": {}}, suggestions=[])


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_director_run_exits_non_zero_when_the_outcome_failed(
        in_project, monkeypatch, extra):
    import manju.build.director as D

    monkeypatch.setattr(D, "execute", lambda *a, **k: _failing_outcome())
    result = runner.invoke(app, ["director", "run", "p-1", *extra])
    assert result.exit_code == 1, result.stdout
    if extra:  # the JSON body is unchanged — only the status now agrees with it
        assert json.loads(result.stdout)["ok"] is False


@pytest.mark.parametrize("extra", [[], ["--json"]])
def test_director_run_still_exits_zero_on_success(in_project, monkeypatch, extra):
    import manju.build.director as D

    monkeypatch.setattr(D, "execute", lambda *a, **k: _ok_outcome())
    result = runner.invoke(app, ["director", "run", "p-1", *extra])
    assert result.exit_code == 0, result.stdout


# ================================================================ CLI-P1-004 (new)


def test_new_illegal_windows_name_is_an_envelope_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "CON"])
    assert result.exit_code != 0, result.stdout
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"refusal escaped as a traceback: {result.exception!r}")
    # the refusal must NOT claim the project already exists
    assert "已存在" not in result.output
    assert not (tmp_path / "CON.manju").exists()


def test_new_existing_project_keeps_the_exists_hint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["new", "片子"]).exit_code == 0
    result = runner.invoke(app, ["new", "片子"])
    assert result.exit_code != 0, result.stdout
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "已存在" in result.output
