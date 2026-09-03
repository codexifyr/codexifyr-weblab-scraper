@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Codexifyr - Build Windows Setup EXE

where py >nul 2>&1
if not errorlevel 1 (set "PY=py -3") else (set "PY=python")

if not exist ".buildvenv\Scripts\python.exe" %PY% -m venv .buildvenv || goto :fail
".buildvenv\Scripts\python.exe" -m pip install --upgrade pip >nul || goto :fail
".buildvenv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller || goto :fail

rmdir /S /Q dist\desktop 2>nul
rmdir /S /Q build 2>nul
".buildvenv\Scripts\pyinstaller.exe" --clean --noconfirm installer\desktop.spec || goto :fail

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo.
  echo Desktop EXE was built successfully in dist\desktop.
  echo Inno Setup 6 is not installed, so the final Setup.exe could not be compiled.
  echo Install Inno Setup 6, then double-click this file again.
  pause
  exit /b 0
)

"%ISCC%" installer\Codexifyr.iss || goto :fail
echo.
echo DONE: dist\installer\Codexifyr-WebLab-Setup-v4.2.3.exe
pause
exit /b 0

:fail
echo Build failed.
pause
exit /b 1
