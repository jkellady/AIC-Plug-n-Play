"""Step 4d: flags comparing HQ's record-tab figures with the system.

Inputs: src/hrbook.py (HR tab figures), src/dayengine.py (system Day
check rows and flags), src/capture.py (via dayengine's punches), and
src/schedule.py (per-day outlet_block). Every rule value is read through
src/rules.py; nothing here is hard-coded.

Does NOT compare overtime 1.5 (that is step 4e).

Remark type rule, same as src/dayengine.py: a flag's remark type is
"Confirmed rule" only when rules.is_confirmed() is True for every rule
id it depends on; otherwise "Pending question" with that rule's question
number(s) as Ref. Flags that are plain data mismatches with no Client
question behind them (outlet/name/punch-list mismatches) are "Possible
error" with no rule dependency and an empty Ref.
"""
from __future__ import annotations

import datetime
import math

from src.dayengine import (
    REMARK_CONFIRMED_RULE,
    REMARK_PENDING_QUESTION,
    REMARK_POSSIBLE_ERROR,
    SEVERITY_CHECK,
    SEVERITY_INFO,
    DayFlag,
)
from src.masterdata import MasterData, _split_clock_file_names
from src.punches import collapse_duplicate_punches
from src.rules import Rules


def _rule_dependent_remark(rules: Rules, rule_ids: list[str]) -> tuple[str, str]:
    confirmed = rules.is_confirmed(rule_ids)
    remark_type = REMARK_CONFIRMED_RULE if confirmed else REMARK_PENDING_QUESTION
    refs = []
    for rule_id in rule_ids:
        q = rules.question(rule_id)
        if q and q not in refs:
            refs.append(q)
    return remark_type, ", ".join(refs)


def _minutes(time_str: str | None) -> int | None:
    if not time_str:
        return None
    try:
        hh, mm = time_str.strip().split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None


def _round_up(minutes: int, step: int) -> int:
    if step <= 0:
        return minutes
    return math.ceil(minutes / step) * step


def _clock_names_by_staff(master_data: MasterData) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for _, row in master_data.staff_aliases.iterrows():
        result[row["id_initials"]] = _split_clock_file_names(row["clock_file_names"])
    return result


def _staff_id_by_record_tab(master_data: MasterData) -> dict[str, str]:
    return {row["record_tab"]: row["id_initials"] for _, row in master_data.staff_aliases.iterrows()}


def _staff_rows_by_id(master_data: MasterData) -> dict[str, dict]:
    return {row["id_initials"]: row.to_dict() for _, row in master_data.staff_aliases.iterrows()}


