@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ============================================================
rem  check.bat - diagnostics for the voice agent setup.
rem  Run this if install.bat or start.bat fail.
rem  All console messages are in English on purpose: Russian
rem  text inside .bat files breaks when cmd.exe uses a non-
rem  UTF-8 code page (this was the original bug). The agent
rem  itself works fully in Russian.
rem  Results are also saved to data\check_report.txt
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
set "REPORT_DIR=%ROOT%data"
set "REPORT=%REPORT_DIR%\check_report.txt"
if not exist "%REPORT_DIR%" mkdir "%REPORT_DIR%"
if not defined VA_CHECK_LOGGING (
    set "VA_CHECK_LOGGING=1"
    echo Report will be saved to: %REPORT%
    call "%~f0" > "%REPORT%" 2>&1
    type "%REPORT%"
    pause
    exit /b 0
)
echo ==================================================================
echo  VOICE AGENT - DIAGNOSTIC CHECK
echo ==================================================================

set "FAILCOUNT=0"

rem ---------- [1] Project files ----------
echo.
echo [1/9] Project files...
call :check_file "%ROOT%app\main.py"
call :check_file "%ROOT%app\config.py"
call :check_file "%ROOT%app\llm.py"
call :check_file "%ROOT%app\stt.py"
call :check_file "%ROOT%app\tts.py"
call :check_file "%ROOT%app\search.py"
call :check_file "%ROOT%app\storage.py"
call :check_file "%ROOT%app\utils.py"
call :check_file "%ROOT%requirements.txt"
echo       Project files check done.

rem ---------- [2] Python interpreter ----------
echo.
echo [2/9] Python...
set "PYVER="
for /f "delims=" %%v in ('"%VENV_PY%" -c "import sys;print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%v"
if defined PYVER (
    echo       OK: virtualenv python found, version !PYVER!
) else (
    echo       WARN: .venv python not found or broken.
    echo       Fix: run install.bat first. Checking system python anyway...
    call :find_python
    if defined PYTHON_EXE (
        echo       System python available: !PYTHON_EXE!
        for /f "delims=" %%v in ('"!PYTHON_EXE!" -c "import sys;print(sys.version.split()[0])" 2^>nul') do echo       Version: %%v
    ) else (
        echo       FAILED: no python found at all. Install Python 3.11 from https://www.python.org/downloads/
        set /a FAILCOUNT+=1
    )
)

rem ---------- [3] Installed packages ----------
echo.
echo [3/9] Required packages...
if not exist "%VENV_PY%" (
    echo       SKIP: no .venv yet. Run install.bat first.
) else (
    call :check_pkg requests        requests
    call :check_pkg sounddevice      sounddevice
    call :check_pkg numpy            numpy
    call :check_pkg vosk             vosk
    call :check_pkg pyttsx3          pyttsx3
)

rem ---------- [4] Microphone devices ----------
echo.
echo [4/9] Microphone ^(sounddevice^)...
if not exist "%VENV_PY%" (
    echo       SKIP: no .venv.
) else (
    "%VENV_PY%" -c "import sounddevice as sd; d=[x for x in sd.query_devices() if x['max_input_channels']>0]; print('      Input devices:', len(d)); [print('        ['+str(i)+']', x['name']) for i,x in enumerate(d)]" 2>&1
    if errorlevel 1 echo       FAILED: sounddevice cannot list devices. Check microphone privacy settings in Windows.
)

rem ---------- [5] Vosk model ----------
echo.
echo [5/9] Vosk speech model...
set "MODEL_FOUND="
call :find_vosk_model "%ROOT%models\vosk-model-small-ru-0.22"
call :find_vosk_model "%ROOT%models\vosk-model-ru-0.42"
call :find_vosk_model "%ROOT%model"
if defined MODEL_FOUND (
    echo       OK: model found: !MODEL_FOUND!
) else (
    echo       INFO: no Vosk model folder detected.
    echo       Expected: models\vosk-model-small-ru-0.22 ^(extracted from the downloaded zip^)
    echo       Download: https://alphacephei.com/vosk/models
    echo       Without a model the app still works in text mode.
)

rem ---------- [6] Ollama service ----------
echo.
echo [6/9] Ollama service ^(http://localhost:11434^)...
set "OLLAMA_OK="
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import requests;requests.get('http://localhost:11434/api/tags',timeout=3)" >nul 2>&1 && set "OLLAMA_OK=1"
) else (
    curl -s -o nul --max-time 3 http://localhost:11434/api/tags >nul 2>&1 && set "OLLAMA_OK=1"
)
if not defined OLLAMA_OK (
    echo       FAILED: cannot reach Ollama at http://localhost:11434
    echo       Fix: install Ollama from https://ollama.com/download and run: ollama pull llama3.1
    echo       Check that the Ollama icon is present in the system tray.
    set /a FAILCOUNT+=1
    goto :ollama_done
)
echo       OK: Ollama is running. Models:
if exist "%VENV_PY%" "%VENV_PY%" -c "import requests,json;[print('        -',m['name']) for m in requests.get('http://localhost:11434/api/tags',timeout=3).json().get('models',[])]" 2>nul
if not exist "%VENV_PY%" curl -s --max-time 3 http://localhost:11434/api/tags 2>nul
:ollama_done

rem ---------- [7] TTS voices ----------
echo.
echo [7/9] Text-to-speech voices...
if not exist "%VENV_PY%" (
    echo       SKIP: no .venv.
) else (
    "%VENV_PY%" -c "import pyttsx3; e=pyttsx3.init(); names=[v.name for v in e.getProperty('voices') or []]; print('      Voices available:', len(names)); [print('        -', n) for n in names[:10]]; e.stop()" 2>&1
    if errorlevel 1 echo       WARN: pyttsx3 failed to initialize. Voice output may be unavailable; text answers still work.
)

rem ---------- [8] App self-test ----------
echo.
echo [8/9] Application modules self-test...
if not exist "%VENV_PY%" (
    echo       SKIP: no .venv.
) else (
    pushd "%ROOT%"
    "%VENV_PY%" -X utf8 -c "import sys; sys.path.insert(0,'.'); from app import config, utils, storage, search, llm, tts; print('      Imports OK'); print('      DB path:', config.DB_PATH); print('      Log path:', config.APP_LOG_PATH)" 2>&1
    popd
)

rem ---------- [9] Recent errors from log ----------
echo.
echo [9/9] Last errors from logs\app.log...
if exist "%ROOT%logs\app.log" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath '%ROOT%logs\app.log' -Tail 200 -ErrorAction SilentlyContinue | Select-String -Pattern 'ERROR|Traceback|Exception' -Context 0,2 | Select-Object -Last 15" 2>nul
    echo       Full log: %ROOT%logs\app.log
) else (
    echo       No log file yet ^(app was never started successfully^).
)

