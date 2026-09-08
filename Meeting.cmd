@echo off
rem Opens the meeting window. Double-click this; there is nothing to type.
rem
rem Unlike cufflink.cmd this leaves its console open, because that is where the
rem devices it chose, the model load, and anything that went wrong are
rem printed -- and while this is still being tried out, that is worth seeing.
rem Closing this window stops the meeting.
rem
rem Arguments pass straight through, so a shortcut to
rem     Meeting.cmd --model tiny --refine-with small --language nl
rem works the way the command line does.

title Meeting

if not exist "%~dp0venv\Scripts\python.exe" (
    echo.
    echo   cufflink is not set up on this machine yet.
    echo   Run Install.cmd once, then use this again.
    echo.
    pause
    exit /b 1
)

echo.
echo   Opening the meeting window.
echo.
echo   It starts NOT recording. Click the dot, or press Ctrl+Alt+L, when you
echo   want it listening -- it hears your microphone and everything your
echo   speakers play, so it waits to be asked.
echo.

"%~dp0venv\Scripts\python.exe" -u "%~dp0meeting.py" %*

echo.
echo   Meeting window closed.
pause
