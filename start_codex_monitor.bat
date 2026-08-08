@echo off
setlocal

rem Always run from this script's directory.
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
echo.
echo [STARTING] Launching Codex Monitor...
echo [INFO] Find Codex Monitor in the Windows system tray after startup.
echo [INFO] Keep this window open or minimize it. Closing it stops monitoring.
echo.
"%PYTHON_EXE%" -m codex_monitor
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [FAILED] Monitor stopped. Error code: %EXIT_CODE%
    pause
)

endlocal & exit /b %EXIT_CODE%
