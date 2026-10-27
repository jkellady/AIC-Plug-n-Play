"""Drop-folder scanner agent.

Lists files in ``daily.DROP_DIR`` (including its YYYY-MM subfolders) that are
new or changed since the last scan, classifies each one, and writes the listing
to ``07_LOGS`` only. It copies, moves and deletes nothing, and opens file
contents only through ``daily._classify_xlsx`` (read-only).

Classes: roster (schedule workbook), hr (HR record workbook), punches (clock
export ``*-All_Report*.xls``), reply (``*_reply.xlsx``), unknown.

ASSUMED (client questionnaire Q4 unanswered): replies are named
``*_reply.xlsx``. ASSUMED (Q5 unanswered): the daily scan time is 12:00. The
time is documented only; nothing is scheduled here.

Writes (07_LOGS only):
* ``scan_listing_<YYYY-MM-DD_HHMM>.csv``: name, month folder, size, mtime, class
  (only when something is new).
* ``last_scan.json``: name+size+mtime signatures already seen.
* ``drop_scan_log.csv``: one line per run.

Statuses: OK, NOTHING_NEW (no listing written), FAILED (drop folder missing or
unreadable). ``alerts`` are plain sentences with counts and classes only; they
never carry file names. ``run()`` returns a dict and prints nothing.
"""
from __future__ import annotations

import csv
import datetime
import fnmatch
import json
import re
from collections import Counter
from pathlib import Path

import src.daily as daily

DEFAULT_SCAN_TIME = "12:00"  # ASSUMED (Q5); documented, not scheduled
REPLY_GLOB = "*_reply.xlsx"  # ASSUMED (Q4)
STATE_NAME = "last_scan.json"
LOG_NAME = "drop_scan_log.csv"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CLASSES = ("roster", "hr", "punches", "reply", "unknown")
REQUIRED = ("roster", "hr", "punches")


def _log(status: str, detail: str) -> None:
    logs_dir = Path(daily.LOGS_DIR)
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / LOG_NAME
    is_new = not path.exists()
    detail = detail.replace('"', "'").replace("\n", " ")
    with open(path, "a", encoding="utf-8", newline="") as f:
        if is_new:
            f.write("run_time,status,detail\n")
        f.write(f'{datetime.datetime.now().isoformat(timespec="seconds")},{status},"{detail}"\n')


def _classify(path: Path) -> str:
    name = path.name
    if fnmatch.fnmatch(name.lower(), REPLY_GLOB):
        return "reply"
    if fnmatch.fnmatch(name, daily.CLOCK_EXPORT_GLOB):
        return "punches"
    if path.suffix.lower() == ".xlsx":
        kind = daily._classify_xlsx(path)
        return {"schedule": "roster", "hr": "hr"}.get(kind, "unknown")
    return "unknown"


def _month_of(drop: Path, path: Path) -> str:
    parent = path.parent
    if parent != drop and MONTH_RE.match(parent.name):
        return parent.name
    return ""


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("seen", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _alerts(rows: list[dict]) -> list[str]:
    alerts: list[str] = []
    unknown = sum(1 for r in rows if r["class"] == "unknown")
    if unknown:
        alerts.append(f"{unknown} file(s) in the drop folder could not be recognised (class: unknown).")
    by_month: dict[str, Counter] = {}
    for r in rows:
        if r["month"]:
            by_month.setdefault(r["month"], Counter())[r["class"]] += 1
    for month in sorted(by_month):
        missing = [c for c in REQUIRED if not by_month[month][c]]
        if missing:
            alerts.append(f"Month folder {month} has no {', '.join(missing)} file yet.")
    if any(r["class"] == "reply" and not r["month"] for r in rows):
        alerts.append("A reply file is not inside a YYYY-MM month folder, so it has no matching month.")
    return alerts


def run(now: datetime.datetime | None = None) -> dict:
    now = now or datetime.datetime.now()
    result = {"status": "OK", "scanned": 0, "new": 0,
              "counts_by_class": {c: 0 for c in CLASSES}, "listing_path": None, "alerts": []}
    drop = Path(daily.DROP_DIR)
    logs_dir = Path(daily.LOGS_DIR)
    try:
        if not drop.is_dir():
            raise FileNotFoundError("drop folder not found")
        rows = []
        for p in sorted(q for q in drop.rglob("*") if q.is_file()):
            st = p.stat()
            rows.append({"name": p.name, "month": _month_of(drop, p),
                         "size": st.st_size, "mtime": st.st_mtime_ns,
                         "key": p.relative_to(drop).as_posix(), "class": _classify(p)})
        result["scanned"] = len(rows)
        state_path = logs_dir / STATE_NAME
        seen = _load_state(state_path)
        new_rows = [r for r in rows if seen.get(r["key"]) != [r["size"], r["mtime"]]]
        result["new"] = len(new_rows)
        if not new_rows:
            result["status"] = "NOTHING_NEW"
            _log("NOTHING_NEW", f"scanned {len(rows)}, new 0")
            return result
        counts = Counter(r["class"] for r in new_rows)
        result["counts_by_class"] = {c: counts.get(c, 0) for c in CLASSES}
        result["alerts"] = _alerts(rows)
        logs_dir.mkdir(parents=True, exist_ok=True)
        listing = logs_dir / f"scan_listing_{now.strftime('%Y-%m-%d_%H%M')}.csv"
        with open(listing, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "month_folder", "size", "mtime", "class"])
            for r in new_rows:
                mtime = datetime.datetime.fromtimestamp(r["mtime"] / 1e9).isoformat(timespec="seconds")
                w.writerow([r["name"], r["month"], r["size"], mtime, r["class"]])
        state = {"scanned_at": now.isoformat(timespec="seconds"),
                 "seen": {r["key"]: [r["size"], r["mtime"]] for r in rows}}
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        result["listing_path"] = str(listing)
        _log("OK", f"scanned {len(rows)}, new {len(new_rows)}, alerts {len(result['alerts'])}")
        return result
    except Exception as exc:  # noqa: BLE001 - agent must report, never raise
        result["status"] = "FAILED"
        result["alerts"] = [f"The drop folder scan failed: {type(exc).__name__}."]
        try:
            _log("FAILED", f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return result


_SUCCESS = {"OK", "NOTHING_NEW"}


def main() -> int:
    """Entry point for Task Scheduler. Returns 0 on success, 1 on failure."""
    result = run()
    return 0 if result["status"] in _SUCCESS else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
