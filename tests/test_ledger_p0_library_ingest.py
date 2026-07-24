"""Ledger P0 regressions — private library + batch ingest trust the index /
source tree too much (group: library_ingest).

Each test drives the exact escape the external audit demonstrated, then pins
the fixed behaviour:

- LIBRARY-P0-001: ``lib rm`` must never unlink a file the index row points at
  OUTSIDE the shelf (absolute value or ``..`` escape).
- LIBRARY-P0-002: ``lib use`` must refuse a blob whose path escapes the root
  or whose bytes no longer hash to the recorded index hash.
- LIBRARY-P0-003: ``lib add`` must not follow a pre-planted predictable
  ``.part`` symlink, and the index lock open must not follow a link.
- INGEST-P0-001: batch ingest must not follow file symlinks or traverse a
  symlinked/junction directory (root OR subdir) into an external tree.
- INGEST-P0-002: ``--on-duplicate=link`` must re-verify the library blob's
  actual bytes and fall back to the dropped file when the index is forged.
"""

from __future__ import annotations

import json
import os

import pytest

from manju.build.ingest import plan_ingest
from manju.core.hashing import hash_file
from manju.core.library import Library, LibraryError, _hex, library_root


@pytest.fixture
def iso_lib(tmp_path, monkeypatch):
    """Point MANJU_LIBRARY at a tmp shelf so the real ~/.manju is untouched."""
    root = tmp_path / "user_lib"
    monkeypatch.setenv("MANJU_LIBRARY", str(root))
    return root


@pytest.fixture
def src_file(tmp_path):
    def _make(name: str, content: bytes = b"asset-bytes"):
        p = tmp_path / name
        p.write_bytes(content)
        return p
    return _make


