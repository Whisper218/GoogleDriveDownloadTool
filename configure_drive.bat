@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist "tools\rclone.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
echo Create a remote named gdrive, storage type drive, scope drive.readonly.
echo Use your own OAuth client ID. See README.md for details.
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u "download_drive.py" --configure
pause
