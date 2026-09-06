@echo off
rem Starts Murmur, and says hello properly while it does.
rem
rem This is what the desktop and startup shortcuts point at, so the banner
rem shows on every sign-in. It closes itself once Murmur is on its feet --
rem Murmur keeps running in the tray.

title Murmur

if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo.
    echo   Murmur is not set up on this machine yet.
    echo   Run Install.cmd once, then use this again.
    echo.
    pause
    exit /b 1
)

rem The banner is 108 columns wide and uses block-drawing characters, so give
rem the console room and a codepage that can render them.
mode con: cols=114 lines=34 >nul 2>&1
chcp 65001 >nul 2>&1
cls

type "%~dp0assets\banner.txt"

start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0murmur.py"

echo.
echo                      Starting up. Look for the purple speaker in the tray,
echo                      next to the clock. It says hello when it is ready.
echo.

rem Long enough to read, short enough not to be in the way. `timeout` is not
rem available in every context, so ping stands in for it when it is missing.
timeout /t 7 >nul 2>&1 || ping -n 8 127.0.0.1 >nul 2>&1
exit /b 0
