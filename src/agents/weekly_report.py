"""Weekly reporter agent (B2.2).

Builds a weekly attendance summary workbook from the existing monthly engine.
It never edits the engine: it calls ``src.daily.run_month`` once per month the
week touches, keeps the per-person-day rows (``day_check_rows``) that fall in
the window, and counts them. Counts come from ``day_check_rows`` and the length
of each row's ``flag_refs`` only. Those refs are indices into the engine's
un-rolled flag list, which ``run_month`` does not return, so there is no
per-flag-type breakdown here.

Side effects (all inside this project, none in 01_DROP_FOLDER):
* ``run_month`` itself may write ``06_REPORTS/<YYYY-MM>/NEEDS_ATTENTION.txt``
  (existing daily behaviour, accepted) and, if the engine raises, append to
  ``07_LOGS/daily_task.log``.
* This module writes one workbook,
  ``06_REPORTS/<YYYY-MM of the window's END date>/Weekly_<YYYY>-W<NN>.xlsx``,
  and appends one line per run to ``07_LOGS/weekly_log.csv``.
* An existing weekly file is never overwritten; the run reports
  ``SKIPPED_EXISTS`` instead.

Statuses: OK, PARTIAL (a needed month folder is missing, or the engine said
PARTIAL; the workbook covers only the dates that had data), SKIPPED_EXISTS,
FAILED (engine FAILED or no month folder available; no weekly file written).

``run()`` returns a dict and prints nothing.
"""
from __future__ import annotations

import datetime
import io
from collections import Counter
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

import src.daily as daily
from src.agents.scope_resolver import ScopeWindow, iso_week_number, month_window, resolve

LOG_NAME = "weekly_log.csv"

DAY_COLUMNS = [
    "person", "date", "day", "schedule_code", "type", "shift_start", "shift_end",
    "punches", "clock_outlet", "system_in", "system_out", "hq_in", "hq_out",
]


def _months_touched(start: datetime.date, end: datetime.date) -> list[str]:
    months = [f"{start.year}-{start.month:02d}"]
    end_month = f"{end.year}-{end.month:02d}"
    if end_month != months[0]:
        months.append(end_month)
    return months


def _log(status: str, window: ScopeWindow, detail: str) -> None:
    logs_dir = Path(daily.LOGS_DIR)
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / LOG_NAME
    is_new = not path.exists()
    detail = detail.replace('"', "'").replace("\n", " ")
    with open(path, "a", encoding="utf-8", newline="") as f:
        if is_new:
            f.write("run_time,window,status,detail\n")
        f.write(f'{datetime.datetime.now().isoformat(timespec="seconds")},'
                f'{window.filename_stem},{status},"{detail}"\n')


def _count(rows: list[dict]) -> dict:
    flags = sum(len(r.get("flag_refs") or []) for r in rows)
    flagged = sum(1 for r in rows if r.get("flag_refs"))
    return {
        "person_days": len(rows),
        "flagged_person_days": flagged,
        "flags": flags,
        "people": len({r["person"] for r in rows}),
    }


def _build_workbook(window: ScopeWindow, status: str, covered: tuple, missing_months: list[str],
                    notes: list[str], rows: list[dict], counts: dict) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Weekly attendance summary (draft, counts from the day check)"])
    ws["A1"].font = Font(bold=True)
    ws.append(["Week", window.label])
    ws.append(["Status", status])
    ws.append(["Dates covered", f"{covered[0]} to {covered[1]}" if covered[0] else "none"])
    if missing_months:
        ws.append(["Month folder(s) missing", ", ".join(missing_months)])
    for note in notes:
        ws.append(["Note", note])
    ws.append(["Run time", datetime.datetime.now().isoformat(timespec="seconds")])
    ws.append([])
    ws.append(["Scheduled person-days", counts["person_days"]])
    ws.append(["Person-days with at least one flag", counts["flagged_person_days"]])
    ws.append(["Total flags", counts["flags"]])
    ws.append(["People", counts["people"]])

    by_person: dict[str, list[int]] = {}
    for r in rows:
        t = by_person.setdefault(r["person"], [0, 0, 0])
        t[0] += 1
        n = len(r.get("flag_refs") or [])
        t[1] += 1 if n else 0
        t[2] += n
    ws2 = wb.create_sheet("By person")
    ws2.append(["person", "scheduled_days", "flagged_days", "flags"])
    for person in sorted(by_person):
        ws2.append([person, *by_person[person]])

    by_day: dict[datetime.date, list[int]] = {}
    for r in rows:
        t = by_day.setdefault(r["date"], [0, 0, 0])
        t[0] += 1
        n = len(r.get("flag_refs") or [])
        t[1] += 1 if n else 0
        t[2] += n
    ws3 = wb.create_sheet("By day")
    ws3.append(["date", "weekday", "scheduled_person_days", "flagged_days", "flags"])
    for d in sorted(by_day):
        ws3.append([d, d.strftime("%a"), *by_day[d]])

    ws4 = wb.create_sheet("Day check")
    ws4.append(DAY_COLUMNS + ["flag_count"])
    for r in sorted(rows, key=lambda x: (x["date"], str(x["person"]))):
        ws4.append([r.get(c) for c in DAY_COLUMNS] + [len(r.get("flag_refs") or [])])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run(as_of: datetime.date | None = None, starts_on: str = "Mon", previous: bool = True) -> dict:
    """Build the weekly workbook. Defaults to the last completed business week."""
    window = resolve("week", as_of=as_of, starts_on=starts_on, previous=previous)
    iso_year, iso_week = iso_week_number(window.start)
    result = {
        "status": "FAILED", "window": window.label,
        "start": window.start.isoformat(), "end": window.end.isoformat(),
        "covered_start": None, "covered_end": None,
        "missing_months": [], "missing": [], "output_path": None,
        "counts": {"person_days": 0, "flagged_person_days": 0, "flags": 0, "people": 0},
        "error": "",
    }

    from src.run_lock import RunLocked, engine_lock
    try:
        with engine_lock("weekly_report"):
            return _run_locked(window, iso_year, iso_week, result)
    except RunLocked as exc:
        result["error"] = f"Engine locked: {exc}"
        _log("FAILED", window, result["error"])
        return result


