@echo off
rem MANUAL TRIGGER: runs the check now, even if no new files were found, then opens the Latest folder.
cd /d "%~dp0"
call run_daily.bat --force
echo.
echo Done. The report and the note are in 06_REPORTS\LATEST
set "REVIEW="
for %%F in (06_REPORTS\LATEST\Review_*.html) do set "REVIEW=%%~fF"
if defined REVIEW (start "" "%REVIEW%") else (start "" "%CD%\06_REPORTS\LATEST")
pause
