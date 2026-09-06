@echo off
rem Double-click me. PowerShell refuses to run downloaded .ps1 files by default,
rem so this asks for that one script by name rather than changing any setting.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%errorlevel%
if not "%RC%"=="0" (
    echo.
    echo Setup did not finish. The message above says why.
    pause
)
exit /b %RC%
