@echo off
rem ============================================================
rem  Voice Agent - запуск приложения
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Сначала запустите install.bat
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ОШИБКА: не удалось активировать окружение. Запустите install.bat заново.
    pause
    exit /b 1
)

python app\main.py
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo Приложение завершилось с ошибкой (код %EXITCODE%).
    echo Подробности в файле logs\app.log
)
pause
