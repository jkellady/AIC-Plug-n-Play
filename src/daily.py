"""Daily runner (step 6): a draft trial report, not the final Flag Report.

Orchestrates only committed, tested modules (capture, masterdata, rules,
schedule, dayengine, hrbook, hrcompare). No overtime (that is step 4e's
own module, not wired in here).

Read-only on every input: this runner never moves, renames, writes into
or deletes anything under 01_DROP_FOLDER, the schedule workbook or
the HR workbook. Every input's size and mtime is recorded before and
after the run in 07_LOGS/run_log.csv; if anything changed, the run is
FAILED, not reported as a success built on a moving target.

Status: OK (ran fully), PARTIAL (schedule and/or HR workbook missing, or
a month-mismatch was found; ran whatever was possible), NOTHING_NEW (the
manifest says no input changed since the last run), FAILED (an error
stopped the run; nothing but a plain sentence reaches the user -- the
traceback goes only to 07_LOGS/daily_task.log).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
from html import escape as _esc
import sys
import time
import tomllib
import traceback
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill

from src.capture import capture_folder
from src.dayengine import DayFlag, build_day_check
from src.hrbook import read_hr_workbook
from src.hrcompare import compare_hr_with_system
from src.masterdata import Flag, load_master_data
from src.rules import load_rules
from src.roster_gen import generate_roster
from src.schedule import parse_schedule_workbook

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DROP_DIR = PROJECT_ROOT / "01_DROP_FOLDER"
MASTER_DATA_DIR = PROJECT_ROOT / "03_MASTER_DATA"
REPORTS_DIR = PROJECT_ROOT / "06_REPORTS"
LOGS_DIR = PROJECT_ROOT / "07_LOGS"
MANIFEST_PATH = LOGS_DIR / "processed_manifest.json"
RUN_LOG_PATH = LOGS_DIR / "run_log.csv"
DAILY_TASK_LOG_PATH = LOGS_DIR / "daily_task.log"
APPROVALS_PATH = MASTER_DATA_DIR / "approvals.csv"
PENDING_QUESTIONS_PATH = MASTER_DATA_DIR / "pending_questions.toml"
MAX_PENDING_QUESTIONS_SHOWN = 5

CLOCK_EXPORT_GLOB = "*-All_Report*.xls"

ROLLUP_FLAG_NAMES = {"Stayed after scheduled end", "Punched at another outlet"}

# One flag per person per month for these types (were one flag per day).
ROLLUP_PER_PERSON_FLAGS = {
    "Outlet differs between schedule and record tab",
    "Missing clock-in",
    "Missing clock-out",
    "Missing clock-in and clock-out",
}
OUTLET_DETAIL_RE = re.compile(r"Schedule block '(?P<sched>.*?)'; record tab outlet '(?P<tab>.*?)'\.")

FILL_RED = PatternFill("solid", fgColor="F8CBAD")
FILL_AMBER = PatternFill("solid", fgColor="FFE699")
FILL_GREEN = PatternFill("solid", fgColor="C6E0B4")


# Staff-specific remarks: one Info flag per month, logged only. Info flags are
# never counted as red, amber or green, so these can never change a colour.
SPECIAL_REMARKS = {
    "STAFF_N": "Status unknown (shareholder or salaried). Calculated normally, to be confirmed.",
}


def special_remark_flags(staff_rows: dict[str, dict], excluded_ids: set[str]) -> list[DayFlag]:
    """One Info flag per month for each SPECIAL_REMARKS id that is in the
    staff list and not excluded. staff_rows maps staff id to its alias row.
    """
    flags: list[DayFlag] = []
    for sid, remark in SPECIAL_REMARKS.items():
        if sid in excluded_ids or sid not in staff_rows:
            continue
        flags.append(DayFlag(
            severity="Info", flag="Status unknown (special remark)", person=sid,
            home_outlet_tab=staff_rows[sid].get("home_outlet_tab", ""), date="Month",
            details=remark, remark=remark, remark_type="Pending question", ref="",
        ))
    return flags


def _outlet_pair(flag: DayFlag) -> tuple[str, str]:
    match = OUTLET_DETAIL_RE.search(flag.details)
    if not match:
        raise ValueError(f"Cannot read the outlet pair from flag details for {flag.flag!r}; rollup stopped.")
    return match.group("sched"), match.group("tab")


def rollup_flags(flags: list[DayFlag]) -> list[DayFlag]:
    """Collapse per-day flags of ROLLUP_PER_PERSON_FLAGS into one flag per
    person per flag type. Other flags pass through unchanged, in order. The
    sum of .days over the rolled flags equals the number of per-day flags
    rolled up, so no day is lost or double-counted.
    """
    groups: dict[tuple[str, str], list[DayFlag]] = {}
    out: list[object] = []
    for f in flags:
        if f.flag not in ROLLUP_PER_PERSON_FLAGS:
            out.append(f)
            continue
        key = (f.person, f.flag)
        if key not in groups:
            groups[key] = []
            out.append(key)  # placeholder keeps the first-seen position
        groups[key].append(f)

    result: list[DayFlag] = []
    for item in out:
        if not isinstance(item, tuple):
            result.append(item)
            continue
        group = groups[item]
        first = group[0]
        dates = sorted(f.date for f in group)
        span = f"{dates[0].isoformat()} to {dates[-1].isoformat()}"
        if first.flag == "Outlet differs between schedule and record tab":
            pair_days: dict[tuple[str, str], list] = {}
            for f in group:
                pair_days.setdefault(_outlet_pair(f), []).append(f.date)
            parts = []
            for (sched, tab), pdates in sorted(pair_days.items()):
                pdates.sort()
                parts.append(
                    f"schedule {sched!r} vs record tab {tab!r}: {len(pdates)} day(s), "
                    f"{pdates[0].isoformat()} to {pdates[-1].isoformat()}"
                )
            details = "; ".join(parts) + "."
        else:
            details = f"{len(group)} day(s), {span}."
        result.append(DayFlag(
            severity=first.severity, flag=first.flag, person=first.person,
            home_outlet_tab=first.home_outlet_tab, date="Month", details=details,
            remark=first.remark, remark_type=first.remark_type, ref=first.ref,
            days=sum(f.days for f in group), span=span,
        ))
    return result


# --------------------------------------------------------------------- IO

def _file_signature(path: Path) -> dict:
    stat = path.stat()
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"size": stat.st_size, "mtime": stat.st_mtime, "sha256": h.hexdigest()}


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def _save_manifest(manifest: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _classify_xlsx(path: Path) -> str:
    """Return 'hr', 'schedule' or 'unknown' by content, read-only."""
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return "unknown"
    try:
        for name in wb.sheetnames:
            if wb[name].cell(8, 4).value == "IN":
                return "hr"
        ws0 = wb[wb.sheetnames[0]]
        if ws0.cell(2, 1).value == "Date" and ws0.cell(2, 2).value == "Day":
            return "schedule"
        return "unknown"
    finally:
        wb.close()


def _classify_month_folder(month_dir: Path) -> tuple[list[Path], Path | None, Path | None, list[str]]:
    clock_files = sorted(month_dir.glob(CLOCK_EXPORT_GLOB))
    schedule_path = None
    hr_path = None
    needs_attention: list[str] = []
    for path in sorted(month_dir.glob("*.xlsx")):
        kind = _classify_xlsx(path)
        if kind == "hr":
            if hr_path is not None:
                needs_attention.append(
                    f"{path.name}: looks like another HR workbook; only {hr_path.name} is used."
                )
            else:
                hr_path = path
        elif kind == "schedule":
            if schedule_path is not None:
                needs_attention.append(
                    f"{path.name}: looks like another schedule workbook; only {schedule_path.name} is used."
                )
            else:
                schedule_path = path
        else:
            needs_attention.append(f"{path.name}: could not tell if this is the schedule or the HR workbook.")
    return clock_files, schedule_path, hr_path, needs_attention


def _write_needs_attention(month_dir: Path, lines: list[str]) -> None:
    # Written to the month's report folder, never the drop folder (folder 01
    # is read-only to us).
    report_dir = REPORTS_DIR / month_dir.name
    if not lines:
        # A clean re-run must not leave an old warning behind (it would be
        # copied to LATEST as well).
        stale = report_dir / "NEEDS_ATTENTION.txt"
        if stale.exists():
            stale.unlink()
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "NEEDS_ATTENTION.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------- one month

def _load_approvals() -> list[dict] | None:
    if not APPROVALS_PATH.exists():
        return None
    import pandas as pd
    df = pd.read_csv(APPROVALS_PATH, dtype=str, keep_default_na=False)
    return df.to_dict("records")


def run_month(month: str, force: bool = False, _test_hook=None) -> dict:
    """Process one YYYY-MM month folder. Returns the result dict used to
    build all outputs. _test_hook is called with (signatures_before,
    paths) right after inputs are read, for tests only.
    """
    year, mon = (int(p) for p in month.split("-"))
    month_dir = DROP_DIR / month

    manifest = _load_manifest()
    prior = manifest.get(month, {})

    if not month_dir.exists():
        return {"status": "FAILED", "month": month, "missing": [],
                "error": f"No folder found for {month}; expected {month_dir}."}

    clock_files, schedule_path, hr_path, needs_attention = _classify_month_folder(month_dir)
    _write_needs_attention(month_dir, needs_attention)

    tracked_paths = list(clock_files)
    if schedule_path:
        tracked_paths.append(schedule_path)
    if hr_path:
        tracked_paths.append(hr_path)

    # the manifest and run-log key every tracked file by name alone; two
    # files sharing a name would silently collide in that keying, masking
    # a real change to one of them. The duplicate is kept out of the
    # signature set (only its first occurrence is tracked) and the run
    # still produces a report, flagged PARTIAL, rather than being blocked.
    names_seen: dict[str, Path] = {}
    duplicate_names: list[str] = []
    deduped_tracked_paths: list[Path] = []
    for p in tracked_paths:
        if p.name in names_seen:
            duplicate_names.append(p.name)
        else:
            names_seen[p.name] = p
            deduped_tracked_paths.append(p)
    if duplicate_names:
        lines = [f"{n}: appears more than once among this month's tracked files." for n in duplicate_names]
        needs_attention = needs_attention + lines
        _write_needs_attention(month_dir, needs_attention)
    tracked_paths = deduped_tracked_paths

    signatures_before = {p.name: _file_signature(p) for p in tracked_paths}

    if not force and not duplicate_names and prior.get("files") == signatures_before:
        return {
            "status": "NOTHING_NEW", "month": month,
            "missing": prior.get("missing", []),
            "prior_status": prior.get("last_status"),
        }

    missing: list[str] = []
    if duplicate_names:
        # counts only: file names can carry staff or outlet names and this
        # text reaches AGGREGATE_SUMMARY.json and the daily note.
        missing.append(f"{len(duplicate_names)} duplicate file name(s) found; see NEEDS_ATTENTION.txt.")
    if not clock_files:
        missing.append(f"No clock export files found for {month}; put them in 01_DROP_FOLDER/{month}/.")
    if schedule_path is None:
        missing.append(f"No schedule workbook found for {month}; put it in 01_DROP_FOLDER/{month}/.")
    if hr_path is None:
        missing.append(f"No HR record workbook found for {month}; put it in 01_DROP_FOLDER/{month}/.")

    try:
        master_data = load_master_data(MASTER_DATA_DIR)
        rules = load_rules(MASTER_DATA_DIR / "rules.toml")
        approvals = _load_approvals()

        schedule_rows: list[dict] = []
        if schedule_path is not None:
            schedule_rows, _ = parse_schedule_workbook(schedule_path, master_data)
            if not any(r["date"].year == year and r["date"].month == mon for r in schedule_rows):
                missing.append(f"The schedule workbook has no dates in {month}; check it is the right month's file.")

        punches_long: list[dict] = []
        for cf in clock_files:
            rows, _ = capture_folder(cf.parent, pattern=cf.name)
            punches_long.extend(rows)
        if punches_long:
            off_month = [r for r in punches_long if not r["date"].startswith(month)]
            if off_month:
                missing.append(f"Some clock punches are outside {month}; check the clock files are the right month's exports.")

        day_check_rows: list[dict] = []
        day_engine_flags = []
        if schedule_rows:
            day_check_rows, day_engine_flags = build_day_check(
                schedule_rows, punches_long, master_data, rules, approvals=approvals,
            )

        hr_rows: list[dict] = []
        hr_skipped: list[tuple[str, str]] = []
        hrcompare_flags = []
        if hr_path is not None:
            hr_rows, hr_skipped = read_hr_workbook(hr_path, year, mon)
            if not hr_rows:
                missing.append(f"The HR record workbook has no tabs for {month}; check it is the right month's file.")
            elif day_check_rows:
                hrcompare_flags = compare_hr_with_system(
                    hr_rows, day_check_rows, day_engine_flags, schedule_rows, master_data, rules,
                )

        if _test_hook is not None:
            _test_hook(signatures_before, tracked_paths)

        signatures_after = {p.name: _file_signature(p) for p in tracked_paths}
        if signatures_after != signatures_before:
            return {
                "status": "FAILED", "month": month, "missing": missing,
                "error": "An input file changed while the run was in progress; nothing was written.",
            }

        # Restrict every count to this target month plus month-level flags
        # ("Month"-dated): the schedule carries carry-in/carry-out weeks
        # from the neighbouring months, which must not inflate this
        # month's counts.
        def _in_target_month(d) -> bool:
            return d == "Month" or (hasattr(d, "year") and d.year == year and d.month == mon)

        excluded_staff_ids = set(rules.get("excluded_staff_ids") or [])
        all_flags = [
            f for f in (list(day_engine_flags) + list(hrcompare_flags))
            if _in_target_month(f.date) and f.person not in excluded_staff_ids
        ]
        result_day_total_before = {
            name: sum(f.days for f in all_flags if f.flag == name) for name in ROLLUP_PER_PERSON_FLAGS
        }
        all_flags = rollup_flags(all_flags)
        for name in ROLLUP_PER_PERSON_FLAGS:
            after = sum(f.days for f in all_flags if f.flag == name)
            if after != result_day_total_before[name]:
                raise RuntimeError(f"Rollup of {name!r} changed the day count; nothing written.")
        staff_rows_by_id = {row["id_initials"]: row for _, row in master_data.staff_aliases.iterrows()}
        all_flags = all_flags + special_remark_flags(staff_rows_by_id, excluded_staff_ids)
        month_day_check_rows = [r for r in day_check_rows if r["date"].year == year and r["date"].month == mon]
        hr_skipped = [(name, reason) for name, reason in hr_skipped]

        from src.masterdata import _split_clock_file_names
        included_staff = [
            row for _, row in master_data.staff_aliases.iterrows()
            if row["id_initials"] not in excluded_staff_ids
        ]

        status = "OK" if not missing else "PARTIAL"

        result = {
            "status": status, "month": month, "missing": missing,
            "rules": rules, "all_flags": all_flags, "day_check_rows": month_day_check_rows,
            "hr_skipped": hr_skipped, "master_data": master_data,
            "staff_total": len(included_staff),
            "staff_with_clock_file": len({
                row["id_initials"] for row in included_staff
                if _split_clock_file_names(row["clock_file_names"])
            }),
            "signatures": signatures_after,
        }
        return result
    except Exception:
        DAILY_TASK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DAILY_TASK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.datetime.now().isoformat()} {month} ---\n")
            f.write(traceback.format_exc())
        return {
            "status": "FAILED", "month": month, "missing": missing,
            "error": "Something went wrong while processing this month. "
                     f"See {DAILY_TASK_LOG_PATH} for details, or re-run with --force.",
        }


# ----------------------------------------------------------------- output

def _counts_by_severity_and_remark(flags) -> dict:
    counts_needing_look = {"red": 0, "amber": 0, "green": 0}
    info_total = 0
    remark_to_color = {
        "Possible error": "red", "Pending question": "amber", "Confirmed rule": "green",
    }
    for f in flags:
        if f.severity == "Info":
            info_total += 1
            continue
        color = remark_to_color.get(f.remark_type)
        if color:
            counts_needing_look[color] += 1
    return counts_needing_look, info_total


def _open_questions(rules, flags) -> list[dict]:
    """One row per question ref: its non-confirmed rule ids and how many
    distinct Blocker/Check flags are still "Pending question" because of
    it. "Confirmed rule" flags are excluded even if their ref string still
    carries a (now-confirmed) rule's question code."""
    flags_by_ref: dict[str, set[int]] = {}
    for f in flags:
        if f.severity == "Info" or not f.ref or f.remark_type != "Pending question":
            continue
        for q in [r.strip() for r in f.ref.split(",") if r.strip()]:
            flags_by_ref.setdefault(q, set()).add(id(f))

    rule_ids_by_ref: dict[str, list[str]] = {}
    for rule in rules.all_rules():
        if rule.status == "CONFIRMED":
            continue
        rule_ids_by_ref.setdefault(rule.question, []).append(rule.rule_id)

    out = [
        {
            "ref": ref,
            "rule_ids": sorted(rule_ids),
            "flags_affected": len(flags_by_ref.get(ref, set())),
        }
        for ref, rule_ids in rule_ids_by_ref.items()
    ]
    out.sort(key=lambda r: -r["flags_affected"])
    return out


