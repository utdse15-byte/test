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
SHORTCUT = SCRIPTS / "manju-shortcut.ps1"
ALL = (INSTALL, UPDATE, UNINSTALL, SHORTCUT)


def _text(p: Path) -> str:
    assert p.exists(), f"missing installer script: {p.name}"
    return p.read_text(encoding="utf-8")


def test_non_ascii_scripts_keep_windows_powershell_utf8_bom():
    """Windows PowerShell 5.1 otherwise decodes UTF-8 source as ANSI/DBCS.

    A multibyte Chinese sequence can consume a quote byte and turn valid source
    into a parser error, so every shipped non-ASCII script needs the BOM.
    """
    for path in ALL:
        raw = path.read_bytes()
        assert any(byte >= 0x80 for byte in raw), path.name
        assert raw.startswith(b"\xef\xbb\xbf"), path.name


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
        "pythonw.exe",
        "manju.gui.windows_app",
    ):
        assert needle in install, f"install-manju.ps1 missing {needle!r}"
    # the pointer switch must be a rename, not an in-place write
    assert re.search(r"Move-Item -Force .*current\.txt", install.replace("$tmpPointer", "current.txt.tmp")) or "Move-Item -Force $tmpPointer $Pointer" in install


def test_update_keeps_previous_version_for_rollback():
    update = _text(UPDATE)
    assert "previous.txt" in update
    assert "-Rollback" in update
    assert "install-manju.ps1" in update, "update delegates to the installer (one flow)"
    assert "Sync-Shortcut" in update
    assert "HadOwnedShortcut" in update
    assert "ShortcutOwnership" in update
    assert "-CreateShortcut" in update and "-NoShortcut" in update


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


def test_click_first_shortcut_is_windowless_branded_and_safely_owned():
    install = _text(INSTALL)
    helper = _text(SHORTCUT)
    uninstall = _text(UNINSTALL)

    # Direct personal installs create a click-first entry unless explicitly
    # asked not to; the old -CreateShortcut spelling remains compatible.
    assert "$WantShortcut = -not $NoShortcut" in install
    assert "-CreateShortcut and -NoShortcut cannot be used together" in install
    assert "Set-ManjuShortcut" in install

    # No console .cmd target: pythonw hosts a recoverable launcher module and
    # the shipped icon is used instead of Python's generic mark.
    assert "pythonw.exe" in helper
    assert '"-m manju.gui.windows_app"' in helper
    assert "installed_icon_path" in helper
    assert "IconLocation" in helper

    # A same-name shortcut is only overwritten/removed when its target and
    # arguments identify this per-user Manju install.
    assert "Get-ManjuShortcutOwnership" in helper
    assert 'return "unknown"' in helper
    assert "Test-ManjuShortcutOwned" in helper
    assert "left untouched" in helper
    assert 'status.product -eq "manju"' in helper
    assert 'status.protocol -eq "manju-gui-app-status.1"' in helper
    assert 'if (-not $processPath) { return $true }' in helper
    assert "valid live session must never be deleted" in helper
    assert "Remove-ManjuShortcut" in uninstall
    assert "Test-ManjuInstalledProcessRunning" in uninstall
    assert "No files were removed" in uninstall
    assert "No application files were removed" in uninstall


def test_gui_assets_are_declared_as_wheel_package_data():
    pyproject = (SCRIPTS.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"manju.gui" = ["assets/*.ico", "assets/*.svg"]' in pyproject
