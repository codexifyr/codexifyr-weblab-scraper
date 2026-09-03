@echo off
setlocal EnableExtensions
set "APPROOT=%LOCALAPPDATA%\Codexifyr WebLab"
set "APPDIR=%APPROOT%\App"
set "DATADIR=%APPROOT%\Data"
title Codexifyr WebLab - Uninstaller

echo.
echo CODEXIFYR WEBLAB UNINSTALLER
echo.
echo The application will be removed, but your scan/repair/migration data
echo will be PRESERVED by default in:
echo %DATADIR%
echo.
choice /C YN /N /M "Remove Codexifyr application? [Y/N]: "
if errorlevel 2 exit /b 0

REM Remove shortcuts using Windows known folders (works with OneDrive/redirection).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $d=$w.SpecialFolders.Item('Desktop'); $p=$w.SpecialFolders.Item('Programs'); Remove-Item -LiteralPath (Join-Path $d 'Codexifyr WebLab.lnk') -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath (Join-Path $p 'Codexifyr WebLab') -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CodexifyrWebLab' -Recurse -Force -ErrorAction SilentlyContinue"

rmdir /S /Q "%APPDIR%" 2>nul
del /Q "%APPROOT%\Launch Codexifyr.vbs" 2>nul

echo.
choice /C YN /N /M "Also permanently delete all Codexifyr job/data files? [Y/N]: "
if errorlevel 2 goto :keep
rmdir /S /Q "%DATADIR%" 2>nul
rmdir /S /Q "%APPROOT%\Browsers" 2>nul
:keep
echo Uninstall complete.
pause
