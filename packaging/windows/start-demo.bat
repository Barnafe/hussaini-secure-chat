@echo off
setlocal
cd /d "%~dp0"
title Hussaini Secure Chat - Demo Launcher

echo ==========================================
echo       HUSSAINI SECURE CHAT - DEMO
echo ==========================================
echo.

if not exist "HussainiServer.exe" goto :missing
if not exist "HussainiChat.exe" goto :missing

echo [1/3] Starting the server (a black window will open - leave it open)...
start "Hussaini Chat Server" "%~dp0HussainiServer.exe"
timeout /t 3 /nobreak >nul

echo [2/3] Opening chat window 1...
start "" "%~dp0HussainiChat.exe"
timeout /t 2 /nobreak >nul

echo [3/3] Opening chat window 2...
start "" "%~dp0HussainiChat.exe"

echo.
echo Done. In BOTH chat windows the server address is:  localhost:8765
echo.
echo   1. Register two different users (one in each window).
echo   2. Write down each user's passphrase when it is shown.
echo   3. Sign in, add each other as friends and start chatting.
echo.
echo If Windows asks about the firewall, click "Allow access".
echo Close the black server window when you are finished.
echo.
pause
exit /b 0

:missing
echo ERROR: HussainiServer.exe or HussainiChat.exe was not found next to this file.
echo Make sure you extracted the whole zip folder before running start-demo.bat.
echo.
pause
exit /b 1
