---
description: Show where the attendance check stands (read-only)
---
Show the current state of the attendance check. Change nothing.

1. Read `C:\Client\CLAUDE.md` section 7 for the last recorded state.
2. List the month folders in `01_DROP_FOLDER` with a count of files in each (counts only, no file names).
3. Show the last 5 lines of `07_LOGS\run_log.csv`.
4. Read `06_REPORTS\LATEST\AGGREGATE_SUMMARY.json` and report month, status, red / amber / green, info, staff with a clock file, and the number of open questions. Quote numbers exactly.
5. Run `git status --short` and `git log --oneline -3`.

Reply with one small table (item, value, source file) and a final line "Needs attention:" listing anything odd, such as an empty month folder, a PARTIAL or FAILED status, or uncommitted changes.
