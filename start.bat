@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ============================================================
rem  Voice Agent - launcher
rem  Console messages are English on purpose (Russian text in
rem  .bat files breaks under non-UTF8 code pages). The agent
rem  itself works fully in Russian.
rem ============================================================

chcp 65001 >nul
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%" || (
    echo FAILED: cannot enter project folder "%ROOT%"
    pause
    exit /b 1
)

set "VPY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%VPY%" (
    echo ERROR: virtual environment not found ^(.venv^).
    echo Run install.bat first, then start.bat again.
    pause
    exit /b 1
)

"%VPY%" -c "print(1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: the virtual environment is broken ^(python.exe does not run^).
    echo Fix: delete the ".venv" folder, run install.bat again, then start.bat.
    pause
    exit /b 1
)

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs" 2>nul

echo Starting Voice Agent... ^(log: logs\last_run.log^)
"%VPY%" -X utf8 "%ROOT%\app\main.py"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The application exited with error code %EXITCODE%.
    echo Look at the lines above, at logs\last_run.log and logs\app.log.
    echo Run check.bat for a full diagnostic report.
)
pause
exit /b %EXITCODE%
