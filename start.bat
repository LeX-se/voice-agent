@echo off
rem ============================================================
rem  Voice Agent - application launcher
rem  Console messages are in English on purpose (see README.md):
rem  Russian text inside .bat files breaks under some Windows
rem  console code pages. The agent itself works fully in Russian.
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "VPY=.venv\Scripts\python.exe"

if not exist "%VPY%" (
    echo Virtual environment not found.
    echo Please run install.bat first.
    echo If install.bat failed, run check.bat for diagnostics.
    pause
    exit /b 1
)

chcp 65001 >nul 2>&1

rem --- quick sanity check of the interpreter before launching ---
"%VPY%" -c "print(1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: "%VPY%" cannot run. The virtual environment is broken.
    echo Fix: delete the folder .venv and run install.bat again.
    echo You can also run check.bat to see what exactly is wrong.
    pause
    exit /b 1
)

"%VPY%" -X utf8 app\main.py
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo The application exited with an error code %EXITCODE%.
    echo Look at the lines above and at logs\app.log for details.
    echo Run check.bat for a full diagnostic report.
) else (
    echo Application finished normally.
)
endlocal
pause
