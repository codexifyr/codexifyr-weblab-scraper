@echo off
setlocal
cd /d "%~dp0"
title Install Codexifyr Dependencies
set "BASE_PY="
where py >nul 2>nul && set "BASE_PY=py"
if not defined BASE_PY where python >nul 2>nul && set "BASE_PY=python"
if not defined BASE_PY (
  echo Python 3.10+ is required.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" "%BASE_PY%" -m venv .venv
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -r "full_scraper\requirements.txt"
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :fail
echo.
echo Installation complete. Double-click START CODEXIFYR.bat to launch.
pause
exit /b 0
:fail
echo.
echo Installation failed. Review the error above.
pause
exit /b 1
