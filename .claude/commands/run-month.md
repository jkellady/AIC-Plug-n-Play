---
description: Run the attendance check for one month, e.g. /run-month 2026-05
argument-hint: <YYYY-MM>
---
Run the attendance check for the single month in `$ARGUMENTS`.

Before running:
1. `$ARGUMENTS` must look like `2026-05` (four digits, dash, two digits). If not, stop and say so.
2. `01_DROP_FOLDER\$ARGUMENTS` must exist and contain clock export files (`*-All_Report*.xls`), one schedule workbook and one HR record workbook. List counts only. If something is missing, say which and stop.
3. Say in one line which month you are about to run and that the run is read-only on all inputs.

Run: `cmd.exe /c "run_daily.bat --month $ARGUMENTS --force"`

After running:
- Read `06_REPORTS\LATEST\AGGREGATE_SUMMARY.json` and report status, red / amber / green, info, and anything under `missing`. Quote numbers exactly.
- Read `06_REPORTS\LATEST\DAILY_NOTE.txt` and show it.
- Tell the user the Review page and workbook are in `06_REPORTS\LATEST`.
- If the status is PARTIAL or FAILED, explain in plain English from `missing` and `07_LOGS\daily_task.log` (last 20 lines). Do not retry more than once.
