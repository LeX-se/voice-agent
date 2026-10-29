@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
rem ============================================================
rem  check.bat - diagnostics for the voice agent setup.
rem  Run this if install.bat or start.bat fail.
rem  All console messages are in English on purpose: Russian
rem  text inside .bat files breaks when cmd.exe uses a non-
rem  UTF-8 code page (that was the original bug). The agent
rem  itself works fully in Russian.
rem  The report is saved to: logs\check_report.txt
rem ============================================================

rem --- always work from the folder where this script lives
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

rem --- fix UTF-8 report encoding on Windows 7 / PowerShell 2.0/3.0,
rem --- where Out-File -Encoding utf8 writes BOM and cmd.exe redirection
rem --- writes OEM bytes (that produced unreadable mojibake in the report).
for /f "tokens=2 delims==" %%v in ('wmic os get Version /value 2^>nul ^| findstr Version') do set "WINVER=%%v"
for /f "delims=. tokens=1" %%m in ("%WINVER%") do set "WINMAJOR=%%m"
if not defined WINMAJOR for /f "delims=. tokens=1" %%m in ("%WINVER:~0,6%") do set "WINMAJOR=%%m"
if "%WINMAJOR%" geq 10 (
    rem PowerShell 5+: console output is already UTF-8, redirection is safe.
    set "REPORT_ENC="
) else (
    rem Old PowerShell: write files explicitly via .NET (no BOM, UTF-8).
    set "REPORT_ENC=-Command $OutputEncoding=[Text.Encoding]::UTF8; [Console]::InputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8"
)
cd /d "%ROOT%" || (
    echo FAILED: cannot enter project folder "%ROOT%"
    pause
    exit /b 1
)

set "VA_ROOT=%ROOT%"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
set "LOG_DIR=%ROOT%\logs"
set "REPORT=%LOG_DIR%\check_report.txt"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" 2>nul

rem --- force UTF-8 for every python child process: without this, on the
rem --- default OEM code page (866) Russian output becomes mojibake both in
rem --- the console and in the saved report file.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not defined VA_CHECK_LOGGING (
    set "VA_CHECK_LOGGING=1"
    echo Report will be saved to: %REPORT%
    echo.
    call "%~f0" > "%REPORT%" 2>&1
    type "%REPORT%"
    pause
    exit /b 0
)

echo ==================================================================
echo  VOICE AGENT - DIAGNOSTIC CHECK
echo  Project folder: %ROOT%
echo ==================================================================

set "FAILCOUNT=0"

rem ---------- [1] Project files ----------
echo.
echo [1/10] Project files...
call :check_file "app\__init__.py"
call :check_file "app\main.py"
call :check_file "app\config.py"
call :check_file "app\llm.py"
call :check_file "app\stt.py"
call :check_file "app\tts.py"
call :check_file "app\search.py"
call :check_file "app\storage.py"
call :check_file "app\utils.py"
call :check_file "requirements.txt"
call :check_file "start.bat"
call :check_file "install.bat"
call :check_file "tools\mic_check.py"
call :check_file "tools\tts_check.py"
call :check_file "tools\selftest_check.py"
call :check_file "tools\ollama_check.py"
call :check_file "tools\ollama_models.py"
call :check_file "tools\log_tail.py"
echo       Project files check done.

rem ---------- [2] Python interpreter ----------
echo.
echo [2/10] Python...
set "RUN_PY="
if exist "%VENV_PY%" (
    rem venv python.exe exists - verify it actually runs (broken venv = instant silent failures)
    "%VENV_PY%" -c "print(1)" >nul 2>&1
    if not errorlevel 1 set "RUN_PY=%VENV_PY%"
)
if defined RUN_PY (
    echo       OK: virtualenv python found: %VENV_PY%
    for /f "delims=" %%v in ('"%RUN_PY%" -c "import sys;print(sys.version.split()[0])" 2^>nul') do echo       Version: %%v
) else (
    if exist "%VENV_PY%" (
        echo       FAILED: .venv python.exe EXISTS but does not run - the virtual environment is broken.
        echo       Fix: delete the folder .venv, then run install.bat again.
        set /a FAILCOUNT+=1
    ) else (
        echo       WARN: no .venv found. Run install.bat first. Checking system python anyway...
    )
    call :find_python
    if defined PYTHON_EXE (
        echo       System python available: !PYTHON_EXE!
        for /f "delims=" %%v in ('"!PYTHON_EXE!" -c "import sys;print(sys.version.split()[0])" 2^>nul') do echo       Version: %%v
        if not defined RUN_PY set "RUN_PY=!PYTHON_EXE!"
    ) else (
        if not exist "%VENV_PY%" (
            echo       FAILED: no python found at all. Install Python 3.11 from https://www.python.org/downloads/
            set /a FAILCOUNT+=1
        )
    )
)

rem ---------- [3] Installed packages ----------
echo.
echo [3/10] Required packages...
if not defined RUN_PY (
    echo       SKIP: no python yet. Run install.bat first.
) else (
    call :check_pkg requests
    call :check_pkg sounddevice
    call :check_pkg numpy
    call :check_pkg vosk
    call :check_pkg pyttsx3
)

rem ---------- [4] Microphone devices ----------
echo.
echo [4/10] Microphone ^(sounddevice^)...
if defined RUN_PY goto :mic_run
echo       SKIP: no python available.
goto :mic_done
:mic_run
if not exist "%ROOT%\tools\mic_check.py" (
    echo       FAILED: tools\mic_check.py is missing. Re-download the project files.
    set /a FAILCOUNT+=1
) else (
    "%RUN_PY%" -X utf8 "%ROOT%\tools\mic_check.py" 2>&1
    if errorlevel 1 echo       WARN: microphone test failed. See lines above.
)
:mic_done

