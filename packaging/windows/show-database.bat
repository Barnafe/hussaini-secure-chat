@echo off
setlocal
cd /d "%~dp0"
title Hussaini - what the server stores
if not exist "HussainiServer.exe" (
  echo HussainiServer.exe was not found next to this file.
  pause
  exit /b 1
)
echo.
echo This shows what is stored on the server: password HASHES and ENCRYPTED messages.
"%~dp0HussainiServer.exe" --show-db
echo.
pause
