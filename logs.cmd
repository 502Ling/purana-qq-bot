@echo off
rem PURANA_ECUST - live log viewer (double-click; Ctrl+C to quit)
rem   logs.cmd       -> bot log     data\logs\purana.log
rem   logs.cmd dev   -> supervisor  data\dev.log
rem NOTE: keep this file ASCII-only (cmd reads batch files as GBK).
setlocal
cd /d "%~dp0"
title PURANA_ECUST logs

set "FILE=%~dp0data\logs\purana.log"
if /i "%~1"=="dev" set "FILE=%~dp0data\dev.log"

if not exist "%FILE%" (
    echo log file not found yet: %FILE%
    echo    it shows up after the bot has started once
    pause
    exit /b 1
)

echo == live tail: %FILE% ==
echo == Ctrl+C to quit ==
echo.
powershell -NoProfile -Command "Get-Content -LiteralPath '%FILE%' -Wait -Tail 40 -Encoding UTF8"
pause
