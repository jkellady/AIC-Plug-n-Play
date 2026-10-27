"""Generate the ROSTER GENERATED output.

Copies the original roster format exactly, replacing shift codes with
actual clock-in/out times for staff whose punches did not match the
scheduled shift (late-in or early-out). The original roster is never
modified.

Output name: Roster_Generated_YYYY-MM.xlsx
"""
from __future__ import annotations

import datetime
from copy import copy
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter


def _time_str_to_minutes(t: str) -> int | None:
    """Parse 'HH:MM' to minutes since midnight, or None."""
    if not t or t == "None":
        return None
    parts = t.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _format_clock_time(t: str) -> str:
    """Format 'HH:MM' (24h) into a roster-style string like '10:15AM' or '10:17PM'."""
    mins = _time_str_to_minutes(t)
    if mins is None:
        return t or ""
    h, m = divmod(mins, 60)
    if h < 12:
        suffix = "AM"
        display_h = h if h != 0 else 12
    elif h == 12:
        suffix = "PM"
        display_h = 12
    else:
        suffix = "PM"
        display_h = h - 12
    if m == 0:
        return f"{display_h}{suffix}"
    return f"{display_h}:{m:02d}{suffix}"


def _is_mismatch(shift_start: str | None, shift_end: str | None,
                 system_in: str | None, system_out: str | None) -> bool:
    """True if actual clock differs from scheduled shift (late-in or early-out)."""
    ss = _time_str_to_minutes(str(shift_start) if shift_start else "")
    se = _time_str_to_minutes(str(shift_end) if shift_end else "")
    si = _time_str_to_minutes(str(system_in) if system_in else "")
    so = _time_str_to_minutes(str(system_out) if system_out else "")
    if si is None or so is None or ss is None or se is None:
        return False
    return si != ss or so != se


def _actual_code(system_in: str | None, system_out: str | None) -> str:
    """Build a roster-style code from actual clock times."""
    si = str(system_in) if system_in else ""
    so = str(system_out) if system_out else ""
    return f"{_format_clock_time(si)}-{_format_clock_time(so)}"


def generate_roster(
    original_roster_path: Path,
    day_check_rows: list[dict],
    output_path: Path,
) -> Path:
    """Copy the original roster and replace mismatched shift codes with actuals.

    day_check_rows: list of dicts with keys person, date (date obj),
        schedule_code, shift_start, shift_end, system_in, system_out.
    """
    wb = openpyxl.load_workbook(original_roster_path)
    ws = wb.active

    name_row_cells = list(ws.iter_rows(min_row=2, max_row=2))[0]
    staff_cols: dict[str, int] = {}
    for cell in name_row_cells:
        if cell.value and isinstance(cell.value, str) and cell.value.strip():
            name = cell.value.strip().upper()
            if name not in ("DATE", "DAY", "REMARK"):
                staff_cols.setdefault(name, cell.column)

    mismatch_lookup: dict[tuple[str, str], str] = {}
    for row in day_check_rows:
        if not _is_mismatch(row.get("shift_start"), row.get("shift_end"),
                            row.get("system_in"), row.get("system_out")):
            continue
        person = row["person"]
        if isinstance(row["date"], str):
            date_str = row["date"]
        else:
            date_str = row["date"].isoformat()
        actual = _actual_code(row.get("system_in"), row.get("system_out"))
        mismatch_lookup[(person.upper(), date_str)] = actual

    replacements = 0
    for row_idx in range(4, ws.max_row + 1):
        date_cell = ws.cell(row_idx, 1).value
        if date_cell is None:
            continue
        if isinstance(date_cell, datetime.datetime):
            date_str = date_cell.strftime("%Y-%m-%d")
        elif isinstance(date_cell, datetime.date):
            date_str = date_cell.isoformat()
        else:
            continue

        for staff_name, col in staff_cols.items():
            key = (staff_name, date_str)
            if key in mismatch_lookup:
                ws.cell(row_idx, col).value = mismatch_lookup[key]
                replacements += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.xlsx")
    wb.save(tmp_path)
    try:
        tmp_path.replace(output_path)
    except PermissionError:
        stamped = output_path.with_name(
            f"{output_path.stem}_{datetime.datetime.now():%Y%m%d_%H%M%S}{output_path.suffix}"
        )
        tmp_path.replace(stamped)
        output_path = stamped

    return output_path, replacements
