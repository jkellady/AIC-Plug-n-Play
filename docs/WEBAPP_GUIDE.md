# HR Attendance web app: guide

## Document control
| Item | Value |
|---|---|
| Date of creation | 2026-10-18 |
| Version | 4 (2026-10-19: forgot-password steps, master sign-in security view, unlock, user audit log; v3 added the role model, Users tab, rules editing, staff/outlet records, logs, in-app report view, Help pop-up) |
| Supersedes | Version 3 of this same file (earlier 2026-10-19; no copy kept), version 2 (2026-10-18, LAN section) and version 1. |

Written from reading `src\webapp`, `scripts` and `tests` on 2026-10-18. No counts are quoted here because none were run for this document. For current numbers, run the tests and read `AGGREGATE_SUMMARY.json`.

## 1. What it is
A small local web app that shows the attendance check in a browser window. It reads the same engine output as the `.bat` launchers (`src.daily.run_month`) and shows overview, exceptions, staff, outlets, reports, rules and log files.

- Standard-library Python server, `src\webapp\server.py`. It binds **127.0.0.1 only** (this PC only; it refuses any other address). Default port 8765.
- Screens are static files in `src\webapp\static` (`index.html`, `app.js`, `app.css`). It has a web manifest and service worker, so Edge can install it as an app.
- Data comes from `src\webapp\queries.py` (read-only). Help search comes from `src\webapp\help_search.py`. Login is `src\webapp\auth.py`.
- The app does not decide flags. Counting and flag decisions stay in plain Python in the engine. HR decides every flag.

## 2. Roles and permissions
Five roles. Least privilege: a role gets only what it needs. The server checks the permission table (`PERMS` in `src\webapp\auth.py`) on every request; the menu only hides what you cannot use. Roles are not "levels": read the table.

| Role | Who | Short description |
|---|---|---|
| **Master Admin** | Developer (account `master000`, the only one) | Developer and owner. All access. Cannot be edited or deleted by anyone else |
| **CEO** | Client owner | Rules, users, read-only staff and outlets, reports |
| **Admin** | Client Admin | Runs scans, uploads, reports, staff and outlet records, logs, IT help |
| **HRM** | HR Attendance | Exceptions, full reports, adds Viewer and Admin users |
| **Viewer** | Client manager | Overview, Review pages, rules (read) |

| Permission | Master | CEO | Admin | HRM | Viewer |
|---|:-:|:-:|:-:|:-:|:-:|
| Overview; reports list; rules (read); help bot | yes | yes | yes | yes | yes |
| View/download Review page (`Review_*.html`, names hidden) | yes | yes | yes | yes | yes |
| View/download flag workbook (`.xlsx`, per-person flags) | yes | yes | yes | yes | **no** |
| Exceptions | yes | yes | yes | yes | no |
| Staff and Outlets: view | yes | yes | yes | no | no |
| Staff and Outlets: add / edit | yes | no | yes | no | no |
| Attendance scan, upload, create reports | yes | no | yes | no | no |
| System health; Log files (view, download); IT diagnosis and repair | yes | no | yes | no | no |
| Open reports / logs / drop folder in File Explorer (on the app PC only) | yes | no | yes | no | no |
| Sign-in security view (read-only) | yes | no | no | no | no |
| Unlock a locked-out user | yes (anyone) | managed roles | no | managed roles | no |
| Rules: add / edit | yes | yes | no | no | no |
| Office network (LAN): see state | yes | yes | yes | no | no |
| Office network (LAN): turn on/off | yes | no | no | no | no |
| Users tab: add, reset password, edit, delete | yes | yes | no | yes | no |
| Edit own name | yes (only) | no | no | no | no |
| Change own password (Settings) | yes | yes | yes | yes | yes |

Who may manage which accounts (`ASSIGNABLE` in `auth.py`):
- Master Admin: CEO, Admin, HRM, Viewer.
- CEO: Admin, HRM, Viewer.
- HRM: Admin, Viewer. **Decision for the developer:** HRM can create Admin users; remove `admin` from the HRM list if that is too wide.
- Nobody manages a role equal to or above their own, nobody manages their own account here, and nobody but the Master Admin touches the Master Admin account (not even to reset the password: the Master Admin changes it in Settings, or the developer runs `scripts\set_user_password.py`).
- Flag workbook for Viewer (council item S2): decided as **HRM and above only**, because the workbook lists flags per person and some details quote names. Viewer still sees the Review page, which hides those details.

