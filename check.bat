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
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = @'
import sounddevice as sd
devs = list(sd.query_devices())
ins = [x for x in devs if x['max_input_channels'] > 0]
print('      Input devices:', len(ins))
try:
    default_in = int(sd.default.device[0])
except Exception:
    default_in = -1
for i, x in enumerate(devs):
    if x['max_input_channels'] > 0:
        mark = '   <-- DEFAULT' if i == default_in else ''
        name = x['name'].splitlines()[0]
        print('        [%d] %s%s' % (i, name, mark))
try:
    sd.check_input_status()
    print('      Windows microphone access: ALLOWED')
except Exception:
    print('      FAILED: Windows denies microphone access to desktop apps.')
    print('      Fix: Settings - Privacy - Microphone - allow desktop apps.')
'@; $f = Join-Path $env:TEMP 'va_mic_test.py'; [IO.File]::WriteAllText($f, $c); & '%RUN_PY%' -X utf8 $f; $rc = $LASTEXITCODE; Remove-Item $f -ErrorAction SilentlyContinue; exit $rc" 2>&1
if errorlevel 1 echo       WARN: microphone test failed. See lines above.
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
if defined RUN_PY (
    "%RUN_PY%" -c "import requests;requests.get('http://localhost:11434/api/tags',timeout=3)" >nul 2>&1 && set "OLLAMA_OK=1"
) else (
    curl -s -o nul --max-time 3 http://localhost:11434/api/tags >nul 2>&1 && set "OLLAMA_OK=1"
)
if not defined OLLAMA_OK (
    echo       FAILED: cannot reach Ollama at http://localhost:11434
    echo       Fix: install Ollama from https://ollama.com/download and run: ollama pull qwen2.5:3b
    echo       Check that the Ollama icon is present in the system tray.
    set /a FAILCOUNT+=1
    goto :summary
)
echo       OK: Ollama is running. Models:
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { $m = Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 3; $m.models | ForEach-Object { Write-Output ('        - ' + $_.name) } } catch { }" 2>nul

rem ---------- [7] TTS voices ----------
echo.
echo [7/10] Text-to-speech voices...
if defined RUN_PY goto :tts_run
echo       SKIP: no python available.
goto :tts_done
:tts_run
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = @'
import sys, os
sys.path.insert(0, os.environ['VA_ROOT'])
from app.tts import TextToSpeech
t = TextToSpeech()
print('      TTS available:', t.available)
print('      Voice:', t.voice_name or '(none)')
voices = t.list_voices()
print('      Voices in Windows:', len(voices))
for n, i in voices[:10]:
    print('        -', n)
if not t.available and t.error:
    print('      Reason:', t.error)
'@; $f = Join-Path $env:TEMP 'va_tts_test.py'; [IO.File]::WriteAllText($f, $c); & '%RUN_PY%' -X utf8 $f; $rc = $LASTEXITCODE; Remove-Item $f -ErrorAction SilentlyContinue; exit $rc" 2>&1
:tts_done

rem ---------- [8] App self-test ----------
echo.
echo [8/10] Application modules self-test...
if defined RUN_PY goto :selftest_run
echo       SKIP: no python available.
goto :selftest_done
:selftest_run
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = @'
import sys, os
sys.path.insert(0, os.environ['VA_ROOT'])
from app import config, utils, storage, search, llm, tts
print('      Imports OK')
print('      DB path:', config.DB_PATH)
print('      Log path:', config.APP_LOG_PATH)
import app.main
print('      main.py loads OK (direct-run fix active)')
'@; $f = Join-Path $env:TEMP 'va_selftest.py'; [IO.File]::WriteAllText($f, $c); & '%RUN_PY%' -X utf8 $f; $rc = $LASTEXITCODE; Remove-Item $f -ErrorAction SilentlyContinue; exit $rc" 2>&1
if errorlevel 1 (
    echo       FAILED: application modules cannot be imported. See lines above.
    set /a FAILCOUNT+=1
)
:selftest_done

