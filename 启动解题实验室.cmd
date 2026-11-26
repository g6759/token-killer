@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0" || exit /b 1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B -X utf8 lab.py --open %*
    goto done
)
where py >nul 2>nul
if errorlevel 1 goto python
py -3 -B -X utf8 lab.py --open %*
goto done
:python
python -B -X utf8 lab.py --open %*
:done
set "result=%errorlevel%"
popd
if not "%result%"=="0" pause
exit /b %result%
