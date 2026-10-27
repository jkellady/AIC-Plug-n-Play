"""Tests for src/hrcompare.py using synthetic data with fake names.

No real staff names, IDs or master data are read here. hr_rows,
day_check_rows, schedule_rows and day_engine_flags are hand-built dicts
matching the shapes produced by src/hrbook.py, src/dayengine.py and
src/schedule.py, so each test can focus on one comparison without
needing a real workbook.
"""
import datetime
from pathlib import Path

import pytest

from src.dayengine import (
    REMARK_CONFIRMED_RULE,
    REMARK_PENDING_QUESTION,
    REMARK_POSSIBLE_ERROR,
    DayFlag,
)
from src.hrcompare import compare_hr_with_system
from src.masterdata import load_master_data
from src.rules import load_rules

OUTLETS_CSV = (
    "outlet_id,code_on_clock_file,name_on_record_tab,label_on_schedule,other_names_used,"
    "website_listing_crosscheck,status,client_confirm\n"
    "AA,AA,Shop AA,AA,,,Per your table,\n"
)
STAFF_ALIASES_CSV = (
    "id_initials,record_tab,full_name_on_tab,clock_file_names,home_outlet_tab,"
    "schedule_block,evidence,status,client_confirm\n"
    "FAKEONE,FAKEONE,Fake One,fakeone (AA),AA,AA,,Matched,\n"
    "FAKETWO,FAKETWO,Fake Two,faketwo (AA),AA,AA,,Matched,\n"
    "FAKENOCLOCK,FAKENOCLOCK,Fake NoClock,(no clock file in batch),AA,AA,,Draft,\n"
)
SCHEDULE_CODES_CSV = (
    "code,count_may_2026,day_type,start,end,hours_in_roster,note\n"
    "AM,5,WORK,10:00,18:00,8.0,ASSUMED\n"
    "UPL,5,LEAVE,,,8.0,Q11\n"
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
value = 15
status = "CONFIRMED"
question = "C5"
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

[rules.standard_day_hours]
label = "x"
value = 8.5
status = "{standard_day_hours_status}"
question = "Q11"
source = "{standard_day_hours_source}"

[rules.leave_day_hours]
label = "x"
value = {leave_day_hours}
status = "{leave_day_hours_status}"
question = "Q11"
source = "{leave_day_hours_source}"

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

[rules.early_clock_in_counts_as_start_all_shifts]
label = "x"
value = true
status = "ASSUMED"
question = "Q1b"
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


def _write_rules(
    tmp_path: Path, leave_day_hours=8.5, leave_day_hours_status="ASSUMED", leave_day_hours_source="",
    standard_day_hours_status="ASSUMED", standard_day_hours_source="",
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    toml_path = tmp_path / "rules.toml"
    toml_path.write_text(
        RULES_TOML_TEMPLATE.format(
            leave_day_hours=leave_day_hours,
            leave_day_hours_status=leave_day_hours_status,
            leave_day_hours_source=leave_day_hours_source,
            standard_day_hours_status=standard_day_hours_status,
            standard_day_hours_source=standard_day_hours_source,
        ),
        encoding="utf-8",
    )
    return load_rules(toml_path)


def _hr_row(person, name_in_tab, tab_outlet, date, **overrides):
    row = {
        "person": person, "name_in_tab": name_in_tab, "tab_outlet": tab_outlet, "date": date,
        "hq_in": None, "hq_in_raw": "", "hq_in_is_formula": False,
        "hq_out": None, "hq_out_raw": "", "hq_out_is_formula": False,
        "hq_working_hours": None, "hq_working_hours_raw": "", "hq_working_hours_is_formula": True,
        "hq_ot_1_5": None, "hq_ot_1_5_raw": "", "hq_ot_1_5_is_formula": True,
        "raw_punches": [None, None, None, None],
    }
    row.update(overrides)
    return row


def _day_check_row(person, date, **overrides):
    row = {
        "person": person, "date": date, "day": date.strftime("%a"),
        "schedule_code": "AM", "type": "WORK", "shift_start": "10:00", "shift_end": "18:00",
        "punches": None, "clock_outlet": "AA", "system_in": None, "system_out": None,
        "hq_in": None, "hq_out": None, "flag_refs": [],
    }
    row.update(overrides)
    return row


@pytest.fixture
def master_data(tmp_path):
    return _write_master_data(tmp_path / "md")


@pytest.fixture
def rules(tmp_path):
    return _write_rules(tmp_path / "rules")


def _flags_named(flags, name):
    return [f for f in flags if f.flag == name]


def test_hq_recorded_later_start_than_rule_allows(master_data, rules):
    # punch is 5 min late, inside the 15-min grace -- the rule says this is
    # not late at all, but HQ typed a later (rounded) start anyway.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date, system_in="10:05", system_out="18:00", punches="10:05, 18:00")]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        hq_in="10:30", hq_in_is_formula=False, hq_out="18:00", hq_out_is_formula=False,
        hq_working_hours=8.0, hq_working_hours_is_formula=True,
        raw_punches=["10:05", None, None, "18:00"],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    found = _flags_named(flags, "HQ recorded a later start than the rule allows")
    assert len(found) == 1
    assert found[0].remark_type == REMARK_CONFIRMED_RULE


def test_early_arrival_on_non_morning_shift_does_not_flag(master_data, rules):
    # punch is BEFORE a non-10:00 shift start -- HQ recording the scheduled
    # start for an early arrival is normal practice, not a rule violation.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row(
        "FAKEONE", date, schedule_code="PM", shift_start="12:00", shift_end="22:00",
        system_in="11:54", system_out="22:00", punches="11:54, 22:00",
    )]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        hq_in="12:00", hq_in_is_formula=False, hq_out="22:00", hq_out_is_formula=False,
        raw_punches=["11:54", None, None, "22:00"],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    assert not _flags_named(flags, "HQ recorded a later start than the rule allows")
    assert not _flags_named(flags, "HQ rounded a late start up to :30")


