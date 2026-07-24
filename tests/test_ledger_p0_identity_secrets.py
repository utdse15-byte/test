"""Ledger P0 regressions — group ``identity_secrets``.

Covers the external-audit P0 entries owned by this group:

- CLI-P0-001  — a plain ``project.yaml`` mistaken for a Manju project (and
  polluted by write commands);
- SERIES-P0-001 — the same for a plain ``series.yaml``;
- CLI-P0-002  — the secret scan missing ``.env`` (and extensionless config);
- CLI-P0-003  — a >2 MB file skipped by the secret scan wholesale;
- HISTORY-P0-001 — default ``.gitignore`` not excluding ``.env`` + ``snapshot``
  committing a key into git history.

Red-first: each asserts the FIXED behaviour. The fixes live in core/container.py,
core/models.py, core/series.py, core/check.py, core/history.py (owners of the
identity marker, the discovery gate, and the secret scanner).
"""

from __future__ import annotations

import shutil

import pytest

from manju.core.check import file_has_secret, run_check, should_scan_for_secret
from manju.core.container import PROJECT_FILE, Project, ProjectError
from manju.core.models import PROJECT_FORMAT, ProjectConfig
from manju.core.series import (
    SERIES_FILE,
    SERIES_FORMAT,
    Series,
    SeriesError,
    new_episode,
)
from manju.core.yamlio import read_yaml, write_yaml

# A syntactically valid fake OpenAI-style key (matches SECRET_PATTERNS' sk- form
# without being a real credential).
_FAKE_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


# ============================================================ CLI-P0-001
# project identity marker + strict discovery + write-before verification


def test_new_project_seeds_format_marker(tmp_project):
    """`manju new` (Project.create) stamps the non-defaultable format marker."""
    raw = read_yaml(tmp_project.root / PROJECT_FILE)
    assert raw["format"] == PROJECT_FORMAT
    assert PROJECT_FORMAT == "manju.project/v1"  # the documented on-disk value


def test_bare_project_yaml_is_not_a_project(tmp_path):
    """A plain project.yaml (another tool's) with no marker and no structure is
    refused by discovery with a migration hint — not silently taken over."""
    bare = tmp_path / "unrelated"
    bare.mkdir()
    (bare / PROJECT_FILE).write_text("name: unrelated\n", encoding="utf-8")
    with pytest.raises(ProjectError) as exc:
        Project.find(bare)
    assert PROJECT_FORMAT in str(exc.value)  # the hint names the marker to add


def test_old_project_without_marker_still_resolves_via_structure(tmp_path):
    """An OLD project (no marker) is still recognized by a structure signal —
    discovery never forces a migration."""
    old = tmp_path / "old"
    (old / ".manju").mkdir(parents=True)
    (old / PROJECT_FILE).write_text("name: old\n", encoding="utf-8")
    found = Project.find(old)
    assert found.root == old.resolve()


def test_find_walks_past_bare_to_a_real_parent(tmp_project, tmp_path):
    """A bare project.yaml on the way up is stepped over, not stopped at: a real
    project further up still resolves."""
    stray = tmp_project.root / "sub"
    stray.mkdir()
    (stray / PROJECT_FILE).write_text("name: stray\n", encoding="utf-8")
    assert Project.find(stray).root == tmp_project.root


def test_write_command_refuses_bare_project_dir(tmp_path):
    """Write-before verification at the Project layer: opening a bare
    project.yaml directly and writing to it is refused (no pollution)."""
    bare = tmp_path / "unrelated"
    bare.mkdir()
    (bare / PROJECT_FILE).write_text("name: unrelated\n", encoding="utf-8")
    project = Project(bare)  # __init__ stays lenient (compat), but writes refuse
    with pytest.raises(ProjectError):
        project.save_config(ProjectConfig(name="unrelated"))
    with pytest.raises(ProjectError):
        project.verify_manju_identity()
    # nothing was created in the unrelated directory
    assert not (bare / "shots").exists()
    assert not (bare / "events.jsonl").exists()


def test_absent_format_serializes_byte_identically(tmp_path):
    """Drop-None like the other optional ProjectConfig fields: a config without
    the marker emits no `format` key in EITHER dump form (old project.yaml stays
    byte-stable)."""
    c = ProjectConfig(name="t")
    assert c.format is None
    assert "format" not in c.model_dump()
    assert "format" not in c.model_dump(exclude_none=True)
    # an old project.yaml loads and re-saves without gaining the key
    old = tmp_path / "old"
    (old / ".manju").mkdir(parents=True)
    write_yaml(old / PROJECT_FILE, {"name": "old", "fps": 24})
    project = Project(old)
    project.save_config(project.load_config())  # allowed: structure signal present
    assert "format" not in (old / PROJECT_FILE).read_text(encoding="utf-8")


# ============================================================ SERIES-P0-001


def test_new_series_seeds_format_marker(tmp_path):
    series = Series.create(tmp_path / "show", name="show", git_init=False)
    raw = read_yaml(series.root / SERIES_FILE)
    assert raw["format"] == SERIES_FORMAT
    assert SERIES_FORMAT == "manju.series/v1"


