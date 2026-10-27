"""Per-person-day attendance engine: Day check rows and flags.

Inputs only: the punch table (src/capture.py), the schedule
(src/schedule.py), master data (src/masterdata.py) and rules
(src/rules.py). No HR record workbook data here — HQ In/Out, OT 1.5 and
any flag that needs the HR tabs are step 4c/4d (see CLAUDE.md Progress >
Deferred). Every rule value is read through src/rules.py; nothing here
is hard-coded.

Remark type rule: a flag's remark type is "Confirmed rule" only when
rules.is_confirmed() is True for every rule id it depends on (its
`rule_ids`); otherwise it is "Pending question" with that rule's
question number(s) as Ref. Flags that are plain data-quality problems,
not tied to an open Client question (unmapped clock names, unknown
schedule codes, missing punches), are "Possible error" with no rule
dependency and an empty Ref.
"""
from __future__ import annotations

import datetime
import hashlib
import math
from dataclasses import dataclass, field

from src.masterdata import Flag, MasterData, _split_clock_file_names
from src.punches import collapse_duplicate_punches
from src.rules import Rules

MEANING_TO_CONFIRM_MARKER = "meaning to confirm"

SEVERITY_BLOCKER = "Blocker"
SEVERITY_CHECK = "Check"
SEVERITY_INFO = "Info"

REMARK_POSSIBLE_ERROR = "Possible error"
REMARK_PENDING_QUESTION = "Pending question"
REMARK_CONFIRMED_RULE = "Confirmed rule"

DAY_CHECK_COLUMNS = [
    "person", "date", "day", "schedule_code", "type", "shift_start", "shift_end",
    "punches", "clock_outlet", "system_in", "system_out", "hq_in", "hq_out", "flag_refs",
]


@dataclass
class DayFlag:
    severity: str
    flag: str
    person: str
    home_outlet_tab: str
    date: object  # datetime.date, or "Month" for month-level flags
    details: str
    remark: str
    remark_type: str
    ref: str
    days: int = 1  # number of days this flag stands for (>1 only after rollup)
    span: str = ""  # "first to last" date text, set only after rollup

    @property
    def flag_id(self) -> str:
        date_str = self.date if isinstance(self.date, str) else self.date.isoformat()
        raw = f"{self.person}|{date_str}|{self.flag}|{self.details}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _rule_dependent_remark(rules: Rules, rule_ids: list[str], remark: str) -> tuple[str, str]:
    """Return (remark_type, ref) for a flag that depends on the given rules."""
    confirmed = rules.is_confirmed(rule_ids)
    remark_type = REMARK_CONFIRMED_RULE if confirmed else REMARK_PENDING_QUESTION
    refs = []
    for rule_id in rule_ids:
        q = rules.question(rule_id)
        if q and q not in refs:
            refs.append(q)
    return remark_type, ", ".join(refs)


def _minutes(time_str: str) -> int | None:
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


def _classify_single_punch(
    punch_time: str, shift_start: str | None, shift_end: str | None,
    classification: str, tie_minutes: int,
) -> tuple[str, str, str]:
    """Classify a lone punch as clock-in-missing, clock-out-missing, or unclear.

    Returns (flag_name, details, remark).
    """
    punch_min = _minutes(punch_time)
    start_min = _minutes(shift_start)
    end_min = _minutes(shift_end)

    if punch_min is None or start_min is None or end_min is None:
        return (
            "Single punch, cannot tell clock-in from clock-out",
            f"Only one punch recorded: {punch_time}. No scheduled start/end to compare against.",
            "Cannot classify a lone punch without a scheduled start and end.",
        )

    midpoint = (start_min + end_min) / 2
    if abs(punch_min - midpoint) <= tie_minutes:
        return (
            "Single punch, cannot tell clock-in from clock-out",
            f"Only one punch recorded: {punch_time}. Scheduled {shift_start}-{shift_end}, "
            f"too close to the midpoint to classify.",
            "Within the tie zone around the shift midpoint; not guessed.",
        )

    if classification == "closer_to_boundary" and punch_min < midpoint:
        return (
            "Missing clock-out",
            f"Only one punch recorded: {punch_time}. Scheduled start {shift_start}.",
            "Lone punch is closer to the scheduled start; treated as the clock-in.",
        )
    return (
        "Missing clock-in",
        f"Only one punch recorded: {punch_time}. Scheduled end {shift_end}.",
        "Lone punch is closer to the scheduled end; treated as the clock-out.",
    )


def _clock_names_by_staff(master_data: MasterData) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for _, row in master_data.staff_aliases.iterrows():
        result[row["id_initials"]] = _split_clock_file_names(row["clock_file_names"])
    return result