def test_early_arrival_flags_when_rule_set_to_false(tmp_path, master_data):
    # same scenario as test_early_arrival_on_non_morning_shift_does_not_flag,
    # but early_clock_in_counts_as_start_all_shifts is set to false here.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row(
        "FAKEONE", date, schedule_code="PM", shift_start="12:00", shift_end="22:00",
        system_in="11:54", system_out="22:00", punches="11:54, 22:00",
    )]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        hq_in="12:00", hq_in_is_formula=False, hq_out="22:00", hq_out_is_formula=False,
        raw_punches=["11:54", None, None, "22:00"],
    )]
    rules_false = RULES_TOML_TEMPLATE.format(
        leave_day_hours=8.5, leave_day_hours_status="ASSUMED", leave_day_hours_source="",
        standard_day_hours_status="ASSUMED", standard_day_hours_source="",
    ).replace(
        '[rules.early_clock_in_counts_as_start_all_shifts]\nlabel = "x"\nvalue = true',
        '[rules.early_clock_in_counts_as_start_all_shifts]\nlabel = "x"\nvalue = false',
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    toml_path = tmp_path / "rules.toml"
    toml_path.write_text(rules_false, encoding="utf-8")
    rules_obj = load_rules(toml_path)

    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules_obj)
    found = _flags_named(flags, "HQ recorded a later start than the rule allows")
    assert len(found) == 1
    assert found[0].remark_type == REMARK_PENDING_QUESTION
    assert found[0].ref == "Q1b"


def test_hq_rounded_late_start_up_to_30(master_data, rules):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date, system_in="10:20", system_out="18:00", punches="10:20, 18:00")]
    late_flag = DayFlag(
        severity="Check", flag="Late in (over 15 min)", person="FAKEONE", home_outlet_tab="AA",
        date=date, details="", remark="", remark_type=REMARK_PENDING_QUESTION, ref="Q1",
    )
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        hq_in="10:30", hq_in_is_formula=False, hq_out="18:00", hq_out_is_formula=False,
        raw_punches=["10:20", None, None, "18:00"],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [late_flag], [], master_data, rules)
    found = _flags_named(flags, "HQ rounded a late start up to :30")
    assert len(found) == 1
    assert found[0].remark_type == REMARK_PENDING_QUESTION
    assert "Q1" in found[0].ref
    assert "10:20" in found[0].details and "10:30" in found[0].details


