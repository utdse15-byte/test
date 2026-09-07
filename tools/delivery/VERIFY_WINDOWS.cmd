@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0LAUNCH.py" verify
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python 3.11 or newer was not found.
        echo The HTML workbench requires NO Python. Open OPEN_MODEL_WORKBENCH.html instead.
        pause
        exit /b 2
    )
    python "%~dp0LAUNCH.py" verify
)
set "MANJU_RC=%ERRORLEVEL%"
if not "%MANJU_RC%"=="0" (
    echo The operation did not complete. Existing projects were not overwritten.
    pause
)
exit /b %MANJU_RC%
