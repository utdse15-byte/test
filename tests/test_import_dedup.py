"""Tests for find_duplicate_import — the content-dedup warning for `manju import`.

Imports are sacred (§3): the helper never deletes or rewrites anything under
imports/ — it only finds an existing identical file so the CLI can warn
"已导入过 / already imported" instead of silently minting another full copy
(§11 import conveniences; optimization assessment 2.10-2).
"""

from __future__ import annotations

import shutil

import pytest

from manju.media.preview import find_duplicate_import, make_preview


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


# --------------------------------------------------- round-W #68: make_preview

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


def _clip(dest, seconds=1.0, size="64x64"):
    import subprocess

    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={seconds}:size={size}:rate=10",
         "-pix_fmt", "yuv420p", str(dest)],
        check=True, capture_output=True,
    )


@needs_ffmpeg
def test_make_preview_is_keyed_by_content_not_stem(tmp_path):
    """round-W #68: two DIFFERENT source files that happen to share a
    basename in different directories must NOT collide on the same thumb —
    the destination name is the source's content hash, not `src.stem`."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    clip_a = dir_a / "take.mp4"
    clip_b = dir_b / "take.mp4"  # SAME basename, DIFFERENT content
    _clip(clip_a, size="64x64")
    _clip(clip_b, size="96x96")

    thumbs_dir = tmp_path / "thumbs"
    out_a = make_preview(clip_a, thumbs_dir)
    out_b = make_preview(clip_b, thumbs_dir)

    assert out_a is not None and out_b is not None
    assert out_a != out_b  # distinct cache entries, not a collision
    assert out_a.name != "take.jpg"  # no longer stem-keyed
    assert out_a.exists() and out_b.exists()
    assert out_a.read_bytes() != out_b.read_bytes()  # genuinely different thumbs


@needs_ffmpeg
def test_make_preview_same_content_reuses_the_same_name(tmp_path):
    """Content-addressing cuts both ways: identical bytes at two different
    paths/names produce the SAME thumb entry (a legitimate cache hit, not a
    collision — the two files really do have the same picture)."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    clip_a = dir_a / "one.mp4"
    clip_b = dir_b / "two.mp4"
    _clip(clip_a)
    shutil.copyfile(clip_a, clip_b)  # byte-identical, different name/dir

    thumbs_dir = tmp_path / "thumbs"
    out_a = make_preview(clip_a, thumbs_dir)
    out_b = make_preview(clip_b, thumbs_dir)
    assert out_a == out_b
