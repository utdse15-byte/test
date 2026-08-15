# install-manju.ps1 — per-user Manju install (MANJU_WINDOWS_ONLY_LEAN_V3 W2 §4.2-4.3).
#
# Layout (§4.1):
#   %LOCALAPPDATA%\Manju\App\<version>\venv   the versioned install (its own venv)
#   %LOCALAPPDATA%\Manju\App\current.txt      pointer to the active version
#   %LOCALAPPDATA%\Manju\bin\manju.cmd        launcher (reads the pointer at run time)
#   %LOCALAPPDATA%\Manju\Logs\                install logs
#
# Hard rules (§4.2): per-user only (no admin), no dynamic evaluation of
# downloaded text, no registry writes, no Defender changes, PATH untouched
# unless -AddToPath (and then ONLY the *user* PATH). Projects and ~/.manju config are NEVER touched. The flow is
# staging → self-test (manju --version + doctor) → atomic pointer switch; a
# failed install never disturbs the currently active version (§4.3).
#
# Usage:
#   .\install-manju.ps1                       # install from the repo this script lives in
#   .\install-manju.ps1 -Source C:\src\manju  # install from a checkout or a wheel
#   .\install-manju.ps1 -AddToPath            # ALSO prepend %LOCALAPPDATA%\Manju\bin to the USER PATH
#   .\install-manju.ps1 -NoShortcut           # CLI-only install; do not create the Start-menu entry

[CmdletBinding()]
param(
    [string]$Source = "",
    [switch]$AddToPath,
    [switch]$CreateShortcut,  # retained for older automation; shortcut is now the default
    [switch]$NoShortcut
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "manju-shortcut.ps1")

if ($CreateShortcut -and $NoShortcut) {
    throw "-CreateShortcut and -NoShortcut cannot be used together."
}
$WantShortcut = -not $NoShortcut

function Write-Step([string]$msg) { Write-Host "==> $msg" }

# ---------------------------------------------------------------- locations
$AppRoot = Join-Path $env:LOCALAPPDATA "Manju"
$AppDir  = Join-Path $AppRoot "App"
$BinDir  = Join-Path $AppRoot "bin"
$LogDir  = Join-Path $AppRoot "Logs"
$Pointer = Join-Path $AppDir "current.txt"
foreach ($d in @($AppDir, $BinDir, $LogDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# ---------------------------------------------------------------- source
if (-not $Source) {
    # scripts/windows/ -> repo root, two levels up
    $Source = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not (Test-Path $Source)) { throw "Source not found: $Source" }
Write-Step "Install source: $Source"

# ---------------------------------------------------------------- python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    throw "Python not found. Install Python 3.11+ (per-user) from python.org, then re-run."
}
$pyExe = $py.Source
$ver = & $pyExe -c "import sys; print('%d.%d' % sys.version_info[:2])"
# UX audit F31: on stock Windows 11, `python` may be the Microsoft Store
# App-Execution-Alias stub — it prints a Store hint and exits nonzero, so
# $ver stays empty and the [version] cast used to die with an opaque
# 'Cannot convert value ""' instead of naming the actual problem.
if ($LASTEXITCODE -ne 0 -or -not $ver) {
    throw "python probe failed (Microsoft Store alias stub?) — install real Python 3.11+ (per-user) from python.org, then re-run."
}
if ([version]$ver -lt [version]"3.11") {
    throw "Python $ver found at $pyExe — Manju requires >= 3.11."
}
Write-Step "Python $ver at $pyExe"

# ---------------------------------------------------------------- version + staging
# Version = the package's own version string, read WITHOUT importing the package
# (a broken source tree must fail loudly later in pip, not here).
$srcVersion = & $pyExe -c @"
import pathlib, re, sys
root = pathlib.Path(r'''$Source''')
init = root / 'src' / 'manju' / '__init__.py'
if init.exists():
    m = re.search(r'__version__\s*=\s*[\'\"]([^\'\"]+)', init.read_text(encoding='utf-8'))
    print(m.group(1) if m else 'unknown')
else:
    print('wheel')
"@
$stamp = Get-Date -Format "yyyyMMddTHHmmss"
$versionId = "$srcVersion-$stamp"
$targetDir = Join-Path $AppDir $versionId
$stagingDir = "$targetDir.staging"
Write-Step "Installing version $versionId (staging first — the active version stays untouched)"

if (Test-Path $stagingDir) { Remove-Item -Recurse -Force $stagingDir }
New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
$log = Join-Path $LogDir "install-$stamp.log"

try {
    # ---------------------------------------------------------- venv + pip
    Write-Step "Creating venv"
    & $pyExe -m venv (Join-Path $stagingDir "venv") 2>&1 | Tee-Object -FilePath $log -Append | Out-Null
    # UX audit F31: a swallowed venv/pip failure used to surface much later as
    # 'manju entry point missing' — pointing away from the real cause. Name
    # the failing step at the step.
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed (exit $LASTEXITCODE) — see log: $log" }
    $venvPy = Join-Path $stagingDir "venv\Scripts\python.exe"

    Write-Step "Installing Manju (pip, constrained when constraints.txt is present)"
    $constraints = Join-Path $Source "constraints.txt"
    $pipArgs = @("-m", "pip", "install", "--no-input")
    if (Test-Path $constraints) { $pipArgs += @("-c", $constraints) }
    $pipArgs += @("$Source")
    & $venvPy @pipArgs 2>&1 | Tee-Object -FilePath $log -Append | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE) — see log: $log" }

    # ---------------------------------------------------------- self-test (§4.3)
    # STRICT: a failed self-test must abort BEFORE the pointer switch (the
    # first real windows-latest run proved the gap — `manju --version` failed
    # and the switch still happened). PowerShell does not throw on native
    # exit codes, so each probe checks $LASTEXITCODE explicitly.
    Write-Step "Self-test: CLI + desktop launcher + doctor"
    $manjuExe = Join-Path $stagingDir "venv\Scripts\manju.exe"
    $pythonwExe = Join-Path $stagingDir "venv\Scripts\pythonw.exe"
    if (-not (Test-Path $manjuExe)) { throw "manju entry point missing after install" }
    if (-not (Test-Path $pythonwExe)) { throw "windowless pythonw launcher missing after install" }
    & $manjuExe --version 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "self-test failed: manju --version exited $LASTEXITCODE" }
    # This probe imports the installed CLI and verifies the packaged icon and
    # exact app-mode/free-port contract.  It never binds a socket, opens a
    # browser, reads credentials or contacts a Provider.
    & $venvPy -m manju.gui.windows_app --self-test 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        throw "self-test failed: windowless desktop entry is incomplete (exit $LASTEXITCODE)"
    }
    # doctor exit code 1 is env-dependent (ffmpeg may be absent — a doctor
    # FINDING for the user, not an install failure); anything else (crash,
    # import error) aborts before the switch.
    & $manjuExe doctor 2>&1 | Tee-Object -FilePath $log -Append | Out-Null
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
        throw "self-test failed: manju doctor crashed (exit $LASTEXITCODE)"
    }


    # ---------------------------------------------------------- atomic switch
    Write-Step "Activating $versionId"
    Move-Item $stagingDir $targetDir
    $tmpPointer = "$Pointer.tmp"
    Set-Content -Path $tmpPointer -Value $versionId -Encoding ASCII -NoNewline
    Move-Item -Force $tmpPointer $Pointer
}
catch {
    Write-Host "Install FAILED — the previously active version (if any) is untouched." -ForegroundColor Red
    Write-Host "Log: $log"
    if (Test-Path $stagingDir) { Remove-Item -Recurse -Force $stagingDir }
    throw
}

