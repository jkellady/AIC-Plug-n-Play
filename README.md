# AIC Plug-n-Play: Attendance check

A ready-to-deploy attendance check for multi-outlet retail clients. Admin drops raw clock-machine exports into a folder; the system applies the client's attendance rules (plain Python, no AI for any count or flag) and produces an Attendance Flag Report plus a local HR web app.

This copy holds no client data. Every name, outlet and rule value below is a template.

## Set up for a new client
1. Create a Python 3.12+ virtual environment in `.venv` and `pip install -r requirements.txt`.
2. Run `CREATE_FOLDERS.bat` to make the data folders (01, 02, 04, 05, 06, 07).
3. Fill in `03_MASTER_DATA/`:
   - `outlets.csv`: one row per outlet (the OUT1..OUT5 rows are examples).
   - `staff_aliases.csv`: copy `staff_aliases.example.csv`, one row per person. It is gitignored; never commit it.
   - `schedule_codes.csv`: the client's roster codes (examples included).
   - `rules.toml`: review every rule. Values are a starting set; set each `status` to CONFIRMED only after the client confirms it.
4. Create the first web-app users with `scripts/create_first_users.py`, then open the app with `scripts/launch_app.ps1`.
5. Run `CHECK_INSTALL.bat`, then `python -m pytest` (all tests use made-up data).

| Folder | What lives here |
|---|---|
| 00_START_HERE | Staff guide for the web app |
| 01_DROP_FOLDER | Admin drops the raw clock files here |
| 02_RAW_ARCHIVE | Raw files kept exactly as received, by month |
| 03_MASTER_DATA | Outlets, staff aliases, schedule codes, rules, flag catalogue |
| 04_REFERENCE_SOURCES | HR record workbook and management schedule (read-only inputs) |
| 05_CAPTURED_DATA | Raw clock data turned into one clean table, plus checks |
| 06_REPORTS | The Attendance Flag Report |
| 07_LOGS | Run log, web-app users and logs |
| scripts, docs | `scripts\launch_app.ps1` opens the web app (Viewer / HRM / Admin / CEO / Master); `docs\WEBAPP_GUIDE.md` explains it |

Folders 01, 02, 04, 05, 06 and 07 hold personal data and are gitignored.

## Document control
| Item | Value |
|---|---|
| Date of creation | 2026-10-28 |
| Version | 1 |
| Supersedes | None (first version of the client-neutral copy) |
