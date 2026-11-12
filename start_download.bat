@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Please run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u "download_drive.py" %*
set "RESULT=%ERRORLEVEL%"
echo.
echo Exit code: %RESULT%. Files and progress are preserved unless you confirmed deletion.
pause
exit /b %RESULT%
