@echo off
setlocal
cd /d "%~dp0"
title Hussaini Chat - Run From Source

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
        echo During installation, tick "Add Python to PATH".
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

echo [2/4] Installing required packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error

echo [3/4] Starting the server in its own window...
start "Hussaini Chat Server" cmd /k ".venv\Scripts\python.exe run_server.py"
timeout /t 3 /nobreak >nul

echo [4/4] Starting two client windows...
start "" ".venv\Scripts\python.exe" run_client.py
timeout /t 2 /nobreak >nul
start "" ".venv\Scripts\python.exe" run_client.py

echo.
echo In each client, the server address is:  localhost:8765
pause
exit /b 0

:error
echo.
echo Setup failed. Read the message above, check your internet connection, and try again.
pause
exit /b 1
