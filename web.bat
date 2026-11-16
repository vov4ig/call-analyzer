@echo off
setlocal
chcp 65001 >nul
title Call Analyzer - local web
set "HERE=%~dp0"
if not defined CALL_ANALYZER_PY set "CALL_ANALYZER_PY=C:\tools\miniconda3\envs\wx\python.exe"
if not exist "%CALL_ANALYZER_PY%" for %%p in (python.exe) do set "CALL_ANALYZER_PY=%%~$PATH:p"
if not exist "%CALL_ANALYZER_PY%" (
  echo Python from the wx env was not found.
  echo Set CALL_ANALYZER_PY to the full path of python.exe and try again.
  pause
  exit /b 1
)
set PYTHONUTF8=1
"%CALL_ANALYZER_PY%" "%HERE%webapp.py" --open %*
if errorlevel 1 pause
