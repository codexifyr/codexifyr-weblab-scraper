@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "APPROOT=%LOCALAPPDATA%\Codexifyr WebLab"
set "APPDIR=%APPROOT%\App"

echo.
echo ============================================================
echo    CODEXIFYR WEBLAB - FINISH WINDOWS SETUP 4.2.3
echo ============================================================
echo.
if not exist "%APPDIR%\installer\install_shell.ps1" (
  echo Installed WebLab files were not found.
  echo Please run INSTALL CODEXIFYR.bat instead.
  pause
  exit /b 1
)

REM Copy the corrected shell helper from this package into the already-installed app.
copy /Y "%~dp0installer\install_shell.ps1" "%APPDIR%\installer\install_shell.ps1" >nul || goto :fail
copy /Y "%~dp0UNINSTALL CODEXIFYR.bat" "%APPROOT%\UNINSTALL CODEXIFYR.bat" >nul || goto :fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%\installer\install_shell.ps1"
if errorlevel 1 goto :fail

echo.
echo Setup finished successfully. No Python packages or Chromium were reinstalled.
echo.
choice /C YN /N /M "Launch Codexifyr now? [Y/N]: "
if errorlevel 2 exit /b 0
start "" "%APPROOT%\Launch Codexifyr.vbs"
exit /b 0

:fail
echo.
echo Setup could not be completed. Your Data folder was not removed.
pause
exit /b 1
