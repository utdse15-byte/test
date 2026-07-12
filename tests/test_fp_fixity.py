"""FP Loop K — archive fixity for the .manjupkg pack/unpack path (roadmap §7.8).

Red-first. These pin the fixity CONTRACT as a black box (the literal strings
``MANJU_FIXITY.json`` / ``manju-fixity.1`` are part of the contract, so they are
hardcoded here rather than imported from cli):

  pack  → writes ONE in-zip member MANJU_FIXITY.json (last member) recording
          {sha256, bytes} for every OTHER member, plus totals + manju_version;
          content is deterministic for an unchanged tree.
  unpack→ when the manifest is present, verifies every extracted file's
          sha256+size AFTER extraction; any mismatch/missing/extra is a
          structured FAILURE, the just-created destination is REMOVED, and the
          exit is nonzero. A pack with NO manifest unpacks exactly as before
          (exit 0) with a one-line advisory note.
  fixity→ `manju fixity <pack>` verifies WITHOUT extracting (reads only).

The FP-security unpack preflight (test_fp_security.py) must stay green — these
tests EXTEND that path, they do not rewrite it.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app

MANIFEST = "MANJU_FIXITY.json"
FORMAT = "manju-fixity.1"

runner = CliRunner()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _pack(project, out: Path, monkeypatch) -> Path:
    """Pack ``project`` to ``out`` via the real CLI (from inside the project)."""
    # a known media payload so tamper/drop/extra tests do not depend on the
    # exact scaffold file set
    media = project.root / "media" / "probe.bin"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"probe-payload-" + b"x" * 32)
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0, res.output
    return out


def _rebuild(src: Path, dst: Path, *, drop=(), replace=None, add=None) -> Path:
    """Copy ``src`` into ``dst`` with member-level edits (tamper/delete/add).

    The MANJU_FIXITY.json member is copied verbatim unless explicitly dropped/
    replaced, so it keeps describing the ORIGINAL payload — exactly the shape a
    corrupted/edited archive has in the wild.
    """
    replace = replace or {}
    add = add or {}
    drop = set(drop)
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            zout.comment = zin.comment
            for info in infos:
                nm = info.filename
                if nm in drop:
                    continue
                data = replace[nm] if nm in replace else zin.read(nm)
                zout.writestr(zipfile.ZipInfo(nm), data)
            for nm, data in add.items():
                zout.writestr(zipfile.ZipInfo(nm), data)
    return dst


def _make_manjupkg_no_manifest(path: Path, members: dict[str, bytes]) -> Path:
    """A pre-fixity .manjupkg (mirrors test_fp_security's builder)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name), data)
    return path


# --------------------------------------------------------------- pack side


def test_pack_writes_fixity_manifest_present_last_and_accurate(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert MANIFEST in names, "pack must write the in-zip fixity manifest"
        assert names[-1] == MANIFEST, "manifest must be the LAST member"
        doc = json.loads(zf.read(MANIFEST).decode("utf-8"))
        # format is a PLAIN string, deliberately not a manju.*/vN schema id
        assert doc["format"] == FORMAT
        assert "manju_version" in doc
        files = doc["files"]
        assert MANIFEST not in files, "manifest must EXCLUDE itself"
        # every other member is recorded accurately (bare-hex sha256 + bytes)
        payload = [n for n in names if n != MANIFEST]
        assert set(files) == set(payload)
        for nm in payload:
            raw = zf.read(nm)
            assert files[nm]["sha256"] == hashlib.sha256(raw).hexdigest()
            assert files[nm]["bytes"] == len(raw)
        assert doc["totals"]["count"] == len(payload)
        assert doc["totals"]["bytes"] == sum(files[n]["bytes"] for n in payload)


def test_pack_manifest_deterministic_same_tree_same_bytes(tmp_project, tmp_path, monkeypatch):
    a = _pack(tmp_project, tmp_path / "a.manjupkg", monkeypatch)
    b = _pack(tmp_project, tmp_path / "b.manjupkg", monkeypatch)
    ba = zipfile.ZipFile(a).read(MANIFEST)
    bb = zipfile.ZipFile(b).read(MANIFEST)
    assert ba == bb, "same tree must yield byte-identical manifest content"


# ------------------------------------------------------------- unpack side


def test_unpack_clean_pack_verifies_and_drops_manifest(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    dest = tmp_path / "restored.manju"
    res = runner.invoke(app, ["unpack", str(archive), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    assert (dest / "project.yaml").exists()
    assert (dest / ".manju").is_dir()
    # a PRESENT manifest is verified (reported), and the transport-only manifest
    # is then consumed — never left behind in the restored tree
    assert "完整性" in res.output or "fixity" in res.output.lower()
    assert not (dest / MANIFEST).exists()


def test_unpack_tampered_member_fails_and_removes_dest(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    bad = _rebuild(archive, tmp_path / "tampered.manjupkg",
                   replace={"media/probe.bin": b"TAMPERED-DIFFERENT-BYTES"})
    dest = tmp_path / "tampered.manju"
    res = runner.invoke(app, ["unpack", str(bad), "--dest", str(dest)])
    assert res.exit_code != 0, res.output
    assert not dest.exists(), "a failed-fixity restore must be REMOVED"
    assert "media/probe.bin" in res.output


def test_unpack_deleted_member_reports_missing_and_removes_dest(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    bad = _rebuild(archive, tmp_path / "short.manjupkg", drop=["media/probe.bin"])
    dest = tmp_path / "short.manju"
    res = runner.invoke(app, ["unpack", str(bad), "--dest", str(dest)])
    assert res.exit_code != 0, res.output
    assert not dest.exists()
    out = res.output.lower()
    assert "missing" in out and "media/probe.bin" in res.output


def test_unpack_extra_member_reports_extra_and_removes_dest(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    bad = _rebuild(archive, tmp_path / "plus.manjupkg", add={"STRAY.txt": b"unexpected"})
    dest = tmp_path / "plus.manju"
    res = runner.invoke(app, ["unpack", str(bad), "--dest", str(dest)])
    assert res.exit_code != 0, res.output
    assert not dest.exists()
    out = res.output.lower()
    assert "extra" in out and "STRAY.txt" in res.output


def test_unpack_old_pack_without_manifest_unchanged_advisory(tmp_path):
    """Pin 'today's behavior' for a pre-fixity pack: extracts, exit 0, .manju
    created, plus a one-line advisory note that no manifest was present."""
    pkg = _make_manjupkg_no_manifest(
        tmp_path / "old.manjupkg",
        {"project.yaml": b"name: old\n", "shots/S001.yaml": b"id: S001\n"},
    )
    dest = tmp_path / "old.manju"
    res = runner.invoke(app, ["unpack", str(pkg), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    assert (dest / "project.yaml").read_text(encoding="utf-8") == "name: old\n"
    assert (dest / ".manju").is_dir()
    # advisory note fired (mentions the absent manifest, without failing)
    assert MANIFEST in res.output or "完整性" in res.output or "fixity" in res.output.lower()


# -------------------------------------------------------------- fixity CLI


def test_fixity_cli_verifies_without_writing(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    before = sorted(p.name for p in tmp_path.iterdir())
    res = runner.invoke(app, ["fixity", str(archive), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["present"] is True
    assert payload["checked"] >= 1
    assert payload["mismatched"] == [] and payload["missing"] == [] and payload["extra"] == []
    # verify-WITHOUT-extract: nothing new written next to the archive
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_fixity_cli_detects_tamper(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    bad = _rebuild(archive, tmp_path / "tampered.manjupkg",
                   replace={"media/probe.bin": b"NOPE"})
    res = runner.invoke(app, ["fixity", str(bad), "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert any(r["path"] == "media/probe.bin" for r in payload["mismatched"])


def test_fixity_cli_no_manifest_is_not_verified(tmp_path):
    pkg = _make_manjupkg_no_manifest(tmp_path / "old.manjupkg",
                                     {"project.yaml": b"name: x\n"})
    res = runner.invoke(app, ["fixity", str(pkg), "--json"])
    # cannot verify a pack that carries no manifest → not a success
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["present"] is False
    assert payload["ok"] is False
