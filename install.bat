@echo off
rem ============================================================
rem  Voice Agent - установка окружения (запустите этот файл один раз)
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo Установка локального голосового агента...
echo.

rem --- Поиск Python 3.11 (или любого Python 3.x как запасного варианта) ---
set "PY_CMD="
py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3.11"
    goto :python_found
)
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :python_found
)
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto :python_found
)

echo ОШИБКА: Python не найден.
echo Установите Python 3.11 (64-bit) с сайта https://www.python.org/downloads/
echo При установке обязательно отметьте галочку "Add Python to PATH".
echo После установки запустите install.bat снова.
pause
exit /b 1

:python_found
echo Найден Python: 
%PY_CMD% --version

rem --- Создание виртуального окружения, если его нет ---
if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение .venv ...
    %PY_CMD% -m venv ".venv"
    if errorlevel 1 (
        echo ОШИБКА: не удалось создать виртуальное окружение.
        echo Подробности выше. Обратитесь за помощью или повторите установку.
        pause
        exit /b 1
    )
)

rem --- Активация окружения ---
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ОШИБКА: не удалось активировать виртуальное окружение.
    pause
    exit /b 1
)

rem --- Обновление pip и установка зависимостей ---
echo Обновляю pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo ОШИБКА: не удалось обновить pip. Проверьте подключение к интернету
    echo (это единственное сетевое действие установки) и повторите запуск.
    pause
    exit /b 1
)

echo Устанавливаю зависимости из requirements.txt ...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ОШИБКА: не удалось установить зависимости.
    echo Текст ошибки находится выше. Повторите запуск install.bat
    echo или передайте этот экран тому, кто помогал с установкой.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Установка завершена успешно!
echo  Теперь дважды щёлкните по файлу start.bat
echo ============================================================
pause
