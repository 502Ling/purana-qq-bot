@echo off
rem PURANA_ECUST QQ Bot - run hidden in background (no VSCode terminal needed)
rem dev.py supervises: auto-restart on code change / crash, output -> data\dev.log
rem NOTE: keep this file ASCII-only (cmd reads batch files as GBK; UTF-8 breaks parsing)
cd /d "%~dp0"
if not exist "%~dp0data" mkdir "%~dp0data"
rem hidden run: do NOT let dev.py watch for Esc (use stop.cmd to stop it)
set PURANA_NO_ESC=1
"%~dp0venv\Scripts\python.exe" "%~dp0dev.py" >> "%~dp0data\dev.log" 2>&1
