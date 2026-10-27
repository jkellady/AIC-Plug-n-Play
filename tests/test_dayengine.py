"""Tests for src/dayengine.py using synthetic data with fake names.

No real staff names, IDs or master data are read here.
"""
import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.dayengine import (
    MEANING_TO_CONFIRM_MARKER,
    REMARK_CONFIRMED_RULE,
    REMARK_PENDING_QUESTION,
    REMARK_POSSIBLE_ERROR,
    SEVERITY_CHECK,
    SEVERITY_INFO,
    build_day_check,
)
from src.masterdata import load_master_data
from src.rules import load_rules

OUTLETS_CSV = (
    "outlet_id,code_on_clock_file,name_on_record_tab,label_on_schedule,other_names_used,"
    "website_listing_crosscheck,status,client_confirm\n"
    "AA,AA,Shop AA,AA,,,Per your table,\n"
    "BB,BB,Shop BB,BB,,,Per your table,\n"
)
STAFF_ALIASES_CSV = (
    "id_initials,record_tab,full_name_on_tab,clock_file_names,home_outlet_tab,"
    "schedule_block,evidence,status,client_confirm\n"
    "FAKEONE,FAKEONE,Fake One,fakeone (AA),AA,AA,,Matched,\n"
    "FAKETWO,FAKETWO,Fake Two,faketwo (AA); faketwo2 (BB),AA,AA,,Matched,\n"
    "FAKENOCLOCK,FAKENOCLOCK,Fake NoClock,(no clock file in batch),AA,AA,,Draft,\n"
)
SCHEDULE_CODES_CSV = (
    "code,count_may_2026,day_type,start,end,hours_in_roster,note\n"
    "AM,5,WORK,10:00,18:00,8.0,ASSUMED\n"
    "OFF,5,OFF,,,,\n"
    "OFF*,1,OFF,,,,asterisk meaning to confirm\n"
    "PM,5,WORK,14:00,22:00,8.0,ASSUMED\n"
)

RULES_TOML_TEMPLATE = """
[meta]
schema_version = 1
allowed_status = ["CONFIRMED", "ASSUMED", "OPEN"]

[rules.morning_open]
label = "x"
value = "10:00"
status = "CONFIRMED"
question = "C6"
source = "fake"

[rules.early_clock_in_counts_as_open]
label = "x"
value = true
status = "CONFIRMED"
question = "C6"
source = "fake"

[rules.late_grace_minutes]
label = "x"
value = {late_grace_minutes}
status = "CONFIRMED"
question = "C5"
source = "fake"

[rules.late_clears_if_approved]
label = "x"
value = true
status = "CONFIRMED"
question = "C5"
source = "fake"

[rules.transfer_is_error]
label = "x"
value = false
status = "CONFIRMED"
question = "C4"
source = "fake"

[rules.ot_sum_from_clock]
label = "x"
value = true
status = "CONFIRMED"
question = "C7"
source = "fake"

[rules.late_deduction_mode]
label = "x"
value = "show_both"
options = ["actual_minutes", "round_up", "show_both"]
status = "OPEN"
question = "Q1"
source = ""

[rules.late_minutes_counted_from]
label = "x"
value = "opening"
options = ["opening", "end_of_grace"]
status = "ASSUMED"
question = "Q1"
source = ""

[rules.grace_applies_to_all_shifts]
label = "x"
value = true
options = [true, false]
status = "ASSUMED"
question = "Q1"
source = ""

[rules.late_round_up_to_minutes]
label = "x"
value = 30
status = "OPEN"
question = "Q1"
source = ""

[rules.approval_source]
label = "x"
value = "none"
options = ["none", "approvals_csv"]
status = "OPEN"
question = "Q8"
source = ""

[rules.count_time_after_end]
label = "x"
value = false
status = "ASSUMED"
question = "Q2"
source = ""

[rules.ot_rate_day_types]
label = "x"
value = ""
status = "OPEN"
question = "Q3"
source = ""

[rules.single_punch_classification]
label = "x"
value = "closer_to_boundary"
status = "ASSUMED"
question = "Q12"
source = ""

[rules.single_punch_tie_minutes]
label = "x"
value = 60
status = "ASSUMED"
question = "Q12"
source = ""

[rules.duplicate_punch_window_minutes]
label = "x"
value = 5
status = "ASSUMED"
question = "Q15"
source = ""

[rules.duplicate_punch_keep]
label = "x"
value = "first"
options = ["first", "last"]
status = "ASSUMED"
question = "Q15"
source = ""

[rules.upl_absent_unpaid]
label = "x"
value = "unknown"
options = ["yes", "no", "unknown"]
status = "OPEN"
question = "Q11"
source = ""

[rules.excluded_staff_ids]
label = "x"
value = []
status = "ASSUMED"
question = "Q6"
source = ""
"""


