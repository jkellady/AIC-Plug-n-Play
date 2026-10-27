@echo off
rem BUTTON: run the check for September 2026 only. Read-only on all inputs.
rem Admin: put September's clock exports, schedule workbook and HR record workbook in
rem 01_DROP_FOLDER\2026-09, then double-click this file.
rem It first checks all three kinds of file are there. If anything is missing it stops
rem without running, so 06_REPORTS\LATEST is never replaced by a partial report.
cd /d "%~dp0"
if not exist "01_DROP_FOLDER\2026-09\" goto :nofolder
".venv\Scripts\python.exe" -B -m src.precheck 2026-09
if errorlevel 1 goto :notready
call .\RUN_MONTH.bat 2026-09
goto :eof

:nofolder
echo There is no folder 01_DROP_FOLDER\2026-09. Run CREATE_FOLDERS.bat first.
pause
goto :eof

:notready
echo.
pause
goto :eof
