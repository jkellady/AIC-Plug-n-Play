"""Shared date/scope helper for every agent.

All date math lives here so no agent does its own calendar arithmetic.
Business week is ISO (Mon-Sun) by default, overridable via client_config.toml
[week].starts_on.

Filename convention (confirmed with user, ISO working default):
    Daily    2026-10-17_Daily.xlsx
    Weekly   2026-W42_Weekly.xlsx
    Monthly  2026-10_Monthly.xlsx

Reply/revision suffixes:
    *_reply.xlsx   client's response to a sent report
    *_v2.xlsx      system-regenerated after a reply (bumps on each revision)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

Scope = Literal["day", "week", "month"]
WeekStart = Literal["Mon", "Sun", "Sat"]


@dataclass(frozen=True)
class ScopeWindow:
    """A resolved date range for one report scope."""
    scope: Scope
    start: date   # inclusive
    end: date     # inclusive
    label: str    # human label, e.g. "2026-W42 (12-18 Oct)"
    filename_stem: str  # e.g. "2026-W42_Weekly" (no extension)


_WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def today() -> date:
    """Current local date. Centralized so tests can patch one place."""
    return datetime.now().date()


def week_window(as_of: date, starts_on: WeekStart = "Mon") -> tuple[date, date]:
    """Return (start, end) of the business week containing as_of, inclusive.

    starts_on picks the first day of the business week; week is 7 days.
    """
    start_idx = _WEEKDAY_INDEX[starts_on]
    offset = (as_of.weekday() - start_idx) % 7
    start = as_of - timedelta(days=offset)
    end = start + timedelta(days=6)
    return start, end


def iso_week_number(d: date) -> tuple[int, int]:
    """Return (iso_year, iso_week) for d. Uses ISO 8601 weeks regardless of
    business week start — the week NUMBER is always ISO for filename stability.
    """
    iso = d.isocalendar()
    return iso.year, iso.week


def month_window(as_of: date) -> tuple[date, date]:
    """Return (start, end) of the calendar month containing as_of, inclusive."""
    start = as_of.replace(day=1)
    if start.month == 12:
        next_month_start = start.replace(year=start.year + 1, month=1)
    else:
        next_month_start = start.replace(month=start.month + 1)
    end = next_month_start - timedelta(days=1)
    return start, end


def previous_week(as_of: date, starts_on: WeekStart = "Mon") -> tuple[date, date]:
    """Return (start, end) of the business week BEFORE the one containing as_of."""
    this_start, _ = week_window(as_of, starts_on)
    prev_start = this_start - timedelta(days=7)
    prev_end = prev_start + timedelta(days=6)
    return prev_start, prev_end


def previous_month(as_of: date) -> tuple[date, date]:
    """Return (start, end) of the calendar month BEFORE the one containing as_of."""
    this_start = as_of.replace(day=1)
    prev_end = this_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    return prev_start, prev_end


def resolve(
    scope: Scope,
    as_of: date | None = None,
    starts_on: WeekStart = "Mon",
    previous: bool = False,
) -> ScopeWindow:
    """Return a ScopeWindow for the requested scope.

    scope      "day" | "week" | "month"
    as_of      reference date; defaults to today()
    starts_on  business week start (ignored for day/month)
    previous   if True, use the prior period (prior week/month). For "day"
               this means yesterday.
    """
    d = as_of or today()

    if scope == "day":
        day = d - timedelta(days=1) if previous else d
        return ScopeWindow(
            scope="day",
            start=day,
            end=day,
            label=day.isoformat(),
            filename_stem=f"{day.isoformat()}_Daily",
        )

    if scope == "week":
        start, end = (previous_week(d, starts_on) if previous
                      else week_window(d, starts_on))
        iso_year, iso_week = iso_week_number(start)
        label = (
            f"{iso_year}-W{iso_week:02d} "
            f"({start.day}-{end.day} {end.strftime('%b')})"
        )
        return ScopeWindow(
            scope="week",
            start=start,
            end=end,
            label=label,
            filename_stem=f"{iso_year}-W{iso_week:02d}_Weekly",
        )

    if scope == "month":
        start, end = previous_month(d) if previous else month_window(d)
        label = f"{start.strftime('%B %Y')}"
        return ScopeWindow(
            scope="month",
            start=start,
            end=end,
            label=label,
            filename_stem=f"{start.year}-{start.month:02d}_Monthly",
        )

    raise ValueError(f"Unknown scope: {scope!r}")