def test_day_typed_8_instead_of_8_30(master_data, rules):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date, schedule_code="UPL", type="LEAVE", shift_start=None, shift_end=None)]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        hq_working_hours=8.0, hq_working_hours_is_formula=False,
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    found = _flags_named(flags, "Day typed as 8:00 instead of 8:30")
    assert len(found) == 1
    assert found[0].remark_type == REMARK_PENDING_QUESTION
    assert found[0].ref == "Q11"


def test_day_typed_8_instead_of_8_30_matches_subtyped_leave(master_data, rules):
    # day_type can be "LEAVE (medical)"/"LEAVE (annual)" (from schedule_codes.csv
    # for MC/AL codes), not only the plain "LEAVE" used for UPL.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row(
        "FAKEONE", date, schedule_code="MC*", type="LEAVE (medical)", shift_start=None, shift_end=None,
    )]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        hq_working_hours=8.0, hq_working_hours_is_formula=False,
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    assert len(_flags_named(flags, "Day typed as 8:00 instead of 8:30")) == 1


def test_punches_in_tab_differ_from_clock_file(master_data, rules):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date, system_in="09:52", system_out="12:03", punches="09:52, 12:03")]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        raw_punches=["09:52", None, None, None],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    found = _flags_named(flags, "Raw punches pasted in the tab differ from the clock file")
    assert len(found) == 1
    assert found[0].remark_type == REMARK_POSSIBLE_ERROR
    assert "09:52, 12:03" in found[0].details
    assert "09:52" in found[0].details


def test_duplicate_tap_collapsed_reports_info_not_differ(master_data, rules):
    # clock file has a near-duplicate tap 4 min after the real clock-in
    # (within the default 5-minute window); the tab correctly dropped it.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row(
        "FAKEONE", date, system_in="09:51", system_out="22:26", punches="09:51, 09:55, 22:26",
    )]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        raw_punches=["09:51", None, None, "22:26"],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    assert not _flags_named(flags, "Raw punches pasted in the tab differ from the clock file")
    found = _flags_named(flags, "Clock file has a likely duplicate tap, dropped in the tab")
    assert len(found) == 1
    assert found[0].severity == "Info"
    assert "09:55" in found[0].details


def test_duplicate_keep_first_does_not_auto_match_a_keep_last_tab(master_data, rules):
    # with duplicate_punch_keep="first" (the default), a tab that actually
    # kept the LATER punch of a near-duplicate pair must NOT be auto-matched
    # by trying the other policy -- it stays a genuine "punches differ".
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row(
        "FAKEONE", date, system_in="09:54", system_out="21:11", punches="09:54, 21:08, 21:11",
    )]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        raw_punches=["09:54", None, None, "21:11"],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    assert not _flags_named(flags, "Clock file has a likely duplicate tap, dropped in the tab")
    found = _flags_named(flags, "Raw punches pasted in the tab differ from the clock file")
    assert len(found) == 1


def test_duplicate_keep_last_resolves_a_keep_last_tab(tmp_path, master_data):
    # the same scenario, but with duplicate_punch_keep set to "last" --
    # now it resolves to the Info flag.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row(
        "FAKEONE", date, system_in="09:54", system_out="21:11", punches="09:54, 21:08, 21:11",
    )]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        raw_punches=["09:54", None, None, "21:11"],
    )]
    rules_keep_last_text = RULES_TOML_TEMPLATE.format(
        leave_day_hours=8.5, leave_day_hours_status="ASSUMED", leave_day_hours_source="",
        standard_day_hours_status="ASSUMED", standard_day_hours_source="",
    ).replace(
        '[rules.duplicate_punch_keep]\nlabel = "x"\nvalue = "first"',
        '[rules.duplicate_punch_keep]\nlabel = "x"\nvalue = "last"',
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    toml_path = tmp_path / "rules.toml"
    toml_path.write_text(rules_keep_last_text, encoding="utf-8")
    rules_keep_last = load_rules(toml_path)

    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules_keep_last)
    assert not _flags_named(flags, "Raw punches pasted in the tab differ from the clock file")
    found = _flags_named(flags, "Clock file has a likely duplicate tap, dropped in the tab")
    assert len(found) == 1
    assert "21:08" in found[0].details


