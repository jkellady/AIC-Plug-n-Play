"""Tests for agents.scope_resolver — the one agent module with no PL deps."""
from datetime import date

import pytest

from src.agents.scope_resolver import (
    ScopeWindow,
    iso_week_number,
    month_window,
    previous_month,
    previous_week,
    resolve,
    week_window,
)


# Reference date: 2026-10-17 is a Saturday, in ISO week 42 (Mon 12 - Sun 18).
REF = date(2026, 10, 17)


class TestWeekWindow:
    def test_mon_sun_default(self):
        start, end = week_window(REF, "Mon")
        assert start == date(2026, 10, 12)
        assert end == date(2026, 10, 18)

    def test_sun_sat_variant(self):
        start, end = week_window(REF, "Sun")
        assert start == date(2026, 10, 11)
        assert end == date(2026, 10, 17)

    def test_at_week_boundary(self):
        monday = date(2026, 10, 12)
        start, end = week_window(monday, "Mon")
        assert start == monday
        assert end == date(2026, 10, 18)


class TestMonthWindow:
    def test_mid_month(self):
        start, end = month_window(REF)
        assert start == date(2026, 10, 1)
        assert end == date(2026, 10, 31)

    def test_december_rollover(self):
        start, end = month_window(date(2026, 12, 15))
        assert start == date(2026, 12, 1)
        assert end == date(2026, 12, 31)

    def test_february_leap(self):
        start, end = month_window(date(2028, 2, 15))
        assert start == date(2028, 2, 1)
        assert end == date(2028, 2, 29)


class TestPrevious:
    def test_previous_week(self):
        start, end = previous_week(REF, "Mon")
        assert start == date(2026, 10, 5)
        assert end == date(2026, 10, 11)

    def test_previous_month(self):
        start, end = previous_month(REF)
        assert start == date(2026, 9, 1)
        assert end == date(2026, 9, 30)

    def test_previous_month_year_boundary(self):
        start, end = previous_month(date(2026, 1, 15))
        assert start == date(2025, 12, 1)
        assert end == date(2025, 12, 31)


class TestIsoWeekNumber:
    def test_normal(self):
        assert iso_week_number(REF) == (2026, 42)

    def test_week_1_crosses_year(self):
        # 2026-01-01 is a Thursday, ISO week 1 of 2026
        assert iso_week_number(date(2026, 1, 1)) == (2026, 1)


class TestResolve:
    def test_day_stem(self):
        w = resolve("day", as_of=REF)
        assert w.filename_stem == "2026-10-17_Daily"
        assert w.start == w.end == REF

    def test_day_previous(self):
        w = resolve("day", as_of=REF, previous=True)
        assert w.filename_stem == "2026-10-16_Daily"

    def test_week_stem(self):
        w = resolve("week", as_of=REF)
        assert w.filename_stem == "2026-W42_Weekly"
        assert w.label == "2026-W42 (12-18 Oct)"

    def test_week_previous(self):
        w = resolve("week", as_of=REF, previous=True)
        assert w.filename_stem == "2026-W41_Weekly"

    def test_month_stem(self):
        w = resolve("month", as_of=REF)
        assert w.filename_stem == "2026-10_Monthly"
        assert w.label == "October 2026"

    def test_month_previous(self):
        w = resolve("month", as_of=REF, previous=True)
        assert w.filename_stem == "2026-09_Monthly"

    def test_unknown_scope_raises(self):
        with pytest.raises(ValueError, match="Unknown scope"):
            resolve("quarter", as_of=REF)  # type: ignore[arg-type]
