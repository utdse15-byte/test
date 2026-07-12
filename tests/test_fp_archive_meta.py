"""FP Loop S3 — archive self-description for .manjupkg (roadmap §7.8 extensions).

Red-first. `manju pack` writes THREE additional synthesized members — restore
instructions + engine contract-registry snapshot + toolchain snapshot — BEFORE
the MANJU_FIXITY.json member, so all three are FIXITY-COVERED (tampering any of
them fails verification exactly like a payload member). Like the fixity
manifest, they are TRANSPORT metadata: after a passing verify `manju unpack`
drops them, so the restored tree equals the original project. Old packs
without them keep restoring byte-for-byte. `manju fixity --info` surfaces a
read-only summary (restore-note presence, contracts-snapshot presence,
toolchain manju/ffmpeg versions) under a capped stream read — nothing written.

The member names and the restore note's key phrases are the CONTRACT — literal
strings here, deliberately not imported from cli (mirrors test_fp_fixity).
Loop K's pins (test_fp_fixity.py) and the FP-security pins must stay green —
this loop EXTENDS the pack/unpack/fixity region, it does not rewrite it.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app

FIXITY = "MANJU_FIXITY.json"
RESTORE = "MANJU_RESTORE.txt"
CONTRACTS = "MANJU_CONTRACTS.yaml"
TOOLCHAIN = "MANJU_TOOLCHAIN.json"
META3 = (CONTRACTS, RESTORE, TOOLCHAIN)

runner = CliRunner()


# --------------------------------------------------------------------------
# helpers (mirroring test_fp_fixity's — small dup is the existing convention)
# --------------------------------------------------------------------------

def _pack(project, out: Path, monkeypatch) -> Path:
    """Pack ``project`` to ``out`` via the real CLI (from inside the project)."""
    media = project.root / "media" / "probe.bin"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"probe-payload-" + b"x" * 32)
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0, res.output
    return out


def _rebuild(src: Path, dst: Path, *, drop=(), replace=None, add=None) -> Path:
    """Copy ``src`` into ``dst`` with member-level edits (tamper/delete/add)."""
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


def _make_manjupkg_no_meta(path: Path, members: dict[str, bytes]) -> Path:
    """A pre-fixity/pre-S3 .manjupkg (no synthesized members at all)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name), data)
    return path


# --------------------------------------------------------------- pack side


