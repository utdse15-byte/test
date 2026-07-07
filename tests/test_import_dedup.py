"""Tests for find_duplicate_import — the content-dedup warning for `manju import`.

Imports are sacred (§3): the helper never deletes or rewrites anything under
imports/ — it only finds an existing identical file so the CLI can warn
"已导入过 / already imported" instead of silently minting another full copy
(§11 import conveniences; optimization assessment 2.10-2).
"""

from __future__ import annotations

from manju.media.preview import find_duplicate_import


def test_same_content_different_name_is_found(tmp_project, tmp_path):
    existing = tmp_project.imports_dir / "rushes_a.mp4"
    existing.write_bytes(b"same-bytes" * 100)
    candidate = tmp_path / "rushes_dropped_again.mp4"
    candidate.write_bytes(b"same-bytes" * 100)
    assert find_duplicate_import(tmp_project.imports_dir, candidate) == existing
    # read-only by contract: both files still exist untouched
    assert existing.exists() and candidate.exists()


def test_same_size_different_content_returns_none(tmp_project, tmp_path):
    (tmp_project.imports_dir / "clip.mp4").write_bytes(b"aaaa-bytes")
    candidate = tmp_path / "clip.mp4"
    candidate.write_bytes(b"bbbb-bytes")  # same size -> sha256 must decide
    assert find_duplicate_import(tmp_project.imports_dir, candidate) is None


def test_different_size_returns_none_via_fast_path(tmp_project, tmp_path):
    (tmp_project.imports_dir / "clip.mp4").write_bytes(b"short")
    candidate = tmp_path / "clip.mp4"
    candidate.write_bytes(b"much-longer-content")
    assert find_duplicate_import(tmp_project.imports_dir, candidate) is None


def test_empty_imports_dir_returns_none(tmp_project, tmp_path):
    candidate = tmp_path / "clip.mp4"
    candidate.write_bytes(b"content")
    assert find_duplicate_import(tmp_project.imports_dir, candidate) is None


def test_candidate_inside_imports_is_not_its_own_duplicate(tmp_project):
    inside = tmp_project.imports_dir / "clip.mp4"
    inside.write_bytes(b"unique-bytes")
    assert find_duplicate_import(tmp_project.imports_dir, inside) is None


def test_first_duplicate_in_sorted_order_wins(tmp_project, tmp_path):
    for name in ("b_copy.mp4", "a_copy.mp4"):
        (tmp_project.imports_dir / name).write_bytes(b"dup-bytes")
    candidate = tmp_path / "incoming.mp4"
    candidate.write_bytes(b"dup-bytes")
    found = find_duplicate_import(tmp_project.imports_dir, candidate)
    assert found == tmp_project.imports_dir / "a_copy.mp4"  # deterministic
