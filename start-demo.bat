@echo off
setlocal
cd /d "%~dp0"
title Hussaini Chat - One Click Launcher

echo ==========================================
echo       HUSSAINI SECURE CHAT LAUNCHER
echo ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/
        echo During installation, enable "Add Python to PATH".
        pause
        exit /b 1
    )
    set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating the virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :error
)

echo [2/4] Checking/installing required packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error

echo [3/4] Starting the server in its own terminal...
start "Hussaini Chat Server" cmd /k "cd /d "%~dp0" && .venv\Scripts\python.exe run_server.py"
timeout /t 3 /nobreak >nul

echo [4/4] Starting two client windows...
start "Hussaini Chat Client 1" cmd /k "cd /d "%~dp0" && .venv\Scripts\python.exe run_client.py"
timeout /t 2 /nobreak >nul
start "Hussaini Chat Client 2" cmd /k "cd /d "%~dp0" && .venv\Scripts\python.exe run_client.py"

echo.
echo All three processes have been launched.
echo In each client, use server address: localhost:8765
 echo (The current project uses WebSocket networking; this launcher does not convert it to raw TCP.)
echo You can close this launcher window now. Keep the server window open while chatting.
pause
exit /b 0

:error
echo.
echo Startup failed. Read the error above, check your internet connection, and try again.
pause
exit /b 1
