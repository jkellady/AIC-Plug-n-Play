"""Turn raw clock-machine exports into punches_long and clock_roster tables.

Reads the "Attend. Logs" sheet of each `<OUTLET>-All_Report_<date>.xls` file.
Read-only: never opens files for writing, never touches the source files.

Layout (see CLAUDE.md section 4): each person has a 3-4 row group somewhere
in the sheet containing, in no fixed order:
  - one "ID :" / "Name :" / "Dept. :" label row (marks whose data follows)
  - one row of day numbers 1..31 (a sheet-wide header, ignored)
  - optionally one row of weekday abbreviations (ignored)
  - one punch row: each cell holds 0+ punch times separated by "\n"
Row order and presence of the weekday row differs by outlet export, so rows
are classified by content, not by fixed position.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import xlrd

ATTEND_LOGS_SHEET = "Attend. Logs"
WEEKDAY_NAMES = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

PUNCHES_LONG_COLUMNS = [
    "outlet_clock", "clock_id", "clock_name", "clock_dept",
    "date", "n_punches", "first_punch", "last_punch", "all_punches",
]
CLOCK_ROSTER_COLUMNS = [
    "outlet_clock", "clock_id", "clock_name", "clock_dept", "days_with_punches",
]


@dataclass
class PersonBlock:
    clock_id: str
    clock_name: str
    clock_dept: str


def _norm_label(value) -> str:
    return str(value).strip().rstrip(":").strip().lower()


def _row_values(sheet: xlrd.sheet.Sheet, row: int) -> list:
    return [sheet.cell_value(row, c) for c in range(sheet.ncols)]


def _is_id_row(row: list) -> bool:
    for cell in row:
        if _norm_label(cell) == "id":
            return True
    return False


def _is_day_number_row(row: list) -> bool:
    return row[0] == 1.0 and len(row) > 1 and row[1] == 2.0


def _is_weekday_row(row: list) -> bool:
    first = _norm_label(row[0])
    return first in WEEKDAY_NAMES


def _cell_text(cell_value) -> str:
    if isinstance(cell_value, float) and cell_value.is_integer():
        return str(int(cell_value))
    return str(cell_value).strip()


def _label_value(row: list, label: str) -> str:
    """Find `label :` in row and return the next non-empty cell after it."""
    for i, cell in enumerate(row):
        if _norm_label(cell) == label:
            for cell2 in row[i + 1:]:
                text = _cell_text(cell2)
                if text:
                    return text
            return ""
    return ""


def _parse_id_row(row: list) -> PersonBlock:
    clock_id = _label_value(row, "id")
    clock_name = _label_value(row, "name")
    clock_dept = _label_value(row, "dept.") or _label_value(row, "dept")
    return PersonBlock(clock_id=clock_id, clock_name=clock_name, clock_dept=clock_dept)


def _parse_month(sheet: xlrd.sheet.Sheet) -> tuple[int, int]:
    """Read the 'Date :' header, e.g. '2026-05-01 ~ 2026-05-31', return (year, month)."""
    for r in range(min(sheet.nrows, 5)):
        for c in range(sheet.ncols):
            value = sheet.cell_value(r, c)
            if isinstance(value, str) and "~" in value:
                start = value.split("~")[0].strip()
                year, month, _day = start.split("-")
                return int(year), int(month)
    raise ValueError("Could not find a 'Date :' header with a date range")


def _split_punches(cell_value) -> list[str]:
    if not cell_value:
        return []
    return [p.strip() for p in str(cell_value).split("\n") if p.strip()]


def parse_attend_logs(xls_path: Path) -> tuple[list[dict], list[dict]]:
    """Parse one clock export. Returns (punches_long_rows, roster_rows)."""
    outlet_clock = xls_path.name.split("-")[0]
    book = xlrd.open_workbook(str(xls_path))
    sheet = book.sheet_by_name(ATTEND_LOGS_SHEET)
    year, month = _parse_month(sheet)

    punches_long_rows: list[dict] = []
    roster_rows: list[dict] = []
    current: PersonBlock | None = None
    days_with_punches = 0

    def flush_roster():
        if current is not None:
            roster_rows.append({
                "outlet_clock": outlet_clock,
                "clock_id": current.clock_id,
                "clock_name": current.clock_name,
                "clock_dept": current.clock_dept,
                "days_with_punches": days_with_punches,
            })

    for r in range(sheet.nrows):
        row = _row_values(sheet, r)
        if _is_id_row(row):
            flush_roster()
            current = _parse_id_row(row)
            days_with_punches = 0
            continue
        if current is None:
            continue  # header area before the first person block
        if _is_day_number_row(row) or _is_weekday_row(row):
            continue
        # anything else is this person's punch row
        for col, cell_value in enumerate(row):
            punches = _split_punches(cell_value)
            if not punches:
                continue
            day = col + 1
            date = f"{year:04d}-{month:02d}-{day:02d}"
            days_with_punches += 1
            punches_long_rows.append({
                "outlet_clock": outlet_clock,
                "clock_id": current.clock_id,
                "clock_name": current.clock_name,
                "clock_dept": current.clock_dept,
                "date": date,
                "n_punches": len(punches),
                "first_punch": punches[0],
                "last_punch": punches[-1] if len(punches) > 1 else None,
                "all_punches": " | ".join(punches),
            })
    flush_roster()

    return punches_long_rows, roster_rows


def capture_folder(clock_exports_dir: Path, pattern: str = "*-All_Report*.xls") -> tuple[list[dict], list[dict]]:
    """Capture every matching clock export in a folder. Read-only on the files."""
    punches_long_rows: list[dict] = []
    roster_rows: list[dict] = []
    for xls_path in sorted(Path(clock_exports_dir).glob(pattern)):
        plr, rr = parse_attend_logs(xls_path)
        punches_long_rows.extend(plr)
        roster_rows.extend(rr)
    return punches_long_rows, roster_rows
