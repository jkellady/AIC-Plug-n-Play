"""Load master data: outlets, staff aliases, schedule codes.

Read-only on 03_MASTER_DATA/*.csv — never writes or edits these files.
Unknown clock names or schedule codes return a Flag, never raise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

OUTLETS_FILE = "outlets.csv"
STAFF_ALIASES_FILE = "staff_aliases.csv"
SCHEDULE_CODES_FILE = "schedule_codes.csv"

CSV_READ_KWARGS = dict(dtype=str, keep_default_na=False, encoding="utf-8-sig")


@dataclass
class Flag:
    kind: str
    detail: str


@dataclass
class StaffAliasRecord:
    staff_id: str
    record_tab: str
    home_outlet_tab: str
    alias_status: str


@dataclass
class ScheduleCodeRecord:
    code: str
    day_type: str
    start: str
    end: str
    hours_in_roster: float | None
    note: str


def _norm_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def _split_clock_file_names(cell_value: str) -> list[str]:
    """Split a clock_file_names cell on ';' and extract the name before ' ('.

    Commas appear inside the parenthetical notes (e.g. "STAFF_O (OUT1, 0 punches)")
    so splitting must happen on ';' only, never on ','. Entries that are just
    a parenthetical note (e.g. "(no clock file in batch)") are skipped.
    """
    if not cell_value:
        return []
    names = []
    for part in cell_value.split(";"):
        part = part.strip()
        if not part or part.startswith("("):
            continue
        name = part.split(" (")[0].strip()
        if name:
            names.append(name)
    return names


@dataclass
class MasterData:
    outlets: pd.DataFrame
    staff_aliases: pd.DataFrame
    schedule_codes: pd.DataFrame
    _clock_name_index: dict[str, StaffAliasRecord | Flag] = field(default_factory=dict)
    _schedule_code_index: dict[str, ScheduleCodeRecord] = field(default_factory=dict)

    def __post_init__(self):
        claimed_by: dict[str, set[str]] = {}
        for _, row in self.staff_aliases.iterrows():
            record = StaffAliasRecord(
                staff_id=row["id_initials"],
                record_tab=row["record_tab"],
                home_outlet_tab=row["home_outlet_tab"],
                alias_status=row["status"],
            )
            for name in _split_clock_file_names(row["clock_file_names"]):
                key = _norm_key(name)
                claimed_by.setdefault(key, set()).add(record.staff_id)
                self._clock_name_index[key] = record

        for key, staff_ids in claimed_by.items():
            if len(staff_ids) > 1:
                self._clock_name_index[key] = Flag(
                    kind="ambiguous_clock_name",
                    detail=f"Clock name maps to multiple staff_ids: {sorted(staff_ids)}",
                )

        for _, row in self.schedule_codes.iterrows():
            hours_raw = row["hours_in_roster"]
            hours = float(hours_raw) if hours_raw else None
            record = ScheduleCodeRecord(
                code=row["code"],
                day_type=row["day_type"],
                start=row["start"],
                end=row["end"],
                hours_in_roster=hours,
                note=row["note"],
            )
            self._schedule_code_index[_norm_key(row["code"])] = record

    def map_clock_name(self, clock_name: str) -> StaffAliasRecord | Flag:
        record = self._clock_name_index.get(_norm_key(clock_name))
        if record is None:
            return Flag(
                kind="unmapped_clock_name",
                detail=f"Clock name {clock_name!r} is not in staff_aliases.csv",
            )
        return record

    def lookup_schedule_code(self, code: str) -> ScheduleCodeRecord | Flag:
        record = self._schedule_code_index.get(_norm_key(code))
        if record is None:
            return Flag(
                kind="unknown_schedule_code",
                detail=f"Schedule code {code!r} is not in schedule_codes.csv",
            )
        return record


def load_master_data(master_data_dir: Path) -> MasterData:
    """Load outlets, staff aliases and schedule codes. Read-only on the CSVs."""
    master_data_dir = Path(master_data_dir)
    outlets = pd.read_csv(master_data_dir / OUTLETS_FILE, **CSV_READ_KWARGS)
    staff_aliases = pd.read_csv(master_data_dir / STAFF_ALIASES_FILE, **CSV_READ_KWARGS)
    schedule_codes = pd.read_csv(master_data_dir / SCHEDULE_CODES_FILE, **CSV_READ_KWARGS)
    return MasterData(outlets=outlets, staff_aliases=staff_aliases, schedule_codes=schedule_codes)