# ---------------------------------------------------------------- launcher
# The launcher resolves the pointer AT RUN TIME, so switching/rolling back a
# version never rewrites the launcher (and never needs PATH churn).
$launcher = @"
@echo off
setlocal
rem UTF-8 everywhere, matching the repo invariant: without this, redirected
rem output (manju doctor ^> log.txt) dies on the ANSI codepage (UX audit F30).
set "PYTHONUTF8=1"
set "MANJU_APP=%LOCALAPPDATA%\Manju\App"
if not exist "%MANJU_APP%\current.txt" (
  echo manju: no installed version found. Run install-manju.ps1 first. 1>&2
  exit /b 1
)
set /p MANJU_VER=<"%MANJU_APP%\current.txt"
"%MANJU_APP%\%MANJU_VER%\venv\Scripts\manju.exe" %*
endlocal
"@
Set-Content -Path (Join-Path $BinDir "manju.cmd") -Value $launcher -Encoding ASCII

# ------------------------------------------------------- click-first shortcut
# A normal personal-app install should be launchable after one double-click, so
# the per-user Start-menu entry is now the default.  -NoShortcut preserves a
# deliberate CLI-only install.  The shortcut targets the ACTIVE version's
# pythonw.exe (no console flash) and the recoverable Windows launcher module;
# update/rollback refresh it after switching versions.  A same-name shortcut
# not owned by %LOCALAPPDATA%\Manju is never overwritten.
if ($WantShortcut) {
    try {
        if (Set-ManjuShortcut -AppRoot $AppRoot -VersionDir $targetDir) {
            Write-Step "Start-menu shortcut: $(Get-ManjuShortcutPath)"
            Write-Step "Double-click opens the windowless Manju workspace (app mode, free port)."
        }
    }
    catch {
        # The installed version is already active and self-tested.  A shortcut
        # failure is recoverable and must not roll back a healthy application.
        Write-Warning "Manju is installed, but the Start-menu shortcut was not created: $($_.Exception.Message)"
        Write-Warning "Run this installer again, or start: $BinDir\manju.cmd gui --app --port 0"
    }
} else {
    Write-Step "Start-menu shortcut skipped (-NoShortcut)."
}

# ---------------------------------------------------------------- PATH (opt-in ONLY)
if ($AddToPath) {
    # USER PATH only, never the machine PATH; idempotent.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($null -eq $userPath) { $userPath = "" }
    if ($userPath -notlike "*$BinDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$BinDir;$userPath", "User")
        Write-Step "Added $BinDir to the USER Path (new terminals will see it)"
    }
} else {
    Write-Step "PATH untouched (default). Launcher: $BinDir\manju.cmd  — or re-run with -AddToPath"
}

Write-Step "Done. Active version: $versionId"
Write-Step "Log: $log"
# pwsh propagates the LAST native exit code ($LASTEXITCODE) as the script's —
# doctor legitimately exits 1 on env findings (run #4: a fully successful
# install "failed" its CI step this way). Success is explicit.
exit 0
