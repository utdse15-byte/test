"""The install-smoke contract, EXECUTED — not grepped.

`tests/test_windows_install.py` checks these scripts as TEXT: that every
`Remove-Item` line mentions `$p` or `$AppRoot`, that "previous.txt" appears,
and so on. That catches a careless edit, but it cannot catch a wrong value —
a `$p` computed one directory too high still passes every grep while deleting
the user's work.

The parts of the contract that do not touch a venv are portable PowerShell
(`Join-Path`, `Test-Path`, `Remove-Item`, pointer files), so they RUN under
pwsh on Linux against a temporary `$env:LOCALAPPDATA`. That covers the two
properties worth the most:

* uninstall removes the app and NEVER the user's work (§4.6 卸载不删除项目);
* rollback re-points to the previous version, and is itself reversible.

Not covered here, and still Windows-only: everything downstream of
`venv\\Scripts\\python.exe` — the staging install, the self-test, and the
atomic pointer switch that follows them. Those need a Windows venv layout.

Skips cleanly without pwsh, the same precedent as the ffmpeg/chromium gates.
GitHub's ubuntu and windows runners both ship PowerShell, so this executes on
both CI lanes rather than only on the hard gate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "windows"
PWSH = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(
    PWSH is None, reason="no PowerShell on this host (pwsh/powershell)")


def _run(script: str, local_appdata: Path, *args: str,
         expect_ok: bool = True) -> subprocess.CompletedProcess:
    """Run one of the scripts with LOCALAPPDATA pointed at a temp tree."""
    env = {"LOCALAPPDATA": str(local_appdata), "HOME": str(local_appdata.parent),
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    # Windows PowerShell cannot initialize its managed host without the OS
    # root. Preserve only that machine fact; provider credentials and the rest
    # of the parent environment remain excluded from this isolation test.
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(SCRIPTS / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    if expect_ok:
        assert proc.returncode == 0, f"{script} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


@pytest.fixture
def installed(tmp_path: Path):
    """An installed-looking app tree PLUS the user's work beside it."""
    lad = tmp_path / "LocalAppData"
    for sub in ("App", "bin", "Cache", "Logs"):
        (lad / "Manju" / sub).mkdir(parents=True)
    (lad / "Manju" / "App" / "marker.txt").write_text("app", encoding="utf-8")

    # A project with a space AND CJK in the path — the gate uses the same shape.
    project = tmp_path / "作品 集" / "雨夜 便利店.manju"
    project.mkdir(parents=True)
    (project / "project.yaml").write_text("name: 雨夜便利店\n", encoding="utf-8")
    dotmanju = tmp_path / ".manju"
    dotmanju.mkdir()
    (dotmanju / "providers.yaml").write_text("x: 1\n", encoding="utf-8")
    return lad, project, dotmanju


# ------------------------------------------------------------------ uninstall


def test_uninstall_removes_the_app(installed) -> None:
    lad, _, _ = installed
    _run("uninstall-manju.ps1", lad)
    for sub in ("App", "bin", "Cache"):
        assert not (lad / "Manju" / sub).exists(), sub


def test_uninstall_never_touches_the_users_work(installed) -> None:
    """The one that matters: this is somebody's film."""
    lad, project, dotmanju = installed
    _run("uninstall-manju.ps1", lad)
    assert (project / "project.yaml").read_text(encoding="utf-8").startswith("name:")
    assert (dotmanju / "providers.yaml").exists()


def test_uninstall_keeps_logs_unless_asked(installed) -> None:
    lad, _, _ = installed
    _run("uninstall-manju.ps1", lad)
    assert (lad / "Manju" / "Logs").exists()


def test_uninstall_logs_removes_them(installed) -> None:
    lad, _, _ = installed
    _run("uninstall-manju.ps1", lad, "-Logs")
    assert not (lad / "Manju" / "Logs").exists()


def test_uninstall_is_idempotent(installed) -> None:
    """Running it twice must not become an error the user has to interpret."""
    lad, project, _ = installed
    _run("uninstall-manju.ps1", lad)
    _run("uninstall-manju.ps1", lad)
    assert (project / "project.yaml").exists()


# ------------------------------------------------------------------- rollback


def _versions(lad: Path, current: str, previous: str | None) -> Path:
    app = lad / "Manju" / "App"
    for v in filter(None, (current, previous)):
        (app / v).mkdir(parents=True, exist_ok=True)
    (app / "current.txt").write_text(current, encoding="ascii")
    if previous:
        (app / "previous.txt").write_text(previous, encoding="ascii")
    return app


def test_rollback_repoints_to_the_previous_version(installed) -> None:
    lad, _, _ = installed
    app = _versions(lad, "0.1.0-B", "0.1.0-A")
    _run("update-manju.ps1", lad, "-Rollback")
    assert app.joinpath("current.txt").read_text(encoding="ascii").strip() == "0.1.0-A"


def test_rollback_is_itself_reversible(installed) -> None:
    """It swaps the pointers, so a rollback can be rolled back — otherwise the
    user is stuck one version behind with no way forward but a reinstall."""
    lad, _, _ = installed
    app = _versions(lad, "0.1.0-B", "0.1.0-A")
    _run("update-manju.ps1", lad, "-Rollback")
    assert app.joinpath("previous.txt").read_text(encoding="ascii").strip() == "0.1.0-B"
    _run("update-manju.ps1", lad, "-Rollback")
    assert app.joinpath("current.txt").read_text(encoding="ascii").strip() == "0.1.0-B"


def test_rollback_with_nothing_to_roll_back_to_fails_loudly(installed) -> None:
    """Silently doing nothing would leave the user believing they rolled back."""
    lad, _, _ = installed
    _versions(lad, "0.1.0-B", None)
    proc = _run("update-manju.ps1", lad, "-Rollback", expect_ok=False)
    assert proc.returncode != 0
    assert "No previous version" in (proc.stdout + proc.stderr)


def test_rollback_to_a_deleted_version_fails_loudly(installed) -> None:
    """Pointing current.txt at a version that is gone would break the launcher
    for good — worse than refusing."""
    lad, _, _ = installed
    app = _versions(lad, "0.1.0-B", "0.1.0-A")
    shutil.rmtree(app / "0.1.0-A")
    proc = _run("update-manju.ps1", lad, "-Rollback", expect_ok=False)
    assert proc.returncode != 0
    assert "no longer on disk" in (proc.stdout + proc.stderr)
    assert app.joinpath("current.txt").read_text(encoding="ascii").strip() == "0.1.0-B"
