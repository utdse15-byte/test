# update-manju.ps1 — update to a new version, keep the old one for rollback
# (MANJU_WINDOWS_ONLY_LEAN_V3 W2 §4.3: 更新失败必须继续使用旧版本).
#
# A thin, honest wrapper: an update IS an install (staging → self-test →
# atomic pointer switch) — install-manju.ps1 already never disturbs the active
# version on failure, so this script only adds: remembering the previous
# version for -Rollback, and pruning nothing (old versions stay on disk until
# uninstall-manju.ps1 -PruneOldVersions).
#
# Usage:
#   .\update-manju.ps1                        # update from the repo this script lives in
#   .\update-manju.ps1 -Source C:\src\manju
#   .\update-manju.ps1 -Rollback              # re-point to the previous version

[CmdletBinding()]
param(
    [string]$Source = "",
    [switch]$Rollback
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AppDir   = Join-Path (Join-Path $env:LOCALAPPDATA "Manju") "App"
$Pointer  = Join-Path $AppDir "current.txt"
$Previous = Join-Path $AppDir "previous.txt"

function Read-Pointer([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    (Get-Content -Path $path -Raw).Trim()
}

if ($Rollback) {
    $prev = Read-Pointer $Previous
    if (-not $prev) { throw "No previous version recorded — nothing to roll back to." }
    if (-not (Test-Path (Join-Path $AppDir $prev))) {
        throw "Previous version '$prev' is no longer on disk."
    }
    $cur = Read-Pointer $Pointer
    $tmp = "$Pointer.tmp"
    Set-Content -Path $tmp -Value $prev -Encoding ASCII -NoNewline
    Move-Item -Force $tmp $Pointer
    if ($cur) { Set-Content -Path $Previous -Value $cur -Encoding ASCII -NoNewline }
    Write-Host "==> Rolled back to $prev (was: $cur)"
    exit 0
}

$before = Read-Pointer $Pointer
if ($before) { Write-Host "==> Currently active: $before" }

# Delegate to the installer — staging + self-test + atomic switch live there.
$installer = Join-Path $PSScriptRoot "install-manju.ps1"
if ($Source) { & $installer -Source $Source } else { & $installer }

# Only reached on success (ErrorActionPreference=Stop): record the rollback target.
if ($before) {
    Set-Content -Path $Previous -Value $before -Encoding ASCII -NoNewline
    Write-Host "==> Previous version kept for rollback: $before  (update-manju.ps1 -Rollback)"
}
