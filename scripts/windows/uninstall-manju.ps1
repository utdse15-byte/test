# uninstall-manju.ps1 — remove the Manju APP, never the user's work
# (MANJU_WINDOWS_ONLY_LEAN_V3 W2 §4.6: 卸载不删除项目和用户配置).
#
# Removes:   %LOCALAPPDATA%\Manju\App, %LOCALAPPDATA%\Manju\bin,
#            %LOCALAPPDATA%\Manju\Cache, %LOCALAPPDATA%\Manju\Logs (with -Logs)
# NEVER touches: any *.manju project directory, ~/.manju (providers, routing,
#            recents, skills, library, GUI state), or anything outside
#            %LOCALAPPDATA%\Manju. No admin, no registry, no PATH edits beyond
#            removing the entry install-manju.ps1 -AddToPath added (opt-in).

[CmdletBinding()]
param(
    [switch]$Logs,
    [switch]$RemoveUserPathEntry
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AppRoot = Join-Path $env:LOCALAPPDATA "Manju"
$BinDir  = Join-Path $AppRoot "bin"

foreach ($sub in @("App", "bin", "Cache")) {
    $p = Join-Path $AppRoot $sub
    if (Test-Path $p) {
        Write-Host "==> Removing $p"
        Remove-Item -Recurse -Force $p
    }
}
if ($Logs) {
    $p = Join-Path $AppRoot "Logs"
    if (Test-Path $p) {
        Write-Host "==> Removing $p"
        Remove-Item -Recurse -Force $p
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
