@echo off
rem ============================================================
rem  Voice Agent - environment setup (run this file once)
rem  If you see garbled characters below, the console codepage
rem  is not set automatically. This message is in English so it
rem  will always be readable.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo  Voice Agent - Installation
echo ============================================
echo.

rem --- Detect Python launcher or interpreter ---
set "PY_CMD="
py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3.11"
    goto :python_found
)
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto :python_found
)
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :python_found
)

echo ERROR: Python was not found on this computer.
echo Install Python 3.11 (64-bit) from https://www.python.org/downloads/
echo During installation, check the box "Add Python to PATH".
echo Then run install.bat again.
pause
exit /b 1

:python_found
echo Found Python:
%PY_CMD% --version
echo.

rem --- Create virtual environment if missing ---
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    %PY_CMD% -m venv ".venv"
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        echo See the message above, then try running install.bat again.
        pause
        exit /b 1
    )
)

rem --- Activate environment ---
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ERROR: failed to activate the virtual environment.
    echo Delete the folder .venv and run install.bat again.
    pause
    exit /b 1
)

rem --- Upgrade pip and install dependencies ---
echo Upgrading pip ...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: could not upgrade pip. Check your internet connection
    echo ^(this is the only network action of the installer^) and run again.
    pause
    exit /b 1
)

echo Installing dependencies from requirements.txt ...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    echo The error text is above. Run install.bat again, or show
    echo this screen to whoever helps you with the installation.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Verifying installed packages ...
echo ============================================
python -c "import requests, sounddevice, numpy, vosk, pyttsx3; print('All required packages are OK.')"
if errorlevel 1 (
    echo ERROR: some packages are still missing. See the message above.
    echo You can run check.bat for a full diagnostic report.
    pause
    exit /b 1
)
echo ============================================
echo  Installation completed successfully!
echo  Now double-click start.bat to run the agent.
echo  If anything fails later, run check.bat.
echo ============================================
endlocal
pause
