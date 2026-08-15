# update-manju.ps1 — update to a new version, keep the old one for rollback.
#
# An update is the same staged/self-tested/atomic install as install-manju.ps1.
# This wrapper remembers the prior pointer and preserves the owner's Start-menu
# preference: an existing owned shortcut is refreshed to the new version; an
# absent shortcut stays absent unless -CreateShortcut is explicit.
#
# Usage:
#   .\update-manju.ps1
#   .\update-manju.ps1 -Source C:\src\manju
#   .\update-manju.ps1 -Rollback
#   .\update-manju.ps1 -CreateShortcut       # create/repair the click-first entry
#   .\update-manju.ps1 -NoShortcut           # remove an owned Manju shortcut

[CmdletBinding()]
param(
    [string]$Source = "",
    [switch]$Rollback,
    [switch]$CreateShortcut,
    [switch]$NoShortcut
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "manju-shortcut.ps1")

if ($CreateShortcut -and $NoShortcut) {
    throw "-CreateShortcut and -NoShortcut cannot be used together."
}

$AppRoot  = Join-Path $env:LOCALAPPDATA "Manju"
$AppDir   = Join-Path $AppRoot "App"
$Pointer  = Join-Path $AppDir "current.txt"
$Previous = Join-Path $AppDir "previous.txt"
$ShortcutPath = Get-ManjuShortcutPath
$ShortcutOwnership = if ($ShortcutPath) {
    Get-ManjuShortcutOwnership -ShortcutPath $ShortcutPath -AppRoot $AppRoot
} else { "absent" }
$HadOwnedShortcut = $ShortcutOwnership -eq "owned"
if ($ShortcutOwnership -eq "unknown") {
    if ($NoShortcut) {
        throw "The existing Start-menu shortcut could not be inspected, so -NoShortcut made no changes."
    }
    Write-Warning "The existing Start-menu shortcut could not be inspected; the version update will continue but the shortcut will be left untouched."
}
$WantShortcut = if ($CreateShortcut) { $true } elseif ($NoShortcut) { $false } else { $HadOwnedShortcut }

function Read-Pointer([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    (Get-Content -LiteralPath $path -Raw -Encoding ASCII).Trim()
}

function Sync-Shortcut([string]$versionId) {
    try {
        if ($WantShortcut) {
            $versionDir = Join-Path $AppDir $versionId
            if (Set-ManjuShortcut -AppRoot $AppRoot -VersionDir $versionDir) {
                Write-Host "==> Start-menu shortcut now opens $versionId"
            }
        }
        elseif ($NoShortcut -and (Remove-ManjuShortcut -AppRoot $AppRoot)) {
            Write-Host "==> Removed the owned Manju Start-menu shortcut"
        }
    }
    catch {
        # Pointer activation/rollback is already complete.  A recoverable shell
        # integration problem must not misreport the healthy version switch as
        # an application update failure.
        Write-Warning "Manju version switch succeeded, but the Start-menu shortcut could not be refreshed: $($_.Exception.Message)"
        Write-Warning "Re-run update-manju.ps1 -CreateShortcut to repair the click-first entry."
    }
}

if ($Rollback) {
    $prev = Read-Pointer $Previous
    if (-not $prev) { throw "No previous version recorded — nothing to roll back to." }
    if (-not (Test-Path -LiteralPath (Join-Path $AppDir $prev) -PathType Container)) {
        throw "Previous version '$prev' is no longer on disk."
    }
    $cur = Read-Pointer $Pointer
    $tmp = "$Pointer.tmp"
    Set-Content -LiteralPath $tmp -Value $prev -Encoding ASCII -NoNewline
    Move-Item -Force -LiteralPath $tmp -Destination $Pointer
    if ($cur) { Set-Content -LiteralPath $Previous -Value $cur -Encoding ASCII -NoNewline }
    Sync-Shortcut $prev
    Write-Host "==> Rolled back to $prev (was: $cur)"
    exit 0
}

$before = Read-Pointer $Pointer
if ($before) { Write-Host "==> Currently active: $before" }

# Delegate to the installer — staging + self-test + atomic switch live there.
# Pass an explicit shortcut policy because install's direct-user default is to
# create one, whereas an update must preserve an owner who removed it.
$installer = Join-Path $PSScriptRoot "install-manju.ps1"
$installArgs = @{}
if ($Source) { $installArgs["Source"] = $Source }
if ($WantShortcut) { $installArgs["CreateShortcut"] = $true }
else { $installArgs["NoShortcut"] = $true }
& $installer @installArgs
if ($LASTEXITCODE -ne 0) { throw "install-manju.ps1 failed (exit $LASTEXITCODE)" }

$after = Read-Pointer $Pointer
if (-not $after) { throw "Update completed without an active version pointer." }

# Only reached on success: record the rollback target.  The installer already
# refreshed the shortcut to $after when wanted; keeping this explicit call also
# repairs an old shortcut created by a pre-windowless installer.
if ($before) {
    Set-Content -LiteralPath $Previous -Value $before -Encoding ASCII -NoNewline
    Write-Host "==> Previous version kept for rollback: $before  (update-manju.ps1 -Rollback)"
}
Sync-Shortcut $after
exit 0