def _staff_rows_by_id(master_data: MasterData) -> dict[str, dict]:
    return {row["id_initials"]: row.to_dict() for _, row in master_data.staff_aliases.iterrows()}


def _punches_by_staff_date(
    punches_long: list[dict], master_data: MasterData
) -> tuple[dict[tuple[str, datetime.date], list[tuple[str, str]]], list[DayFlag]]:
    """Group punches by (staff_id, date). Returns (grouped, name_flags).

    name_flags holds one DayFlag per punch row whose clock_name is unmapped
    or ambiguous (passed through, never dropped silently).
    """
    grouped: dict[tuple[str, datetime.date], list[tuple[str, str]]] = {}
    name_flags: list[DayFlag] = []
    for row in punches_long:
        mapped = master_data.map_clock_name(row["clock_name"])
        if isinstance(mapped, Flag):
            name_flags.append(DayFlag(
                severity=SEVERITY_CHECK,
                flag=f"Clock name issue: {mapped.kind}",
                person=row["clock_name"],
                home_outlet_tab="",
                date=datetime.date.fromisoformat(row["date"]),
                details=mapped.detail,
                remark="Clock name could not be matched to a staff member.",
                remark_type=REMARK_POSSIBLE_ERROR,
                ref="",
            ))
            continue
        date = datetime.date.fromisoformat(row["date"])
        key = (mapped.staff_id, date)
        times = [t.strip() for t in row["all_punches"].split("|") if t.strip()]
        grouped.setdefault(key, []).extend((t, row["outlet_clock"]) for t in times)
    return grouped, name_flags


