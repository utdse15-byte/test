"""W2 installer scripts — static safety pins (MANJU_WINDOWS_ONLY_LEAN_V3 §4.2-4.4).

PowerShell cannot execute on the authoring host, so these pins freeze the
SAFETY PROPERTIES of scripts/windows/*.ps1 as text: strict mode, no
Invoke-Expression / no unverified download-and-run, no registry writes, no
admin elevation, USER-scope-only and opt-in-only PATH edits, the §4.1 layout,
staging + self-test + atomic pointer switch, and an uninstall that can never
touch projects or ~/.manju. The windows-ci.yml install-smoke job is the
executable half of this contract on a real windows-latest host.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "windows"
INSTALL = SCRIPTS / "install-manju.ps1"
UPDATE = SCRIPTS / "update-manju.ps1"
UNINSTALL = SCRIPTS / "uninstall-manju.ps1"
ALL = (INSTALL, UPDATE, UNINSTALL)


def _text(p: Path) -> str:
    assert p.exists(), f"missing installer script: {p.name}"
    return p.read_text(encoding="utf-8")


def test_scripts_exist_with_strict_mode_and_stop():
    """§4.2 统一要求: Set-StrictMode -Version Latest + ErrorActionPreference Stop."""
    for p in ALL:
        text = _text(p)
        assert "Set-StrictMode -Version Latest" in text, p.name
        assert '$ErrorActionPreference = "Stop"' in text, p.name


def test_no_forbidden_constructs():
    """§4.2 禁止: Invoke-Expression, unverified download-and-run, registry
    writes, elevation. None of these strings may EVER appear."""
    forbidden = (
        "Invoke-Expression",
        "iex ",
        "iex(",
        "DownloadString",
        "Invoke-WebRequest",  # the installer installs from a LOCAL source only
        "Set-ItemProperty",
        "New-ItemProperty",
        "reg.exe",
        "reg add",
        "-Verb RunAs",
        "Start-BitsTransfer",
    )
    for p in ALL:
        text = _text(p)
        for bad in forbidden:
            assert bad not in text, f"{p.name} contains forbidden construct: {bad}"


def test_path_edits_are_user_scope_and_opt_in_only():
    """§4.3 默认不修改 PATH: every SetEnvironmentVariable call uses the "User"
    scope, never "Machine", and only runs behind an explicit switch."""
    for p in ALL:
        text = _text(p)
        assert '"Machine"' not in text, f"{p.name}: machine-scope env edit"
    install = _text(INSTALL)
    assert "if ($AddToPath)" in install
    assert 'SetEnvironmentVariable("Path"' in install
    uninstall = _text(UNINSTALL)
    assert "if ($RemoveUserPathEntry)" in uninstall


def test_install_layout_matches_plan():
    """§4.1 目录: App\\<version>, bin launcher, Logs — all under
    %LOCALAPPDATA%\\Manju; staging + self-test + atomic pointer switch (§4.3)."""
    install = _text(INSTALL)
    for needle in (
        'Join-Path $env:LOCALAPPDATA "Manju"',
        '"App"',
        '"bin"',
        '"Logs"',
        "current.txt",
        ".staging",
        "--version",
        "doctor",
        "manju.cmd",
    ):
        assert needle in install, f"install-manju.ps1 missing {needle!r}"
    # the pointer switch must be a rename, not an in-place write
    assert re.search(r"Move-Item -Force .*current\.txt", install.replace("$tmpPointer", "current.txt.tmp")) or "Move-Item -Force $tmpPointer $Pointer" in install


def test_update_keeps_previous_version_for_rollback():
    update = _text(UPDATE)
    assert "previous.txt" in update
    assert "-Rollback" in update
    assert "install-manju.ps1" in update, "update delegates to the installer (one flow)"


def test_uninstall_never_touches_projects_or_user_config():
    """§4.6 卸载不删除项目: only %LOCALAPPDATA%\\Manju subtrees are removed;
    *.manju projects and ~/.manju are named as untouchable."""
    uninstall = _text(UNINSTALL)
    assert ".manju project" in uninstall or "*.manju" in uninstall
    # every Remove-Item operates on a path joined under $AppRoot (LOCALAPPDATA\Manju)
    for m in re.finditer(r"Remove-Item[^\n]*", uninstall):
        line = m.group(0)
        assert "$p" in line or "$AppRoot" in line, f"unscoped Remove-Item: {line}"
    assert 'Join-Path $env:LOCALAPPDATA "Manju"' in uninstall
