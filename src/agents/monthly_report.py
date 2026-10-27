"""Monthly reporter agent.

Builds a monthly attendance summary workbook from the existing engine. It never
edits the engine: it calls ``src.daily.run_month`` once for the month, keeps the
per-person-day rows (``day_check_rows``) and counts them. Counts come from
``day_check_rows`` and the length of each row's ``flag_refs`` only. Those refs are
indices into the engine's un-rolled flag list, so there is no per-flag-type
breakdown per person or day. If ``run_month`` also returns ``all_flags`` the
Summary adds severity counts (red/amber/green, plus Info) labelled
"month-level flags"; this is read defensively and skipped if it cannot be read.

Side effects (all inside this project, none in 01_DROP_FOLDER):
* ``run_month`` itself may write ``06_REPORTS/<YYYY-MM>/NEEDS_ATTENTION.txt``
  (existing daily behaviour, accepted) and, if the engine raises, append to
  ``07_LOGS/daily_task.log``.
* This module writes one workbook,
  ``06_REPORTS/<YYYY-MM>/Monthly_<YYYY>-<MM>.xlsx``, and appends one line per
  run to ``07_LOGS/monthly_log.csv``.
* An existing monthly file is never overwritten; the run reports
  ``SKIPPED_EXISTS`` instead.

Statuses: OK, PARTIAL (the engine said PARTIAL), SKIPPED_EXISTS, FAILED (engine
FAILED or the month folder is missing; no monthly file written).

``run()`` returns a dict and prints nothing.
"""
from __future__ import annotations

import datetime
import io
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

import src.daily as daily
from src.agents.scope_resolver import ScopeWindow, resolve

LOG_NAME = "monthly_log.csv"

DAY_COLUMNS = [
    "person", "date", "day", "schedule_code", "type", "shift_start", "shift_end",
    "punches", "clock_outlet", "system_in", "system_out", "hq_in", "hq_out",
]


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


def _month_level_flags(all_flags) -> dict | None:
    """Severity counts from the engine's flag list, or None if unavailable."""
    if not all_flags:
        return None
    try:
        colours, info = daily._counts_by_severity_and_remark(all_flags)
        return {"red": colours["red"], "amber": colours["amber"],
                "green": colours["green"], "info": info}
    except Exception:
        return None


def _group(rows: list[dict], key) -> dict:
    out: dict = {}
    for r in rows:
        t = out.setdefault(key(r), [0, 0, 0])
        t[0] += 1
        n = len(r.get("flag_refs") or [])
        t[1] += 1 if n else 0
        t[2] += n
    return out