def test_gap_beyond_duplicate_window_still_flags_as_differ(master_data, rules):
    # a 19-minute gap exceeds the 5-minute duplicate window, so it is a
    # real missing punch, not a collapsible duplicate.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row(
        "FAKEONE", date, system_in="09:50", system_out="20:28", punches="09:50, 20:09, 20:28",
    )]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        raw_punches=["09:50", None, None, "20:28"],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    assert not _flags_named(flags, "Clock file has a likely duplicate tap, dropped in the tab")
    found = _flags_named(flags, "Raw punches pasted in the tab differ from the clock file")
    assert len(found) == 1
    assert "20:09" in found[0].details


def test_outlet_differs_between_schedule_and_record_tab(master_data, rules):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date)]
    schedule_rows = [{"person": "FAKEONE", "date": date, "outlet_block": "OUT2", "raw_code": "AM", "hours": None, "resolved": None}]
    hr_rows = [_hr_row("FAKEONE", "FAKEONE", "Client OUT1", date)]
    flags = compare_hr_with_system(hr_rows, day_check, [], schedule_rows, master_data, rules)
    found = _flags_named(flags, "Outlet differs between schedule and record tab")
    assert len(found) == 1
    assert found[0].remark_type == REMARK_POSSIBLE_ERROR


def test_name_in_tab_differs_from_tab_name(master_data, rules):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date)]
    hr_rows = [_hr_row("FAKEONE", "SOMEONE ELSE", "AA", date)]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    found = _flags_named(flags, "Tab name differs from the name typed inside it")
    assert len(found) == 1
    assert found[0].remark_type == REMARK_POSSIBLE_ERROR
    assert "SOMEONE ELSE" in found[0].details


def test_sheet_name_short_id_with_full_name_inside_is_normal(master_data, rules):
    # a tab named by short ID ("FAKETWO") with the person's full name typed
    # inside (matching staff_aliases.csv full_name_on_tab) is normal and
    # must not flag, even though name_in_tab != the sheet name itself.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKETWO", date)]
    hr_rows = [_hr_row("FAKETWO", "Fake Two", "AA", date)]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    assert not _flags_named(flags, "Tab name differs from the name typed inside it")


def test_two_different_tabs_sharing_the_same_name_in_tab_flags_both(master_data, rules):
    # full_name_on_tab comes from the same workbook, so comparing name_in_tab
    # to it cannot catch a copied tab. If two DIFFERENT record tabs both
    # have the same name_in_tab (e.g. a tab literally copied from another
    # person's), both staff_ids must be flagged.
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date), _day_check_row("FAKETWO", date)]
    hr_rows = [
        _hr_row("FAKEONE", "Copied Name", "AA", date),
        _hr_row("FAKETWO", "Copied Name", "AA", date),
    ]
    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules)
    found = _flags_named(flags, "Same name typed in two different tabs")
    assert {f.person for f in found} == {"FAKEONE", "FAKETWO"}
    assert len(found) == 2
    for f in found:
        assert f.date == "Month"
        assert f.remark_type == REMARK_POSSIBLE_ERROR


