@echo off
rem Client install check. Read-only: it only looks, it changes nothing.
setlocal
cd /d "%~dp0"
set FAIL=0
echo ===== Client attendance: install check =====
echo Folder: %CD%
echo.
echo -- Folders
for %%D in (00_START_HERE 01_DROP_FOLDER 02_RAW_ARCHIVE 03_MASTER_DATA 04_REFERENCE_SOURCES 05_CAPTURED_DATA 06_REPORTS 07_LOGS src) do (
  if exist "%%D\" (echo [OK] %%D) else (echo [MISSING] %%D & set FAIL=1)
)
echo.
echo -- Files
for %%F in (config.toml run_daily.bat RUN_NOW.bat 03_MASTER_DATA\rules.toml 03_MASTER_DATA\staff_aliases.csv src\daily.py) do (
  if exist "%%F" (echo [OK] %%F) else (echo [MISSING] %%F & set FAIL=1)
)
echo.
echo -- Python
if exist ".venv\Scripts\python.exe" (
  echo [OK] .venv found
  ".venv\Scripts\python.exe" --version
  ".venv\Scripts\python.exe" -c "import pandas, openpyxl, xlrd; print('[OK] libraries: pandas, openpyxl, xlrd')" 2>nul || (echo [PROBLEM] libraries missing: run  .venv\Scripts\pip install -r requirements.txt & set FAIL=1)
  echo.
  echo -- Rules
  ".venv\Scripts\python.exe" -m src.rules check || set FAIL=1
) else (
  echo [MISSING] .venv  - create it:  python -m venv .venv   then   .venv\Scripts\pip install -r requirements.txt
  set FAIL=1
)
echo.
echo -- Optional: Claude note step
where claude >nul 2>&1 && (echo [OK] claude found) || (echo [INFO] claude not found: the plain template note will be used)
if exist "%~dp0..\DailyNote\CLAUDE.md" (echo [OK] DailyNote\CLAUDE.md) else (echo [INFO] DailyNote\CLAUDE.md missing: the plain template note will be used)
echo.
echo -- Drop folder
dir /b /ad "01_DROP_FOLDER" 2>nul
echo.
if "%FAIL%"=="0" (echo RESULT: ALL CHECKS PASSED) else (echo RESULT: PROBLEMS FOUND - send a photo or copy of this window to JK Zentra)
echo.
pause
