CODEXIFYR WEBLAB — WINDOWS DESKTOP EDITION 4.2.3

QUICKEST INSTALL (no compiler required)
1. Extract the project ZIP.
2. Double-click INSTALL CODEXIFYR.bat.
3. The installer creates a Desktop shortcut and Start Menu shortcut.
4. Double-click the purple Codexifyr shortcut to open the app in a native desktop window.

The first install creates a private Python environment and installs Chromium.
Normal daily use does NOT require a command prompt or browser tab.

DATA SAFETY
Application files:
  %LOCALAPPDATA%\Codexifyr WebLab\App
Persistent scan/repair data:
  %LOCALAPPDATA%\Codexifyr WebLab\Data
Playwright browsers:
  %LOCALAPPDATA%\Codexifyr WebLab\Browsers

Updating/reinstalling the application does not overwrite the Data directory.
The uninstaller preserves Data unless the user explicitly chooses to delete it.

OPTIONAL REAL SETUP.EXE BUILD
Double-click BUILD WINDOWS INSTALLER.bat on a Windows development PC.
It builds the branded desktop EXE with PyInstaller. If Inno Setup 6 is installed,
it also creates:
  dist\installer\Codexifyr-WebLab-Setup-v4.2.3.exe

For public distribution, code-sign the final EXE/installer with a trusted Windows
code-signing certificate to reduce SmartScreen 'Unknown publisher' warnings.
