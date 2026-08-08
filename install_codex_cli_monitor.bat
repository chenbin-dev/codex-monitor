@echo off
setlocal

rem Install once. Later, run `codex` normally from any project directory.
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Virtual environment was not found:
    echo %PYTHON_EXE%
    echo Install the project dependencies first, then run this script again.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"
"%PYTHON_EXE%" -c "from pathlib import Path; from codex_monitor.cli_wrapper import install_cli_wrapper; from codex_monitor.config import Settings; result = install_cli_wrapper(Settings.load(Path('config.json'))); print(result.detail); raise SystemExit(0 if result.outcome == 'installed' else 1)"
if errorlevel 1 pause

endlocal
