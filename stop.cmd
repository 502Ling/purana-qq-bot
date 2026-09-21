@echo off
rem Stop PURANA_ECUST: kill every python process that belongs to this project.
rem (hidden mode has no Esc, so use this.)
rem NOTE: keep this file ASCII-only (cmd reads batch files as GBK).
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*PURANA_ECUST*' }; if ($p) { $p | ForEach-Object { Write-Host ('kill pid ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Host 'not running' }"
echo done.
pause
