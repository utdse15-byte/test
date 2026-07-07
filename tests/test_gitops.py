"""gitops — the read-mostly git surface (§1-② "git IS the patch engine").

Hermetic: every test inits a REAL git repo inside tmp_path and never touches
the developer's repo or global git config — setup commits pass ``-c user.*``
per command, and an autouse fixture points GIT_CONFIG_GLOBAL/SYSTEM at
os.devnull so whatever is (or isn't) configured on the host cannot leak in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from manju.core import gitops


@pytest.fixture(autouse=True)
def _hermetic_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No global/system git config, English messages, no ambient GIT_* scoping."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("LC_ALL", "C.UTF-8")  # keep "nothing to commit" asserts stable
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Setup-only git runner with explicit -c identity (never global config)."""
    return subprocess.run(
        [
            "git", "-C", str(root),
            "-c", "user.name=Setup Bot",
            "-c", "user.email=setup@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    return root


# ---------------------------------------------------------------- unavailable


def test_non_repo_dir_returns_unavailable_values(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    (root / "note.txt").write_text("not a repo", encoding="utf-8")

    assert gitops.is_repo(root) is False
    assert gitops.repo_status(root) is None
    assert gitops.diff_text(root) is None
    assert gitops.file_log(root) == []
    result = gitops.commit_all(root, "should not land")
    assert result["ok"] is False
    assert result["hash"] is None
    assert result["error"]


def test_nonexistent_dir_never_raises(tmp_path: Path) -> None:
    ghost = tmp_path / "does-not-exist"
    assert gitops.is_repo(ghost) is False
    assert gitops.repo_status(ghost) is None
    assert gitops.diff_text(ghost) is None
    assert gitops.file_log(ghost) == []
    assert gitops.commit_all(ghost, "nope")["ok"] is False


# ------------------------------------------------------------ core lifecycle


def test_edit_commit_lifecycle(repo: Path) -> None:
    assert gitops.is_repo(repo) is True

    truth = repo / "shots" / "S001.yaml"
    truth.parent.mkdir()
    truth.write_text("dialogue: 原句\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed project")

    truth.write_text("dialogue: 这不可能。\n", encoding="utf-8")

    status = gitops.repo_status(repo)
    assert status is not None
    assert status["branch"] == "main"
    assert status["dirty"] is True
    assert status["truncated"] is False
    assert status["ahead"] == 0 and status["behind"] == 0
    assert "shots/S001.yaml" in [f["path"] for f in status["files"]]

    diff = gitops.diff_text(repo)
    assert diff is not None
    assert "这不可能。" in diff  # the changed line is visible

    result = gitops.commit_all(repo, "S001: tighten dialogue")
    assert result["ok"] is True
    assert result["error"] is None
    assert result["hash"] and 6 <= len(result["hash"]) <= 16  # short hash

    after = gitops.repo_status(repo)
    assert after is not None
    assert after["dirty"] is False
    assert after["files"] == []
    assert gitops.diff_text(repo) == ""  # clean tree: empty diff, not None

    log = gitops.file_log(repo)
    assert [e["subject"] for e in log] == ["S001: tighten dialogue", "seed project"]
    assert log[0]["author"] == "manju-human"
    assert log[0]["hash"] == result["hash"]
    assert "T" in log[0]["ts"]  # ISO-8601 author date

    # -c overrides set BOTH author and committer, and repo config stays clean.
    ident = _git(repo, "log", "-1", "--format=%an|%ae|%cn|%ce").stdout.strip()
    assert ident == "manju-human|human@manju.local|manju-human|human@manju.local"
    cfg = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "user.name"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert cfg.returncode != 0  # commit_all never wrote repo config


def test_actor_attribution(repo: Path) -> None:
    (repo / "t.yaml").write_text("v: 1\n", encoding="utf-8")
    result = gitops.commit_all(repo, "agent-made edit", actor="agent")
    assert result["ok"] is True
    log = gitops.file_log(repo)
    assert log[0]["author"] == "manju-agent"
    email = _git(repo, "log", "-1", "--format=%ae").stdout.strip()
    assert email == "agent@manju.local"


# ------------------------------------------------------- porcelain -z parsing


def test_cjk_filename_roundtrips_exactly(repo: Path) -> None:
    (repo / "雨夜.yaml").write_text("scene: 雨夜便利店\n", encoding="utf-8")
    status = gitops.repo_status(repo)
    assert status is not None
    assert status["dirty"] is True
    entry = next(f for f in status["files"] if f["path"] == "雨夜.yaml")
    assert entry["status"] == "??"  # untracked, path verbatim (no C-quoting)


def test_rename_with_spaces_does_not_crash_z_parser(repo: Path) -> None:
    old = repo / "old name.yaml"  # space in path: -z parsing must not split it
    old.write_text("a: 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    _git(repo, "mv", "old name.yaml", "新 名前.yaml")

    status = gitops.repo_status(repo)
    assert status is not None
    assert status["dirty"] is True
    paths = [f["path"] for f in status["files"]]
    assert "新 名前.yaml" in paths  # rename entry keeps the NEW path
    assert "old name.yaml" not in paths  # origin field consumed, not a phantom
    entry = next(f for f in status["files"] if f["path"] == "新 名前.yaml")
    assert entry["status"][0] == "R"


def test_file_cap_truncation_flag(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(3):
        (repo / f"f{i}.yaml").write_text(f"n: {i}\n", encoding="utf-8")

    monkeypatch.setattr(gitops, "_FILE_CAP", 2)
    status = gitops.repo_status(repo)
    assert status is not None
    assert status["truncated"] is True
    assert len(status["files"]) == 2
    assert status["dirty"] is True

    monkeypatch.setattr(gitops, "_FILE_CAP", 500)
    status = gitops.repo_status(repo)
    assert status is not None
    assert status["truncated"] is False
    assert len(status["files"]) == 3


# ------------------------------------------------------------ diff & log opts


def test_per_path_diff_and_log_filter(repo: Path) -> None:
    a, b = repo / "a.yaml", repo / "b.yaml"
    a.write_text("k: alpha0\n", encoding="utf-8")
    _git(repo, "add", "a.yaml")
    _git(repo, "commit", "-q", "-m", "touch a")
    b.write_text("k: beta0\n", encoding="utf-8")
    _git(repo, "add", "b.yaml")
    _git(repo, "commit", "-q", "-m", "touch b")

    a.write_text("k: alpha1\n", encoding="utf-8")
    b.write_text("k: beta1\n", encoding="utf-8")

    diff_a = gitops.diff_text(repo, "a.yaml")
    assert diff_a is not None
    assert "alpha1" in diff_a and "beta1" not in diff_a
    diff_all = gitops.diff_text(repo)
    assert "alpha1" in diff_all and "beta1" in diff_all

    assert [e["subject"] for e in gitops.file_log(repo, "a.yaml")] == ["touch a"]
    assert [e["subject"] for e in gitops.file_log(repo)] == ["touch b", "touch a"]
    assert len(gitops.file_log(repo, n=1)) == 1


def test_staged_diff(repo: Path) -> None:
    f = repo / "s.yaml"
    f.write_text("v: 0\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    f.write_text("v: 1\n", encoding="utf-8")
    _git(repo, "add", "s.yaml")

    staged = gitops.diff_text(repo, staged=True)
    assert staged is not None and "v: 1" in staged
    assert gitops.diff_text(repo) == ""  # nothing left unstaged


def test_diff_truncated_at_max_bytes(repo: Path) -> None:
    f = repo / "big.yaml"
    f.write_text("line: 0\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    f.write_text("".join(f"line{i}: 夜\n" for i in range(500)), encoding="utf-8")

    diff = gitops.diff_text(repo, max_bytes=120)
    assert diff is not None
    assert diff.endswith("… (truncated)")
    tail_len = len("\n… (truncated)".encode("utf-8"))
    assert len(diff.encode("utf-8")) <= 120 + tail_len  # multibyte-safe cut


# ------------------------------------------------------------- commit guards


def test_empty_message_rejected(repo: Path) -> None:
    (repo / "x.yaml").write_text("x: 1\n", encoding="utf-8")
    for msg in ("", "   ", " \n\t"):
        assert gitops.commit_all(repo, msg) == {
            "ok": False,
            "hash": None,
            "error": "empty message",
        }
    status = gitops.repo_status(repo)
    assert status is not None and status["dirty"] is True  # nothing landed


def test_nothing_to_commit_reports_git_message_one_lined(repo: Path) -> None:
    (repo / "z.yaml").write_text("z: 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    result = gitops.commit_all(repo, "no-op commit")
    assert result["ok"] is False
    assert result["hash"] is None
    assert "nothing to commit" in result["error"]
    assert "\n" not in result["error"]  # one-lined for GUI display


# ---------------------------------------------------------------- containment


def _init_repo(path, *, commit=True):
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    if commit:
        (path / "outer.txt").write_text("outer secret\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "outer"], cwd=path, check=True)


def test_nested_project_reads_as_no_repo(tmp_path):
    """A project WITHOUT its own .git inside an outer repo must be scoped out:
    pre-fix, every function here leaked the enclosing tree (status listed
    outer files, diff served outer content over the GUI, commit_all staged
    the whole outer repository)."""
    from manju.core import gitops

    outer = tmp_path / "outer"
    _init_repo(outer)
    project = outer / "proj.manju"
    project.mkdir()
    (project / "project.yaml").write_text("name: p\n", encoding="utf-8")

    assert gitops.is_repo(project) is False
    assert gitops.repo_status(project) is None
    assert gitops.diff_text(project) is None
    assert gitops.file_log(project) == []
    result = gitops.commit_all(project, "should not land", actor="human")
    assert result["ok"] is False and "not its own git repository" in result["error"]
    # and the outer repo was NOT touched
    import subprocess

    log = subprocess.run(["git", "log", "--oneline"], cwd=outer,
                         capture_output=True, text=True).stdout
    assert "should not land" not in log


def test_pathspec_escape_rejected(tmp_path):
    from manju.core import gitops

    outer = tmp_path / "outer"
    _init_repo(outer)
    inner = outer / "inner"
    _init_repo(inner, commit=False)
    (inner / "a.txt").write_text("x\n", encoding="utf-8")

    assert gitops.is_repo(inner) is True  # its own repo -> allowed
    assert gitops.diff_text(inner, "../outer.txt") is None
    assert gitops.diff_text(inner, str(outer / "outer.txt")) is None
    assert gitops.file_log(inner, "../outer.txt") == []
    assert gitops.diff_text(inner, "a.txt") is not None  # in-scope still fine
