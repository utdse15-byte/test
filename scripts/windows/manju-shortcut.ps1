# Shared, local-only Start-menu shortcut helpers for Manju's per-user installer.
# Dot-sourced by install/update/uninstall; never downloads or evaluates remote text.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ManjuShortcutPath {
    if (-not $env:APPDATA) { return $null }
    $dir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    return (Join-Path $dir "Manju 工作台.lnk")
}

function Get-ManjuShortcutOwnership {
    param(
        [Parameter(Mandatory=$true)][string]$ShortcutPath,
        [Parameter(Mandatory=$true)][string]$AppRoot
    )
    if (-not $ShortcutPath) { return "absent" }
    if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) { return "absent" }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($ShortcutPath)
        $target = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($link.TargetPath))
        $root = [System.IO.Path]::GetFullPath($AppRoot).TrimEnd("\") + "\"
        if (-not $target.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            return "foreign"
        }
        $args = [string]$link.Arguments
        $newLauncher = $args -match "(^|\s)-m\s+manju\.gui\.windows_app($|\s)"
        $legacyLauncher = (
            ([System.IO.Path]::GetFileName($target)).Equals("manju.cmd", [System.StringComparison]::OrdinalIgnoreCase) -and
            $args.Trim().StartsWith("gui", [System.StringComparison]::OrdinalIgnoreCase)
        )
        if ($newLauncher -or $legacyLauncher) { return "owned" }
        return "foreign"
    }
    catch {
        return "unknown"
    }
}

function Test-ManjuShortcutOwned {
    param(
        [Parameter(Mandatory=$true)][string]$ShortcutPath,
        [Parameter(Mandatory=$true)][string]$AppRoot
    )
    return (Get-ManjuShortcutOwnership -ShortcutPath $ShortcutPath -AppRoot $AppRoot) -eq "owned"
}

function Set-ManjuShortcut {
    param(
        [Parameter(Mandatory=$true)][string]$AppRoot,
        [Parameter(Mandatory=$true)][string]$VersionDir
    )
    $shortcutPath = Get-ManjuShortcutPath
    if (-not $shortcutPath) {
        throw "APPDATA is unavailable; cannot create a per-user Start-menu shortcut."
    }
    $ownership = Get-ManjuShortcutOwnership -ShortcutPath $shortcutPath -AppRoot $AppRoot
    if ($ownership -eq "foreign") {
        Write-Warning "A different shortcut already exists at '$shortcutPath'; Manju left it untouched."
        return $false
    }
    if ($ownership -eq "unknown") {
        throw "The existing shortcut could not be inspected; Manju left it untouched."
    }

    $pythonw = Join-Path $VersionDir "venv\Scripts\pythonw.exe"
    $python = Join-Path $VersionDir "venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        throw "Windowless launcher missing: $pythonw"
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Installed Python missing: $python"
    }

    $icon = (& $python -c "from manju.gui.windows_app import installed_icon_path; print(installed_icon_path())").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $icon -or -not (Test-Path -LiteralPath $icon -PathType Leaf)) {
        throw "Installed Manju icon is unavailable; shortcut was not changed."
    }

    $shortcutDir = Split-Path -Parent $shortcutPath
    New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null
    # Build beside the final name, then atomically replace it.  A COM or disk
    # failure leaves the last known-good shortcut intact instead of truncating
    # the user's only click-first entry.
    $tempShortcut = Join-Path $shortcutDir (".Manju-Workspace-{0}-{1}.lnk" -f $PID, [guid]::NewGuid().ToString("N"))
    try {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($tempShortcut)
        $link.TargetPath = $pythonw
        $link.Arguments = "-m manju.gui.windows_app"
        $workingDirectory = if ($env:USERPROFILE) { $env:USERPROFILE } else { $AppRoot }
        $link.WorkingDirectory = $workingDirectory
        $link.Description = "Manju 本地电影工作台"
        $link.IconLocation = "$icon,0"
        $link.WindowStyle = 1
        $link.Save()
        if (-not (Test-Path -LiteralPath $tempShortcut -PathType Leaf)) {
            throw "Windows did not create the staged shortcut."
        }
        $probe = $shell.CreateShortcut($tempShortcut)
        $probeTarget = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($probe.TargetPath))
        $wantedTarget = [System.IO.Path]::GetFullPath($pythonw)
        $probeIcon = ([string]$probe.IconLocation -split ",", 2)[0]
        if (-not $probeTarget.Equals($wantedTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Staged shortcut target changed unexpectedly: $probeTarget"
        }
        if ([string]$probe.Arguments -ne "-m manju.gui.windows_app") {
            throw "Staged shortcut arguments changed unexpectedly: $($probe.Arguments)"
        }
        if (-not ([System.IO.Path]::GetFullPath($probeIcon)).Equals(
            [System.IO.Path]::GetFullPath($icon),
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Staged shortcut icon changed unexpectedly: $probeIcon"
        }
        if ((Test-Path -LiteralPath $shortcutPath) -and
            -not (Test-ManjuShortcutOwned -ShortcutPath $shortcutPath -AppRoot $AppRoot)) {
            throw "Shortcut ownership changed while updating; the existing entry was left untouched."
        }
        Move-Item -LiteralPath $tempShortcut -Destination $shortcutPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $tempShortcut) {
            Remove-Item -LiteralPath $tempShortcut -Force -ErrorAction SilentlyContinue
        }
    }
    return $true
}

