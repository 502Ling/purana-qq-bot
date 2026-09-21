@echo off
rem PURANA_ECUST - is it running? (double-click me)
rem NOTE: keep this file ASCII-only (cmd reads batch files as GBK).
rem NOTE: do not use ( ) blocks or echo ( ) here - cmd parses them as sub-blocks.
cd /d "%~dp0"
title PURANA_ECUST status

echo === PURANA_ECUST status ===
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*PURANA_ECUST*' }; if ($p) { Write-Host ('running: ' + @($p).Count + ' python process(es)') } else { Write-Host 'NOT running - no python process for this project' }"

echo.
echo --- data\dev.log tail ---
powershell -NoProfile -Command "if (Test-Path '%~dp0data\dev.log') { Get-Content -LiteralPath '%~dp0data\dev.log' -Tail 5 -Encoding UTF8 } else { Write-Host '(no dev.log yet)' }"

echo.
echo --- data\logs\nico.log tail ---
powershell -NoProfile -Command "if (Test-Path '%~dp0data\logs\nico.log') { Get-Content -LiteralPath '%~dp0data\logs\nico.log' -Tail 8 -Encoding UTF8 } else { Write-Host '(no nico.log yet)' }"

echo.
pause