## 3. Logging in, users and passwords
- Users are stored only in `07_LOGS\webapp\users.json` as salted scrypt hashes. **There are no default accounts.** Until the developer creates users, login says "no users set up".
- **Passwords can never be shown.** Only hashes are stored. The Users tab shows "Set", "Set, must change" or "Not set", never the password. A reset means typing a new *initial* password; the person must change it at their first sign-in. No plain password is ever logged or stored.
- the developer can still create or reset an account in a terminal: `.venv\Scripts\python.exe scripts\set_user_password.py` (role, username, password typed twice, never shown). The role `master` is accepted only for username `master000`.
- **Settings > Users** (Master Admin, CEO, HRM): list with name, username, role, created by/at, last sign-in and password status; **+ Add user** (name, username, role, initial password), **Edit** (name, role, reset initial password), **Delete**.
- **Change password** is only under **Settings**. A user who must change a temporary password is sent to Settings first.
- Minimum password length is 10. 5 wrong attempts lock that sign-in for 5 minutes (and 10 per address). A session ends after 30 minutes idle, or 12 hours in total.
- **Forgot password.** No email or SMS (out of scope). The login page shows "Forgot password?" with these steps: the person asks a user manager (Master Admin, CEO or HRM); the manager opens Settings > Users > Edit and types a one-time temporary password (never shown back, never logged); the person signs in with it and must choose their own at once. Managers can reset only roles they manage (section 2). The Master Admin account cannot be reset by anyone else: on the app PC, the developer runs `scripts\set_user_password.py` (username `master000`, type the new password twice). No file-marker or web recovery path exists, on purpose: it would be an unauthenticated way in.
- **Sign-in security view** (Settings, Master Admin only, read-only): per user, role, password set / not set, last password change, last sign-in, locked out, must change. **Unlock** clears a lock-out (also in the Users table for managers). Hashes and salts are never sent to the browser. Plain passwords do not exist anywhere. `users.json` cannot be listed, viewed or downloaded in the app, and is excluded from backups and the transfer copy (`07_LOGS` and `users.json` are in the exclude lists). If the developer wants the raw file, he opens `07_LOGS\webapp\users.json` on the app PC himself; it holds only hashes.
- **User audit log** `07_LOGS\webapp\user_audit.csv`: when, who, action (added, updated with what changed, unlocked, deleted, own password changed), target. Never a password, hash or salt.
- Migration: existing accounts keep their hashes. The old `master000` record (role `admin`) is read as `master`; `viewer`, `hrm`, `admin` and others are unchanged. Nothing is deleted.
- Client people never share a password in chat, email or notes.

## 4. Opening it
**Desktop icon (normal way).** The icon is named "HR Attendance". It runs `scripts\launch_app.ps1`, which:
1. reuses the server if it is already running, otherwise starts it hidden;
2. waits up to 20 seconds for it to answer;
3. opens Edge (or Chrome if Edge is missing) in a plain app window with its own profile.

If it cannot start, a message box names `07_LOGS\webapp` as the folder to send to the developer.

The icon is made by `scripts\make_desktop_shortcut.ps1`. Run with no switch it only prints what it would create. the developer adds `-Create` to make it. **State on 2026-10-18: the icon had not been created yet.**

**From PowerShell** (the developer):
```
powershell -ExecutionPolicy Bypass -File scripts\launch_app.ps1            # start and open
powershell -ExecutionPolicy Bypass -File scripts\launch_app.ps1 -WhatIf    # print only
powershell -ExecutionPolicy Bypass -File scripts\launch_app.ps1 -Stop      # stop the server
```
`-Stop` ends only the process whose id is recorded in `07_LOGS\webapp\server.pid`, and only if it is a Python process. It does not touch anything else.

**Edge "Install this site as an app" (alternative).** With the server running, open `http://127.0.0.1:8765/` in Edge, then menu (three dots) > Apps > **Install this site as an app**. Edge makes its own icon and window. The server must still be running; the installed app does not start it. Use the desktop icon, or ask the developer, if the page says it cannot connect.

## 4b. Office network (LAN) access: opt-in, off by default
the developer approved (2026-10-18) the relevant people opening the app from their own desk PCs on the same office Wi-Fi/LAN. By default the app is still this-PC-only.
- Turn on, by the developer in an **Administrator** PowerShell: `scripts\enable_lan_access.ps1` (no switch = print the plan and the URL only), then `-Apply`. It adds a firewall rule "HR Attendance (LAN)" allowing inbound TCP 8765 from the **local subnet only** (Private/Domain profiles), and writes the marker `07_LOGS\webapp\lan.enabled`. `-Remove` prints what would be removed; `-Remove -Apply` removes it.
- Rules for LAN mode: login is always required; private-network clients only; plain HTTP (not encrypted), so use it only on the office network, never over public Wi-Fi.
- Staff URL: `http://<this PC's address>:8765/` in Edge, then sign in with your own account. The PC must be on and the app running (desktop icon, section 4).
- Boundary: viewing through the app on the office network is allowed when LAN mode is on. Copying staff files off the PC is still forbidden.
- State when written: the script exists; the server code did not yet mention LAN mode when checked, and the script had not been applied. Verify before telling Client it works.

