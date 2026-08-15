# uninstall-manju.ps1 — remove the Manju application, never the user's work.
#
# Removes:   %LOCALAPPDATA%\Manju\App, bin, Cache, the owned Start-menu
#            shortcut, and Logs only with -Logs.
# NEVER touches: any *.manju project, ~/.manju (providers/routing/recents/
#            skills/library/GUI state), or a same-name shortcut not owned by
#            this per-user install.  A running process from the versioned
#            install is never killed; uninstall refuses so project writes can
#            finish or be cancelled by the existing in-app safe-exit flow.

[CmdletBinding()]
param(
    [switch]$Logs,
    [switch]$RemoveUserPathEntry
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "manju-shortcut.ps1")

$AppRoot = Join-Path $env:LOCALAPPDATA "Manju"
$BinDir  = Join-Path $AppRoot "bin"

if (Test-ManjuInstalledProcessRunning -AppRoot $AppRoot) {
    throw "An installed Manju process is still running. Use the in-app ‘退出’ action (or stop the CLI command), then run uninstall again. No files were removed."
}

try {
    if (Remove-ManjuShortcut -AppRoot $AppRoot) {
        Write-Host "==> Removed the owned Manju Start-menu shortcut"
    }
}
catch {
    # This runs before any application directory is removed.  Leaving an owned
    # shortcut that points into a deleted version is worse than asking the user
    # to retry cleanup, so fail without touching the installed application.
    throw "Could not safely remove the Manju Start-menu shortcut. No application files were removed. $($_.Exception.Message)"
}

foreach ($sub in @("App", "bin", "Cache")) {
    $p = Join-Path $AppRoot $sub
    if (Test-Path -LiteralPath $p) {
        Write-Host "==> Removing $p"
        Remove-Item -LiteralPath $p -Recurse -Force
    }
}
if ($Logs) {
    $p = Join-Path $AppRoot "Logs"
    if (Test-Path -LiteralPath $p) {
        Write-Host "==> Removing $p"
        Remove-Item -LiteralPath $p -Recurse -Force
    }
}

if ($RemoveUserPathEntry) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath) {
        $parts = $userPath -split ";" | Where-Object { $_ -and ($_ -ne $BinDir) }
        [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
        Write-Host "==> Removed $BinDir from the USER Path"
    }
}

Write-Host "==> Done. Projects (*.manju) and ~/.manju config were NOT touched."
if ($Logs) { Write-Host "==> Logs were removed." }
else { Write-Host "==> Logs were kept for troubleshooting." }
exit 0
