@echo off
rem Makes a clean copy of the project to move to Client's PC. Originals are not changed.
rem WARNING: the copy contains 03_MASTER_DATA\staff_aliases.csv (staff names). Do not upload it to a shared place or git.
setlocal
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
for %%I in ("%SRC%\..") do set "PARENT=%%~fI"
set "OUT=%PARENT%\TRANSFER_PACKAGE"
set DST=%OUT%\Admin_Attendance

echo Copying project (code, rules, master data, launchers) to %DST% ...
robocopy "%SRC%" "%DST%" /E /XD .venv .git .claude .pytest_cache __pycache__ HandsoffData031026 Client_Admin_Attendance 01_DROP_FOLDER 02_RAW_ARCHIVE 04_REFERENCE_SOURCES 05_CAPTURED_DATA 06_REPORTS 07_LOGS _backup /XF *_real.py .env users.json approvals.csv HandsoffData031026.zip ~$* /NFL /NDL /NJH /NJS
if errorlevel 8 (echo [PROBLEM] copy failed & pause & exit /b 1)

echo Creating the empty working folders ...
for %%D in (01_DROP_FOLDER 02_RAW_ARCHIVE 04_REFERENCE_SOURCES 05_CAPTURED_DATA 06_REPORTS 07_LOGS) do mkdir "%DST%\%%D" 2>nul
if exist "%SRC%\01_DROP_FOLDER\READ_ME_FIRST.txt" copy /y "%SRC%\01_DROP_FOLDER\READ_ME_FIRST.txt" "%DST%\01_DROP_FOLDER\" >nul

if exist "%PARENT%\DailyNote\CLAUDE.md" (
  mkdir "%OUT%\DailyNote" 2>nul
  copy /y "%PARENT%\DailyNote\CLAUDE.md" "%OUT%\DailyNote\" >nul
)

echo.
echo Done. Move the whole folder  %OUT%  to the Client PC:
echo   %OUT%\Admin_Attendance   -^>  <Client folder>\Admin_Attendance
echo   %OUT%\DailyNote          -^>  <Client folder>\DailyNote
echo Then on that PC: install Python, create the .venv, and run CHECK_INSTALL.bat (see the manual).
echo.
dir /b "%DST%"
pause