def test_bare_series_yaml_is_not_a_series(tmp_path):
    bare = tmp_path / "unrelated"
    bare.mkdir()
    (bare / SERIES_FILE).write_text("name: unrelated\nowner: other-tool\n", encoding="utf-8")
    with pytest.raises(SeriesError) as exc:
        Series.find(bare)
    assert SERIES_FORMAT in str(exc.value)


def test_new_episode_refuses_bare_series_dir(tmp_path):
    """A write command (new_episode) against a foreign series.yaml is refused —
    no episodes/ dir created, original file untouched."""
    bare = tmp_path / "unrelated"
    bare.mkdir()
    (bare / SERIES_FILE).write_text("name: unrelated\nowner: other-tool\n", encoding="utf-8")
    series = Series(bare)  # lenient __init__
    with pytest.raises(SeriesError):
        new_episode(series, "E01")
    assert not (bare / "episodes").exists()
    assert read_yaml(bare / SERIES_FILE) == {"name": "unrelated", "owner": "other-tool"}


def test_old_series_without_marker_resolves_via_structure(tmp_path):
    old = tmp_path / "oldshow"
    (old / "episodes").mkdir(parents=True)
    (old / SERIES_FILE).write_text("name: oldshow\n", encoding="utf-8")
    assert Series.find(old).root == old.resolve()


# ============================================================ CLI-P0-002
# secret scan selection: suffix whitelist ∪ filename pattern


def test_env_file_is_selected_and_flagged(tmp_project):
    (tmp_project.root / ".env").write_text(f"OPENAI_API_KEY={_FAKE_KEY}\n", encoding="utf-8")
    assert should_scan_for_secret(tmp_project.root / ".env")
    report = run_check(tmp_project)
    assert any(".env" in e and "API key" in e for e in report.errors)


def test_env_variants_and_extensionless_configs_are_selected(tmp_project):
    for name in (".env.local", ".env.production", "credentials", ".netrc", ".pypirc"):
        assert should_scan_for_secret(tmp_project.root / name), name
    # a redacted example template is still selected (so a real key in it is
    # caught) but is NOT gitignored — see the .gitignore test below
    assert should_scan_for_secret(tmp_project.root / ".env.example")


def test_binary_file_with_nul_is_not_a_false_positive(tmp_project):
    """The NUL heuristic excludes binary content from a scanned suffix (a stray
    key-looking byte run inside a binary blob is not a leaked credential)."""
    p = tmp_project.root / "blob.txt"
    p.write_bytes(b"\x00\x01\x02" + f"OPENAI_API_KEY={_FAKE_KEY}".encode() + b"\x00")
    assert file_has_secret(p) is False


# ============================================================ CLI-P0-003
# streamed scan — an oversize/padded file no longer skips the scan wholesale


@pytest.mark.parametrize("at", ["head", "tail"])
def test_oversize_file_key_is_found(tmp_project, at):
    """A 2,000,001-byte file's key is found whether it sits at the head or the
    tail — padding past 2 MB no longer bypasses the scan."""
    key_line = f"OPENAI_API_KEY={_FAKE_KEY}\n"
    filler = "A" * 2_000_000
    content = (key_line + filler) if at == "head" else (filler + "\n" + key_line)
    p = tmp_project.root / f"big_{at}.txt"
    p.write_text(content, encoding="utf-8")
    assert p.stat().st_size > 2_000_000
    assert file_has_secret(p) is True
    report = run_check(tmp_project)
    assert any(f"big_{at}.txt" in e for e in report.errors)


# ============================================================ HISTORY-P0-001


def test_default_gitignore_excludes_env(tmp_project):
    text = (tmp_project.root / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in text and ".env.*" in text
    # explicit template exceptions stay tracked
    assert "!.env.example" in text


@pytest.mark.skipif(shutil.which("git") is None, reason="git required")
def test_snapshot_fails_closed_on_staged_secret(tmp_path):
    """snapshot refuses (fail-closed) to commit a key into git history, and
    unstages so nothing is left staged."""
    from manju.core.history import HistoryError, snapshot

    project = Project.create(tmp_path / "sec", git_init=True)
    # a key hardcoded into a TRACKED truth-text file (not .env — that is
    # gitignored; this exercises the preflight itself)
    (project.root / "story" / "leak.txt").write_text(
        f"token: {_FAKE_KEY}\n", encoding="utf-8"
    )
    with pytest.raises(HistoryError) as exc:
        snapshot(project, label="wip")
    assert "leak.txt" in str(exc.value)
    # unstaged: a follow-up staged diff is clean of the offender
    staged = project.root  # nothing committed
    from manju.core import gitops

    proc = gitops._run(staged, "diff", "--cached", "--name-only")
    assert proc is not None and "leak.txt" not in proc.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git required")
def test_snapshot_succeeds_and_env_never_committed(tmp_path):
    """A clean snapshot still works, and a gitignored .env never reaches the
    HEAD tree."""
    from manju.core.history import snapshot

    project = Project.create(tmp_path / "ok", git_init=True)
    (project.root / ".env").write_text(f"OPENAI_API_KEY={_FAKE_KEY}\n", encoding="utf-8")
    result = snapshot(project, label="first")
    assert result["sha"] and result["clean"] is False
    from manju.core import gitops

    tree = gitops._run(project.root, "ls-files")
    assert tree is not None
    assert ".env" not in tree.stdout.splitlines()
