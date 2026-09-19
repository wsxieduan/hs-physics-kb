@echo off
rem ===========================================================
rem  Collaboration loop launcher  (Codex produces, QC gates)
rem
rem  Manual    : double-click this file  -- window stays open
rem  Scheduled : call it with the word  quiet   -- no pause
rem
rem  All Chinese text is printed by orchestrator.py, which
rem  handles UTF-8 by itself. This file stays ASCII on purpose:
rem  cmd.exe reads .cmd with the OEM codepage, so non-ASCII
rem  here would come out garbled.
rem ===========================================================
chcp 65001 >nul
cd /d D:\codex

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set HTTP_PROXY=http://127.0.0.1:10808
set HTTPS_PROXY=http://127.0.0.1:10808
set NO_PROXY=127.0.0.1,localhost

set PYEXE=C:\Users\Lenovo\miniconda3\python.exe
if not exist "%PYEXE%" set PYEXE=C:\Users\Lenovo\.workbuddy\binaries\python\versions\3.13.12\python.exe

"%PYEXE%" "D:\codex\work\orchestrator.py" --rounds 6 --minutes 170
set RC=%ERRORLEVEL%

if /i "%1"=="quiet" exit /b %RC%

echo.
echo ==================== finished, exit code %RC% ====================
echo  plan  : D:\codex\work\
echo  log   : D:\codex\logs\
echo  qc    : D:\codex\outputs\
echo  state : D:\codex\work\
echo ==================================================================
pause >nul
