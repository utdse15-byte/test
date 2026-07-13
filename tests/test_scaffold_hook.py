"""scaffold_shots + install_check_hook — the two opt-in "mature-ecosystem"
conveniences: a cargo-new-style shot skeleton and a husky-style pre-commit gate.

Both obey the house doctrine: a scaffold shapes a file but never AUTHORS its
content (§2), and the pre-commit hook keeps broken truth out of history (§1-②)
without ever clobbering a hook the human already wrote.

Hermetic: the hook tests init REAL git repos inside tmp_path and point
GIT_CONFIG_GLOBAL/SYSTEM at os.devnull so host git config cannot leak in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from manju.core import gitops
from manju.core.check import run_check
from manju.core.container import Project, scaffold_shots


@pytest.fixture(autouse=True)
def _hermetic_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No global/system git config, no ambient GIT_* scoping."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


def _git_init(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)


# ========================================================= install_check_hook


def test_hook_refuses_non_repo(tmp_path: Path) -> None:
    """No repo -> nothing to hook into; must return None and create nothing."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "note.txt").write_text("not a repo\n", encoding="utf-8")

    assert gitops.install_check_hook(plain) is None
    assert not (plain / ".git").exists()


def test_hook_refuses_nonexistent_dir(tmp_path: Path) -> None:
    assert gitops.install_check_hook(tmp_path / "ghost") is None


def test_hook_installs_executable_with_marker(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _git_init(root)

    hook = gitops.install_check_hook(root)
    assert hook == root / ".git" / "hooks" / "pre-commit"
    assert hook is not None and hook.exists()

    text = hook.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")          # portable sh script
    assert "installed by manju" in text          # our marker line
    assert "exec manju check" in text            # the actual gate

    mode = hook.stat().st_mode
    if os.name != "nt":  # exec bits are POSIX; git-for-windows runs hooks via sh
        assert mode & 0o111 == 0o111                  # exec bit set (0o755)


def test_hook_reinstall_over_own_marker_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _git_init(root)

    first = gitops.install_check_hook(root)
    assert first is not None
    first_text = first.read_text(encoding="utf-8")

    # re-installing over OUR OWN hook is fine: same path, same content, still exec
    second = gitops.install_check_hook(root)
    assert second == first
    assert second.read_text(encoding="utf-8") == first_text
    if os.name != "nt":  # exec bits are POSIX; git-for-windows runs hooks via sh
        assert second.stat().st_mode & 0o111 == 0o111


def test_hook_preserves_foreign_pre_commit(tmp_path: Path) -> None:
    """A pre-commit hook the human/another tool wrote must never be clobbered."""
    root = tmp_path / "repo"
    _git_init(root)

    hook_path = root / ".git" / "hooks" / "pre-commit"
    foreign = "#!/bin/sh\necho my own hook\nexit 0\n"
    hook_path.write_text(foreign, encoding="utf-8")
    hook_path.chmod(0o755)

    assert gitops.install_check_hook(root) is None            # refused
    assert hook_path.read_text(encoding="utf-8") == foreign   # untouched


# =============================================================== scaffold_shots


@pytest.fixture
def bare_project(tmp_path: Path) -> Project:
    """A fresh project with EMPTY bibles (no scenes/characters) and no git —
    the exact 'empty-bible' condition scaffold_shots must pass ``check`` on."""
    return Project.create(tmp_path / "空白项目", git_init=False)


def test_scaffold_creates_files_and_index_entries(bare_project: Project) -> None:
    created = scaffold_shots(bare_project, 3)
    assert created == ["S001", "S002", "S003"]
    for sid in created:
        assert bare_project.shot_path(sid).exists()
    assert bare_project.load_index().order == ["S001", "S002", "S003"]

    # each scaffolded file is a valid ShotSpec with an empty (None) scene
    shot = bare_project.load_shot("S001")
    assert shot.id == "S001"
    assert shot.scene is None
    assert shot.characters == []


def test_scaffold_passes_check_on_empty_bible_project(bare_project: Project) -> None:
    """The whole point: empty scene/characters must VALIDATE — scene is
    ``str | None`` so a missing scene is not a referential-integrity error."""
    scaffold_shots(bare_project, 4)
    report = run_check(bare_project)
    assert report.ok, report.errors


def test_scaffold_never_overwrites_existing_shot(bare_project: Project) -> None:
    existing = bare_project.shot_path("S002")
    human_text = "id: S002\ndialogue:\n  text: 我早就知道了。\n"
    existing.write_text(human_text, encoding="utf-8")

    created = scaffold_shots(bare_project, 3)
    assert created == ["S001", "S003"]                          # S002 skipped
    assert existing.read_text(encoding="utf-8") == human_text   # content preserved


def test_scaffold_start_offsets_ids(bare_project: Project) -> None:
    created = scaffold_shots(bare_project, 2, start=5)
    assert created == ["S005", "S006"]
    assert bare_project.shot_path("S005").exists()
    assert bare_project.shot_path("S006").exists()
    assert bare_project.load_index().order == ["S005", "S006"]


def test_scaffold_comments_survive_in_file_text(bare_project: Project) -> None:
    """Written as raw text, not via write_yaml (which drops comments) — the
    human guidance must be readable in the file itself."""
    scaffold_shots(bare_project, 1)
    text = bare_project.shot_path("S001").read_text(encoding="utf-8")
    assert "# bible/scenes.yaml 中的场景 id" in text
    assert "duration: auto" in text
    assert 'speaker: ""' in text


def test_scaffold_appends_without_duplicating_order(bare_project: Project) -> None:
    scaffold_shots(bare_project, 2)             # S001, S002
    created = scaffold_shots(bare_project, 3)   # S001,S002 exist -> only S003 new
    assert created == ["S003"]
    assert bare_project.load_index().order == ["S001", "S002", "S003"]


def test_scaffold_zero_or_negative_is_noop(bare_project: Project) -> None:
    assert scaffold_shots(bare_project, 0) == []
    assert scaffold_shots(bare_project, -3) == []
    assert bare_project.load_index().order == []