def test_pack_writes_three_meta_members_before_fixity_and_fixity_covered(
        tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        for nm in META3:
            assert nm in names, f"pack must write {nm}"
            # written BEFORE the fixity manifest ⇒ fixity-coverable
            assert names.index(nm) < names.index(FIXITY), (
                f"{nm} must be written before {FIXITY} so it is fixity-covered")
        assert names[-1] == FIXITY, "fixity manifest stays the LAST member (K pin)"
        # fixity-covered: each meta member is recorded accurately in the manifest
        doc = json.loads(zf.read(FIXITY).decode("utf-8"))
        for nm in META3:
            raw = zf.read(nm)
            assert doc["files"][nm]["sha256"] == hashlib.sha256(raw).hexdigest()
            assert doc["files"][nm]["bytes"] == len(raw)


def test_restore_note_content_is_honest_and_self_consistent(
        tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    canonical = tmp_project.root.stem + ".manjupkg"
    with zipfile.ZipFile(archive) as zf:
        note = zf.read(RESTORE).decode("utf-8")
        tool = json.loads(zf.read(TOOLCHAIN).decode("utf-8"))
    assert "manju-restore.1" in note.splitlines()[0], "self-describing format marker"
    # the exact restore + verify commands, spelled with the canonical pack name
    assert f"manju unpack {canonical}" in note
    assert f"manju fixity {canonical}" in note
    # what fixity guarantees — and, honestly, what it does not (no signature)
    assert FIXITY in note
    assert "not signed" in note.lower()
    # pointers to both snapshots
    assert TOOLCHAIN in note and CONTRACTS in note
    # version from the SAME source the toolchain manifest uses — self-consistent
    ver = tool["facts"]["manju"]["version"]
    assert f"Packed by manju version: {ver}" in note


def test_contracts_snapshot_is_byte_copy_of_engine_registry(
        tmp_project, tmp_path, monkeypatch):
    from manju.core.contracts import REGISTRY_PATH

    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    with zipfile.ZipFile(archive) as zf:
        member = zf.read(CONTRACTS)
    assert member == Path(REGISTRY_PATH).read_bytes(), (
        "MANJU_CONTRACTS.yaml must be a byte copy of the engine's registry "
        "at pack time — never re-rendered")


def test_toolchain_snapshot_reuses_the_toolchain_manifest_document(
        tmp_project, tmp_path, monkeypatch):
    from manju.core.toolchain import SCHEMA, manifest_digest

    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    with zipfile.ZipFile(archive) as zf:
        doc = json.loads(zf.read(TOOLCHAIN).decode("utf-8"))
    assert doc["schema"] == SCHEMA
    assert doc["manifest_digest"] == manifest_digest(doc), (
        "the in-pack toolchain snapshot must BE a core.toolchain manifest "
        "document (reused collectors), not a parallel collection")
    assert doc["facts"]["manju"]["version"]


def test_two_packs_same_tree_identical_meta_member_bytes(
        tmp_project, tmp_path, monkeypatch):
    a = _pack(tmp_project, tmp_path / "a.manjupkg", monkeypatch)
    b = _pack(tmp_project, tmp_path / "b.manjupkg", monkeypatch)
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        for nm in META3:
            assert za.read(nm) == zb.read(nm), (
                f"{nm} must be byte-identical across two packs of the same "
                "tree (no timestamps, no --out-dependent content)")


# ------------------------------------------------- fixity coverage (tamper)


def test_tampered_restore_note_fails_unpack_and_removes_dest(
        tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    bad = _rebuild(archive, tmp_path / "tampered.manjupkg",
                   replace={RESTORE: b"TAMPERED RESTORE INSTRUCTIONS\n"})
    dest = tmp_path / "tampered.manju"
    res = runner.invoke(app, ["unpack", str(bad), "--dest", str(dest)])
    assert res.exit_code != 0, res.output
    assert not dest.exists(), "a failed-fixity restore must be REMOVED"
    assert RESTORE in res.output


def test_fixity_cli_detects_tampered_toolchain_snapshot(
        tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    bad = _rebuild(archive, tmp_path / "tampered.manjupkg",
                   replace={TOOLCHAIN: b"{}"})
    res = runner.invoke(app, ["fixity", str(bad), "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert any(r["path"] == TOOLCHAIN for r in payload["mismatched"])


# ------------------------------------------------------------- unpack side


def test_unpack_drops_all_meta_members_restore_equals_original_tree(
        tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    with zipfile.ZipFile(archive) as zf:
        payload = {n for n in zf.namelist()
                   if n not in (FIXITY, *META3) and not n.endswith("/")}
    dest = tmp_path / "restored.manju"
    res = runner.invoke(app, ["unpack", str(archive), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    for nm in (FIXITY, *META3):
        assert not (dest / nm).exists(), (
            f"{nm} is transport metadata — it must be dropped after a "
            "passing verify (restore == original tree)")
    restored = {p.relative_to(dest).as_posix() for p in dest.rglob("*")
                if p.is_file() and not p.relative_to(dest).as_posix()
                .startswith(".manju")}
    assert restored == payload, "restored tree must equal the packed payload"


def test_old_pack_without_meta_members_unchanged(tmp_path):
    """Zero behavior change for a pre-S3/pre-K pack: extracts, exit 0, no
    synthesized members appear anywhere, advisory only (K's pin, re-pinned
    from the S3 angle)."""
    pkg = _make_manjupkg_no_meta(
        tmp_path / "old.manjupkg",
        {"project.yaml": b"name: old\n", "shots/S001.yaml": b"id: S001\n"},
    )
    dest = tmp_path / "old.manju"
    res = runner.invoke(app, ["unpack", str(pkg), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    assert (dest / "project.yaml").read_text(encoding="utf-8") == "name: old\n"
    assert (dest / "shots" / "S001.yaml").exists()
    restored = {p.name for p in dest.rglob("*")}
    assert not any(n.startswith("MANJU_") for n in restored), (
        "an old pack must not grow synthesized members on restore")


# --------------------------------------------------- honest absent fallback


def test_registry_path_absent_omits_contracts_snapshot_honestly(
        tmp_project, tmp_path, monkeypatch):
    """Installed-wheel-without-CONTRACTS.yaml case: the member is OMITTED and
    MANJU_RESTORE.txt says so — a fabricated snapshot is never synthesized.
    The pack still round-trips green."""
    import manju.core.contracts as contracts_mod

    monkeypatch.setattr(contracts_mod, "REGISTRY_PATH",
                        tmp_path / "no-such-CONTRACTS.yaml")
    archive = _pack(tmp_project, tmp_path / "noreg.manjupkg", monkeypatch)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert CONTRACTS not in names, "absent registry ⇒ member omitted"
        assert RESTORE in names and TOOLCHAIN in names
        note = zf.read(RESTORE).decode("utf-8")
        doc = json.loads(zf.read(FIXITY).decode("utf-8"))
    assert CONTRACTS in note and "not included" in note.lower(), (
        "the restore note must say the contracts snapshot is absent, honestly")
    assert CONTRACTS not in doc["files"]
    dest = tmp_path / "noreg.manju"
    res = runner.invoke(app, ["unpack", str(archive), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    assert not (dest / RESTORE).exists()


# ------------------------------------------------------------- --info side


def test_fixity_info_surfaces_summary_readonly(tmp_project, tmp_path, monkeypatch):
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    before = sorted(p.name for p in tmp_path.iterdir())
    res = runner.invoke(app, ["fixity", str(archive), "--info", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    info = payload["info"]
    assert info["restore_note"] is True
    assert info["contracts_snapshot"] is True
    assert info["toolchain_snapshot"] is True
    assert info["toolchain"]["summarized"] is True
    assert isinstance(info["toolchain"]["manju"], str)
    assert isinstance(info["toolchain"]["ffmpeg"], str)  # version line or "missing"
    # read-only: nothing new written next to the archive
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    # human mode surfaces the same summary, still read-only
    res2 = runner.invoke(app, ["fixity", str(archive), "--info"])
    assert res2.exit_code == 0, res2.output
    assert "restore-note" in res2.output
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_fixity_json_without_info_flag_has_no_info_key(
        tmp_project, tmp_path, monkeypatch):
    """The existing --json surface is frozen: no ``info`` key unless asked."""
    archive = _pack(tmp_project, tmp_path / "orig.manjupkg", monkeypatch)
    res = runner.invoke(app, ["fixity", str(archive), "--json"])
    assert res.exit_code == 0, res.output
    assert "info" not in json.loads(res.stdout)


def test_fixity_info_on_old_pack_reports_absent_members(tmp_path):
    """--info on a pre-S3 pack: honest all-absent summary; the no-manifest
    verdict (nonzero exit, present:false — K's pin) is unchanged."""
    pkg = _make_manjupkg_no_meta(tmp_path / "old.manjupkg",
                                 {"project.yaml": b"name: x\n"})
    res = runner.invoke(app, ["fixity", str(pkg), "--info", "--json"])
    assert res.exit_code != 0
    payload = json.loads(res.stdout)
    assert payload["present"] is False and payload["ok"] is False
    info = payload["info"]
    assert info["restore_note"] is False
    assert info["contracts_snapshot"] is False
    assert info["toolchain_snapshot"] is False
    assert info["toolchain"] is None
