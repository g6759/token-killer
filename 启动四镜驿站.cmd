@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0四境驿站"
where python >nul 2>nul
if errorlevel 1 goto launcher
python -B solo_server.py --open --port 0
goto done
:launcher
py -3 -B solo_server.py --open --port 0
:done
if errorlevel 1 pause
