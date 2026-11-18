@echo off
setlocal

rem Run from the directory containing this script.
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
if errorlevel 1 (
    echo [ERROR] Cannot enter the project directory: %SCRIPT_DIR%
    pause
    exit /b 1
)

set "POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL%" (
    echo [ERROR] Windows PowerShell not found. Please run start.ps1 manually.
    pause
    exit /b 1
)

rem Keep dependency checks, the frontend build and server startup in one source of truth.
"%POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\start.ps1"
set "exitCode=%ERRORLEVEL%"

if not "%exitCode%"=="0" (
    echo.
    echo [ERROR] Startup failed with exit code %exitCode%. See the messages above.
    pause
)

exit /b %exitCode%