rem ---------- Summary ----------
echo.
echo ==================================================================
if "%FAILCOUNT%"=="0" (
    echo  SUMMARY: no critical problems found.
    echo  If start.bat still closes instantly, open a command prompt in this
    echo  folder and run:  ".venv\Scripts\python.exe" -X utf8 -m app.main
    echo  then read the error printed there. Details: logs\app.log
) else (
    echo  SUMMARY: !FAILCOUNT! critical problem^(s^) found. See FAILED lines above.
)
echo ==================================================================
pause
exit /b 0

rem ================= subroutines =================
:check_file
if exist "%~1" (
    echo       OK: %~nx1
) else (
    echo       FAILED: missing %~nx1
    set /a FAILCOUNT+=1
)
exit /b 0

:check_pkg
rem %1 = module name, %2 = package name
"%VENV_PY%" -c "import %1" >nul 2>&1
if errorlevel 1 (
    echo       FAILED: package %2 is not installed correctly. Re-run install.bat.
    set /a FAILCOUNT+=1
) else (
    echo       OK: %2
)
exit /b 0

:find_vosk_model
if defined MODEL_FOUND exit /b 0
if exist "%~1\conf\model.conf" set "MODEL_FOUND=%~1"
exit /b 0

:find_python
rem Sets PYTHON_EXE to the first working system python (py launcher preferred).
set "PYTHON_EXE="
for /f "delims=" %%p in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%p"
if not defined PYTHON_EXE for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%p"
if not defined PYTHON_EXE for /f "delims=" %%p in ('python -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%p"
exit /b 0
