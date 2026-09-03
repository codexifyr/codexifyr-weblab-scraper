@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Codexifyr WebLab

if not exist ".venv\Scripts\pythonw.exe" (
  echo Codexifyr Desktop dependencies are not installed yet.
  echo Please double-click INSTALL CODEXIFYR.bat first.
  pause
  exit /b 1
)

set "CODEXIFYR_DATA_DIR=%LOCALAPPDATA%\Codexifyr WebLab\Data"
start "" /b ".venv\Scripts\pythonw.exe" "desktop_app.py"
exit /b 0
