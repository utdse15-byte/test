"""Ledger P0 (provider_refs): the media/refs fallback tier must never read or
upload a file that lives outside the project.

PROVIDER-REF-001 — the three DECLARED tiers (params / shot refs / bible) go
through ``refs.resolve_local_ref`` → ``Project.resolve``, which is realpath-based
and therefore already refuses an in-project symlink aimed outside. The
``media/refs`` FALLBACK tier bypassed that guard entirely: it selected entries
with ``Path.is_file()``, which FOLLOWS a link, so a symlink planted in
``media/refs`` became a valid primary reference and its bytes were base64'd into
a cloud request. The bytes are re-verified again on the open descriptor right
before upload (the stored path is never trusted on its own).

PROVIDER-REF-002 — ``primary_image`` and ``primary_image_path_str()`` disagreed
about which item is "the primary" when the first image ref is blocked/missing;
comfyui path mode and local_cmd consume the string accessor.

POSIX symlink/hardlink/FIFO shapes run here; the Windows junction / reparse
point / casefold shapes have their own branch (skipped off Windows).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from manju.providers.refs import (
    TIER_PARAMS,
    TIER_REFS_DIR,
    RefItem,
    RefSet,
    base64_ref,
    read_ref_bytes,
    resolve_refs,
)

WINDOWS = os.name == "nt"
SECRET = b"OUTSIDE-SECRET-BYTES"


def _symlink_ok(tmp_path: Path) -> bool:
    try:
        (tmp_path / "_tgt").write_bytes(b"x")
        link = tmp_path / "_lnkchk"
        link.symlink_to(tmp_path / "_tgt")
        link.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def _outside_secret(tmp_path: Path) -> Path:
    outside = tmp_path / "outside" / "outside-secret.png"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(SECRET)
    return outside


def _refset(project, add_shot, shot_id="S001", **overrides):
    shot = add_shot(project, shot_id, **overrides)
    return resolve_refs(project, shot, {})


# ------------------------------------------------- PROVIDER-REF-001 (intake)


def test_refs_dir_symlink_to_outside_is_never_a_reference(
    tmp_project, add_shot, tmp_path
):
    """The ledger repro: media/refs/00-first.png → an outside file."""
    if not _symlink_ok(tmp_path):
        pytest.skip("symlinks not creatable on this platform/privilege level")
    outside = _outside_secret(tmp_path)
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.refs_dir / "00-first.png").symlink_to(outside)

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image is None
    assert rs.images == []
    blocked = [it for it in rs.image_items() if it.blocked_reason]
    assert blocked, "the refused entry must stay visible in the lineage"
    assert blocked[0].tier == TIER_REFS_DIR and blocked[0].exists is False


def test_refs_dir_symlink_does_not_shadow_a_real_fallback_image(
    tmp_project, add_shot, tmp_path
):
    """A refused entry is skipped, not fatal: the next safe file still wins."""
    if not _symlink_ok(tmp_path):
        pytest.skip("symlinks not creatable on this platform/privilege level")
    outside = _outside_secret(tmp_path)
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.refs_dir / "00-first.png").symlink_to(outside)
    real = tmp_project.refs_dir / "10-real.png"
    real.write_bytes(b"inside-png")

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image == real
    assert rs.primary_image_source == TIER_REFS_DIR
    assert read_ref_bytes(rs.image_items()[0]) == b"inside-png"


def test_refs_dir_symlink_pointing_inside_the_project_is_still_refused(
    tmp_project, add_shot, tmp_path
):
    """No-follow intake is unconditional — an in-project link target does not
    make a link an acceptable reference (identity is the file, not the name)."""
    if not _symlink_ok(tmp_path):
        pytest.skip("symlinks not creatable on this platform/privilege level")
    inside = tmp_project.resolve("media/imports/real.png")
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(b"inside")
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.refs_dir / "00-link.png").symlink_to(inside)

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image is None


def test_refs_dir_hardlink_to_outside_file_is_refused(tmp_project, add_shot, tmp_path):
    """A hardlink carries no link flag — only the extra name count betrays it,
    and it reads the outside bytes just as well as a symlink."""
    outside = _outside_secret(tmp_path)
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.link(outside, tmp_project.refs_dir / "00-first.png")
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hardlinks not creatable across these paths")

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image is None
    assert any(it.blocked_reason for it in rs.image_items())


@pytest.mark.skipif(WINDOWS, reason="POSIX FIFO")
def test_refs_dir_fifo_is_refused_without_opening_it(tmp_project, add_shot, tmp_path):
    """A FIFO named ``*.png`` must be refused by lstat, never opened (an open
    of a FIFO blocks until a writer appears)."""
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    os.mkfifo(tmp_project.refs_dir / "00-first.png")

    add_shot(tmp_project, "S001")
    code = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "from manju.core.container import Project;"
        "from manju.providers.refs import resolve_refs;"
        "p = Project.find(%r);"
        "rs = resolve_refs(p, p.load_shot('S001'), {});"
        "print('PRIMARY', rs.primary_image)"
    ) % (str(Path(__file__).resolve().parents[1] / "src"), str(tmp_project.root))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    assert "PRIMARY None" in proc.stdout


@pytest.mark.skipif(WINDOWS, reason="POSIX FIFO")
def test_declared_fifo_ref_readability_check_does_not_block(tmp_project, add_shot):
    """Side-effect of routing the readability probe through the verified open:
    the pre-submit check used to ``open(path, "rb")`` a DECLARED ref, which
    blocks forever on a FIFO. It must now refuse from lstat instead."""
    fifo = tmp_project.resolve("media/refs/pipe.png")
    fifo.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(fifo)
    shot = add_shot(
        tmp_project, "S001",
        generation={"params": {"image": "media/refs/pipe.png"}},
    )

    code = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "from manju.core.container import Project;"
        "from manju.providers.refs import resolve_refs, unreadable_ref_message;"
        "p = Project.find(%r);"
        "rs = resolve_refs(p, p.load_shot(%r), {});"
        "print('MSG', unreadable_ref_message(rs.image_items()) is not None)"
    ) % (
        str(Path(__file__).resolve().parents[1] / "src"),
        str(tmp_project.root),
        shot.id,
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    assert "MSG True" in proc.stdout


def test_refs_dir_linked_parent_directory_is_refused(tmp_project, add_shot, tmp_path):
    """media/refs itself replaced by a link to an outside directory."""
    if not _symlink_ok(tmp_path):
        pytest.skip("symlinks not creatable on this platform/privilege level")
    outside_dir = tmp_path / "outside_refs"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "00-first.png").write_bytes(SECRET)
    refs_dir = tmp_project.refs_dir
    if refs_dir.exists():
        for child in refs_dir.iterdir():
            child.unlink()
        refs_dir.rmdir()
    refs_dir.symlink_to(outside_dir, target_is_directory=True)

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image is None
    assert rs.images == []


def test_declared_tier_symlink_escape_is_refused_by_the_containment_guard(
    tmp_project, add_shot, tmp_path
):
    """The AUTHORIZED path: an in-project symlink named from generation.params
    resolves outside the root, so ``resolve_local_ref`` blocks it. Proven, not
    inferred from reading Project.resolve."""
    if not _symlink_ok(tmp_path):
        pytest.skip("symlinks not creatable on this platform/privilege level")
    outside = _outside_secret(tmp_path)
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.refs_dir / "declared.png").symlink_to(outside)

    rs = _refset(
        tmp_project,
        add_shot,
        generation={"params": {"image": "media/refs/declared.png"}},
    )

    item = rs.image_items()[0]
    assert item.tier == TIER_PARAMS
    assert item.blocked_reason is not None
    assert item.exists is False
    assert rs.primary_image is None


# ------------------------------------------------ PROVIDER-REF-001 (upload)


def test_read_ref_bytes_refuses_a_path_swapped_to_a_link_after_resolution(
    tmp_project, add_shot, tmp_path
):
    """上传前重新校验:the stored path is re-verified on the OPEN descriptor, so
    a file swapped for a link between resolution and upload never uploads."""
    if not _symlink_ok(tmp_path):
        pytest.skip("symlinks not creatable on this platform/privilege level")
    outside = _outside_secret(tmp_path)
    real = tmp_project.refs_dir
    real.mkdir(parents=True, exist_ok=True)
    target = real / "00-first.png"
    target.write_bytes(b"inside-png")

    rs = _refset(tmp_project, add_shot)
    item = rs.image_items()[0]
    assert item.exists

    target.unlink()
    target.symlink_to(outside)  # TOCTOU swap

    with pytest.raises(Exception) as exc:
        read_ref_bytes(item)
    assert SECRET not in str(exc.value).encode("utf-8", "replace")

    with pytest.raises(Exception):
        base64_ref(item)


def test_base64_ref_still_encodes_a_normal_contained_ref(tmp_project, add_shot):
    """Byte-identity for the honest path."""
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.refs_dir / "00-first.png").write_bytes(b"hello-bytes")

    rs = _refset(tmp_project, add_shot)
    encoded = base64_ref(rs.image_items()[0])

    assert encoded.startswith("data:image/png;base64,")
    import base64 as _b64

    assert _b64.b64decode(encoded.split(",", 1)[1]) == b"hello-bytes"


# ------------------------------------------------------- PROVIDER-REF-002


def test_primary_image_path_str_agrees_with_primary_image(tmp_project, add_shot):
    """First ref missing, second one real: both accessors must name the SAME
    verified item (comfyui path mode / local_cmd consume the string one)."""
    real = tmp_project.resolve("media/refs/second.png")
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"png")

    rs = _refset(
        tmp_project,
        add_shot,
        generation={
            "params": {"images": ["media/refs/missing.png", "media/refs/second.png"]}
        },
    )

    assert rs.primary_image == real
    assert rs.primary_image_path_str() == str(real)


def test_primary_image_path_str_never_returns_a_blocked_path(tmp_project, add_shot):
    """An out-of-bounds first ref must not survive as "the primary path string"
    — it would be handed straight to a local command."""
    rs = _refset(
        tmp_project,
        add_shot,
        generation={"params": {"images": ["../../outside-secret.png"]}},
    )

    assert rs.primary_image is None
    assert rs.image_items()[0].blocked_reason is not None
    assert rs.primary_image_path_str() == ""


def test_primary_image_path_str_keeps_a_plain_missing_path(tmp_project, add_shot):
    """Byte-identity guard: with no usable image the accessor still reports the
    contained-but-missing path (comfyui path mode has always passed it on)."""
    rs = _refset(
        tmp_project,
        add_shot,
        generation={"params": {"image": "media/refs/gone.png"}},
    )

    assert rs.primary_image is None
    assert rs.primary_image_path_str().endswith("gone.png")


def test_refset_accessors_agree_on_url_only_refs():
    items = [
        RefItem(ref="http://x/a.png", tier=TIER_PARAMS, kind="image", path=None,
                is_url=True, exists=True),
    ]
    rs = RefSet.from_items(items)
    assert rs.primary_image is None
    assert rs.primary_image_path_str() == "http://x/a.png"


# --------------------------------------------------------- Windows branch


@pytest.mark.skipif(not WINDOWS, reason="Windows junction / reparse point")
def test_refs_dir_junction_to_outside_directory_is_refused(
    tmp_project, add_shot, tmp_path
):
    """NTFS junction: ``Path.is_symlink()`` misses it, ``st_reparse_tag`` does
    not — the safeio owner is what makes this refusal work on Windows."""
    outside_dir = tmp_path / "outside_refs"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "00-first.png").write_bytes(SECRET)
    junction = tmp_project.root / "media" / "junc"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_dir)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        pytest.skip(f"mklink /J unavailable: {proc.stderr}")
    refs_dir = tmp_project.refs_dir
    if refs_dir.exists():
        for child in refs_dir.iterdir():
            child.unlink()
        refs_dir.rmdir()
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(refs_dir), str(outside_dir)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        pytest.skip(f"mklink /J unavailable: {proc.stderr}")

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image is None


@pytest.mark.skipif(not WINDOWS, reason="Windows reparse point at the leaf")
def test_refs_dir_file_symlink_reparse_point_is_refused(tmp_project, add_shot, tmp_path):
    outside = _outside_secret(tmp_path)
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    link = tmp_project.refs_dir / "00-first.png"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", str(link), str(outside)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        pytest.skip(f"mklink unavailable (needs dev mode / admin): {proc.stderr}")

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image is None
    assert any(it.blocked_reason for it in rs.image_items())


@pytest.mark.skipif(not WINDOWS, reason="Windows 8.3 short name / casefold")
def test_refs_dir_casefold_name_does_not_bypass_the_guard(tmp_project, add_shot, tmp_path):
    """An UPPERCASE-named symlink is the same object on a casefolding volume —
    the refusal is by file identity, never by name spelling."""
    outside = _outside_secret(tmp_path)
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    link = tmp_project.refs_dir / "00-FIRST.PNG"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", str(link), str(outside)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        pytest.skip(f"mklink unavailable (needs dev mode / admin): {proc.stderr}")

    rs = _refset(tmp_project, add_shot)

    assert rs.primary_image is None