rem ---------- [5] Vosk model ----------
echo.
echo [5/10] Vosk speech model...
set "MODEL_FOUND="
call :find_vosk_model "%ROOT%\models\vosk-model-small-ru-0.22"
call :find_vosk_model "%ROOT%\models\vosk-model-ru-0.42"
call :find_vosk_model "%ROOT%\model"
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
echo [6/10] Ollama service ^(http://localhost:11434^)...
set "OLLAMA_OK="
rem Use the application check itself: TCP ping first (fails instantly when the
rem port is closed, bounded by a short timeout when something hangs), direct
rem connection with proxy bypass - no multi-minute stalls like raw requests.get.
if not defined RUN_PY goto :ollama_skip
if not exist "%ROOT%\tools\ollama_check.py" goto :ollama_missing
"%RUN_PY%" -X utf8 "%ROOT%\tools\ollama_check.py" >nul 2>&1 && set "OLLAMA_OK=1"
if not defined OLLAMA_OK goto :ollama_fail
echo       OK: Ollama is running. Models:
"%RUN_PY%" -X utf8 "%ROOT%\tools\ollama_models.py" 2>&1
goto :ollama_done
:ollama_skip
echo       SKIP: no python available.
goto :ollama_done
:ollama_missing
echo       FAILED: tools\ollama_check.py is missing. Re-download the project files.
set /a FAILCOUNT+=1
goto :summary
:ollama_fail
echo       FAILED: cannot reach Ollama at http://localhost:11434
echo       Fix: install Ollama from https://ollama.com/download and run: ollama pull qwen2.5:3b
echo       Check that the Ollama icon is present in the system tray.
set /a FAILCOUNT+=1
goto :summary
:ollama_done

rem ---------- [7] TTS voices ----------
echo.
echo [7/10] Text-to-speech voices...
if defined RUN_PY goto :tts_run
echo       SKIP: no python available.
goto :tts_done
:tts_run
if not exist "%ROOT%\tools\tts_check.py" (
    echo       FAILED: tools\tts_check.py is missing. Re-download the project files.
    set /a FAILCOUNT+=1
) else (
    "%RUN_PY%" -X utf8 "%ROOT%\tools\tts_check.py" 2>&1
)
:tts_done

rem ---------- [8] App self-test ----------
echo.
echo [8/10] Application modules self-test...
if defined RUN_PY goto :selftest_run
echo       SKIP: no python available.
goto :selftest_done
:selftest_run
if not exist "%ROOT%\tools\selftest_check.py" (
    echo       FAILED: tools\selftest_check.py is missing. Re-download the project files.
    set /a FAILCOUNT+=1
    goto :selftest_done
)
"%RUN_PY%" -X utf8 "%ROOT%\tools\selftest_check.py" 2>&1
if errorlevel 1 (
    echo       FAILED: application modules cannot be imported. See lines above.
    set /a FAILCOUNT+=1
)
:selftest_done

rem ---------- [9] Recent errors from log ----------
echo.
echo [9/10] Last errors from logs\app.log...
if not defined RUN_PY goto :logtail_skip
if not exist "%ROOT%\tools\log_tail.py" goto :logtail_skip
if exist "%LOG_DIR%\app.log" (
    rem Pure-Python helper: always writes UTF-8 without BOM (PowerShell one-liners
    rem added a BOM on old Windows and cmd redirection used the OEM code page -
    rem both produced unreadable mojibake in the report).
    "%RUN_PY%" -X utf8 "%ROOT%\tools\log_tail.py" "%LOG_DIR%\app.log" "%TEMP%\va_log_tail.txt" >nul 2>nul
    type "%TEMP%\va_log_tail.txt"
    del "%TEMP%\va_log_tail.txt" >nul 2>&1
    echo       Full log: %LOG_DIR%\app.log ^(UTF-8; if console shows mojibake, open the file in Notepad^)
    goto :logtail_done
)
echo       No log file yet ^(app was never started successfully^).
goto :logtail_done
:logtail_skip
echo       SKIP: cannot read the log without python/tools.
:logtail_done

rem ---------- [10] One-shot question test ----------
echo.
echo [10/10] Running the app with a single test question...
if defined RUN_PY goto :oneq_run
echo       SKIP: no python available.
goto :oneq_done
:oneq_run
"%RUN_PY%" -X utf8 "%ROOT%\app\main.py" "What is the name of this project" 2>&1
if errorlevel 1 (
    echo       FAILED: the application returned an error code. See lines above.
    set /a FAILCOUNT+=1
) else (
    echo       OK: the application started, answered and exited cleanly.
)
:oneq_done

:summary
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
exit /b 0

rem ================= subroutines =================
:check_file
rem Uses an explicit flag instead of ERRORLEVEL: checking errorlevel
rem inside a parenthesized FOR block reads the value from BEFORE the
rem loop started - that produced false "FAILED" lines for every file.
set "ok=0"
if exist "%ROOT%\%~1" set "ok=1"
if "!ok!"=="1" (
    echo       OK: %~nx1
) else (
    echo       FAILED: missing %~1
    set /a FAILCOUNT+=1
)
exit /b 0

:check_pkg
rem %1 = module name
"%RUN_PY%" -c "import %1" >nul 2>&1
if errorlevel 1 (
    echo       FAILED: package %1 is not installed correctly. Re-run install.bat.
    set /a FAILCOUNT+=1
) else (
    echo       OK: %1
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
