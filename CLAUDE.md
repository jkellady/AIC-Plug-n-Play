# CLAUDE.md: AIC Plug-n-Play attendance check

Read this fully before doing anything. Then read `README.md` and `03_MASTER_DATA/rules.md`.

## 1. What this is
An automatic attendance check for a multi-outlet retail client. Admin drops raw clock-machine exports into a folder. The system reads them, applies the client's attendance rules, and produces the **Attendance Flag Report** (.xlsx) and a local HR web app. A person reviews every flag.

- **Phase 1 is read-only.** The check never writes into HR record tabs, never changes salary, leave or any staff record.
- Target machine: a standalone Windows PC at the client. Keep it portable (section 7).

## 2. Hard limits (never break these)
1. Never modify, rename or delete anything in `01_DROP_FOLDER`, `02_RAW_ARCHIVE` or `04_REFERENCE_SOURCES`. Moving a processed raw file from 01 to 02 is the only allowed change, and only in the runner.
2. Never write to the HR record workbook or the management schedule.
3. All counting, time maths and flag decisions are plain Python. Never use a language model for arithmetic or to decide a flag.
4. Do not invent rules. If a rule is not in `rules.toml`, add it to the Pending list and ask the client.
5. Staff data is personal data. Never commit it to git, never paste it into prompts beyond what is needed, never send it to any outside service. Never put real names, IC numbers or punch data in code, tests, docs or logs; tests use made-up data only.
6. Output only to `06_REPORTS/<YYYY-MM>/` and `07_LOGS/`.

## 3. Folder map
```
01_DROP_FOLDER/   Admin drops raw files (read-only to us)
02_RAW_ARCHIVE/<YYYY-MM>/clock_exports/   raw files as received
03_MASTER_DATA/          outlets.csv, staff_aliases.csv, schedule_codes.csv, flag_catalogue.csv, rules.toml, rules.md, pending_questions.toml
04_REFERENCE_SOURCES/<YYYY-MM>/   HR record workbook + management schedule (read-only)
05_CAPTURED_DATA/<YYYY-MM>/       punches_long.csv, clock_roster.csv, capture_check.csv
06_REPORTS/<YYYY-MM>/    Attendance flag report
07_LOGS/                 run_log.csv, webapp/ (users.json hashes, app logs)
```
Code: `src/` (capture, masterdata, rules, schedule, dayengine, hrbook, hrcompare, punches, daily, agents/, webapp/), `scripts/`, `tests/`, `docs/`.

## 4. Inputs
- **Clock exports** `<OUTLET>-All_Report_<date>.xls` (old .xls, read with `xlrd`). `Attend. Logs` is the source of truth for punches; `Attendance summary` is used only to validate the capture.
- **Management schedule** (.xlsx): outlet block headers, staff names with an hours column, schedule codes per day (see `schedule_codes.csv`).
- **HR record workbook** (.xlsx): one tab per person with IN, OUT, hours, OT and pasted raw punches. Compare, never write.
- Clock names map to staff IDs only through `03_MASTER_DATA/staff_aliases.csv` (gitignored; template `staff_aliases.example.csv`). Unmapped clock names are flagged, never guessed.

## 5. Rules
Authoritative copy: `03_MASTER_DATA/rules.toml` (`src/rules.py` loads it; `rules.md` is the plain-language summary). Each rule has a status: CONFIRMED (given by the client), ASSUMED (working default), OPEN (no safe default). A flag's colour comes from `rules.is_confirmed()`, never hard-coded. Changing a rule is a data edit, not a code change. Open questions for the client go in `pending_questions.toml` and the report's Pending sheet.

## 6. Output: Attendance Flag Report
Sheets: Summary, Pending, Flags (`# | Severity | Flag | Person | Home outlet (tab) | Date | Details | Remark | Remark type | Ref`), Day check (one row per person per day), Aliases. Severity: Blocker / Check / Info. Remark type: Possible error (red) / Pending question (amber) / Confirmed rule (green). Hours are decimal hours.

## 7. Portability
- Every path comes from `config.toml`. No hard-coded `C:\...` anywhere.
- Windows-first (`pathlib`), no Unix-only tools. Python 3.12+.
- Git tracks code, config and master-data templates only (see `.gitignore`).
- No API key or login in the project. Phase 1 needs no Claude API call.

## 8. Web app
`src/webapp` on 127.0.0.1:8765 (LAN mode only with `--lan` or `AIC_LAN=1`). Roles: Master, CEO, HRM, Admin, Viewer. PINs/passwords are stored as hashes in `07_LOGS/webapp/users.json`, never in any other file. Opened by `scripts/launch_app.ps1`; desktop icon by `scripts/make_desktop_shortcut.ps1 -Create`. Guide: `docs/WEBAPP_GUIDE.md`.

## 9. How to work with the user
- Short, direct answers and action.
- When blocked by a missing client answer, list it in Pending, continue with the stated assumption, and say so in one line.
- Before large changes, state the plan in 3 to 5 lines. Never claim something works without running its test.

## Document control
| Item | Value |
|---|---|
| Date of creation | 2026-10-28 |
| Version | 1 |
| Supersedes | None (first version of the client-neutral copy, derived from a client project's CLAUDE.md with all client data removed) |
