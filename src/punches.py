"""Shared duplicate-punch collapsing, used by src/dayengine.py (so a
double-tap doesn't masquerade as a complete in/out pair) and
src/hrcompare.py (so a dropped duplicate tap is an Info flag, not
"punches differ"). Both read duplicate_punch_window_minutes and
duplicate_punch_keep through src/rules.py; no value is hard-coded here.
"""
from __future__ import annotations


def minutes(time_str: str | None) -> int | None:
    if not time_str:
        return None
    try:
        hh, mm = time_str.strip().split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None


def collapse_duplicate_punches(
    items: list, window_minutes: int, keep: str, time_of=lambda item: item,
) -> tuple[list, list]:
    """Collapse a run of items within window_minutes of each other.

    `items` must already be time-sorted. `time_of(item)` extracts the
    "HH:MM" string used for timing; the items themselves are returned
    unchanged (so callers can pass plain time strings or (time, outlet)
    tuples). `keep` is "first" or "last": a single, fixed policy applied
    to every cluster, never chosen per-case by matching another source.

    Returns (kept_items, dropped_items).
    """
    if not items or window_minutes <= 0:
        return list(items), []

    clusters: list[list[int]] = [[0]]
    for i in range(1, len(items)):
        prev_min = minutes(time_of(items[i - 1]))
        this_min = minutes(time_of(items[i]))
        if prev_min is not None and this_min is not None and (this_min - prev_min) <= window_minutes:
            clusters[-1].append(i)
        else:
            clusters.append([i])

    keep_idx_of = (lambda c: c[-1]) if keep == "last" else (lambda c: c[0])
    kept: list = []
    dropped: list = []
    for cluster in clusters:
        keep_idx = keep_idx_of(cluster)
        for i in cluster:
            (kept if i == keep_idx else dropped).append(items[i])
    return kept, dropped