rem ---------- [9] Recent errors from log ----------
echo.
echo [9/10] Last errors from logs\app.log...
if exist "%LOG_DIR%\app.log" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Content -LiteralPath '%LOG_DIR%\app.log' -Encoding UTF8 -Tail 200 -ErrorAction SilentlyContinue | Select-String -Pattern 'ERROR|Traceback|Exception' -Context 0,2 | Select-Object -Last 15 | Out-File -FilePath '%TEMP%\va_log_tail.txt' -Encoding utf8" >nul 2>&1
    type "%TEMP%\va_log_tail.txt"
    del "%TEMP%\va_log_tail.txt" >nul 2>&1
    echo       Full log: %LOG_DIR%\app.log ^(UTF-8; if console shows mojibake, open the file in Notepad^)
) else (
    echo       No log file yet ^(app was never started successfully^).
)

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
)
echo       OK: Ollama is running. Models:
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { $m = Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 3; $m.models | ForEach-Object { Write-Output ('        - ' + $_.name) } } catch { }" 2>nul

rem ---------- [7] TTS voices ----------
echo.
echo [7/10] Text-to-speech voices...
if defined RUN_PY goto :tts_run
echo       SKIP: no python available.
goto :tts_done
:tts_run
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = @'
import sys, os
sys.path.insert(0, os.environ['VA_ROOT'])
from app.tts import TextToSpeech
t = TextToSpeech()
print('      TTS available:', t.available)
print('      Voice:', t.voice_name or '(none)')
voices = t.list_voices()
print('      Voices in Windows:', len(voices))
for n, i in voices[:10]:
    print('        -', n)
if not t.available and t.error:
    print('      Reason:', t.error)
'@; $f = Join-Path $env:TEMP 'va_tts_test.py'; [IO.File]::WriteAllText($f, $c); & '%RUN_PY%' -X utf8 $f; $rc = $LASTEXITCODE; Remove-Item $f -ErrorAction SilentlyContinue; exit $rc" 2>&1
:tts_done

rem ---------- [8] App self-test ----------
echo.
echo [8/10] Application modules self-test...
if defined RUN_PY goto :selftest_run
echo       SKIP: no python available.
goto :selftest_done
:selftest_run
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = @'
import sys, os
sys.path.insert(0, os.environ['VA_ROOT'])
from app import config, utils, storage, search, llm, tts
print('      Imports OK')
print('      DB path:', config.DB_PATH)
print('      Log path:', config.APP_LOG_PATH)
import app.main
print('      main.py loads OK (direct-run fix active)')
'@; $f = Join-Path $env:TEMP 'va_selftest.py'; [IO.File]::WriteAllText($f, $c); & '%RUN_PY%' -X utf8 $f; $rc = $LASTEXITCODE; Remove-Item $f -ErrorAction SilentlyContinue; exit $rc" 2>&1
if errorlevel 1 (
    echo       FAILED: application modules cannot be imported. See lines above.
    set /a FAILCOUNT+=1
)
:selftest_done

rem ---------- [9] Recent errors from log ----------
echo.
echo [9/10] Last errors from logs\app.log...
if exist "%LOG_DIR%\app.log" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Content -LiteralPath '%LOG_DIR%\app.log' -Encoding UTF8 -Tail 200 -ErrorAction SilentlyContinue | Select-String -Pattern 'ERROR|Traceback|Exception' -Context 0,2 | Select-Object -Last 15 | Out-File -FilePath '%TEMP%\va_log_tail.txt' -Encoding utf8" >nul 2>&1
    type "%TEMP%\va_log_tail.txt"
    del "%TEMP%\va_log_tail.txt" >nul 2>&1
    echo       Full log: %LOG_DIR%\app.log ^(UTF-8; if console shows mojibake, open the file in Notepad^)
) else (
    echo       No log file yet ^(app was never started successfully^).
)

rem ---------- [10] One-shot question test ----------
echo.
echo [10/10] Running the app with a single test question...
if not defined RUN_PY (
    echo       SKIP: no python available.
) else (
    "%RUN_PY%" -X utf8 "%ROOT%\app\main.py" "What is the name of this project" 2>&1
    if errorlevel 1 (
        echo       FAILED: the application returned an error code. See lines above.
        set /a FAILCOUNT+=1
    ) else (
        echo       OK: the application started, answered and exited cleanly.
    )
)

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
