"""Tests for src.diskcache: disk-based per-month result cache."""
import datetime
import json
import os
import tempfile
from pathlib import Path

import pytest

import src.daily as daily
from src.dayengine import DayFlag
from src.diskcache import save, load, _input_signature


@pytest.fixture()
def tmp_layout(tmp_path):
    """Create a minimal drop + master layout."""
    drop = tmp_path / "drop"
    master = tmp_path / "master"
    drop.mkdir()
    master.mkdir()
    month = "2026-05"
    month_dir = drop / month
    month_dir.mkdir()
    (month_dir / "OUT1-All_Report_20260501.xls").write_bytes(b"fake clock data")
    (master / "staff_aliases.csv").write_text("id,name\n", encoding="utf-8")
    (master / "outlets.csv").write_text("outlet\n", encoding="utf-8")
    (master / "rules.toml").write_text("[rules]\n", encoding="utf-8")
    return drop, master, month


def _make_flag(**overrides):
    defaults = dict(
        severity="Check", flag="Late in (over 15 min)", person="ALICE",
        home_outlet_tab="OUT1", date=datetime.date(2026, 5, 6),
        details="15 min late", remark="Late", remark_type="Confirmed rule",
        ref="R3", days=1, span="",
    )
    defaults.update(overrides)
    return DayFlag(**defaults)


def _make_result(flags=None, rows=None):
    return {
        "status": "OK", "month": "2026-05", "missing": [],
        "staff_total": 5, "staff_with_clock_file": 3,
        "day_check_rows": rows or [
            {"person": "ALICE", "date": datetime.date(2026, 5, 6), "day": "Wed",
             "schedule_code": "AM", "type": "WORK", "shift_start": "10:00",
             "shift_end": "18:00", "punches": "10:15", "clock_outlet": "OUT1",
             "system_in": "10:15", "system_out": "", "hq_in": "", "hq_out": "",
             "flag_refs": ""},
        ],
        "all_flags": flags if flags is not None else [_make_flag()],
        "hr_skipped": [("DRAFT_TAB", "month mismatch")],
    }


class TestSaveAndLoad:
    def test_round_trip(self, tmp_layout, monkeypatch):
        drop, master, month = tmp_layout
        monkeypatch.setattr(daily, "LOGS_DIR", drop.parent / "logs")
        result = _make_result()
        save(month, result, drop, master)
        loaded = load(month, drop, master)
        assert loaded is not None
        assert loaded["status"] == "OK"
        assert len(loaded["all_flags"]) == 1
        f = loaded["all_flags"][0]
        assert isinstance(f, DayFlag)
        assert f.person == "ALICE"
        assert f.date == datetime.date(2026, 5, 6)
        assert len(loaded["day_check_rows"]) == 1
        r = loaded["day_check_rows"][0]
        assert r["date"] == datetime.date(2026, 5, 6)
        assert r["person"] == "ALICE"

    def test_cache_miss_when_inputs_change(self, tmp_layout, monkeypatch):
        drop, master, month = tmp_layout
        monkeypatch.setattr(daily, "LOGS_DIR", drop.parent / "logs")
        result = _make_result()
        save(month, result, drop, master)

        (drop / month / "OUT2-All_Report_20260501.xls").write_bytes(b"new file")
        loaded = load(month, drop, master)
        assert loaded is None

    def test_cache_miss_when_master_data_changes(self, tmp_layout, monkeypatch):
        drop, master, month = tmp_layout
        monkeypatch.setattr(daily, "LOGS_DIR", drop.parent / "logs")
        save(month, _make_result(), drop, master)

        (master / "rules.toml").write_text("[rules]\nchanged = true\n", encoding="utf-8")
        loaded = load(month, drop, master)
        assert loaded is None

    def test_no_cache_file(self, tmp_layout, monkeypatch):
        drop, master, month = tmp_layout
        monkeypatch.setattr(daily, "LOGS_DIR", drop.parent / "logs")
        assert load(month, drop, master) is None

    def test_month_level_flag(self, tmp_layout, monkeypatch):
        drop, master, month = tmp_layout
        monkeypatch.setattr(daily, "LOGS_DIR", drop.parent / "logs")
        flag = _make_flag(date="Month", span="2026-05-01 to 2026-05-31", days=5)
        save(month, _make_result(flags=[flag]), drop, master)
        loaded = load(month, drop, master)
        assert loaded is not None
        f = loaded["all_flags"][0]
        assert f.date == "Month"
        assert f.days == 5
        assert f.span == "2026-05-01 to 2026-05-31"


class TestStableFlagId:
    def test_same_inputs_same_id(self):
        f1 = _make_flag()
        f2 = _make_flag()
        assert f1.flag_id == f2.flag_id

    def test_different_details_different_id(self):
        f1 = _make_flag(details="15 min late")
        f2 = _make_flag(details="30 min late")
        assert f1.flag_id != f2.flag_id

    def test_different_person_different_id(self):
        f1 = _make_flag(person="ALICE")
        f2 = _make_flag(person="BOB")
        assert f1.flag_id != f2.flag_id

    def test_id_survives_serialisation(self, tmp_layout, monkeypatch):
        drop, master, month = tmp_layout
        monkeypatch.setattr(daily, "LOGS_DIR", drop.parent / "logs")
        flag = _make_flag()
        original_id = flag.flag_id
        save(month, _make_result(flags=[flag]), drop, master)
        loaded = load(month, drop, master)
        assert loaded["all_flags"][0].flag_id == original_id
