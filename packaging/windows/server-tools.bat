@echo off
setlocal
cd /d "%~dp0"
title Hussaini - Server owner tools

if not exist "HussainiServer.exe" (
  echo HussainiServer.exe was not found next to this file.
  pause
  exit /b 1
)

:menu
cls
echo ==========================================
echo    HUSSAINI - SERVER OWNER TOOLS
echo ==========================================
echo.
echo    1  List all accounts
echo    2  Back up the database
echo    3  Reset a user's password
echo    4  Show what the server stores (hashes + ciphertext)
echo    5  Quit
echo.
echo  (These work whether or not the server is running.)
echo.
set choice=
set /p choice=Choose 1-5: 
if "%choice%"=="1" goto list
if "%choice%"=="2" goto backup
if "%choice%"=="3" goto reset
if "%choice%"=="4" goto show
if "%choice%"=="5" exit /b 0
goto menu

:list
"%~dp0HussainiServer.exe" --list-users
pause
goto menu

:backup
"%~dp0HussainiServer.exe" --backup
pause
goto menu

:reset
echo.
set who=
set /p who=Username whose password you want to reset: 
if "%who%"=="" goto menu
"%~dp0HussainiServer.exe" --reset-password "%who%"
pause
goto menu

:show
"%~dp0HussainiServer.exe" --show-db
pause
goto menu
