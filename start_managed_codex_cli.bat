@echo off
setlocal

rem Open a quick-test Codex CLI session. It starts in the user's home directory.
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Virtual environment was not found:
    echo %PYTHON_EXE%
    echo Start the monitor after installing its dependencies first.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"
"%PYTHON_EXE%" -c "from codex_monitor.recovery import launch_managed_cli; result = launch_managed_cli(); print(result.detail); raise SystemExit(0 if result.outcome == 'launched' else 1)"
if errorlevel 1 pause

endlocal
