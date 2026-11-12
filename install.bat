@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo Installation failed. See the message above.
  pause
  exit /b 1
)
echo Installation complete. Next: configure_drive.bat then start_download.bat
pause