def load_pending_catalogue(path: Path, rules) -> dict[str, dict]:
    """Load pending_questions.toml and check every option's effect names a
    real rules.toml key. Raises if one doesn't -- never silently ignored."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    catalogue: dict[str, dict] = {}
    for q in raw.get("questions", []):
        ref = q["ref"]
        for opt in q.get("options", []):
            for rule_key in opt.get("effect", {}):
                rules.get(rule_key)  # raises UnknownRuleError if not in rules.toml
        catalogue[ref] = {
            "ref": ref,
            "topic": q["topic"],
            "question": q["question"],
            "opts": [opt["label"] for opt in q.get("options", [])],
        }
    return catalogue


def _pending_questions_for_review(summary: dict, catalogue: dict[str, dict]) -> list[dict]:
    """Open questions that affect at least one flag and are in the
    catalogue, most-affected first, capped at MAX_PENDING_QUESTIONS_SHOWN."""
    shown = [
        {**catalogue[q["ref"]], "flags_affected": q["flags_affected"]}
        for q in (summary.get("open_questions") or [])
        if q["flags_affected"] > 0 and q["ref"] in catalogue
    ]
    shown.sort(key=lambda q: -q["flags_affected"])
    return shown[:MAX_PENDING_QUESTIONS_SHOWN]


def _by_flag_type(flags) -> dict:
    counts: dict[str, int] = {}
    for f in flags:
        counts[f.flag] = counts.get(f.flag, 0) + 1
    return counts


def _per_person_rows(flags) -> list[tuple]:
    counts: dict[tuple, int] = {}
    for f in flags:
        name = f.flag
        if name in ROLLUP_FLAG_NAMES:
            name = "Stayed after scheduled end / punched at another outlet (month total)"
        key = (f.person, name)
        counts[key] = counts.get(key, 0) + 1
    return sorted((person, name, n) for (person, name), n in counts.items())


def _write_trial_workbook(path: Path, month: str, result: dict) -> None:
    rules = result["rules"]
    flags = result["all_flags"]
    day_check_rows = result["day_check_rows"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Read me"
    ws.append(["DRAFT, NOT FINAL. Red = looks like a data error. Amber = depends on a question "
               "still open with Client. Green = a rule Client confirmed."])
    ws.append([f"Month: {month}"])
    ws.append([f"Run time: {datetime.datetime.now().isoformat(timespec='seconds')}"])
    ws.append([])
    ws.append(["Rule", "Status"])
    for rule in rules.all_rules():
        ws.append([rule.rule_id, rule.status])

    ws2 = wb.create_sheet("Per person")
    ws2.append(["Person", "Flag type", "Count"])
    for person, flag_name, n in _per_person_rows(flags):
        ws2.append([person, flag_name, n])

    ws3 = wb.create_sheet("Flags")
    ws3.append(["Severity", "Flag", "Person", "Date", "Details", "Remark", "Remark type", "Ref"])
    remark_to_fill = {
        "Possible error": FILL_RED, "Pending question": FILL_AMBER, "Confirmed rule": FILL_GREEN,
    }
    for f in flags:
        date_text = f.date if isinstance(f.date, str) else f.date.isoformat()
        ws3.append([f.severity, f.flag, f.person, date_text, f.details, f.remark, f.remark_type, f.ref])
        fill = remark_to_fill.get(f.remark_type)
        if fill:
            ws3.cell(ws3.max_row, 7).fill = fill

    ws4 = wb.create_sheet("Day check")
    ws4.append(["Person", "Date", "Day", "Schedule code", "Type", "Shift start", "Shift end",
                "Punches", "Clock outlet", "System In", "System Out"])
    for row in day_check_rows:
        ws4.append([
            row["person"], row["date"].isoformat(), row["day"], row["schedule_code"], row["type"],
            row["shift_start"], row["shift_end"], row["punches"], row["clock_outlet"],
            row["system_in"], row["system_out"],
        ])

    ws5 = wb.create_sheet("Open questions")
    ws5.append(["Ref", "Rule ids", "Flags affected"])
    for q in _open_questions(rules, flags):
        ws5.append([q["ref"], ", ".join(q["rule_ids"]), q["flags_affected"]])

    ws6 = wb.create_sheet("Coverage")
    ws6.append(["Skipped HR tabs"])
    for name, reason in result.get("hr_skipped", []):
        ws6.append([name, reason])
    ws6.append([])
    ws6.append(["Missing"])
    for m in result.get("missing", []):
        ws6.append([m])

    tmp_path = path.with_suffix(".tmp.xlsx")
    wb.save(tmp_path)
    try:
        tmp_path.replace(path)
    except PermissionError:
        stamped = path.with_name(f"{path.stem}_{datetime.datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        tmp_path.replace(stamped)
        result.setdefault("notes", []).append(
            f"{path.name} was open; wrote {stamped.name} instead."
        )


def _write_aggregate_summary(path: Path, month: str, result: dict) -> dict:
    flags = result.get("all_flags", [])
    counts_needing_look, info_total = _counts_by_severity_and_remark(flags)
    rules = result.get("rules")
    summary = {
        "status": result["status"],
        "month": month,
        "run_time": datetime.datetime.now().isoformat(timespec="seconds"),
        "missing": result.get("missing", []),
        "counts": {**counts_needing_look},
        "info_total": info_total,
        "by_flag_type": _by_flag_type(flags),
        "staff_total": result.get("staff_total", 0),
        "staff_with_clock_file": result.get("staff_with_clock_file", 0),
        "open_questions": _open_questions(rules, flags) if rules else [],
    }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _write_daily_note(path: Path, summary: dict) -> None:
    lines = [
        f"Attendance check for {summary['month']}: {summary['status']}.",
        f"Flags needing a look: red {summary['counts']['red']}, "
        f"amber {summary['counts']['amber']}, green {summary['counts']['green']}.",
        f"Info-only notes: {summary['info_total']}.",
        f"Staff checked: {summary['staff_with_clock_file']} of {summary['staff_total']} have a clock file.",
    ]
    if summary["missing"]:
        lines.append("Missing: " + "; ".join(summary["missing"]))
    else:
        lines.append("Nothing missing.")
    lines.append(f"Open questions still affecting flags: {len(summary['open_questions'])}.")
    lines = lines[:8]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Temporary review page (LATEST only). Read-only, self-contained: inline CSS
# and JS, no external resources. Staff names never reach the page: the two
# name-quoting flags show their remark instead of their details, and a clock
# name issue shows a placeholder for its raw clock name.
REVIEW_HIDE_DETAIL_FLAGS = {
    "Same name typed in two different tabs",
    "Tab name differs from the name typed inside it",
    "Unknown schedule code",
}
REVIEW_COLOR = {"Possible error": "red", "Pending question": "amber", "Confirmed rule": "green"}
REVIEW_ORDER = {"red": 0, "amber": 1, "green": 2}

REVIEW_CSS = """
:root { color-scheme: light; }
body { background: #F5F5F5; color: #222; font-family: Arial, Helvetica, sans-serif; margin: 16px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 20px 0 8px; }
.sub { color: #555; font-size: 13px; margin-bottom: 12px; }
.tiles { display: flex; gap: 10px; flex-wrap: wrap; }
.tile { border-radius: 6px; padding: 10px 16px; min-width: 110px; }
.tile b { display: block; font-size: 24px; }
.t-red { background: #F8CBAD; } .t-amber { background: #FFE699; }
.t-green { background: #C6E0B4; } .t-info { background: #DDEBF7; }
.box { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 10px 14px; margin-top: 10px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 14px 0 8px; }
.filters button { border: 1px solid #999; background: #fff; border-radius: 4px; padding: 4px 10px; cursor: pointer; }
.filters button.on { background: #333; color: #fff; }
.filters input, .filters select { padding: 4px 6px; }
table { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; }
th, td { border: 1px solid #ddd; padding: 5px 7px; text-align: left; vertical-align: top; }
th { background: #EAEAEA; }
tr.c-red td:first-child { border-left: 6px solid #E06C3C; }
tr.c-amber td:first-child { border-left: 6px solid #E0B100; }
tr.c-green td:first-child { border-left: 6px solid #6AA84F; }
.muted { color: #666; }

/* Pending answers from Client: different colour and font on purpose */
.pend { background: #F3EEFB; border: 2px solid #5B3E96; border-radius: 10px; padding: 14px 18px 16px; margin: 16px 0; font-family: Georgia, "Times New Roman", serif; }
.pend .kicker { display:inline-block; background:#5B3E96; color:#fff; font: 700 12px Arial, sans-serif; letter-spacing:.08em; padding:3px 10px; border-radius:12px; }
.pend h2 { font-family: Georgia, serif; font-style: italic; color:#3F2A6B; font-size: 19px; margin: 8px 0 2px; }
.pend .lead { color:#4B3A73; font-size: 14px; margin: 0 0 10px; }
.pq { background:#fff; border:1px solid #D4C8EC; border-radius:8px; padding:10px 14px; margin:8px 0; }
.pq .tp { font-weight:700; color:#3F2A6B; }
.pq .qq { margin:2px 0 6px; font-size:15px; }
.pq .aff { font: 12px Arial, sans-serif; color:#6B5A93; }
.opts { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
.opts label { font: 14px Arial, sans-serif; border:1px solid #5B3E96; color:#3F2A6B; background:#fff; border-radius:18px; padding:6px 14px; cursor:pointer; }
.opts label.sel { background:#5B3E96; color:#fff; }
.opts label.ns.sel { background:#8C8C8C; border-color:#8C8C8C; }
.opts input { position:absolute; opacity:0; width:0; height:0; }
.opts label:focus-within { outline:3px solid #9A82D0; }
.sum { font: 14px Arial, sans-serif; background:#fff; border:1px dashed #5B3E96; border-radius:8px; padding:8px 12px; margin-top:10px; }
.sum li { margin:2px 0; }
.how { font: 14px Arial, sans-serif; margin-top:10px; color:#3F2A6B; }
.how b { color:#5B3E96; }
"""

REVIEW_JS = """
(function () {
  var rows = [].slice.call(document.querySelectorAll('tbody tr'));
  var sevBtns = [].slice.call(document.querySelectorAll('[data-sev-btn]'));
  var outletSel = document.getElementById('outlet');
  var staffBox = document.getElementById('staff');
  var sev = 'all';
  var seen = {};
  rows.forEach(function (r) { seen[r.dataset.outlet] = true; });
  Object.keys(seen).sort().forEach(function (o) {
    var op = document.createElement('option');
    op.value = o; op.textContent = o;
    outletSel.appendChild(op);
  });
  function apply() {
    var o = outletSel.value;
    var s = staffBox.value.trim().toUpperCase();
    rows.forEach(function (r) {
      var ok = (sev === 'all' || r.dataset.color === sev) &&
               (o === '' || r.dataset.outlet === o) &&
               (s === '' || r.dataset.staff.toUpperCase().indexOf(s) >= 0);
      r.style.display = ok ? '' : 'none';
    });
  }
  sevBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      sev = b.dataset.sevBtn;
      sevBtns.forEach(function (x) { x.classList.toggle('on', x === b); });
      apply();
    });
  });
  outletSel.addEventListener('change', apply);
  staffBox.addEventListener('input', apply);
})();
"""

PENDING_JS_TEMPLATE = """
(function(){
  var OPEN=__OPEN_JSON__;
  var NS="Not sure: keep our default";
  var KEY="review_pending___MONTH__", st={};
  try{var s=localStorage.getItem(KEY); if(s) st=JSON.parse(s);}catch(e){}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(st));}catch(e){}}
  var rows=[].slice.call(document.querySelectorAll("#rows tr"));
  var base={red:0,amber:0,green:0};
  rows.forEach(function(r){ r.dataset.base=r.dataset.color; base[r.dataset.color]++; });
  var openRefs=OPEN.map(function(o){return o.ref;});
  var host=document.getElementById("pqs");
  OPEN.forEach(function(o,i){
    var d=document.createElement("div"); d.className="pq";
    d.innerHTML='<div class="tp"></div><div class="qq"></div><div class="aff"></div><div class="opts"></div>';
    d.querySelector(".tp").textContent=(i+1)+". "+o.topic;
    d.querySelector(".qq").textContent=o.q;
    d.querySelector(".aff").textContent=o.n+" item"+(o.n===1?"":"s")+" wait on this answer";
    var ob=d.querySelector(".opts");
    o.opts.concat([NS]).forEach(function(t,k){
      var l=document.createElement("label"); if(k===o.opts.length) l.className="ns";
      var x=document.createElement("input"); x.type="radio"; x.name="pq-"+o.ref; x.value=t;
      if(st[o.ref]===t){x.checked=true;l.classList.add("sel");}
      x.addEventListener("change",function(){
        st[o.ref]=t; save();
        [].forEach.call(ob.children,function(c){c.classList.remove("sel");});
        l.classList.add("sel"); recalc();
      });
      l.appendChild(x); l.appendChild(document.createTextNode(t)); ob.appendChild(l);
    });
    host.appendChild(d);
  });
  function answered(ref){return st[ref] && st[ref]!==NS;}
  function recalc(){
    var amber=base.amber, green=base.green, resolved=0;
    rows.forEach(function(r){
      if(r.dataset.base!=="amber") return;
      var refs=(r.dataset.q||"").split(",").map(function(x){return x.trim();}).filter(function(x){return openRefs.indexOf(x)>=0;});
      var ok=refs.length>0 && refs.every(answered);
      var qcell=r.children[4];
      r.classList.toggle("answered",ok);
      r.classList.toggle("c-green",ok); r.classList.toggle("c-amber",!ok);
      r.dataset.color=ok?"green":"amber";
      var tag=qcell.querySelector(".tag-ans");
      if(ok){ if(!tag){tag=document.createElement("span");tag.className="tag-ans";tag.textContent="answered";qcell.appendChild(tag);} resolved++; }
      else if(tag){ tag.remove(); }
    });
    document.getElementById("n-amber").textContent=base.amber-resolved;
    document.getElementById("n-green").textContent=base.green+resolved;
    document.getElementById("g-note").textContent=resolved?("includes "+resolved+" provisional"):"";
    var list=OPEN.filter(function(o){return st[o.ref];}).map(function(o){return "<li>"+o.topic+": "+escapeHtml(st[o.ref])+"</li>";});
    document.getElementById("sumtxt").innerHTML=list.length?("<ul style='margin:4px 0 0 18px;padding:0'>"+list.join("")+"</ul>"):"none yet";
  }
  function escapeHtml(s){return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c];});}
  recalc();
})();
"""


def _review_person(flag: DayFlag) -> str:
    return "(unmatched clock name)" if flag.flag.startswith("Clock name issue") else flag.person


def _review_details(flag: DayFlag) -> str:
    if flag.flag in REVIEW_HIDE_DETAIL_FLAGS or flag.flag.startswith("Clock name issue"):
        return flag.remark
    return flag.details


def _pending_section_html(month: str, pending_questions: list[dict]) -> str:
    if not pending_questions:
        return "", ""
    esc = _esc
    section = f"""<section class="pend" id="pend" aria-label="Pending answers from Client">
 <span class="kicker">PENDING ANSWERS FROM CLIENT</span>
 <h2>{len(pending_questions)} short question{'s' if len(pending_questions) != 1 else ''}, one click each</h2>
 <p class="lead">Amber items below wait on these answers. Tick one option per question. The counts and the table update straight away. <b>Not sure</b> keeps our default.</p>
 <div id="pqs"></div>
 <div class="sum" id="sum"><b>Your answers:</b> <span id="sumtxt">none yet</span></div>
 <p class="how"><b>When you finish:</b> 1) press <b>Ctrl+S</b> to save this page, 2) take a <b>screenshot</b> of this purple box, 3) send both to JK Zentra. Answers are final only after JK Zentra applies them and runs the check again.</p>
</section>"""
    open_json = json.dumps([
        {"ref": q["ref"], "topic": q["topic"], "q": q["question"], "n": q["flags_affected"], "opts": q["opts"]}
        for q in pending_questions
    ])
    script = (
        PENDING_JS_TEMPLATE
        .replace("__OPEN_JSON__", open_json)
        .replace("__MONTH__", esc(month, True))
    )
    return section, script


def _review_html(
    month: str, summary: dict, flags: list[DayFlag], pending_questions: list[dict] | None = None,
) -> str:
    esc = _esc
    month_label = datetime.datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    counts = summary["counts"]
    rows = []
    rendered = []
    for f in flags:
        if f.severity == "Info":
            continue
        color = REVIEW_COLOR.get(f.remark_type)
        if color is None:
            continue
        rendered.append((REVIEW_ORDER[color], f, color))
    rendered.sort(key=lambda t: t[0])
    for _, f, color in rendered:
        person = _review_person(f)
        outlet = f.home_outlet_tab or "-"
        days = str(f.days) + (f" ({f.span})" if f.span else "")
        rows.append(
            f'<tr class="c-{color}" data-color="{color}" data-outlet="{esc(outlet, True)}" '
            f'data-staff="{esc(person, True)}" data-q="{esc(f.ref or "", True)}">'
            f"<td>{esc(person)}</td><td>{esc(outlet)}</td><td>{esc(f.flag)}</td>"
            f"<td>{esc(days)}</td><td>{esc(f.ref or '')}</td><td>{esc(_review_details(f))}</td></tr>"
        )
    missing = summary.get("missing") or []
    missing_html = "".join(f"<li>{esc(m)}</li>" for m in missing) or "<li>Nothing missing.</li>"
    questions = summary.get("open_questions") or []
    questions_html = "".join(
        f"<li><b>{esc(q['ref'])}</b> ({esc(', '.join(q['rule_ids']))}): "
        f"{q['flags_affected']} flag(s) affected</li>"
        for q in questions
    ) or "<li>None.</li>"
    pending_section_html, pending_script = _pending_section_html(month, pending_questions or [])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Attendance review {esc(month_label)}</title>
<style>{REVIEW_CSS}</style></head>
<body>
<h1>Attendance review: {esc(month_label)}</h1>
<div class="sub">Status: {esc(summary['status'])}. DRAFT, read-only review page. HR decides; this page changes nothing.</div>
<div class="tiles">
 <div class="tile t-red"><b id="n-red">{counts['red']}</b>Red: possible error<small></small></div>
 <div class="tile t-amber"><b id="n-amber">{counts['amber']}</b>Amber: pending question<small></small></div>
 <div class="tile t-green"><b id="n-green">{counts['green']}</b>Green: confirmed rule<small id="g-note"></small></div>
 <div class="tile t-info"><b>{summary['info_total']}</b>Info (not listed)<small></small></div>
</div>
{pending_section_html}
<div class="box"><b>Missing files</b><ul>{missing_html}</ul></div>
<div class="filters">
 <button data-sev-btn="all" class="on">All</button>
 <button data-sev-btn="red">Red</button>
 <button data-sev-btn="amber">Amber</button>
 <button data-sev-btn="green">Green</button>
 <select id="outlet"><option value="">All outlets</option></select>
 <input id="staff" type="text" placeholder="Staff ID">
</div>
<table>
<thead><tr><th>Staff ID</th><th>Outlet</th><th>Flag</th><th>Days</th><th>Q</th><th>Details</th></tr></thead>
<tbody id="rows">
{chr(10).join(rows)}
</tbody></table>
<div class="box"><b>Questions still open</b><ul>{questions_html}</ul></div>
<script>{REVIEW_JS}</script>
<script>{pending_script}</script>
</body></html>
"""


def _write_review_page(
    latest_dir: Path, month: str, summary: dict, flags: list[DayFlag],
    pending_questions: list[dict] | None = None,
) -> Path:
    label = datetime.datetime.strptime(month, "%Y-%m").strftime("%b-%y")
    path = latest_dir / f"Review_{label}.html"
    path.write_text(_review_html(month, summary, flags, pending_questions), encoding="utf-8")
    return path


def _latest_month() -> str | None:
    """The month the current LATEST folder holds, or None."""
    try:
        data = json.loads((REPORTS_DIR / "LATEST" / "AGGREGATE_SUMMARY.json").read_text(encoding="utf-8"))
        return data.get("month")
    except (OSError, ValueError, AttributeError):
        return None


def _may_replace_latest(month: str, status: str) -> bool:
    """LATEST is only replaced by a fully OK month, or refreshed for the
    month it already holds. A PARTIAL month never displaces another month."""
    if status == "OK":
        return True
    held = _latest_month()
    return held is None and not (REPORTS_DIR / "LATEST").exists() or held == month


def _update_latest(month_report_dir: Path) -> None:
    latest_dir = REPORTS_DIR / "LATEST"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(month_report_dir, latest_dir)


def _append_run_log(month: str, result: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not RUN_LOG_PATH.exists()
    with open(RUN_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        if is_new:
            f.write("run_time,month,status,missing_count\n")
        f.write(f"{datetime.datetime.now().isoformat()},{month},{result['status']},{len(result.get('missing', []))}\n")


def _month_folder_is_empty(month: str) -> bool:
    """True when the month folder holds no clock file, schedule or HR workbook."""
    month_dir = DROP_DIR / month
    if not month_dir.is_dir():
        return False  # run_month reports the missing folder as FAILED
    clock_files, schedule_path, hr_path, _ = _classify_month_folder(month_dir)
    return not clock_files and schedule_path is None and hr_path is None


def process_month(month: str, force: bool = False) -> dict:
    if _month_folder_is_empty(month):
        # Nothing to check: write no report, leave LATEST and the manifest alone.
        result = {"status": "NOT_READY", "month": month, "missing": [],
                  "latest_updated": False}
        _append_run_log(month, result)
        print(f"{month}: NOT_READY - the folder has no clock, schedule or HR files; nothing written")
        return result

    result = run_month(month, force=force)
    manifest = _load_manifest()

    if result["status"] == "NOTHING_NEW":
        result["latest_updated"] = False
        _append_run_log(month, result)
        print(f"{month}: NOTHING_NEW")
        return result

    if result["status"] == "FAILED":
        result["latest_updated"] = False
        _append_run_log(month, result)
        print(f"{month}: FAILED - {result.get('error', '')}")
        return result

    month_report_dir = REPORTS_DIR / month
    month_report_dir.mkdir(parents=True, exist_ok=True)
    _write_trial_workbook(month_report_dir / f"Attendance_Output_Report_{month}.xlsx", month, result)
    summary = _write_aggregate_summary(month_report_dir / "AGGREGATE_SUMMARY.json", month, result)
    _write_daily_note(month_report_dir / "DAILY_NOTE_basic.txt", summary)

    # Roster Generated: copy original roster format, replace mismatched shifts with actuals
    month_dir = DROP_DIR / month
    _, schedule_path, _, _ = _classify_month_folder(month_dir)
    if schedule_path is not None and result.get("day_check_rows"):
        roster_out = month_report_dir / f"Roster_Generated_{month}.xlsx"
        try:
            _, n_replaced = generate_roster(schedule_path, result["day_check_rows"], roster_out)
            print(f"{month}: Roster Generated written ({n_replaced} cells replaced)")
        except Exception:
            DAILY_TASK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            import traceback as _tb
            with open(DAILY_TASK_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.datetime.now().isoformat()} roster_gen {month} ---\n")
                f.write(_tb.format_exc())
            print(f"{month}: Roster Generated FAILED (see daily_task.log)")

    latest_updated = _may_replace_latest(month, result["status"])
    if latest_updated:
        _update_latest(month_report_dir)
        catalogue = load_pending_catalogue(PENDING_QUESTIONS_PATH, result["rules"])
        pending_questions = _pending_questions_for_review(summary, catalogue)
        _write_review_page(REPORTS_DIR / "LATEST", month, summary, result["all_flags"], pending_questions)
    else:
        print(f"{month}: {result['status']} - LATEST left as it was (it holds another month)")
    result["latest_updated"] = latest_updated

    manifest[month] = {
        "files": result["signatures"], "last_status": result["status"],
        "missing": result.get("missing", []),
    }
    _save_manifest(manifest)
    _append_run_log(month, result)
    print(f"{month}: {result['status']}")
    return result


def _write_last_run(results: list[dict]) -> None:
    """07_LOGS/last_run.json: lets run_daily.bat skip the daily-note model
    when this run changed nothing. Counts and statuses only."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "run_time": datetime.datetime.now().isoformat(timespec="seconds"),
        "statuses": {r.get("month", "?"): r.get("status") for r in results},
        "latest_updated": any(r.get("latest_updated") for r in results),
    }
    (LOGS_DIR / "last_run.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    from src.run_lock import RunLocked, engine_lock

    try:
        with engine_lock("cli"):
            if args.month:
                months = [args.month]
            else:
                months = sorted(p.name for p in DROP_DIR.iterdir() if p.is_dir() and len(p.name) == 7 and p.name[4] == "-")

            results = [process_month(m, force=args.force) for m in months] if months else []
            _write_last_run(results)
            if not results:
                print("NOTHING_NEW: no month folders found")
                return 0
            if any(r["status"] == "FAILED" for r in results):
                return 1
            return 0
    except RunLocked as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
