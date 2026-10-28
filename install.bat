@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ============================================================
rem  Voice Agent - installer
rem  Creates .venv and installs dependencies.
rem  Console messages are English on purpose (see README.md).
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%" || (
    echo FAILED: cannot enter project folder "%ROOT%"
    pause
    exit /b 1
)

echo ============================================================
echo  Voice Agent - installation
echo  Folder: %ROOT%
echo ============================================================
echo.

rem ---------- [1/4] find Python ----------
echo [1/4] Looking for Python...
set "BASE_PY="
for /f "delims=" %%p in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do if not defined BASE_PY set "BASE_PY=%%p"
if not defined BASE_PY for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined BASE_PY set "BASE_PY=%%p"
if not defined BASE_PY for /f "delims=" %%p in ('python -c "import sys;print(sys.executable)" 2^>nul') do if not defined BASE_PY set "BASE_PY=%%p"
if not defined BASE_PY (
    echo ERROR: Python was not found on this computer.
    echo Install Python 3.11 from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)
echo       Found: !BASE_PY!
"!BASE_PY!" --version

rem ---------- [2/4] create virtual environment ----------
echo.
echo [2/4] Creating virtual environment .venv ...
if exist "%ROOT%\.venv\Scripts\python.exe" (
    echo       .venv already exists - reusing it.
) else (
    "!BASE_PY!" -m venv "%ROOT%\.venv"
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        pause
        exit /b 1
    )
)
set "VPY=%ROOT%\.venv\Scripts\python.exe"
"!VPY!" -c "print(1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: .venv was created but its python.exe does not run.
    echo Delete the ".venv" folder and run install.bat again.
    pause
    exit /b 1
)

rem ---------- [3/4] install dependencies ----------
echo.
echo [3/4] Installing dependencies ^(may take a few minutes^)...
"!VPY!" -m pip install --upgrade pip
if errorlevel 1 (
    echo WARNING: could not upgrade pip, continuing anyway...
)
"!VPY!" -m pip install -r "%ROOT%\requirements.txt"
if errorlevel 1 (
    echo ERROR: dependency installation failed.
    echo Check your internet connection and run install.bat again.
    pause
    exit /b 1
)

rem ---------- [4/4] verify ----------
echo.
echo [4/4] Verifying installation...
"!VPY!" -c "import requests, sounddevice, numpy, vosk, pyttsx3; print('      All packages import OK')"
if errorlevel 1 (
    echo ERROR: some packages are missing after installation.
    echo Scroll up to see which package failed, then run install.bat again.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Installation completed successfully!
echo  Next steps:
echo   1. Make sure Ollama is installed and running ^(ollama.com^)
echo      and a model is pulled, e.g.:  ollama pull qwen2.5:3b
echo   2. Optional: extract a Vosk Russian model into models\
echo     ^(download from https://alphacephei.com/vosk/models^).
echo   3. Double-click start.bat
echo ============================================================
pause
exit /b 0
