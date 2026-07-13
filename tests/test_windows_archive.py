"""W5.2 (MANJU_WINDOWS_ONLY_LEAN_V3) — archive/restore Windows hardening.

The W1 unpack gate already refuses Windows-unportable MEMBERS (reserved stems,
trailing dot/space, colon/ADS, traversal, symlink members). What was still
missing, found by the W5 audit:

* **pack is a trap for its own unpack**: a tree containing ``CON.wav`` or a
  name with a trailing space packs SILENTLY on POSIX — and the resulting
  archive is then REFUSED by ``manju unpack`` (the W1 gate). Backup must never
  be blocked (refusing to back up an existing tree risks data loss exactly
  when the user needs a backup), so pack now WARNS per member, naming the
  hazard, while still packing everything.
* **casefold collisions are silent data loss on restore**: ``A.wav`` and
  ``a.wav`` are two files on ext4 but ONE file on NTFS — extraction keeps
  whichever lands last. pack warns; unpack REFUSES (same class as the W1
  member gate: a hazard that corrupts the restored tree is refused up front).
* **junctions evade the symlink skip**: ``Path.is_symlink()`` is False for an
  NTFS junction, and ``rglob`` happily TRAVERSES it — a junction inside the
  project smuggles an entire outside tree into the archive (the goal-item-14
  smuggle, directory edition). Directories flagged as reparse points are now
  pruned (with a warning) on Windows; POSIX behaviour is byte-identical.
* **cross-drive restore** is pinned: an archive restores to ANY --dest
  (different tree; a different drive on Windows is the same code path by
  construction — pathlib absolute paths, no cwd assumptions).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.container import Project

runner = CliRunner()


def _mk_project(tmp_path: Path, name: str = "归档") -> Project:
    project = Project.create(tmp_path / name, git_init=False)
    media = project.root / "media" / "probe.bin"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"probe-payload-" + b"x" * 32)
    return project


# --------------------------------------------------------------------------
# pack-side portability warnings (warn, NEVER block a backup)
# --------------------------------------------------------------------------


def test_pack_warns_on_windows_unportable_members(tmp_path, monkeypatch):
    """A reserved-stem member (CON.wav) packs fine on POSIX and is then
    REFUSED by our own unpack — pack must say so AT PACK TIME, naming the
    member and the hazard, and must still succeed (a backup is never blocked)."""
    project = _mk_project(tmp_path)
    hazard = project.root / "media" / "CON.wav"
    hazard.write_bytes(b"reserved-stem")

    monkeypatch.chdir(project.root)
    out = tmp_path / "p.manjupkg"
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0, res.output  # the backup itself always succeeds
    assert "CON.wav" in res.output
    assert "便携" in res.output or "unportable" in res.output.lower()
    # the hazard member IS packed (backup completeness beats portability)
    with zipfile.ZipFile(out) as zf:
        assert "media/CON.wav" in zf.namelist()


def test_pack_warns_on_casefold_collision(tmp_path, monkeypatch):
    """Two members differing only by case are ONE file after an NTFS restore —
    silent data loss. pack warns, naming both paths; still packs both."""
    project = _mk_project(tmp_path)
    (project.root / "media" / "Take.wav").write_bytes(b"upper")
    (project.root / "media" / "take.wav").write_bytes(b"lower")

    monkeypatch.chdir(project.root)
    out = tmp_path / "p.manjupkg"
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "Take.wav" in res.output and "take.wav" in res.output
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "media/Take.wav" in names and "media/take.wav" in names


def test_pack_clean_tree_emits_no_portability_warning(tmp_path, monkeypatch):
    """The scaffolded tree is portable — no warning text appears (the warning
    must never become ambient noise that trains the user to ignore it)."""
    project = _mk_project(tmp_path)
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["pack", "--out", str(tmp_path / "p.manjupkg")])
    assert res.exit_code == 0, res.output
    assert "便携" not in res.output
    assert "大小写" not in res.output


def test_pack_json_carries_portability_block_only_when_present(tmp_path, monkeypatch):
    project = _mk_project(tmp_path)
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["pack", "--out", str(tmp_path / "a.manjupkg"), "--json"])
    clean = json.loads(res.output)
    assert "portability" not in clean  # absent when clean (drop-when-empty)

    (project.root / "media" / "CON.wav").write_bytes(b"x")
    (project.root / "media" / "Take.wav").write_bytes(b"u")
    (project.root / "media" / "take.wav").write_bytes(b"l")
    res = runner.invoke(app, ["pack", "--out", str(tmp_path / "b.manjupkg"), "--json"])
    doc = json.loads(res.output)
    port = doc["portability"]
    assert any("CON.wav" in m for m in port["unportable_members"])
    assert any("Take.wav" in m or "take.wav" in m
               for group in port["casefold_collisions"] for m in group)


# --------------------------------------------------------------------------
# junction pruning (the directory-reparse smuggle)
# --------------------------------------------------------------------------


def test_pack_prunes_reparse_dirs(tmp_path, monkeypatch):
    """A directory flagged as a reparse point (NTFS junction/mount) is pruned
    from the pack walk WITH a warning — ``is_symlink()`` is False for a
    junction and rglob TRAVERSES it, so without pruning a junction smuggles an
    entire outside tree into the archive. POSIX hosts have no junctions; the
    helper returns False there and this test drives it via monkeypatch (the
    W1 stub-driven pattern for Windows-only primitives)."""
    import manju.cli as cli

    project = _mk_project(tmp_path)
    junk = project.root / "media" / "junction_dir"
    junk.mkdir()
    (junk / "smuggled.bin").write_bytes(b"outside-tree-bytes")

    monkeypatch.setattr(
        cli, "_dir_is_reparse_point",
        lambda p: p.name == "junction_dir",
    )
    monkeypatch.chdir(project.root)
    out = tmp_path / "p.manjupkg"
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "junction_dir" in res.output  # the prune is named, never silent
    with zipfile.ZipFile(out) as zf:
        assert not any("smuggled" in n for n in zf.namelist())


@pytest.mark.skipif(__import__("os").name == "nt",
                    reason="POSIX-real symlink-dir proof; junctions cover nt")
def test_pack_prunes_symlinked_directories_posix_real(tmp_path, monkeypatch):
    """The SAME smuggle, real on POSIX, no stub: rglob TRAVERSES a symlinked
    directory, and the files inside are not themselves symlinks — so the
    per-file symlink skip never fires for them. One link dir = an entire
    outside tree in the archive. Pruned wholesale, named in the warning."""
    project = _mk_project(tmp_path)
    outside = tmp_path / "outside_tree"
    outside.mkdir()
    (outside / "secret.bin").write_bytes(b"outside-the-project-bytes")
    (project.root / "media" / "linkdir").symlink_to(outside, target_is_directory=True)

    monkeypatch.chdir(project.root)
    out = tmp_path / "p.manjupkg"
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "linkdir" in res.output
    with zipfile.ZipFile(out) as zf:
        assert not any("secret" in n for n in zf.namelist())


def test_pack_posix_reparse_probe_is_inert(tmp_path, monkeypatch):
    """On POSIX the reparse probe answers False for every directory — the walk
    (and the archive membership) is byte-identical to before W5."""
    import manju.cli as cli

    project = _mk_project(tmp_path)
    sub = project.root / "media" / "normal_dir"
    sub.mkdir()
    (sub / "kept.bin").write_bytes(b"kept")
    assert cli._dir_is_reparse_point(sub) is False
    monkeypatch.chdir(project.root)
    out = tmp_path / "p.manjupkg"
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0
    with zipfile.ZipFile(out) as zf:
        assert "media/normal_dir/kept.bin" in zf.namelist()


# --------------------------------------------------------------------------
# unpack refuses casefold-colliding members (silent-loss class)
# --------------------------------------------------------------------------


def _minimal_pkg(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.comment = json.dumps({"manjupkg": 1, "name": "x.manju"}).encode()
        for i, n in enumerate(names):
            zf.writestr(n, f"payload-{i}")
    return path


def test_unpack_refuses_casefold_colliding_members(tmp_path, monkeypatch):
    """Two members that casefold to the same path are ONE file on NTFS — the
    extraction keeps whichever lands last and the fixity check then fails (or
    worse, without a manifest, silently loses one). Refused up front, same
    class as the W1 reserved-name member gate."""
    monkeypatch.chdir(tmp_path)
    pkg = _minimal_pkg(tmp_path / "case.manjupkg",
                       ["media/Take.wav", "media/take.wav"])
    res = runner.invoke(app, ["unpack", str(pkg), "--dest", str(tmp_path / "d")])
    assert res.exit_code != 0
    assert "Take.wav" in res.output or "take.wav" in res.output
    assert not (tmp_path / "d").exists()


def test_unpack_accepts_case_distinct_but_not_colliding_members(tmp_path, monkeypatch):
    """Case-VARIED names that do not collide (different stems) stay accepted —
    the refusal is for collisions only, not for uppercase letters."""
    monkeypatch.chdir(tmp_path)
    pkg = _minimal_pkg(tmp_path / "ok.manjupkg",
                       ["media/Alpha.wav", "media/beta.wav", "project.yaml"])
    res = runner.invoke(app, ["unpack", str(pkg), "--dest", str(tmp_path / "d")])
    # (the archive is not a full project; unpack may fail LATER for other
    # reasons — the pin is only that the casefold gate does not fire)
    assert "大小写" not in res.output


# --------------------------------------------------------------------------
# cross-destination restore (cross-drive by construction)
# --------------------------------------------------------------------------


def test_unpack_restores_to_arbitrary_destination_tree(tmp_path, monkeypatch):
    """Restore into a DIFFERENT, deeply nested destination tree: on Windows a
    different drive is this same code path (absolute pathlib paths, no cwd or
    common-root assumption). The restored project must open and carry the
    packed payload byte-for-byte."""
    from manju.core.hashing import hash_file

    project = _mk_project(tmp_path, name="源 项目")
    src_payload = project.root / "media" / "probe.bin"
    want = hash_file(src_payload)

    monkeypatch.chdir(project.root)
    out = tmp_path / "x.manjupkg"
    assert runner.invoke(app, ["pack", "--out", str(out)]).exit_code == 0

    other_tree = tmp_path / "另一块盘" / "深" / "层" / "目录"
    other_tree.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    dest = other_tree / "恢复.manju"
    res = runner.invoke(app, ["unpack", str(out), "--dest", str(dest)])
    assert res.exit_code == 0, res.output
    restored = Project(dest)  # opens as a real project
    assert hash_file(dest / "media" / "probe.bin") == want
    assert restored.root == dest.resolve()
