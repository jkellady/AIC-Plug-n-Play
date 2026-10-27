"""Tests for src/hrbook.py using a small synthetic workbook with fake names."""
import datetime
from pathlib import Path

import openpyxl
import pytest

from src.hrbook import read_hr_workbook

NAME_ROW, NAME_COL = 3, 4
OUTLET_COL = 8
MONTH_ROW, MONTH_COL = 2, 4
HEADER_ROW = 8
FIRST_DATA_ROW = 9


def _write_person_tab(wb, sheet_name, name_in_tab, outlet, month: datetime.datetime, day_rows: dict):
    ws = wb.create_sheet(sheet_name)
    ws.cell(NAME_ROW, NAME_COL, name_in_tab)
    ws.cell(NAME_ROW, OUTLET_COL, outlet)
    ws.cell(MONTH_ROW, MONTH_COL, month)
    ws.cell(HEADER_ROW, 4, "IN")
    ws.cell(HEADER_ROW, 5, "OUT")
    ws.cell(HEADER_ROW, 6, "Working")
    ws.cell(HEADER_ROW, 8, "OT 1.5")
    ws.cell(HEADER_ROW, 15, "Clock In")
    ws.cell(HEADER_ROW, 18, "Clock Out")
    for day, values in day_rows.items():
        row = FIRST_DATA_ROW + (day - 1)
        ws.cell(row, 3, day)
        for col, value in values.items():
            ws.cell(row, col, value)
    return ws


def _build_synthetic_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    del wb["Sheet"]

    _write_person_tab(
        wb, "FAKEONE", "Fake One", "Shop AA", datetime.datetime(2026, 5, 1),
        {
            # a normal day: formula-derived IN/OUT/working/OT, real punches
            1: {
                4: datetime.time(10, 0), 5: datetime.time(20, 0),
                6: "=E9-D9", 8: "=H9formula",
                15: datetime.time(9, 53), 18: datetime.time(20, 5),
            },
            # a leave day: typed working hours (8:00), no IN/OUT, no punches
            2: {6: datetime.time(8, 0), 8: datetime.time(0, 30)},
        },
    )
    # a draft/template tab for a different month -> must be skipped
    _write_person_tab(wb, "DRAFT_X", "XXX", "Shop ZZ", datetime.datetime(2023, 7, 1), {})

    wb.save(path)


def test_only_matching_month_tabs_are_parsed(tmp_path):
    xlsx_path = tmp_path / "hr.xlsx"
    _build_synthetic_workbook(xlsx_path)

    rows, skipped = read_hr_workbook(xlsx_path, 2026, 5)
    persons = {r["person"] for r in rows}
    assert persons == {"FAKEONE"}
    skipped_names = {name for name, _ in skipped}
    assert "DRAFT_X" in skipped_names
    assert any("2023-07" in reason for name, reason in skipped if name == "DRAFT_X")


def test_name_in_tab_and_outlet_are_captured(tmp_path):
    xlsx_path = tmp_path / "hr.xlsx"
    _build_synthetic_workbook(xlsx_path)
    rows, _ = read_hr_workbook(xlsx_path, 2026, 5)
    row = next(r for r in rows if r["date"] == datetime.date(2026, 5, 1))
    assert row["name_in_tab"] == "Fake One"
    assert row["tab_outlet"] == "Shop AA"


def test_formula_vs_typed_detection(tmp_path):
    xlsx_path = tmp_path / "hr.xlsx"
    _build_synthetic_workbook(xlsx_path)
    rows, _ = read_hr_workbook(xlsx_path, 2026, 5)

    day1 = next(r for r in rows if r["date"] == datetime.date(2026, 5, 1))
    assert day1["hq_working_hours_is_formula"] is True
    assert day1["hq_ot_1_5_is_formula"] is True
    assert day1["hq_in_is_formula"] is False
    assert day1["hq_out_is_formula"] is False

    day2 = next(r for r in rows if r["date"] == datetime.date(2026, 5, 2))
    assert day2["hq_working_hours"] == 8.0
    assert day2["hq_working_hours_raw"] == "08:00:00"
    assert day2["hq_working_hours_is_formula"] is False
    assert day2["hq_in"] is None
    assert day2["hq_out"] is None


def test_raw_punches_captured_in_order(tmp_path):
    xlsx_path = tmp_path / "hr.xlsx"
    _build_synthetic_workbook(xlsx_path)
    rows, _ = read_hr_workbook(xlsx_path, 2026, 5)
    day1 = next(r for r in rows if r["date"] == datetime.date(2026, 5, 1))
    assert day1["raw_punches"] == ["09:53", None, None, "20:05"]

    day2 = next(r for r in rows if r["date"] == datetime.date(2026, 5, 2))
    assert day2["raw_punches"] == [None, None, None, None]


def test_stray_values_beyond_month_length_are_ignored(tmp_path):
    # February 2026 (non-leap) has 28 days; a day-30 row with stray data
    # must never be emitted.
    xlsx_path = tmp_path / "hr_feb.xlsx"
    wb = openpyxl.Workbook()
    del wb["Sheet"]
    _write_person_tab(
        wb, "FAKEONE", "Fake One", "Shop AA", datetime.datetime(2026, 2, 1),
        {1: {4: datetime.time(10, 0), 5: datetime.time(18, 0)}},
    )
    ws = wb["FAKEONE"]
    # a stray value in an unused day-30 row (Feb has only 28 days), with no
    # day-number cell in column C
    ws.cell(FIRST_DATA_ROW + 29, 4, datetime.time(10, 0))
    wb.save(xlsx_path)

    rows, _ = read_hr_workbook(xlsx_path, 2026, 2)
    assert len(rows) == 1
    assert rows[0]["date"] == datetime.date(2026, 2, 1)


def test_file_is_never_written(tmp_path):
    xlsx_path = tmp_path / "hr.xlsx"
    _build_synthetic_workbook(xlsx_path)
    before = xlsx_path.stat()
    read_hr_workbook(xlsx_path, 2026, 5)
    after = xlsx_path.stat()
    assert before.st_size == after.st_size
    assert before.st_mtime == after.st_mtime
