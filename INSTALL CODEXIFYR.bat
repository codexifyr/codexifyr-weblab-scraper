@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Codexifyr WebLab - Installer v4.2.3

set "APPROOT=%LOCALAPPDATA%\Codexifyr WebLab"
set "APPDIR=%APPROOT%\App"
set "DATADIR=%APPROOT%\Data"
set "BROWSERDIR=%APPROOT%\Browsers"

echo.
echo ============================================================
echo      CODEXIFYR WEBLAB - WINDOWS INSTALLER 4.2.3
echo ============================================================
echo.
echo Install location: %APPDIR%
echo Data location:    %DATADIR%
echo.
echo Your Data folder is preserved during installs and updates.
echo.

where py >nul 2>&1
if not errorlevel 1 (
  set "BASEPY=py -3"
) else (
  where python >nul 2>&1 || (
    echo Python 3 was not found on this PC.
    echo Install Python 3.11 or newer from python.org, then run this installer again.
    pause
    exit /b 1
  )
  set "BASEPY=python"
)

if not exist "%APPROOT%" mkdir "%APPROOT%" >nul 2>&1
if not exist "%DATADIR%" mkdir "%DATADIR%" >nul 2>&1

REM Use a staged PowerShell copy instead of Robocopy.
REM This avoids Robocopy error 16 and safely replaces a partial/old App folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install_app.ps1"
if errorlevel 1 goto :copyfail

cd /d "%APPDIR%"
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Creating private Python environment...
  %BASEPY% -m venv .venv || goto :fail
)

echo.
echo Installing/updating Codexifyr dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

echo.
echo Installing Chromium used by the scraper...
set "PLAYWRIGHT_BROWSERS_PATH=%BROWSERDIR%"
".venv\Scripts\python.exe" -m playwright install chromium || goto :fail

REM Create hidden launcher. Runtime customer data stays outside the App folder.
(
  echo Set sh = CreateObject("WScript.Shell"^)
  echo sh.Environment("PROCESS"^)("CODEXIFYR_DATA_DIR"^) = "%DATADIR%"
  echo sh.Environment("PROCESS"^)("PLAYWRIGHT_BROWSERS_PATH"^) = "%BROWSERDIR%"
  echo sh.CurrentDirectory = "%APPDIR%"
  echo sh.Run Chr(34^) ^& "%APPDIR%\.venv\Scripts\pythonw.exe" ^& Chr(34^) ^& " " ^& Chr(34^) ^& "%APPDIR%\desktop_app.py" ^& Chr(34^), 0, False
) > "%APPROOT%\Launch Codexifyr.vbs"

REM Create Desktop/Start Menu shortcuts in a dedicated PowerShell file.
REM Keeping this out of an inline -Command avoids cmd.exe caret/pipe parsing bugs.
copy /Y "%APPDIR%\UNINSTALL CODEXIFYR.bat" "%APPROOT%\UNINSTALL CODEXIFYR.bat" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%\installer\install_shell.ps1"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo                    INSTALL COMPLETE
echo ============================================================
echo.
echo Desktop and Start Menu shortcuts were created.
echo.
echo Your scan/repair/migration data remains in:
echo %DATADIR%
echo.
choice /C YN /N /M "Launch Codexifyr now? [Y/N]: "
if errorlevel 2 goto :done
start "" "%APPROOT%\Launch Codexifyr.vbs"
:done
exit /b 0

:copyfail
echo.
echo ============================================================
echo INSTALLATION COULD NOT COPY THE APPLICATION FILES
echo ============================================================
echo.
echo The Data folder was NOT removed.
echo Close any old Codexifyr window and run INSTALL CODEXIFYR.bat again.
echo.
pause
exit /b 1

:fail
echo.
echo ============================================================
echo INSTALLATION FAILED
echo ============================================================
echo.
echo Your Data folder has NOT been removed.
echo Existing scans, repairs and migration packages remain preserved.
echo.
pause
exit /b 1
