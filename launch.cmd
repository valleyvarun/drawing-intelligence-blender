@echo off
setlocal
rem Starts Drawing Intelligence. A source checkout prepares its private engine on first use.
set "APP=%~dp0Drawing Intelligence.exe"
if exist "%APP%" if exist "%~dp0engine\Drawing Intelligence.exe" goto start
if not exist "%~dp0scripts\setup-engine.ps1" (
    echo Drawing Intelligence is incomplete. Run its setup again to repair it.
    pause
    exit /b 1
)
echo Preparing Drawing Intelligence for first use. This downloads and verifies its engine once.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-engine.ps1"
if errorlevel 1 (
    pause
    exit /b 1
)
:start
start "" "%APP%" %*
exit /b 0