def build_day_check(
    schedule_rows: list[dict],
    punches_long: list[dict],
    master_data: MasterData,
    rules: Rules,
    approvals: list[dict] | None = None,
) -> tuple[list[dict], list[DayFlag]]:
    """Build Day check rows and Flags for every person-day in the schedule.

    schedule_rows: output of src.schedule.parse_schedule_workbook.
    punches_long: output rows of src.capture.capture_folder (first element).
    approvals: optional rows with "person", "date" (ISO string or date) and
        "status" ("approved" clears a late-in; anything else leaves it
        unknown). Read-only; this engine never writes 03_MASTER_DATA.
    """
    clock_names_by_staff = _clock_names_by_staff(master_data)
    staff_rows = _staff_rows_by_id(master_data)
    punches_by_staff_date, flags = _punches_by_staff_date(punches_long, master_data)

    approvals_index: dict[tuple[str, datetime.date], str] = {}
    for row in approvals or []:
        date = row["date"]
        if isinstance(date, str):
            date = datetime.date.fromisoformat(date)
        approvals_index[(row["person"], date)] = row.get("status", "")

    day_check_rows: list[dict] = []
    staff_seen_with_work_day: set[str] = set()

    morning_open = rules.get("morning_open")
    early_as_open = rules.get("early_clock_in_counts_as_open")
    late_grace = rules.get("late_grace_minutes")
    grace_applies_to_all = rules.get("grace_applies_to_all_shifts")
    late_deduction_mode = rules.get("late_deduction_mode")
    late_minutes_from = rules.get("late_minutes_counted_from")
    late_round_step = rules.get("late_round_up_to_minutes")
    count_time_after_end = rules.get("count_time_after_end")
    single_punch_classification = rules.get("single_punch_classification")
    single_punch_tie_minutes = rules.get("single_punch_tie_minutes")
    duplicate_window = rules.get("duplicate_punch_window_minutes")
    duplicate_keep = rules.get("duplicate_punch_keep")
    excluded_staff_ids = set(rules.get("excluded_staff_ids") or [])

    covered_keys: set[tuple[str, datetime.date]] = set()

    for row in schedule_rows:
        person = row["person"]
        if person in excluded_staff_ids:
            continue
        date = row["date"]
        raw_code = row["raw_code"]
        resolved = row["resolved"]
        staff_row = staff_rows.get(person, {})
        home_outlet_tab = staff_row.get("home_outlet_tab", "")
        has_clock_source = bool(clock_names_by_staff.get(person))
        covered_keys.add((person, date))

        row_start_idx = len(flags)

        def add_flag(df: DayFlag) -> None:
            flags.append(df)

        if isinstance(resolved, Flag):
            add_flag(DayFlag(
                severity=SEVERITY_CHECK,
                flag="Unknown schedule code",
                person=person,
                home_outlet_tab=home_outlet_tab,
                date=date,
                details=resolved.detail,
                remark="Schedule code is not in schedule_codes.csv.",
                remark_type=REMARK_POSSIBLE_ERROR,
                ref="",
            ))
            day_check_rows.append({
                "person": person, "date": date, "day": date.strftime("%a"),
                "schedule_code": raw_code, "type": "UNKNOWN", "shift_start": None,
                "shift_end": None, "punches": None, "clock_outlet": None,
                "system_in": None, "system_out": None, "hq_in": None, "hq_out": None,
                "flag_refs": list(range(row_start_idx, len(flags))),
            })
            continue

        day_type = resolved.day_type
        shift_start = resolved.start or None
        shift_end = resolved.end or None

        if resolved.note and MEANING_TO_CONFIRM_MARKER in resolved.note.lower():
            add_flag(DayFlag(
                severity=SEVERITY_CHECK,
                flag="Schedule code needs confirming",
                person=person,
                home_outlet_tab=home_outlet_tab,
                date=date,
                details=f"Code {raw_code!r}: {resolved.note}",
                remark="The code's asterisk/arrow meaning is not yet confirmed by Client.",
                remark_type=REMARK_PENDING_QUESTION,
                ref="",
            ))

        punch_entries = punches_by_staff_date.get((person, date), [])
        punch_entries.sort(key=lambda pe: _minutes(pe[0]) or 0)
        # a near-simultaneous double tap (e.g. two clock-ins, no clock-out)
        # must not masquerade as a complete in/out pair; collapse it first.
        punch_entries, _dropped_duplicate_entries = collapse_duplicate_punches(
            punch_entries, duplicate_window, duplicate_keep, time_of=lambda pe: pe[0],
        )
        punch_times = [t for t, _ in punch_entries]
        outlets_punched = sorted({o for _, o in punch_entries})

        system_in = punch_times[0] if punch_times else None
        system_out = punch_times[-1] if len(punch_times) > 1 else None

        if day_type == "WORK":
            staff_seen_with_work_day.add(person)
            if not punch_times:
                if has_clock_source:
                    add_flag(DayFlag(
                        severity=SEVERITY_CHECK,
                        flag="Missing clock-in and clock-out",
                        person=person, home_outlet_tab=home_outlet_tab, date=date,
                        details=f"Scheduled {raw_code} ({shift_start}-{shift_end}); no punches recorded.",
                        remark="No punch recorded for a scheduled work day.",
                        remark_type=REMARK_POSSIBLE_ERROR, ref="",
                    ))
            elif len(punch_times) == 1:
                flag_name, details, remark = _classify_single_punch(
                    punch_times[0], shift_start, shift_end,
                    single_punch_classification, single_punch_tie_minutes,
                )
                remark_type, ref = _rule_dependent_remark(
                    rules, ["single_punch_classification", "single_punch_tie_minutes"], "",
                )
                add_flag(DayFlag(
                    severity=SEVERITY_CHECK,
                    flag=flag_name,
                    person=person, home_outlet_tab=home_outlet_tab, date=date,
                    details=details,
                    remark=remark,
                    remark_type=remark_type, ref=ref,
                ))
            else:
                shift_start_min = _minutes(shift_start)
                punch_in_min = _minutes(system_in)
                if shift_start_min is not None and punch_in_min is not None:
                    is_morning_open_shift = shift_start == morning_open
                    if early_as_open and is_morning_open_shift and punch_in_min <= shift_start_min:
                        system_in = shift_start
                    effective_grace = late_grace if (is_morning_open_shift or grace_applies_to_all) else 0
                    raw_late = punch_in_min - shift_start_min
                    if raw_late > effective_grace:
                        if late_minutes_from == "end_of_grace":
                            actual_late = raw_late - effective_grace
                        else:
                            actual_late = raw_late
                        rounded_late = _round_up(actual_late, late_round_step) if late_round_step else actual_late

                        approval_status = approvals_index.get((person, date), "unknown")
                        rule_ids = ["late_deduction_mode", "late_minutes_counted_from", "approval_source"]
                        if not is_morning_open_shift:
                            rule_ids.append("grace_applies_to_all_shifts")

                        if approval_status == "approved":
                            remark_type, ref = _rule_dependent_remark(
                                rules, ["late_clears_if_approved"],
                                "Cleared: explained and approved.",
                            )
                            add_flag(DayFlag(
                                severity=SEVERITY_INFO,
                                flag="Late in (over 15 min), approved",
                                person=person, home_outlet_tab=home_outlet_tab, date=date,
                                details=f"Punched {system_in}, start {shift_start}: {actual_late} min late.",
                                remark="Explained and approved by office/superior; grace clears it.",
                                remark_type=remark_type, ref=ref,
                            ))
                        else:
                            remark_type, ref = _rule_dependent_remark(rules, rule_ids, "")
                            details = f"Punched {system_in}, start {shift_start}: {actual_late} min late."
                            if late_deduction_mode == "show_both":
                                details += f" Rounded up to :{late_round_step:02d} step: {rounded_late} min."
                            add_flag(DayFlag(
                                severity=SEVERITY_CHECK,
                                flag="Late in (over 15 min)",
                                person=person, home_outlet_tab=home_outlet_tab, date=date,
                                details=details,
                                remark=f"Approval status: {approval_status}.",
                                remark_type=remark_type, ref=ref,
                            ))

                shift_end_min = _minutes(shift_end)
                system_out_min = _minutes(system_out)
                if shift_end_min is not None and system_out_min is not None and system_out_min > shift_end_min:
                    if not count_time_after_end:
                        remark_type, ref = _rule_dependent_remark(
                            rules, ["count_time_after_end"], "",
                        )
                        add_flag(DayFlag(
                            severity=SEVERITY_INFO,
                            flag="Stayed after scheduled end",
                            person=person, home_outlet_tab=home_outlet_tab, date=date,
                            details=f"Punched out {system_out}, shift end {shift_end}: "
                                    f"{system_out_min - shift_end_min} min after.",
                            remark="Time after the scheduled end is not counted while this is assumed.",
                            remark_type=remark_type, ref=ref,
                        ))

            if outlets_punched and (len(outlets_punched) > 1 or outlets_punched != [home_outlet_tab]):
                remark_type, ref = _rule_dependent_remark(rules, ["transfer_is_error"], "")
                add_flag(DayFlag(
                    severity=SEVERITY_INFO,
                    flag="Punched at another outlet",
                    person=person, home_outlet_tab=home_outlet_tab, date=date,
                    details=f"Home outlet {home_outlet_tab!r}; punched at {outlets_punched}.",
                    remark="Transfers between outlets are common; flagged for visibility only.",
                    remark_type=remark_type, ref=ref,
                ))
        else:
            if punch_times:
                punch_rule_ids = ["ot_rate_day_types"]
                remark = ("Could be rest-day/holiday work or a schedule not updated. "
                          "Overtime rate for this day type is pending.")
                if day_type in ("LEAVE", "ABSENT"):
                    punch_rule_ids.append("upl_absent_unpaid")
                    remark += " Whether this day type is unpaid is also pending."
                remark_type, ref = _rule_dependent_remark(rules, punch_rule_ids, "")
                add_flag(DayFlag(
                    severity=SEVERITY_CHECK,
                    flag=f"Punched on a {day_type} day",
                    person=person, home_outlet_tab=home_outlet_tab, date=date,
                    details=f"Schedule says {raw_code}; punches {punch_times}.",
                    remark=remark,
                    remark_type=remark_type, ref=ref,
                ))

        day_check_rows.append({
            "person": person, "date": date, "day": date.strftime("%a"),
            "schedule_code": raw_code, "type": day_type,
            "shift_start": shift_start, "shift_end": shift_end,
            "punches": ", ".join(punch_times) if punch_times else None,
            "clock_outlet": ", ".join(outlets_punched) if outlets_punched else None,
            "system_in": system_in, "system_out": system_out,
            "hq_in": None, "hq_out": None,
            "flag_refs": list(range(row_start_idx, len(flags))),
        })

    for (person, date), entries in punches_by_staff_date.items():
        if person in excluded_staff_ids:
            continue
        if (person, date) in covered_keys:
            continue
        if len(entries) != 1:
            continue
        punch_time = entries[0][0]
        remark_type, ref = _rule_dependent_remark(
            rules, ["single_punch_classification", "single_punch_tie_minutes"], "",
        )
        flags.append(DayFlag(
            severity=SEVERITY_CHECK,
            flag="Single punch, cannot tell clock-in from clock-out",
            person=person,
            home_outlet_tab=staff_rows.get(person, {}).get("home_outlet_tab", ""),
            date=date,
            details=f"Only one punch recorded: {punch_time}. No schedule row for this day.",
            remark="No scheduled shift to compare the lone punch against.",
            remark_type=remark_type, ref=ref,
        ))

    for person, names in clock_names_by_staff.items():
        if person in excluded_staff_ids:
            continue
        if not names:
            flags.append(DayFlag(
                severity=SEVERITY_INFO,
                flag="No clock file in batch",
                person=person,
                home_outlet_tab=staff_rows.get(person, {}).get("home_outlet_tab", ""),
                date="Month",
                details="Punches for this person are pasted in the record tab, not read from a clock file.",
                remark="Comparison against the HR record workbook is step 4c/4d, not yet built.",
                remark_type=REMARK_PENDING_QUESTION,
                ref="",
            ))

    return day_check_rows, flags