def _write_master_data(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "outlets.csv").write_text(OUTLETS_CSV, encoding="utf-8")
    (tmp_path / "staff_aliases.csv").write_text(STAFF_ALIASES_CSV, encoding="utf-8")
    (tmp_path / "schedule_codes.csv").write_text(SCHEDULE_CODES_CSV, encoding="utf-8")
    return load_master_data(tmp_path)


def _write_rules(tmp_path: Path, late_grace_minutes: int = 15):
    tmp_path.mkdir(parents=True, exist_ok=True)
    toml_path = tmp_path / "rules.toml"
    toml_path.write_text(
        RULES_TOML_TEMPLATE.format(late_grace_minutes=late_grace_minutes), encoding="utf-8"
    )
    return load_rules(toml_path)


def _punch_row(clock_name, outlet, date, times):
    times = sorted(times)
    return {
        "outlet_clock": outlet, "clock_id": "1", "clock_name": clock_name, "clock_dept": "",
        "date": date, "n_punches": len(times), "first_punch": times[0],
        "last_punch": times[-1] if len(times) > 1 else None,
        "all_punches": " | ".join(times),
    }


def _schedule_row(person, date, raw_code, resolved, outlet_block="AA"):
    return {"person": person, "date": date, "outlet_block": outlet_block, "raw_code": raw_code,
            "hours": None, "resolved": resolved}


@pytest.fixture
def master_data(tmp_path):
    return _write_master_data(tmp_path / "md")


@pytest.fixture
def rules(tmp_path):
    return _write_rules(tmp_path / "rules")


def _flags_named(flags, name):
    return [f for f in flags if f.flag == name]