function Remove-ManjuShortcut {
    param([Parameter(Mandatory=$true)][string]$AppRoot)
    $shortcutPath = Get-ManjuShortcutPath
    if (-not $shortcutPath) { return $false }
    $ownership = Get-ManjuShortcutOwnership -ShortcutPath $shortcutPath -AppRoot $AppRoot
    if ($ownership -eq "absent") { return $false }
    if ($ownership -eq "foreign") {
        Write-Warning "Shortcut '$shortcutPath' is not owned by this Manju install; left untouched."
        return $false
    }
    if ($ownership -eq "unknown") {
        throw "Shortcut '$shortcutPath' could not be inspected; no application files were removed."
    }
    Remove-Item -LiteralPath $shortcutPath -Force
    return $true
}

function Test-ManjuAppSessionRunning {
    param([Parameter(Mandatory=$true)][string]$AppRoot)
    $sessionPath = Join-Path $AppRoot "App\workspace-session.json"
    if (-not (Test-Path -LiteralPath $sessionPath -PathType Leaf)) { return $false }
    try {
        $session = Get-Content -LiteralPath $sessionPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$session.format -ne "manju-windows-app-session.1") { return $false }
        $pidValue = [int]$session.pid
        if ($pidValue -le 0) { return $false }

        # Prefer the same authoritative loopback lifecycle that the launcher
        # uses.  A tampered external URL is never requested.
        try {
            $uri = [System.Uri]([string]$session.url)
            if ($uri.Scheme -eq "http" -and $uri.IsLoopback -and $uri.Port -gt 0) {
                $statusUri = ([string]$session.url).TrimEnd("/") + "/api/app/status"
                $status = Invoke-RestMethod -Uri $statusUri -Method Get -TimeoutSec 1 -MaximumRedirection 0 -ErrorAction Stop
                if (
                    [string]$status.product -eq "manju" -and
                    [string]$status.protocol -eq "manju-gui-app-status.1" -and
                    [int]$status.pid -eq $pidValue -and
                    [string]$status.shutdown_state -in @("open", "closing", "stuck")
                ) {
                    return $true
                }
            }
        }
        catch { }

        # A hung server may not answer HTTP. Refuse only when the recorded PID
        # still belongs to this per-user installation; a stale PID reused by an
        # unrelated Python process must not block uninstall forever.
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if (-not $process) { return $false }
        try {
            $processPath = [string]$process.Path
            if (-not $processPath) { return $true }  # live but undecidable => fail closed
            $root = [System.IO.Path]::GetFullPath($AppRoot).TrimEnd("\") + "\"
            $full = [System.IO.Path]::GetFullPath($processPath)
            return $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)
        }
        catch {
            return $true  # a valid live session must never be deleted on an inspection error
        }
    }
    catch {
        return $false
    }
}

function Test-ManjuInstalledProcessRunning {
    param([Parameter(Mandatory=$true)][string]$AppRoot)
    if (Test-ManjuAppSessionRunning -AppRoot $AppRoot) { return $true }
    $root = [System.IO.Path]::GetFullPath($AppRoot).TrimEnd("\") + "\"
    foreach ($name in @("python", "pythonw", "manju")) {
        foreach ($process in @(Get-Process -Name $name -ErrorAction SilentlyContinue)) {
            try {
                $processPath = [string]$process.Path
                if ($processPath) {
                    $full = [System.IO.Path]::GetFullPath($processPath)
                    if ($full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
                        return $true
                    }
                }
            }
            catch { }
        }
    }
    return $false
}