def test_excluded_staff_produces_no_flags(tmp_path, master_data):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date, schedule_code="UPL", type="LEAVE", shift_start=None, shift_end=None)]
    hr_rows = [_hr_row(
        "FAKEONE", "SOMEONE ELSE", "Client OUT1", date,
        hq_working_hours=8.0, hq_working_hours_is_formula=False,
    )]
    excluded_rules_text = RULES_TOML_TEMPLATE.format(
        leave_day_hours=8.5, leave_day_hours_status="ASSUMED", leave_day_hours_source="",
        standard_day_hours_status="ASSUMED", standard_day_hours_source="",
    ).replace(
        '[rules.excluded_staff_ids]\nlabel = "x"\nvalue = []',
        '[rules.excluded_staff_ids]\nlabel = "x"\nvalue = ["FAKEONE"]',
    )
    rules_path = tmp_path / "rules"
    rules_path.mkdir(parents=True, exist_ok=True)
    (rules_path / "rules.toml").write_text(excluded_rules_text, encoding="utf-8")
    excluded_rules = load_rules(rules_path / "rules.toml")

    flags = compare_hr_with_system(hr_rows, day_check, [], [], master_data, excluded_rules)
    assert flags == []


def test_no_clock_file_only_runs_flags_3_5_6_plus_one_month_info(master_data, rules):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKENOCLOCK", date, schedule_code="UPL", type="LEAVE", shift_start=None, shift_end=None)]
    schedule_rows = [{"person": "FAKENOCLOCK", "date": date, "outlet_block": "OUT2", "raw_code": "UPL", "hours": None, "resolved": None}]
    hr_rows = [_hr_row(
        "FAKENOCLOCK", "SOMEONE ELSE", "Client OUT1", date,
        hq_in="10:30", hq_in_is_formula=False,
        hq_working_hours=8.0, hq_working_hours_is_formula=False,
        raw_punches=["09:52", None, None, "18:00"],
    )]
    flags = compare_hr_with_system(hr_rows, day_check, [], schedule_rows, master_data, rules)

    assert not _flags_named(flags, "HQ recorded a later start than the rule allows")
    assert not _flags_named(flags, "HQ rounded a late start up to :30")
    assert not _flags_named(flags, "Raw punches pasted in the tab differ from the clock file")

    assert len(_flags_named(flags, "Day typed as 8:00 instead of 8:30")) == 1
    assert len(_flags_named(flags, "Outlet differs between schedule and record tab")) == 1
    assert len(_flags_named(flags, "Tab name differs from the name typed inside it")) == 1

    month_flags = _flags_named(flags, "No clock file in batch")
    assert len(month_flags) == 1
    assert month_flags[0].person == "FAKENOCLOCK"
    assert month_flags[0].date == "Month"


def test_changing_leave_day_hours_changes_flag_presence_and_remark_type(tmp_path, master_data):
    date = datetime.date(2026, 5, 4)
    day_check = [_day_check_row("FAKEONE", date, schedule_code="UPL", type="LEAVE", shift_start=None, shift_end=None)]
    hr_rows = [_hr_row(
        "FAKEONE", "FAKEONE", "AA", date,
        hq_working_hours=8.0, hq_working_hours_is_formula=False,
    )]

    # value matches the typed figure -> no flag at all
    rules_match = _write_rules(tmp_path / "match", leave_day_hours=8.0)
    flags_match = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules_match)
    assert not _flags_named(flags_match, "Day typed as 8:00 instead of 8:30")

    # value differs, still ASSUMED -> flag, Pending question
    rules_assumed = _write_rules(tmp_path / "assumed", leave_day_hours=8.5, leave_day_hours_status="ASSUMED")
    flags_assumed = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules_assumed)
    found_assumed = _flags_named(flags_assumed, "Day typed as 8:00 instead of 8:30")
    assert len(found_assumed) == 1
    assert found_assumed[0].remark_type == REMARK_PENDING_QUESTION

    # value differs, now CONFIRMED -> same flag, but Confirmed rule
    rules_confirmed = _write_rules(
        tmp_path / "confirmed", leave_day_hours=8.5,
        leave_day_hours_status="CONFIRMED", leave_day_hours_source="Client replied",
        standard_day_hours_status="CONFIRMED", standard_day_hours_source="Client replied",
    )
    flags_confirmed = compare_hr_with_system(hr_rows, day_check, [], [], master_data, rules_confirmed)
    found_confirmed = _flags_named(flags_confirmed, "Day typed as 8:00 instead of 8:30")
    assert len(found_confirmed) == 1
    assert found_confirmed[0].remark_type == REMARK_CONFIRMED_RULE
