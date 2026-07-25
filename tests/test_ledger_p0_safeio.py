"""core/safeio.py — the shared validated-output owner (audit-wave P0 root cause).

Pins the policy the ledger's safe-write P0 family converges on:
outside-project destinations stay usable, inside-project destinations only
under publish subtrees, the leaf is never a link/dir/special file, all bytes
travel via an exclusive random-named sibling temp, and failure leaves the
prior file byte-identical (失败零写入).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from manju.core.safeio import (
    SafeOutError,
    checked_out_path,
    open_append_nofollow,
    publish_bytes,
    publish_text,
    publish_tmp,
)

# `os.mkfifo` does not exist on Windows — the two FIFO cases below raised
# AttributeError there and turned the HARD gate red (run #255). Windows has no
# FIFO to plant in a directory, so the vector genuinely does not exist; the
# sibling module test_ledger_p0_provider_refs.py already skips its FIFO cases
# this way, and this file simply missed the marker. Everything else here —
# symlinks, hardlinks, directories, the atomic-publish contract — still runs on
# Windows, so the module never goes green by wholesale skipping.
WINDOWS = os.name == "nt"


@pytest.fixture()
def proot(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "exports").mkdir(parents=True)
    (root / "story").mkdir()
    (root / "project.yaml").write_text("name: p\n", encoding="utf-8")
    (root / "story" / "script.md").write_text("truth\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------- checked_out_path

def test_outside_project_path_is_allowed(proot: Path, tmp_path: Path) -> None:
    out = tmp_path / "backup.zip"
    assert checked_out_path(out, project_root=proot) == Path(os.path.abspath(out))


def test_truth_file_destination_is_refused(proot: Path) -> None:
    with pytest.raises(SafeOutError):
        checked_out_path(proot / "project.yaml", project_root=proot)
    with pytest.raises(SafeOutError):
        checked_out_path(proot / "story" / "script.md", project_root=proot)


def test_inside_project_exports_is_allowed(proot: Path) -> None:
    out = proot / "exports" / "x.zip"
    assert checked_out_path(out, project_root=proot) == out


def test_dot_git_and_dot_manju_always_refused(proot: Path) -> None:
    for sub in (".git", ".manju"):
        (proot / sub).mkdir(exist_ok=True)
        with pytest.raises(SafeOutError):
            checked_out_path(proot / sub / "f", project_root=proot,
                             inside_roots=(".git", ".manju", "exports"))


def test_existing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SafeOutError):
        checked_out_path(tmp_path)


def test_symlink_leaf_is_refused(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    link = tmp_path / "out.zip"
    link.symlink_to(victim)
    with pytest.raises(SafeOutError):
        checked_out_path(link)
    assert victim.read_text(encoding="utf-8") == "keep me\n"


def test_linked_parent_inside_project_is_refused(proot: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (proot / "exports" / "sub").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SafeOutError):
        checked_out_path(proot / "exports" / "sub" / "x.zip", project_root=proot)


@pytest.mark.skipif(WINDOWS, reason="POSIX FIFO — os.mkfifo does not exist on Windows")
def test_fifo_destination_is_refused(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(SafeOutError):
        checked_out_path(fifo)


# ---------------------------------------------------------------- publish_*

def test_publish_is_atomic_and_replaces_regular_file(tmp_path: Path) -> None:
    out = tmp_path / "o.txt"
    out.write_text("old\n", encoding="utf-8")
    publish_text(out, "new\n")
    assert out.read_text(encoding="utf-8") == "new\n"
    leftovers = [p for p in tmp_path.iterdir() if p.name != "o.txt"]
    assert leftovers == []


def test_failed_publish_leaves_prior_bytes_and_no_temp(tmp_path: Path) -> None:
    out = tmp_path / "o.bin"
    out.write_bytes(b"prior")
    with pytest.raises(RuntimeError, match="boom"):
        with publish_tmp(out) as tmp:
            tmp.write_bytes(b"partial")
            raise RuntimeError("boom")
    assert out.read_bytes() == b"prior"
    assert [p.name for p in tmp_path.iterdir()] == ["o.bin"]


def test_publish_bytes_creates_parents(tmp_path: Path) -> None:
    out = tmp_path / "a" / "b" / "o.bin"
    publish_bytes(out, b"x")
    assert out.read_bytes() == b"x"


# ---------------------------------------------------------------- open_append_nofollow

def test_append_refuses_symlink_leaf(tmp_path: Path) -> None:
    victim = tmp_path / "truth.md"
    victim.write_text("truth\n", encoding="utf-8")
    link = tmp_path / "events.jsonl"
    link.symlink_to(victim)
    with pytest.raises(SafeOutError):
        open_append_nofollow(link)
    assert victim.read_text(encoding="utf-8") == "truth\n"


def test_append_refuses_multi_hardlink_file(tmp_path: Path) -> None:
    a = tmp_path / "events.jsonl"
    a.write_bytes(b"")
    b = tmp_path / "other-name"
    os.link(a, b)
    with pytest.raises(SafeOutError):
        open_append_nofollow(a)


@pytest.mark.skipif(WINDOWS, reason="POSIX FIFO — os.mkfifo does not exist on Windows")
def test_append_refuses_fifo(tmp_path: Path) -> None:
    fifo = tmp_path / "events.jsonl"
    os.mkfifo(fifo)
    with pytest.raises(SafeOutError):
        open_append_nofollow(fifo)


def test_append_normal_file_appends_bytes(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    with open_append_nofollow(p) as f:
        f.write(b'{"a":1}\n')
    with open_append_nofollow(p) as f:
        f.write(b'{"a":2}\n')
    assert p.read_bytes() == b'{"a":1}\n{"a":2}\n'
    st = os.stat(p)
    assert stat.S_ISREG(st.st_mode)
