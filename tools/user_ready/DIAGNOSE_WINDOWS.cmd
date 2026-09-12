@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0APP\LAUNCH.py" diagnostics --save
) else (
    python "%~dp0APP\LAUNCH.py" diagnostics --save
)
set "MANJU_RC=%ERRORLEVEL%"
echo This was a read-only diagnostic. No installation was attempted.
pause
exit /b %MANJU_RC%
