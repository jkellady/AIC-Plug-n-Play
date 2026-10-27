@echo off
rem BUTTON: run the check for ONE chosen month folder. Read-only on all inputs.
rem Use this instead of RUN_NOW.bat while any month folder is still empty.
cd /d "%~dp0"
set "M=%~1"
if not defined M set /p M=Type the month to check, like 2026-09, then press Enter:
echo "%M%"| findstr /r /c:"^.20[0-9][0-9]-[01][0-9].$" >nul
if errorlevel 1 goto :badformat
if not exist "01_DROP_FOLDER\%M%\" goto :nofolder
echo Running the check for %M% ...
call run_daily.bat --month %M% --force
echo.
echo ===== Daily note =====
if exist "06_REPORTS\LATEST\DAILY_NOTE.txt" type "06_REPORTS\LATEST\DAILY_NOTE.txt"
echo.
echo Report folder: 06_REPORTS\LATEST (opening now)
set "REVIEW="
for %%F in (06_REPORTS\LATEST\Review_*.html) do set "REVIEW=%%~fF"
if defined REVIEW (start "" "%REVIEW%") else (start "" "%CD%\06_REPORTS\LATEST")
pause
goto :eof

:badformat
echo "%M%" is not a month. Type it like 2026-09 (year, dash, month).
pause
goto :eof

:nofolder
echo There is no folder named %M% inside 01_DROP_FOLDER.
echo Create it, put that month's files inside, then run this again.
pause
goto :eof
