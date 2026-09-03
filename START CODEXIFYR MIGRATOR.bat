@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Codexifyr Website Migrator

if not exist ".venv\Scripts\python.exe" (
  echo [CODEXIFYR] First-time setup...
  where py >nul 2>nul
  if %errorlevel%==0 (
    set "BASE_PY=py"
  ) else (
    set "BASE_PY=python"
  )
  !BASE_PY! -m venv .venv
  if errorlevel 1 goto :fail
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  ".venv\Scripts\python.exe" -m playwright install chromium
  if errorlevel 1 goto :fail
  echo [CODEXIFYR] Setup complete.
)

echo [CODEXIFYR] Starting Website Migrator...
".venv\Scripts\python.exe" run.py
goto :eof

:fail
echo.
echo [CODEXIFYR] Setup failed. Check that Python 3 is installed.
pause