## 5. Logs and files it touches
| Path | What |
|---|---|
| `07_LOGS\webapp\access.log` | One line per request, written by the server |
| `07_LOGS\webapp\server.out.log`, `server.err.log` | Server output when started by the launcher |
| `07_LOGS\webapp\server.pid` | Process id, used by `-Stop` |
| `07_LOGS\webapp\lan.enabled` | Marker that LAN mode was switched on |
| `07_LOGS\webapp\users.json` | Login records (hashes only). Keep private. Never shown in the Log files page |
| `07_LOGS\webapp\user_audit.csv` | Who added, changed, unlocked or deleted which user, and when (no secrets) |
| `07_LOGS\webapp\registry.json` | Staff and outlet records added or edited in the app (with created/edited by and at). Directory only: the check does not read it |
| `07_LOGS\webapp\rules_audit.csv`, `rules_backups\` | Who changed which rule and when; a copy of `rules.toml` before each edit |
| `07_LOGS\webapp\edge-profile\` | The app window's browser profile |
| `07_LOGS\it\health_latest.json` | Read by the System health page |

Reports are read from `06_REPORTS`. Reports can be viewed in the app (eye icon) or downloaded (arrow icon): the Review page (`.html`) is shown in a sandboxed frame without scripts; the flag workbook (`.xlsx`) is shown as plain tables (first rows of each sheet) and can be downloaded in full. Per the hard limits, output goes only to `06_REPORTS` and `07_LOGS`, with one approved exception: rule edits write `03_MASTER_DATA\rules.toml` (see 5b).

### 5b. Features added 2026-10-19
- **Rules** page: all rules in a table (status, value, question, where used). Master Admin and CEO get **+ Add rule** and an edit icon. An edit changes only the named rule (text edit, comments kept), is checked with the same validation as `python -m src.rules check` before it is saved, and refuses a change that would touch other rules or leave a CONFIRMED rule without a source. Each edit records who and when (in the rule, in a `[[changes]]` entry and in `rules_audit.csv`) and keeps a backup. New rules start "not built yet": no check reads them until the developer wires them in.
- **Staff** and **Outlets** pages (Admin and Master Admin edit; CEO views): table with **+** and edit; each record shows created by/at and edited by/at. These records live in `registry.json`. Editing a master-data row saves an override there and never changes `03_MASTER_DATA` files, the HR workbook or the schedule. **Decision for the developer:** these edits do not yet change what the attendance check uses.
- **Log files** (Admin, Master Admin): list with a From/To date range, view and download. Only `.log`, `.csv`, `.txt`, `.json` files; `users.json`, `registry.json`, `FIRST_LOGIN.txt`, uploads, caches and the browser profile are never listed.
- **Help** (menu button, pop-up): (a) a chat over the knowledge base (`help_search`, no model, quotes rule status such as ASSUMED); (b) IT diagnosis and repair for Admin and Master Admin, using the IT specialist: *Run diagnosis* (read-only health checks), *Show proposed repairs* (nothing changes), then *Approve and repair* which needs your password again and applies only the specialist's whitelisted safe fixes.
- **Office network access** can be turned on or off only by the Master Admin (password re-typed). Others see the state.
- Security: every POST must come from the app's own page (Origin or Sec-Fetch-Site check) and use `Content-Type: application/json`; the Host check, CSRF token, lockouts and security headers are unchanged. Server error text is never sent to the browser. `server.py --port N` is accepted.

## 6. Plain-English steps for Client staff
See `00_START_HERE\WebApp_Guide.html` (same content, printable).

## 7. State of the build (2026-10-19)
- **Present in the repo:** server, auth (five roles, permission table), queries, help search, rules editor (`rules_edit.py`), directory records (`registry.py`), static screens, launcher scripts, and tests `tests\test_webapp_*.py` (new: `test_webapp_roles.py`).
- **Not yet done:** desktop icon not created; the Master Admin must run the app against the real users file and try each role once. **Open directory** = buttons that open the reports, logs or drop folder in File Explorer on the app PC (fixed list, only from that PC, Admin and Master Admin).
- Test results are not quoted here; run `.venv\Scripts\python.exe -m pytest -q` for the current numbers.
- The upload, scan and report-generate buttons call modules in `src\agents` (`drop_scanner`, `dashboard`). If a module is missing, the server answers "not available yet". Uploads still land in `07_LOGS\uploads\staging` (council item S6, open).
- Git: local only; nothing was committed for this change.

## 8. Rules that still apply
Hard limits in `C:\Client\CLAUDE.md` section 5 are unchanged. The web app does not approve, pay or send anything. Staff files stay on this PC.
