@echo off
rem Client daily attendance check. Started by Task Scheduler, or double-click to run now.
rem Step 1: the checking (plain Python, no AI). Step 2: a plain-language note (Claude, optional).
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
for %%I in ("%ROOT%\..") do set "NOTE=%%~fI\DailyNote"
set LATEST=%ROOT%\06_REPORTS\LATEST
cd /d "%ROOT%"
if not exist "%ROOT%\07_LOGS" mkdir "%ROOT%\07_LOGS"
echo [%date% %time%] start >> "%ROOT%\07_LOGS\daily_task.log"

rem ---- Step 1: the check
if exist "%ROOT%\07_LOGS\last_run.json" del "%ROOT%\07_LOGS\last_run.json"
".venv\Scripts\python.exe" -m src.daily %* >> "%ROOT%\07_LOGS\daily_task.log" 2>&1
set PYERR=%errorlevel%

if not exist "%LATEST%\AGGREGATE_SUMMARY.json" goto :finish

rem ---- Step 2: plain-language note. Skipped when nothing is new, or when Claude is not available.
rem Skip the model (and leave the existing note) when this run did not change LATEST.
findstr /r /c:"latest_updated.: true" "%ROOT%\07_LOGS\last_run.json" >nul 2>&1
if errorlevel 1 (
  echo [%date% %time%] note: nothing new, model skipped >> "%ROOT%\07_LOGS\daily_task.log"
  goto :finish
)
where claude >nul 2>&1
if errorlevel 1 goto :basic
if not exist "%NOTE%\CLAUDE.md" goto :basic

copy /y "%LATEST%\AGGREGATE_SUMMARY.json" "%NOTE%\AGGREGATE_SUMMARY.json" >nul
cd /d "%NOTE%"
claude -p "Write today's note from AGGREGATE_SUMMARY.json, following CLAUDE.md." --allowedTools "Read" --output-format text > "%LATEST%\DAILY_NOTE.txt" 2>> "%ROOT%\07_LOGS\daily_task.log"
set CLERR=%errorlevel%
cd /d "%ROOT%"
if not "%CLERR%"=="0" goto :basic
for %%A in ("%LATEST%\DAILY_NOTE.txt") do if %%~zA LSS 40 goto :basic
echo [%date% %time%] note written by Claude >> "%ROOT%\07_LOGS\daily_task.log"
goto :finish

:basic
rem Fallback: the plain template note written by the check itself.
if exist "%LATEST%\DAILY_NOTE_basic.txt" copy /y "%LATEST%\DAILY_NOTE_basic.txt" "%LATEST%\DAILY_NOTE.txt" >nul
echo [%date% %time%] note: basic template used >> "%ROOT%\07_LOGS\daily_task.log"

:finish
echo [%date% %time%] end python=%PYERR% >> "%ROOT%\07_LOGS\daily_task.log"
exit /b %PYERR%
