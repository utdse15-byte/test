@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0LOCAL_WORKBENCH.py"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. No installation was attempted.
        echo Open START_HERE.html directly instead.
        pause
        exit /b 2
    )
    python "%~dp0LOCAL_WORKBENCH.py"
)
set "MANJU_RC=%ERRORLEVEL%"
if not "%MANJU_RC%"=="0" pause
exit /b %MANJU_RC%
