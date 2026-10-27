"""Tests for src/masterdata.py.

Synthetic-fixture tests only, with fake names and codes. No real staff
names, IDs or master data are read here — see test_masterdata_real.py
(gitignored) for the real-May-data check.
"""
from pathlib import Path

import pandas as pd
import pytest

from src.masterdata import Flag, StaffAliasRecord, load_master_data


def _write_csv(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.fixture
def synthetic_master_data(tmp_path):
    _write_csv(tmp_path / "outlets.csv", [
        {"outlet_id": "AA", "code_on_clock_file": "AA", "name_on_record_tab": "Shop AA",
         "label_on_schedule": "AA", "other_names_used": "", "website_listing_crosscheck": "",
         "status": "Per your table", "client_confirm": ""},
    ])
    _write_csv(tmp_path / "staff_aliases.csv", [
        # STAFF_O case: comma inside parens must not split the entry
        {"id_initials": "FAKEIKA", "record_tab": "FAKEIKA", "full_name_on_tab": "Fake Ika",
         "clock_file_names": "FAKEIKA (AA); STAFF_O (AA, 0 punches)", "home_outlet_tab": "AA",
         "schedule_block": "", "evidence": "", "status": "Matched", "client_confirm": ""},
        # STAFF_N case: a parenthetical-only entry must be skipped, not treated as a name
        {"id_initials": "FAKETI", "record_tab": "", "full_name_on_tab": "",
         "clock_file_names": "(no clock file in batch)", "home_outlet_tab": "",
         "schedule_block": "", "evidence": "", "status": "Question", "client_confirm": ""},
        # ambiguous: "dupe" claimed by two different staff_ids
        {"id_initials": "FAKEONE", "record_tab": "FAKEONE", "full_name_on_tab": "Fake One",
         "clock_file_names": "dupe (AA)", "home_outlet_tab": "AA",
         "schedule_block": "", "evidence": "", "status": "Matched", "client_confirm": ""},
        {"id_initials": "FAKETWO", "record_tab": "FAKETWO", "full_name_on_tab": "Fake Two",
         "clock_file_names": "dupe (AA)", "home_outlet_tab": "AA",
         "schedule_block": "", "evidence": "", "status": "Matched", "client_confirm": ""},
    ])
    _write_csv(tmp_path / "schedule_codes.csv", [
        {"code": "OFF", "count_may_2026": "10", "day_type": "OFF", "start": "", "end": "",
         "hours_in_roster": "", "note": ""},
        {"code": "AM", "count_may_2026": "5", "day_type": "WORK", "start": "10:00", "end": "18:00",
         "hours_in_roster": "8.0", "note": "ASSUMED"},
    ])
    return load_master_data(tmp_path)


def test_zul_and_ti_comma_handling(synthetic_master_data):
    # "STAFF_O (AA, 0 punches)" must map to FAKEIKA, not be split on the comma
    record = synthetic_master_data.map_clock_name("STAFF_O")
    assert isinstance(record, StaffAliasRecord)
    assert record.staff_id == "FAKEIKA"

    record = synthetic_master_data.map_clock_name("FAKEIKA")
    assert isinstance(record, StaffAliasRecord)
    assert record.staff_id == "FAKEIKA"

    # the parenthetical-only "(no clock file in batch)" entry contributes no
    # clock name, so STAFF_N (whose row has no real clock name) must be unmapped
    record = synthetic_master_data.map_clock_name("STAFF_N")
    assert isinstance(record, Flag)
    assert record.kind == "unmapped_clock_name"


def test_map_clock_name_case_insensitive(synthetic_master_data):
    record = synthetic_master_data.map_clock_name("fakeika")
    assert isinstance(record, StaffAliasRecord)
    assert record.staff_id == "FAKEIKA"


def test_ambiguous_clock_name(synthetic_master_data):
    record = synthetic_master_data.map_clock_name("dupe")
    assert isinstance(record, Flag)
    assert record.kind == "ambiguous_clock_name"
    assert "FAKEONE" in record.detail
    assert "FAKETWO" in record.detail


def test_schedule_code_off_row_has_empty_start_end(synthetic_master_data):
    record = synthetic_master_data.lookup_schedule_code("OFF")
    assert record.day_type == "OFF"
    assert record.start == ""
    assert record.end == ""
    assert record.hours_in_roster is None


def test_schedule_code_case_and_whitespace_insensitive(synthetic_master_data):
    record = synthetic_master_data.lookup_schedule_code("  am  ")
    assert record.day_type == "WORK"
    assert record.hours_in_roster == 8.0


def test_unknown_schedule_code_flags(synthetic_master_data):
    record = synthetic_master_data.lookup_schedule_code("12-9PM")
    assert isinstance(record, Flag)
    assert record.kind == "unknown_schedule_code"
