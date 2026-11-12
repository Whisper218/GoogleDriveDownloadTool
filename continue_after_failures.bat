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
echo This saves outstanding failures and defers them. Successful files remain until DELETE N confirmation.
".venv\Scripts\python.exe" -u "download_drive.py" --defer-failures
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