def test_on_time_punch_no_late_flag(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["09:56", "18:01"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    assert not _flags_named(flags, "Late in (over 15 min)")
    # 09:56 is before the 10:00 opening, so it is recorded as the opening time
    assert day_check[0]["system_in"] == "10:00"


def test_early_clock_in_recorded_as_opening(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["09:50", "18:00"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    assert day_check[0]["system_in"] == "10:00"
    assert not _flags_named(flags, "Late in (over 15 min)")


def test_late_in_beyond_grace_show_both_mode(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["10:20", "18:00"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    late = _flags_named(flags, "Late in (over 15 min)")
    assert len(late) == 1
    assert "20 min late" in late[0].details
    assert "Rounded up to :30 step: 30 min" in late[0].details
    assert late[0].remark_type == REMARK_PENDING_QUESTION
    assert "Q1" in late[0].ref and "Q8" in late[0].ref
    assert late[0].severity == SEVERITY_CHECK


def test_late_in_on_non_morning_open_shift_flags_q1(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "PM", master_data.lookup_schedule_code("PM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["14:20", "22:00"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    late = _flags_named(flags, "Late in (over 15 min)")
    assert len(late) == 1
    assert late[0].remark_type == REMARK_PENDING_QUESTION
    assert "Q1" in late[0].ref


def test_changing_grace_minutes_changes_output_with_no_code_change(tmp_path, master_data):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["10:20", "18:00"])]

    rules_strict = _write_rules(tmp_path / "strict", late_grace_minutes=15)
    _, flags_strict = build_day_check(schedule, punches, master_data, rules_strict)
    assert _flags_named(flags_strict, "Late in (over 15 min)")

    rules_loose = _write_rules(tmp_path / "loose", late_grace_minutes=25)
    _, flags_loose = build_day_check(schedule, punches, master_data, rules_loose)
    assert not _flags_named(flags_loose, "Late in (over 15 min)")


def test_single_punch_closer_to_start_is_missing_clock_out(master_data, rules):
    # AM shift 10:00-18:00, midpoint 14:00; 11:00 is 180 min from midpoint,
    # well outside the 60-minute tie zone, and closer to the start.
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["11:00"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    missing = _flags_named(flags, "Missing clock-out")
    assert len(missing) == 1
    assert missing[0].remark_type == REMARK_PENDING_QUESTION
    assert missing[0].ref == "Q12"


def test_single_punch_closer_to_end_is_missing_clock_in(master_data, rules):
    # AM shift 10:00-18:00, midpoint 14:00; 16:00 is 120 min from midpoint,
    # outside the tie zone, and closer to the end.
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["16:00"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    missing = _flags_named(flags, "Missing clock-in")
    assert len(missing) == 1
    assert missing[0].remark_type == REMARK_PENDING_QUESTION
    assert missing[0].ref == "Q12"


def test_single_punch_in_tie_zone_is_unclear(master_data, rules):
    # AM shift 10:00-18:00, midpoint 14:00; 14:30 is 30 min from midpoint,
    # inside the default 60-minute tie zone.
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["14:30"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    unclear = _flags_named(flags, "Single punch, cannot tell clock-in from clock-out")
    assert len(unclear) == 1
    assert not _flags_named(flags, "Missing clock-in")
    assert not _flags_named(flags, "Missing clock-out")


def test_single_punch_with_no_schedule_row_is_unclear(master_data, rules):
    date = datetime.date(2026, 5, 4)
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["15:00"])]
    day_check, flags = build_day_check([], punches, master_data, rules)
    unclear = _flags_named(flags, "Single punch, cannot tell clock-in from clock-out")
    assert len(unclear) == 1
    assert unclear[0].person == "FAKEONE"
    assert "No schedule row" in unclear[0].details


def test_missing_clock_in_and_out(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    day_check, flags = build_day_check(schedule, [], master_data, rules)
    missing = _flags_named(flags, "Missing clock-in and clock-out")
    assert len(missing) == 1


def test_double_tap_at_clock_in_only_gives_missing_clock_out(master_data, rules):
    # two near-simultaneous clock-in taps (4 min apart, within the 5-minute
    # duplicate window) with no real clock-out must collapse to ONE punch
    # and flag "Missing clock-out" -- never be treated as a complete
    # in/out pair just because there happen to be two punch times.
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["09:51", "09:55"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    assert len(day_check) == 1
    assert day_check[0]["system_out"] is None
    missing = _flags_named(flags, "Missing clock-out")
    assert len(missing) == 1
    assert not _flags_named(flags, "Missing clock-in and clock-out")


def test_transfer_flag_is_info_and_confirmed_rule(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "BB", "2026-05-04", ["09:56", "18:01"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    transfer = _flags_named(flags, "Punched at another outlet")
    assert len(transfer) == 1
    assert transfer[0].severity == SEVERITY_INFO
    assert transfer[0].remark_type == REMARK_CONFIRMED_RULE
    assert transfer[0].ref == "C4"


def test_multi_outlet_same_day_merges_into_one_row_and_one_info_flag(master_data, rules):
    date = datetime.date(2026, 5, 11)
    schedule = [_schedule_row("FAKETWO", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [
        _punch_row("faketwo", "AA", "2026-05-11", ["10:05"]),
        _punch_row("faketwo2", "BB", "2026-05-11", ["20:05"]),
    ]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    assert len(day_check) == 1
    row = day_check[0]
    assert row["system_in"] == "10:05"
    assert row["system_out"] == "20:05"
    assert row["clock_outlet"] == "AA, BB"
    transfer = _flags_named(flags, "Punched at another outlet")
    assert len(transfer) == 1
    assert "AA" in transfer[0].details and "BB" in transfer[0].details


def test_time_after_scheduled_end_is_info_pending_question(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["09:56", "18:45"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    after_end = _flags_named(flags, "Stayed after scheduled end")
    assert len(after_end) == 1
    assert after_end[0].severity == SEVERITY_INFO
    assert after_end[0].remark_type == REMARK_PENDING_QUESTION
    assert after_end[0].ref == "Q2"


def test_punch_on_off_day_flags_pending_question_q3(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "OFF", master_data.lookup_schedule_code("OFF"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["10:00", "18:00"])]
    day_check, flags = build_day_check(schedule, punches, master_data, rules)
    worked = _flags_named(flags, "Punched on a OFF day")
    assert len(worked) == 1
    assert worked[0].remark_type == REMARK_PENDING_QUESTION
    assert worked[0].ref == "Q3"


def test_meaning_to_confirm_code_flags_pending_question(master_data, rules):
    date = datetime.date(2026, 5, 4)
    resolved = master_data.lookup_schedule_code("OFF*")
    assert MEANING_TO_CONFIRM_MARKER in resolved.note.lower()
    schedule = [_schedule_row("FAKEONE", date, "OFF*", resolved)]
    day_check, flags = build_day_check(schedule, [], master_data, rules)
    needs_confirm = _flags_named(flags, "Schedule code needs confirming")
    assert len(needs_confirm) == 1
    assert needs_confirm[0].remark_type == REMARK_PENDING_QUESTION


def test_unmapped_clock_name_passes_through_as_flag(master_data, rules):
    punches = [_punch_row("totally_unknown_name", "AA", "2026-05-04", ["09:56", "18:00"])]
    day_check, flags = build_day_check([], punches, master_data, rules)
    unmapped = [f for f in flags if "unmapped_clock_name" in f.flag]
    assert len(unmapped) == 1
    assert unmapped[0].remark_type == REMARK_POSSIBLE_ERROR


def test_staff_with_no_clock_file_gets_one_month_info_flag_and_no_missing_punch_flags(master_data, rules):
    date1 = datetime.date(2026, 5, 4)
    date2 = datetime.date(2026, 5, 5)
    schedule = [
        _schedule_row("FAKENOCLOCK", date1, "AM", master_data.lookup_schedule_code("AM")),
        _schedule_row("FAKENOCLOCK", date2, "AM", master_data.lookup_schedule_code("AM")),
    ]
    day_check, flags = build_day_check(schedule, [], master_data, rules)
    assert not _flags_named(flags, "Missing clock-in and clock-out")
    month_flags = [f for f in flags if f.flag == "No clock file in batch" and f.person == "FAKENOCLOCK"]
    assert len(month_flags) == 1
    assert month_flags[0].date == "Month"


def test_excluded_staff_produces_no_day_check_rows_or_flags(tmp_path, master_data):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["10:20", "18:00"])]  # would normally be a late flag

    excluded_rules_text = RULES_TOML_TEMPLATE.format(late_grace_minutes=15).replace(
        '[rules.excluded_staff_ids]\nlabel = "x"\nvalue = []',
        '[rules.excluded_staff_ids]\nlabel = "x"\nvalue = ["FAKEONE"]',
    )
    rules_path = tmp_path / "rules"
    rules_path.mkdir(parents=True, exist_ok=True)
    (rules_path / "rules.toml").write_text(excluded_rules_text, encoding="utf-8")
    excluded_rules = load_rules(rules_path / "rules.toml")

    day_check, flags = build_day_check(schedule, punches, master_data, excluded_rules)
    assert day_check == []
    assert not _flags_named(flags, "Late in (over 15 min)")
    assert all(f.person != "FAKEONE" for f in flags)


def test_approved_late_in_is_info_and_confirmed_rule(master_data, rules):
    date = datetime.date(2026, 5, 4)
    schedule = [_schedule_row("FAKEONE", date, "AM", master_data.lookup_schedule_code("AM"))]
    punches = [_punch_row("fakeone", "AA", "2026-05-04", ["10:20", "18:00"])]
    approvals = [{"person": "FAKEONE", "date": "2026-05-04", "status": "approved"}]
    day_check, flags = build_day_check(schedule, punches, master_data, rules, approvals=approvals)
    approved = _flags_named(flags, "Late in (over 15 min), approved")
    assert len(approved) == 1
    assert approved[0].severity == SEVERITY_INFO
    assert approved[0].remark_type == REMARK_CONFIRMED_RULE
    assert approved[0].ref == "C5"
    assert not _flags_named(flags, "Late in (over 15 min)")