def compare_hr_with_system(
    hr_rows: list[dict],
    day_check_rows: list[dict],
    day_engine_flags: list[DayFlag],
    schedule_rows: list[dict],
    master_data: MasterData,
    rules: Rules,
) -> list[DayFlag]:
    """Compare HR record-tab figures against the system. Read-only inputs."""
    staff_id_by_record_tab = _staff_id_by_record_tab(master_data)
    staff_rows = _staff_rows_by_id(master_data)
    clock_names_by_staff = _clock_names_by_staff(master_data)

    day_check_by_key = {(r["person"], r["date"]): r for r in day_check_rows}
    schedule_by_key = {(r["person"], r["date"]): r for r in schedule_rows}
    late_beyond_grace_keys = {
        (f.person, f.date) for f in day_engine_flags if f.flag == "Late in (over 15 min)"
    }

    morning_open = rules.get("morning_open")
    late_grace = rules.get("late_grace_minutes")
    grace_applies_to_all = rules.get("grace_applies_to_all_shifts")
    late_round_step = rules.get("late_round_up_to_minutes")
    late_minutes_from = rules.get("late_minutes_counted_from")
    leave_day_hours = rules.get("leave_day_hours")
    duplicate_window = rules.get("duplicate_punch_window_minutes")
    duplicate_keep = rules.get("duplicate_punch_keep")
    early_counts_all_shifts = rules.get("early_clock_in_counts_as_start_all_shifts")
    excluded_staff_ids = set(rules.get("excluded_staff_ids") or [])

    flags: list[DayFlag] = []
    seen_month_flags: set[tuple[str, str]] = set()  # (staff_id, flag_name) -> emit once/month

    # (6b) two different record tabs carry the same name_in_tab -- a signal
    # a tab may have been copied from another person's, that comparing
    # name_in_tab to staff_aliases.csv's own full_name_on_tab cannot catch
    # (full_name_on_tab comes from the very same workbook).
    staff_ids_by_name: dict[str, set[str]] = {}
    for hr_row in hr_rows:
        sid = staff_id_by_record_tab.get(hr_row["person"])
        if sid in excluded_staff_ids:
            continue
        name = (hr_row["name_in_tab"] or "").strip().upper()
        if sid is not None and name:
            staff_ids_by_name.setdefault(name, set()).add(sid)
    for name, sids in staff_ids_by_name.items():
        if len(sids) > 1:
            for sid in sids:
                others = sorted(sids - {sid})
                month_flag_key = (sid, "name_in_tab_shared_with_another_tab")
                if month_flag_key not in seen_month_flags:
                    seen_month_flags.add(month_flag_key)
                    flags.append(DayFlag(
                        severity=SEVERITY_CHECK,
                        flag="Same name typed in two different tabs",
                        person=sid,
                        home_outlet_tab=staff_rows.get(sid, {}).get("home_outlet_tab", ""),
                        date="Month",
                        details=f"Row 3 reads {name!r}, the same as tab(s) {', '.join(others)}.",
                        remark="One tab may have been copied from the other.",
                        remark_type=REMARK_POSSIBLE_ERROR, ref="",
                    ))

    for hr_row in hr_rows:
        staff_id = staff_id_by_record_tab.get(hr_row["person"])
        if staff_id is None:
            continue  # sheet name matches no known staff_aliases record_tab
        if staff_id in excluded_staff_ids:
            continue

        staff_row = staff_rows.get(staff_id, {})
        home_outlet_tab = staff_row.get("home_outlet_tab", "")
        has_clock_source = bool(clock_names_by_staff.get(staff_id))
        date = hr_row["date"]
        key = (staff_id, date)
        day_check = day_check_by_key.get(key)
        schedule_row = schedule_by_key.get(key)

        # (6) name_in_tab differs from the staff's known full name on the tab
        # (staff_aliases.csv full_name_on_tab), not from the tab/sheet name
        # itself -- a tab named by short ID (e.g. "STAFF_A") with the full
        # name typed inside ("Full Name") is normal and must not flag.
        expected_full_name = staff_row.get("full_name_on_tab", "")
        if (
            hr_row["name_in_tab"]
            and expected_full_name
            and hr_row["name_in_tab"].strip().upper() != expected_full_name.strip().upper()
        ):
            month_flag_key = (staff_id, "name_in_tab_mismatch")
            if month_flag_key not in seen_month_flags:
                seen_month_flags.add(month_flag_key)
                flags.append(DayFlag(
                    severity=SEVERITY_CHECK,
                    flag="Tab name differs from the name typed inside it",
                    person=staff_id, home_outlet_tab=home_outlet_tab, date="Month",
                    details=f"staff_aliases.csv full_name_on_tab is {expected_full_name!r} "
                            f"but row 3 of the tab reads {hr_row['name_in_tab']!r}.",
                    remark="The tab may be copied from another person's tab.",
                    remark_type=REMARK_POSSIBLE_ERROR, ref="",
                ))

        # (5) tab's outlet differs from the schedule block for the day.
        if schedule_row is not None and hr_row["tab_outlet"]:
            schedule_block = schedule_row.get("outlet_block", "")
            if schedule_block and schedule_block not in hr_row["tab_outlet"]:
                flags.append(DayFlag(
                    severity=SEVERITY_CHECK,
                    flag="Outlet differs between schedule and record tab",
                    person=staff_id, home_outlet_tab=home_outlet_tab, date=date,
                    details=f"Schedule block {schedule_block!r}; record tab outlet {hr_row['tab_outlet']!r}.",
                    remark="The record tab and the schedule disagree on this person's outlet.",
                    remark_type=REMARK_POSSIBLE_ERROR, ref="",
                ))

        # (3) a day typed as 8:00 instead of 8:30 (leave-like day types only).
        # day_type can be "LEAVE", "LEAVE (medical)", "LEAVE (annual)", etc.
        # (from schedule_codes.csv), not only the plain "LEAVE" used for UPL.
        is_leave_like = (
            day_check is not None
            and (day_check["type"].startswith("LEAVE") or day_check["type"] in ("ABSENT", "PUBLIC HOLIDAY"))
        )
        if (
            is_leave_like
            and hr_row["hq_working_hours_is_formula"] is False
            and hr_row["hq_working_hours"] is not None
            and abs(hr_row["hq_working_hours"] - leave_day_hours) > 0.01
        ):
            remark_type, ref = _rule_dependent_remark(rules, ["standard_day_hours", "leave_day_hours"])
            flags.append(DayFlag(
                severity=SEVERITY_CHECK,
                flag="Day typed as 8:00 instead of 8:30",
                person=staff_id, home_outlet_tab=home_outlet_tab, date=date,
                details=f"Schedule {day_check['schedule_code']!r}. Tab shows "
                        f"{hr_row['hq_working_hours']:.2f} hr typed, not a formula.",
                remark="The tab legend credits leave days at 8:30; this day was typed instead of computed.",
                remark_type=remark_type, ref=ref,
            ))

        if not has_clock_source:
            continue  # flags 1, 2 and 4 need clock punches; skip for this person

        # (4) punches pasted in the tab differ from the clock file. Clock
        # punches within duplicate_punch_window_minutes of each other are
        # collapsed first (a likely duplicate tap), and reported as an Info
        # flag rather than "punches differ" when that collapse alone
        # explains the mismatch.
        if day_check is not None:
            system_punches = [p.strip() for p in (day_check["punches"] or "").split(",") if p.strip()]
            tab_punches = [p for p in hr_row["raw_punches"] if p]
            if system_punches or tab_punches:
                collapsed_punches, dropped_duplicates = collapse_duplicate_punches(
                    system_punches, duplicate_window, duplicate_keep,
                )
                if dropped_duplicates and collapsed_punches == tab_punches:
                    remark_type, ref = _rule_dependent_remark(
                        rules, ["duplicate_punch_window_minutes", "duplicate_punch_keep"],
                    )
                    flags.append(DayFlag(
                        severity=SEVERITY_INFO,
                        flag="Clock file has a likely duplicate tap, dropped in the tab",
                        person=staff_id, home_outlet_tab=home_outlet_tab, date=date,
                        details=f"Clock file: {', '.join(system_punches)}. "
                                f"Dropped as duplicate: {', '.join(dropped_duplicates)}. "
                                f"Tab paste: {', '.join(tab_punches)}.",
                        remark="A near-simultaneous second tap on the same punch; the tab correctly kept one.",
                        remark_type=remark_type, ref=ref,
                    ))
                elif collapsed_punches != tab_punches:
                    flags.append(DayFlag(
                        severity=SEVERITY_CHECK,
                        flag="Raw punches pasted in the tab differ from the clock file",
                        person=staff_id, home_outlet_tab=home_outlet_tab, date=date,
                        details=f"Clock file: {', '.join(collapsed_punches) or '(none)'}. "
                                f"Tab paste: {', '.join(tab_punches) or '(none)'}.",
                        remark="A punch may have been dropped, or pasted into the wrong column.",
                        remark_type=REMARK_POSSIBLE_ERROR, ref="",
                    ))

        # (1) and (2): HQ's recorded start vs the system's.
        if day_check is not None and hr_row["hq_in"] and day_check["system_in"] and day_check["shift_start"]:
            hq_in_min = _minutes(hr_row["hq_in"])
            system_in_min = _minutes(day_check["system_in"])
            shift_start_min = _minutes(day_check["shift_start"])
            if hq_in_min is not None and system_in_min is not None and hq_in_min != system_in_min:
                is_morning_open_shift = day_check["shift_start"] == morning_open
                if key in late_beyond_grace_keys:
                    # (2) HQ rounded a late start up to :30.
                    raw_late = system_in_min - shift_start_min
                    effective_grace = late_grace if (is_morning_open_shift or grace_applies_to_all) else 0
                    actual_late = raw_late if late_minutes_from != "end_of_grace" else raw_late - effective_grace
                    rounded_late = _round_up(actual_late, late_round_step) if late_round_step else actual_late
                    rounded_start_min = shift_start_min + rounded_late
                    if hq_in_min == rounded_start_min:
                        remark_type, ref = _rule_dependent_remark(
                            rules, ["late_deduction_mode", "late_round_up_to_minutes", "late_minutes_counted_from"],
                        )
                        flags.append(DayFlag(
                            severity=SEVERITY_INFO,
                            flag="HQ rounded a late start up to :30",
                            person=staff_id, home_outlet_tab=home_outlet_tab, date=date,
                            details=f"Punch {day_check['system_in']}, HQ typed {hr_row['hq_in']}.",
                            remark="Matches HQ's current practice. Whether to keep rounding is pending.",
                            remark_type=remark_type, ref=ref,
                        ))
                else:
                    raw_late = system_in_min - shift_start_min
                    effective_grace = late_grace if (is_morning_open_shift or grace_applies_to_all) else 0
                    is_within_grace_late = 0 < raw_late <= effective_grace

                    if is_within_grace_late and hq_in_min > system_in_min:
                        # (1) HQ recorded a later start than the rule allows:
                        # the punch was late but within grace, yet HQ typed
                        # something later than the actual punch.
                        rule_ids = ["morning_open", "late_grace_minutes", "early_clock_in_counts_as_open"]
                        if not is_morning_open_shift:
                            rule_ids.append("grace_applies_to_all_shifts")
                        remark_type, ref = _rule_dependent_remark(rules, rule_ids)
                        flags.append(DayFlag(
                            severity=SEVERITY_CHECK,
                            flag="HQ recorded a later start than the rule allows",
                            person=staff_id, home_outlet_tab=home_outlet_tab, date=date,
                            details=f"Punch {day_check['system_in']}, start {day_check['shift_start']}. "
                                    f"HQ typed {hr_row['hq_in']}.",
                            remark="HQ typed a later start than the rule would give for this punch.",
                            remark_type=remark_type, ref=ref,
                        ))
                    elif raw_late <= 0 and not is_morning_open_shift and not early_counts_all_shifts:
                        # An early/on-time arrival on a non-10:00 shift, with
                        # HQ typing something different. Confirmed rule
                        # early_clock_in_counts_as_open only covers the
                        # 10:00 opening; whether the same applies to every
                        # shift start is an open assumption
                        # (early_clock_in_counts_as_start_all_shifts). While
                        # that rule says "yes" (the default), this case is
                        # silently normal and not flagged at all; this
                        # branch only runs when it has been set to "no".
                        remark_type, ref = _rule_dependent_remark(
                            rules, ["early_clock_in_counts_as_start_all_shifts"],
                        )
                        flags.append(DayFlag(
                            severity=SEVERITY_CHECK,
                            flag="HQ recorded a later start than the rule allows",
                            person=staff_id, home_outlet_tab=home_outlet_tab, date=date,
                            details=f"Punch {day_check['system_in']}, start {day_check['shift_start']}. "
                                    f"HQ typed {hr_row['hq_in']}.",
                            remark="Whether early arrivals count as the start on every shift is not confirmed.",
                            remark_type=remark_type, ref=ref,
                        ))

    for staff_id, names in clock_names_by_staff.items():
        if staff_id in excluded_staff_ids:
            continue
        if not names and any(r["person"] == staff_rows.get(staff_id, {}).get("record_tab") for r in hr_rows):
            flags.append(DayFlag(
                severity=SEVERITY_INFO,
                flag="No clock file in batch",
                person=staff_id,
                home_outlet_tab=staff_rows.get(staff_id, {}).get("home_outlet_tab", ""),
                date="Month",
                details="This person's record tab exists but has no clock file in the batch; "
                        "only outlet, name and typed-hours checks were done.",
                remark="Comparison against clock punches is not possible without a clock file.",
                remark_type=REMARK_PENDING_QUESTION,
                ref="",
            ))

    return flags
