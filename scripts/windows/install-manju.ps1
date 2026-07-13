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

[CmdletBinding()]
param(
    [string]$Source = "",
    [switch]$AddToPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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
    $venvPy = Join-Path $stagingDir "venv\Scripts\python.exe"

    Write-Step "Installing Manju (pip, constrained when constraints.txt is present)"
    $constraints = Join-Path $Source "constraints.txt"
    $pipArgs = @("-m", "pip", "install", "--no-input")
    if (Test-Path $constraints) { $pipArgs += @("-c", $constraints) }
    $pipArgs += @("$Source")
    & $venvPy @pipArgs 2>&1 | Tee-Object -FilePath $log -Append | Out-Null

    # ---------------------------------------------------------- self-test (§4.3)
    # STRICT: a failed self-test must abort BEFORE the pointer switch (the
    # first real windows-latest run proved the gap — `manju --version` failed
    # and the switch still happened). PowerShell does not throw on native
    # exit codes, so each probe checks $LASTEXITCODE explicitly.
    Write-Step "Self-test: manju --version + doctor"
    $manjuExe = Join-Path $stagingDir "venv\Scripts\manju.exe"
    if (-not (Test-Path $manjuExe)) { throw "manju entry point missing after install" }
    & $manjuExe --version 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "self-test failed: manju --version exited $LASTEXITCODE" }
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
    if (Test-Path $stagingDir) { Remove-Item -Recurse -Force $stagingDir }
    throw
}

# ---------------------------------------------------------------- launcher
# The launcher resolves the pointer AT RUN TIME, so switching/rolling back a
# version never rewrites the launcher (and never needs PATH churn).
$launcher = @"
@echo off
setlocal
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