def _run_locked(window: ScopeWindow, iso_year: int, iso_week: int, result: dict) -> dict:
    rows: list[dict] = []
    notes: list[str] = []
    covered_days: list[datetime.date] = []
    engine_partial = False

    for month in _months_touched(window.start, window.end):
        if not (Path(daily.DROP_DIR) / month).is_dir():
            result["missing_months"].append(month)
            continue
        res = daily.run_month(month, force=True)
        if res.get("status") == "FAILED":
            result["error"] = f"Engine failed for {month}: {res.get('error', '')}"
            _log("FAILED", window, result["error"])
            return result
        engine_partial = engine_partial or res.get("status") != "OK"
        notes.extend(f"{month}: {m}" for m in res.get("missing", []))
        m_start, m_end = month_window(datetime.date(int(month[:4]), int(month[5:]), 1))
        lo, hi = max(m_start, window.start), min(m_end, window.end)
        covered_days.extend([lo, hi])
        rows.extend(r for r in res.get("day_check_rows", []) if window.start <= r["date"] <= window.end)

    result["missing"] = notes
    if not covered_days:
        result["error"] = "No month folder available for this week: " + ", ".join(result["missing_months"])
        _log("FAILED", window, result["error"])
        return result

    covered = (min(covered_days), max(covered_days))
    result["covered_start"], result["covered_end"] = covered[0].isoformat(), covered[1].isoformat()
    result["counts"] = _count(rows)
    status = "PARTIAL" if (result["missing_months"] or engine_partial) else "OK"

    out_dir = Path(daily.REPORTS_DIR) / f"{window.end.year}-{window.end.month:02d}"
    final_path = out_dir / f"Weekly_{iso_year}-W{iso_week:02d}.xlsx"
    draft_path = out_dir / f"Weekly_{iso_year}-W{iso_week:02d}_DRAFT.xlsx"

    if status == "OK":
        out_path = final_path
        if final_path.exists():
            result["status"] = "SKIPPED_EXISTS"
            result["output_path"] = str(final_path)
            _log("SKIPPED_EXISTS", window, "final weekly file already exists")
            return result
    else:
        out_path = draft_path
        if final_path.exists():
            result["status"] = "SKIPPED_EXISTS"
            result["output_path"] = str(final_path)
            _log("SKIPPED_EXISTS", window, "final already exists; draft not needed")
            return result

    result["output_path"] = str(out_path)

    data = _build_workbook(window, status, (covered[0].isoformat(), covered[1].isoformat()),
                           result["missing_months"], notes, rows, result["counts"])
    out_dir.mkdir(parents=True, exist_ok=True)

    if status == "OK" and draft_path.exists():
        draft_path.unlink()

    try:
        with open(out_path, "xb") as f:
            f.write(data)
    except FileExistsError:
        if status != "OK":
            out_path.unlink()
            with open(out_path, "wb") as f:
                f.write(data)
        else:
            result["status"] = "SKIPPED_EXISTS"
            _log("SKIPPED_EXISTS", window, "weekly file already exists")
            return result

    result["status"] = status
    _log(status, window, f"person_days={result['counts']['person_days']} "
                         f"missing_months={len(result['missing_months'])}")
    return result


_SUCCESS = {"OK", "PARTIAL", "SKIPPED_EXISTS"}


def main() -> int:
    """Entry point for Task Scheduler. Returns 0 on success, 1 on failure."""
    result = run()
    return 0 if result["status"] in _SUCCESS else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
