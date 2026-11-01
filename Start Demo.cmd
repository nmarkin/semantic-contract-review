@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\start_demo.py" %*
    goto finished
)
for %%V in (3.11 3.12 3.13 3.14 3.15 3) do (
    py -%%V -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
    if not errorlevel 1 (
        py -%%V "scripts\start_demo.py" %*
        goto finished
    )
)
python -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
if not errorlevel 1 (
    python "scripts\start_demo.py" %*
    goto finished
)
echo Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH", then open Start Demo.cmd again.
pause
exit /b 1
:finished
set "DEMO_RESULT=%errorlevel%"
if not "%DEMO_RESULT%"=="0" (
    echo Demo could not start. Read the error above. The window will stay open.
    echo Launcher events: .logs\start-demo.log
    pause
)
exit /b %DEMO_RESULT%
