@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set "HERE=%~dp0"
if "%~1"=="" (
  echo Usage: transcribe.bat "path\to\call.mp3" [--lang ru] [--num-speakers 2]
  echo Writes call.json / call.txt / call.srt next to the audio file.
  exit /b 1
)
if not defined CALL_ANALYZER_PY set "CALL_ANALYZER_PY=C:\tools\miniconda3\envs\wx\python.exe"
if not exist "%CALL_ANALYZER_PY%" for %%p in (python.exe) do set "CALL_ANALYZER_PY=%%~$PATH:p"
if not exist "%CALL_ANALYZER_PY%" (
  echo Python from the wx env was not found. Set CALL_ANALYZER_PY and retry.
  exit /b 1
)
"%CALL_ANALYZER_PY%" "%HERE%run_call.py" %*
if errorlevel 1 pause