def _build_workbook(window: ScopeWindow, status: str, notes: list[str], rows: list[dict],
                    counts: dict, month_flags: dict | None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Monthly attendance summary (draft, counts from the day check)"])
    ws["A1"].font = Font(bold=True)
    ws.append(["Month", window.label])
    ws.append(["Status", status])
    ws.append(["Dates covered", f"{window.start.isoformat()} to {window.end.isoformat()}"])
    for note in notes:
        ws.append(["Note", note])
    ws.append(["Run time", datetime.datetime.now().isoformat(timespec="seconds")])
    ws.append([])
    ws.append(["Scheduled person-days", counts["person_days"]])
    ws.append(["Person-days with at least one flag", counts["flagged_person_days"]])
    ws.append(["Total flags", counts["flags"]])
    ws.append(["People", counts["people"]])
    if month_flags:
        ws.append([])
        ws.append(["Month-level flags (from the engine's rolled-up list)"])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        ws.append(["Red (possible error)", month_flags["red"]])
        ws.append(["Amber (pending question)", month_flags["amber"]])
        ws.append(["Green (confirmed rule)", month_flags["green"]])
        ws.append(["Info", month_flags["info"]])

    ws2 = wb.create_sheet("By person")
    ws2.append(["person", "scheduled_days", "flagged_days", "flags"])
    by_person = _group(rows, lambda r: r["person"])
    for person in sorted(by_person, key=str):
        ws2.append([person, *by_person[person]])

    ws3 = wb.create_sheet("By day")
    ws3.append(["date", "weekday", "scheduled_person_days", "flagged_days", "flags"])
    by_day = _group(rows, lambda r: r["date"])
    for d in sorted(by_day):
        ws3.append([d, d.strftime("%a"), *by_day[d]])

    ws5 = wb.create_sheet("By outlet")
    ws5.append(["clock_outlet", "scheduled_person_days", "flagged_days", "flags"])
    by_outlet = _group(rows, lambda r: r.get("clock_outlet") or "(none)")
    for outlet in sorted(by_outlet, key=str):
        ws5.append([outlet, *by_outlet[outlet]])

    ws4 = wb.create_sheet("Day check")
    ws4.append(DAY_COLUMNS + ["flag_count"])
    for r in sorted(rows, key=lambda x: (x["date"], str(x["person"]))):
        ws4.append([r.get(c) for c in DAY_COLUMNS] + [len(r.get("flag_refs") or [])])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run(as_of: datetime.date | None = None, previous: bool = True) -> dict:
    """Build the monthly workbook. Defaults to the last completed calendar month."""
    window = resolve("month", as_of=as_of, previous=previous)
    month = f"{window.start.year}-{window.start.month:02d}"
    result = {
        "status": "FAILED", "window": window.label, "month": month,
        "start": window.start.isoformat(), "end": window.end.isoformat(),
        "missing": [], "output_path": None,
        "counts": {"person_days": 0, "flagged_person_days": 0, "flags": 0, "people": 0},
        "month_flags": None, "error": "",
    }

    if not (Path(daily.DROP_DIR) / month).is_dir():
        result["error"] = f"Month folder missing: {month}"
        _log("FAILED", window, result["error"])
        return result

    from src.run_lock import RunLocked, engine_lock
    try:
        with engine_lock("monthly_report"):
            return _run_locked(window, month, result)
    except RunLocked as exc:
        result["error"] = f"Engine locked: {exc}"
        _log("FAILED", window, result["error"])
        return result


def _run_locked(window: ScopeWindow, month: str, result: dict) -> dict:
    res = daily.run_month(month, force=True)
    if res.get("status") == "FAILED":
        result["error"] = f"Engine failed for {month}: {res.get('error', '')}"
        _log("FAILED", window, result["error"])
        return result

    notes = [str(m) for m in res.get("missing", [])]
    result["missing"] = notes
    rows = [r for r in res.get("day_check_rows", []) if window.start <= r["date"] <= window.end]
    result["counts"] = _count(rows)
    month_flags = _month_level_flags(res.get("all_flags"))
    result["month_flags"] = month_flags
    status = "OK" if res.get("status") == "OK" else "PARTIAL"

    today = datetime.date.today()
    month_complete = window.end < today
    if not month_complete:
        rows = [r for r in rows if r["date"] <= today]
        result["counts"] = _count(rows)
        status = "PARTIAL"

    out_dir = Path(daily.REPORTS_DIR) / month
    final_path = out_dir / f"Monthly_{window.start.year}-{window.start.month:02d}.xlsx"
    draft_path = out_dir / f"Monthly_{window.start.year}-{window.start.month:02d}_DRAFT.xlsx"

    if status == "OK" and month_complete:
        out_path = final_path
        if final_path.exists():
            result["status"] = "SKIPPED_EXISTS"
            result["output_path"] = str(final_path)
            _log("SKIPPED_EXISTS", window, "final monthly file already exists")
            return result
    else:
        out_path = draft_path
        if final_path.exists():
            result["status"] = "SKIPPED_EXISTS"
            result["output_path"] = str(final_path)
            _log("SKIPPED_EXISTS", window, "final already exists; draft not needed")
            return result

    result["output_path"] = str(out_path)

    if not month_complete:
        notes.append(f"Month-to-date: data up to {today.isoformat()}")

    data = _build_workbook(window, status, notes, rows, result["counts"], month_flags)
    out_dir.mkdir(parents=True, exist_ok=True)

    if status == "OK" and month_complete and draft_path.exists():
        draft_path.unlink()

    try:
        with open(out_path, "xb") as f:
            f.write(data)
    except FileExistsError:
        if not month_complete or status != "OK":
            out_path.unlink()
            with open(out_path, "wb") as f:
                f.write(data)
        else:
            result["status"] = "SKIPPED_EXISTS"
            _log("SKIPPED_EXISTS", window, "monthly file already exists")
            return result

    result["status"] = status
    _log(status, window, f"person_days={result['counts']['person_days']} missing={len(notes)}"
                         f" month_complete={month_complete}")
    return result


_SUCCESS = {"OK", "PARTIAL", "SKIPPED_EXISTS"}


def main() -> int:
    """Entry point for Task Scheduler. Returns 0 on success, 1 on failure."""
    result = run()
    return 0 if result["status"] in _SUCCESS else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
