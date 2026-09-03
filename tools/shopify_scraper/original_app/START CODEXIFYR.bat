@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Codexifyr - Website Product Scraper
color 0A

REM ============================================================
REM Always switch to the folder containing this BAT file
REM ============================================================
cd /d "%~dp0"

echo.
echo ============================================================
echo              CODEXIFYR PRODUCT SCRAPER
echo ============================================================
echo.
echo Starting Codexifyr...
echo Project folder:
echo %CD%
echo.

REM ============================================================
REM Find Python
REM ============================================================

set "BASE_PY="

where py >nul 2>nul
if not errorlevel 1 (
    set "BASE_PY=py"
)

if not defined BASE_PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "BASE_PY=python"
    )
)

if not defined BASE_PY (
    echo.
    echo [ERROR] Python was not found.
    echo.
    echo Please install Python and make sure:
    echo "Add Python to PATH" is enabled.
    echo.
    pause
    exit /b 1
)

REM ============================================================
REM Create virtual environment if it does not exist
REM ============================================================

if not exist ".venv\Scripts\python.exe" (

    echo [FIRST RUN] Creating local Python environment...
    echo.

    "!BASE_PY!" -m venv ".venv"

    if errorlevel 1 (
        echo.
        echo [ERROR] Could not create the Python environment.
        echo.
        pause
        exit /b 1
    )

    echo.
    echo Installing Python requirements...
    echo.

    ".venv\Scripts\python.exe" -m pip install --upgrade pip

    if exist "full_scraper\requirements.txt" (
        ".venv\Scripts\python.exe" -m pip install -r "full_scraper\requirements.txt"
    ) else (
        echo.
        echo [ERROR] full_scraper\requirements.txt was not found.
        echo.
        pause
        exit /b 1
    )

    if errorlevel 1 (
        echo.
        echo [ERROR] Python requirements installation failed.
        echo.
        pause
        exit /b 1
    )

    echo.
    echo Installing Playwright Chromium...
    echo.

    ".venv\Scripts\python.exe" -m playwright install chromium

    if errorlevel 1 (
        echo.
        echo [ERROR] Playwright Chromium installation failed.
        echo.
        pause
        exit /b 1
    )

    echo.
    echo ============================================================
    echo                CODEXIFYR SETUP COMPLETE
    echo ============================================================
    echo.

)

REM ============================================================
REM Check required application files
REM ============================================================

if not exist "run.py" (
    echo.
    echo [ERROR] run.py was not found.
    echo Expected location:
    echo %CD%\run.py
    echo.
    pause
    exit /b 1
)

if not exist "scraper.py" (
    echo.
    echo [ERROR] scraper.py was not found.
    echo.
    pause
    exit /b 1
)

REM ============================================================
REM Start Codexifyr
REM ============================================================

echo.
echo Starting Codexifyr server...
echo.
echo Dashboard:
echo http://127.0.0.1:8765/
echo.
echo IMPORTANT:
echo Keep this window open while using Codexifyr.
echo To shut down Codexifyr, close this window or press CTRL+C.
echo.
echo ============================================================
echo.

".venv\Scripts\python.exe" "run.py"

REM ============================================================
REM If Python exits unexpectedly, keep the window open
REM ============================================================

set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
echo Codexifyr has stopped.
echo Exit code: %EXIT_CODE%
echo ============================================================
echo.

if not "%EXIT_CODE%"=="0" (
    echo An error occurred. Read the messages above.
    echo.
)

pause

endlocal