def _forge_index(root, assets):
    """Write index.json directly — the audit's threat model is a corrupted or
    hostile machine-local catalogue, so we bypass the safe writers on purpose."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(
        json.dumps({"version": 1, "assets": assets}, ensure_ascii=False),
        encoding="utf-8",
    )


def _row(blob, *, hash_hex="a" * 64, thumb=None):
    return {
        "hash": "sha256:" + hash_hex, "blob": blob, "name": "x", "kind": "other",
        "size": 0, "tags": [], "note": "", "added": "t", "thumb": thumb,
    }


# ------------------------------------------------------- LIBRARY-P0-001 (rm)


def test_remove_refuses_absolute_external_blob(iso_lib, tmp_path):
    root = library_root()
    victim = tmp_path / "delete-victim.txt"
    victim.write_bytes(b"KEEP ME")
    _forge_index(root, [_row(str(victim))])

    Library().remove("a" * 8)

    assert victim.exists() and victim.read_bytes() == b"KEEP ME"
    assert Library().assets() == []  # the bogus row is still cleaned out


def test_remove_refuses_relative_escape_blob_and_thumb(iso_lib, tmp_path):
    root = library_root()
    blob_victim = root.parent / "relative-victim.txt"
    thumb_victim = tmp_path / "thumb-victim.jpg"
    blob_victim.write_bytes(b"BLOB")
    thumb_victim.write_bytes(b"THUMB")
    _forge_index(root, [_row("../relative-victim.txt", thumb=str(thumb_victim))])

    Library().remove("a" * 8)

    assert blob_victim.read_bytes() == b"BLOB"
    assert thumb_victim.read_bytes() == b"THUMB"


def test_remove_deletes_a_legitimate_contained_blob(iso_lib, src_file):
    lib = Library()
    entry = lib.add(src_file("gone.mp4", b"remove-me"))["entry"]
    blob = lib.root / entry["blob"]
    assert blob.exists()
    lib.remove(_hex(entry["hash"])[:8])
    assert not blob.exists() and lib.assets() == []


# ------------------------------------------------------ LIBRARY-P0-002 (use)


def test_open_verified_blob_refuses_external_absolute_blob(iso_lib, tmp_path):
    secret = tmp_path / "outside-secret.txt"
    secret.write_bytes(b"TOP-SECRET-OUTSIDE")
    entry = _row(str(secret), hash_hex="c" * 64)
    with pytest.raises(LibraryError):
        Library().open_verified_blob(entry)


def test_open_verified_blob_refuses_byte_substitution(iso_lib, src_file):
    lib = Library()
    entry = lib.add(src_file("v.mp4", b"real-bytes"))["entry"]
    # substitute the content-addressed blob's bytes: the path stays contained,
    # but the digest no longer matches the recorded hash.
    (lib.root / entry["blob"]).write_bytes(b"SUBSTITUTED-BYTES")
    with pytest.raises(LibraryError, match="do not match"):
        lib.open_verified_blob(entry)


def test_open_verified_blob_reads_a_good_blob(iso_lib, src_file):
    lib = Library()
    entry = lib.add(src_file("v.mp4", b"good-bytes"))["entry"]
    with lib.open_verified_blob(entry) as f:
        assert f.read() == b"good-bytes"


def test_use_into_lands_verified_bytes(iso_lib, src_file, tmp_path):
    lib = Library()
    entry = lib.add(src_file("v.mp4", b"good-bytes"))["entry"]
    dest = tmp_path / "project_copy.mp4"
    lib.use_into(entry, dest)
    assert dest.read_bytes() == b"good-bytes"


def test_use_into_refuses_external_absolute_blob(iso_lib, tmp_path):
    secret = tmp_path / "outside-secret.txt"
    secret.write_bytes(b"TOP-SECRET-OUTSIDE")
    dest = tmp_path / "leak.txt"
    with pytest.raises(LibraryError):
        Library().use_into(_row(str(secret), hash_hex="c" * 64), dest)
    assert not dest.exists()


# ---------------------------------------------------- LIBRARY-P0-003 (add/lock)


def test_add_ignores_preplanted_part_symlink(iso_lib, src_file, tmp_path):
    lib = Library()
    lib.root.mkdir(parents=True, exist_ok=True)
    src = src_file("clip.mp4", b"SOURCE-BYTES")
    blob = lib.root / (_hex(hash_file(src)) + ".mp4")
    victim = tmp_path / "part-victim.txt"
    victim.write_bytes(b"VICTIM-ORIGINAL")
    # the OLD predictable temp name — a symlink here used to be followed by the
    # copy (overwriting victim) and then published as the blob itself.
    preplanted = blob.with_name(blob.name + f".{os.getpid()}.part")
    preplanted.symlink_to(victim)

    lib.add(src)

    assert victim.read_bytes() == b"VICTIM-ORIGINAL"      # never overwritten
    assert blob.is_file() and not blob.is_symlink()        # a real blob, not a link
    assert blob.read_bytes() == b"SOURCE-BYTES"


def test_index_lock_refuses_symlinked_lockfile(iso_lib, src_file, tmp_path):
    lib = Library()
    lib.root.mkdir(parents=True, exist_ok=True)
    external = tmp_path / "lock-target"
    external.write_bytes(b"")
    (lib.root / "index.lock").symlink_to(external)
    with pytest.raises(LibraryError):
        lib.add(src_file("z.mp4", b"z"))


# ------------------------------------------------------- INGEST-P0-001 (walk)


def test_ingest_skips_external_file_symlink(tmp_project, tmp_path, iso_lib):
    secret = tmp_path / "outside-secret.dat"
    secret.write_bytes(b"OUTSIDE-SECRET")
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "innocent.dat").symlink_to(secret)

    plan = plan_ingest(tmp_project, [batch])

    assert [r.action for r in plan.rows] == ["skip_symlink"]
    assert all(r.hash == "" for r in plan.rows)  # never hashed/landed


def test_ingest_prunes_symlinked_root_directory(tmp_project, tmp_path, iso_lib):
    external = tmp_path / "external-tree"
    external.mkdir()
    (external / "secret.bin").write_bytes(b"EXTERNAL")
    link = tmp_path / "drop-link"
    link.symlink_to(external, target_is_directory=True)

    plan = plan_ingest(tmp_project, [link])

    assert [r.action for r in plan.rows] == ["skip_symlink"]
    assert not any(r.name == "secret.bin" for r in plan.rows)


def test_ingest_prunes_symlinked_subdirectory_but_keeps_real_files(
    tmp_project, tmp_path, iso_lib
):
    external = tmp_path / "ext"
    external.mkdir()
    (external / "s.bin").write_bytes(b"EXTERNAL")
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "real.mp4").write_bytes(b"real-bytes")
    (batch / "sublink").symlink_to(external, target_is_directory=True)

    plan = plan_ingest(tmp_project, [batch])

    names = {r.name for r in plan.rows}
    assert "real.mp4" in names
    assert not any(r.name == "s.bin" for r in plan.rows)  # external tree pruned
    assert any(r.action == "skip_symlink" for r in plan.rows)


# ------------------------------------------------------- INGEST-P0-002 (link)


def test_link_rejects_forged_library_blob_and_falls_back(tmp_project, tmp_path, iso_lib):
    lib = Library()
    content = b"dropped-content"
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(content)
    lib.add(seed, tags=["logo"])
    h = hash_file(seed)

    # forge the index: keep the (matching) hash but repoint the blob at an
    # external secret with unrelated bytes.
    substitute = tmp_path / "substitute-secret.txt"
    substitute.write_bytes(b"UNRELATED-EXTERNAL-SECRET")
    idx = lib.load_index()
    idx["assets"][0]["blob"] = str(substitute)
    lib._save_index(idx)

    batch = tmp_path / "batch"
    batch.mkdir()
    dropped = batch / "unmatched.mp4"
    dropped.write_bytes(content)

    row = plan_ingest(tmp_project, [batch], on_duplicate="link").rows[0]

    assert row.action == "import"
    assert row.file == str(dropped)          # honest dropped bytes, not the forgery
    assert row.file != str(substitute)
    assert "校验失败" in (row.library_hint or "")


def test_link_sources_from_a_genuine_library_blob(tmp_project, tmp_path, iso_lib):
    lib = Library()
    content = b"genuine-library-bytes"
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(content)
    entry = lib.add(seed, tags=["logo"])["entry"]

    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "unmatched.mp4").write_bytes(content)

    row = plan_ingest(tmp_project, [batch], on_duplicate="link").rows[0]

    assert row.file == str(lib.root / entry["blob"])
    assert "provenance: library" in (row.library_hint or "")
