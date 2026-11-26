@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0方格子容纳"
where python >nul 2>nul
if errorlevel 1 goto launcher
python -B game.py --open --port 0
goto done
:launcher
py -3 -B game.py --open --port 0
:done
if errorlevel 1 pause
