"""W1 Windows filesystem semantics (MANJU_WINDOWS_ONLY_LEAN_V3 §3.2/§3.3).

Red-first evidence for the Windows-only hardening wave: the four lexical
rejections Windows needs (reserved device names, NTFS ADS colons, trailing
dot/space, case-fold collisions) existed NOWHERE at HEAD — a project authored
on Linux/macOS could carry ``media/CON.wav`` or ``media/A.wav``+``media/a.wav``
and only discover the damage when Windows refuses to open it. Same wave:
``atomic_write_text`` gets the bounded sharing-violation retry (winerror
32/33 ONLY — an AV scanner / indexer briefly holding the target is the one
transient Windows failure worth riding out; access-denied/disk-full stay
immediate), and ``media/align.py`` loses the one remaining string-prefix
directory-boundary check (``str(p).startswith(str(root))`` admits the
sibling ``root_evil/`` and then crashes ``project.relpath``).

House rules honoured: the lexical predicate lives in core/idents.py (the ONE
safe-segment owner), enforcement extends the EXISTING gates (shotpackage's
``_is_unsafe_path``, ``manju check`` warnings) — no parallel validator, no new
schema, old projects only gain warnings (never new errors).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from manju.core.yamlio import atomic_write_text
import manju.core.yamlio as yamlio


# --------------------------------------------------------------------------
# §3.2 — Windows-lexical path rules (owner: core/idents.py)
# --------------------------------------------------------------------------


def test_reserved_device_name_segments_are_flagged():
    """CON/PRN/AUX/NUL/COM1-9/LPT1-9 are unopenable filenames on Windows —
    with ANY extension, in ANY case. A relpath carrying one must be flagged."""
    from manju.core.idents import windows_relpath_problems

    for rel in (
        "media/CON.wav",
        "media/con.txt",
        "shots/NUL",
        "media/nul.tar.gz",
        "media/COM7.mp4",
        "media/refs/LPT1.png",
        "PRN",
        "aux/x.wav",  # the DIRECTORY segment is reserved too
    ):
        assert windows_relpath_problems(rel), rel
    for rel in (
        "media/CONSOLE.wav",   # prefix ≠ reserved
        "media/NULLS.txt",
        "media/COM10.mp4",     # only COM1..COM9 are reserved
        "media/LPT0.png",      # LPT0 is not reserved
        "media/普通 文件.wav",  # interior spaces + CJK stay first-class (§14)
        "media/refs/x.png",
    ):
        assert not windows_relpath_problems(rel), rel


def test_ads_colon_and_trailing_dot_space_are_flagged():
    """NTFS alternate data streams (``:``) and trailing dot/space segments
    (silently stripped by Win32, producing a different file) must be flagged."""
    from manju.core.idents import windows_relpath_problems

    for rel in (
        "media/x.wav:stream",
        "media/x.wav:$DATA",
        "media/c:evil",
        "media/take./f.wav",   # trailing dot on a directory segment
        "media/take.",
        "media/trailing ",     # trailing space segment
    ):
        assert windows_relpath_problems(rel), rel
    for rel in (
        "media/song .wav",     # interior space before the extension is fine
        "media/v1.0/f.wav",
        "media/a.b.c.wav",
    ):
        assert not windows_relpath_problems(rel), rel


def test_backslash_unc_absolute_and_traversal_are_flagged():
    """Backslashes, UNC roots, absolute paths and ``..`` never belong in a
    persisted project-relative path (POSIX-style relative is the §3 contract)."""
    from manju.core.idents import windows_relpath_problems

    for rel in (
        "media\\refs\\x.png",
        "//server/share/f.wav",
        "\\\\server\\share\\f.wav",
        "/abs/f.wav",
        "C:/abs/f.wav",
        "..",
        "a/../b.wav",
        "",
    ):
        assert windows_relpath_problems(rel), rel


def test_windows_collision_key_folds_case_only():
    """The §3.2 comparison key: Windows-insensitive equality WITHOUT touching
    the displayed spelling (display keeps the original case)."""
    from manju.core.idents import windows_collision_key

    assert windows_collision_key("media/A.wav") == windows_collision_key("media/a.WAV")
    assert windows_collision_key("media/雨夜.wav") == windows_collision_key("media/雨夜.wav")
    assert windows_collision_key("media/a.wav") != windows_collision_key("media/b.wav")


def test_shotpackage_rejects_windows_unsafe_path_hints():
    """The shot-package intake gate (`_is_unsafe_path`) must reject Windows
    lexical hazards, not only absolute/traversal paths — a downloaded package
    is exactly how a reserved name would enter a clean project."""
    from manju.build.shotpackage import _is_unsafe_path

    # already rejected at HEAD (pre-existing behaviour must not regress)
    assert _is_unsafe_path("/abs/f.wav")
    assert _is_unsafe_path("C:/f.wav")
    assert _is_unsafe_path("a/../b.wav")
    assert not _is_unsafe_path("media/ok.wav")
    # the W1 gap: Windows lexical hazards passed straight through
    assert _is_unsafe_path("media/CON.wav")
    assert _is_unsafe_path("media/x.wav:stream")
    assert _is_unsafe_path("media/take./f.wav")
    assert _is_unsafe_path("media/nul.txt")


def test_check_warns_on_casefold_collision(tmp_project):
    """Two files differing only in case coexist happily on Linux and then
    clobber each other the moment the project lands on NTFS. `manju check`
    must surface the pair as a WARNING (never an error: old projects must
    keep building — §W1 completion condition)."""
    from manju.core.check import run_check

    media = tmp_project.root / "media"
    media.mkdir(exist_ok=True)
    (media / "Ambience.wav").write_bytes(b"fake-a")
    (media / "ambience.WAV").write_bytes(b"fake-b")
    if len(list(media.iterdir())) < 2:
        pytest.skip("case-insensitive filesystem — the collision cannot be staged here")

    report = run_check(tmp_project)
    hits = [w for w in report.warnings if "Ambience.wav" in w and "ambience.WAV" in w]
    assert hits, f"expected a case-collision warning naming both spellings, got: {report.warnings}"
    assert not any("Ambience" in e for e in report.errors), "collision must warn, not error"


def test_check_warns_on_reserved_name_on_disk(tmp_project):
    """A reserved-name file already on disk (authored on Linux) must surface
    as a `manju check` warning so the project is fixed BEFORE a Windows user
    hits an unopenable file."""
    from manju.core.check import run_check

    media = tmp_project.root / "media"
    media.mkdir(exist_ok=True)
    try:
        (media / "nul.txt").write_bytes(b"fake")
    except OSError:
        pytest.skip("filesystem refuses reserved names (already on Windows)")

    report = run_check(tmp_project)
    assert any("nul.txt" in w for w in report.warnings), report.warnings
    assert not any("nul.txt" in e for e in report.errors)


# --------------------------------------------------------------------------
# §3.3 — atomic write: bounded retry on sharing violations ONLY
# --------------------------------------------------------------------------


class _SharingViolation(PermissionError):
    winerror = 32  # ERROR_SHARING_VIOLATION


class _LockViolation(PermissionError):
    winerror = 33  # ERROR_LOCK_VIOLATION


class _AccessDenied(PermissionError):
    winerror = 5  # ERROR_ACCESS_DENIED — must NEVER be retried


def test_atomic_write_retries_transient_sharing_violation(tmp_path, monkeypatch):
    """An AV scanner holding the target for a few ms is THE transient Windows
    failure — a bounded retry must ride it out and land the write."""
    target = tmp_path / "t.yaml"
    atomic_write_text(target, "old")

    real_replace = os.replace
    attempts = {"n": 0}

    def flaky(src, dst, *a, **kw):
        if Path(dst) == target and attempts["n"] < 2:
            attempts["n"] += 1
            raise _SharingViolation(13, "sharing violation (simulated)")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(yamlio.os, "replace", flaky)
    monkeypatch.setattr(yamlio.time, "sleep", lambda s: None)  # no real waiting
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert attempts["n"] == 2
    assert not list(tmp_path.glob(".t.yaml.*.tmp")), "temp sibling must be cleaned up"


def test_atomic_write_never_retries_access_denied(tmp_path, monkeypatch):
    """Access denied / disk full / bad path are NOT transient — one attempt,
    old file preserved verbatim, temp cleaned (§3.3: 不得无限重试)."""
    target = tmp_path / "t.yaml"
    atomic_write_text(target, "old")

    calls = {"n": 0}

    def denied(src, dst, *a, **kw):
        calls["n"] += 1
        raise _AccessDenied(13, "access denied (simulated)")

    monkeypatch.setattr(yamlio.os, "replace", denied)
    monkeypatch.setattr(yamlio.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        atomic_write_text(target, "new")
    assert calls["n"] == 1, "winerror 5 must not be retried"
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".t.yaml.*.tmp"))


def test_atomic_write_retry_is_bounded(tmp_path, monkeypatch):
    """A file held FOREVER (winerror 33) must fail after a small, bounded
    number of attempts — never an unbounded loop — and leave the old file."""
    target = tmp_path / "t.yaml"
    atomic_write_text(target, "old")

    calls = {"n": 0}

    def held(src, dst, *a, **kw):
        calls["n"] += 1
        raise _LockViolation(13, "lock violation (simulated)")

    monkeypatch.setattr(yamlio.os, "replace", held)
    monkeypatch.setattr(yamlio.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        atomic_write_text(target, "new")
    assert 1 < calls["n"] <= 16, f"expected bounded retry, got {calls['n']} attempts"
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".t.yaml.*.tmp"))


def test_posix_permission_error_is_not_retried(tmp_path, monkeypatch):
    """A plain POSIX PermissionError (no winerror) must stay a single-attempt
    failure — the retry is a WINDOWS sharing-violation ride-out only."""
    target = tmp_path / "t.yaml"
    atomic_write_text(target, "old")

    calls = {"n": 0}

    def denied(src, dst, *a, **kw):
        calls["n"] += 1
        raise PermissionError(13, "posix denied")

    monkeypatch.setattr(yamlio.os, "replace", denied)
    monkeypatch.setattr(yamlio.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        atomic_write_text(target, "new")
    assert calls["n"] == 1
    assert target.read_text(encoding="utf-8") == "old"


# --------------------------------------------------------------------------
# §3.2 — the last string-prefix directory-boundary check (media/align.py)
# --------------------------------------------------------------------------


def test_align_apply_survives_sibling_prefix_dir(tmp_project):
    """``str(media).startswith(str(project.root))`` admits the SIBLING
    directory ``<root>_evil/`` as 'inside the project': the foreign source is
    then never copied into media/imports and ``project.relpath`` crashes with
    ValueError while writing the batch record. Real containment (resolve +
    is_relative_to) must treat it as foreign and import-copy it."""
    from manju.media.align import apply_multi_shot

    evil = tmp_project.root.parent / (tmp_project.root.name + "_evil")
    evil.mkdir()
    src = evil / "src.wav"
    src.write_bytes(b"fake-wav-bytes")

    result = apply_multi_shot(tmp_project, {"media": str(src), "rows": []})

    assert result["applied"] == []
    imported = tmp_project.root / "media" / "imports" / "src.wav"
    assert imported.exists(), "foreign sibling-prefix source must be import-copied"
    from manju.core.yamlio import read_yaml

    batch = read_yaml(
        tmp_project.root / "reports" / "ingest_batches" / f"{result['batch']}.yaml"
    )
    assert batch["media"] == "media/imports/src.wav"


# --------------------------------------------------------------------------
# §3.1 — a project directory with SPACES + CJK exercises the whole stack
# --------------------------------------------------------------------------


def test_project_lifecycle_in_space_cjk_directory(tmp_path):
    """§3.1: 至少一个测试目录包含空格和中文 — scaffold, check, atomic write
    and the process lock must all work under ``含 空格/雨夜 便利店.manju``."""
    from manju.core.check import run_check
    from manju.core.container import Project
    from manju.runtime.buildlock import BuildLock

    root = tmp_path / "含 空格" / "雨夜 便利店.manju"
    project = Project.create(root, git_init=False)
    assert project.root.exists()

    report = run_check(project)
    assert report.ok, report.errors

    target = project.root / "timeline" / "note.yaml"
    atomic_write_text(target, "note: 测试 空格\n")
    assert target.read_text(encoding="utf-8") == "note: 测试 空格\n"

    with BuildLock(project.root, actor="engine"):
        assert (project.root / ".manju" / "build.lock").exists()
    assert not (project.root / ".manju" / "build.lock").exists()
