@echo off
rem BUTTON: re-run the May 2026 trial only (September is on hold). Read-only on all inputs.
cd /d "%~dp0"
echo Running the May 2026 trial check...
call run_daily.bat --month 2026-05 --force
echo.
echo ===== Daily note =====
if exist "06_REPORTS\LATEST\DAILY_NOTE.txt" type "06_REPORTS\LATEST\DAILY_NOTE.txt"
echo.
echo Report folder: 06_REPORTS\LATEST (opening now)
set "REVIEW="
for %%F in (06_REPORTS\LATEST\Review_*.html) do set "REVIEW=%%~fF"
if defined REVIEW (start "" "%REVIEW%") else (start "" "%CD%\06_REPORTS\LATEST")
pause
