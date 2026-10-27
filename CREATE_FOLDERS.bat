@echo off
rem SETUP: creates any missing folders listed below. Safe to run more than once.
rem It never changes, moves or deletes any file.
cd /d "%~dp0"
echo Creating missing folders...
for %%D in (
  "01_DROP_FOLDER\2026-09"
  "01_DROP_FOLDER\2026-10"
  "04_REFERENCE_SOURCES\app_export"
  "07_LOGS\feedback"
  "..\DailyNote"
) do (
  if exist "%%~D\" (echo [exists] %%~D) else (mkdir "%%~D" && echo [created] %%~D)
)
echo.
echo Done. Month folders are empty on purpose: put a month's files in only when you are ready to run it.
echo To run one month, use RUN_MONTH.bat. RUN_NOW.bat runs every month folder.
pause
