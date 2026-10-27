"""Read-only data/query layer for the HR Attendance attendance web app.

Every public function returns plain JSON-serialisable data, never raises and
never prints or logs. Staff names appear only in the returned data (this is a
local app); exception text carries no names.

Source: ``src.daily.run_month`` (looked up on the module at call time, so tests
can monkeypatch it) gives ``day_check_rows`` (one per scheduled person-day) and
``all_flags`` (DayFlag, already rolled up: a flag can carry ``days`` > 1 and a
``span``). ``flag_refs`` inside the rows index an unreturned flag list and are
NOT used. Names/outlets come from ``src.masterdata``.

Nearly read-only: the only files this module writes are the per-month
disk-cache JSON files under ``07_LOGS/cache/``. ``run_month`` itself may
write ``NEEDS_ATTENTION.txt`` or the engine error log (existing engine
behaviour); that is outside this module.

Definitions (all counts are plain Python over engine output):
* Scheduled day  = a day-check row with ``type == "WORK"`` for a *tracked*
  person. A tracked person has a clock file in the batch (no
  "No clock file in batch" flag), because without punches nothing can be said.
* Missing punch  = scheduled day with fewer than 2 punches (none, or one).
* Late           = a dated "Late in (over 15 min)" flag (approved or not) on a
  scheduled day that is not already missing a punch.
* On time        = scheduled - late - missing.
* Attendance     = scheduled days with at least one punch / scheduled days.
* Percentages are rounded to 1 decimal; None when there are no scheduled days.

Status mapping for exceptions (first match wins) -- STATUS_MAPPING below:
    severity "Info"                 -> "Info"
    remark_type "Possible error"    -> "Needs review"
    remark_type "Pending question"  -> "Pending question"
    remark_type "Confirmed rule"    -> "Confirmed rule"
    anything else                   -> "Needs review" (safe default)
"Open" exceptions = Needs review + Pending question.
Month-level flags (date "Month") appear in a week when their span overlaps it;
month-level flags with no span appear only if they are not Info, and then in
every week touching that month.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import re
import threading
import time
from pathlib import Path

import src.daily as daily
import src.diskcache as diskcache
from src.agents import scope_resolver

CACHE_TTL_SECONDS = 30.0
STATUS_MAPPING = {
    "Possible error": "Needs review",
    "Pending question": "Pending question",
    "Confirmed rule": "Confirmed rule",
}
OPEN_STATUSES = ("Needs review", "Pending question")
LATE_FLAG_PREFIX = "Late in (over 15 min)"
NO_CLOCK_FLAG = "No clock file in batch"
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_SPAN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})$")

_cache: dict = {}
_cache_lock = threading.Lock()


# ------------------------------------------------------------------ helpers

def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
    cache_dir = Path(daily.LOGS_DIR) / "cache"
    if cache_dir.is_dir():
        import shutil
        shutil.rmtree(cache_dir, ignore_errors=True)


def _cached(key, compute):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
    value = compute()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _sig(path: Path):
    try:
        st = Path(path).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _month_signature(month: str):
    """Cheap signature of the inputs: month folder files plus master data."""
    parts = []
    try:
        folder = Path(daily.DROP_DIR) / month
        for p in sorted(folder.glob("*")):
            if p.is_file():
                parts.append((p.name, _sig(p)))
    except OSError:
        pass
    md = Path(daily.MASTER_DATA_DIR)
    for name in ("staff_aliases.csv", "outlets.csv", "rules.toml", "approvals.csv"):
        parts.append((name, _sig(md / name)))
    return tuple(parts)


def parse_week(week: str | None) -> tuple[_dt.date, _dt.date, str]:
    """'YYYY-Www' -> (monday, sunday, label). None -> last completed week.
    Raises ValueError for text that is not a valid week (callers catch it)."""
    if week is None or week == "":
        win = scope_resolver.resolve("week", as_of=scope_resolver.today(), previous=True)
        start = win.start
    else:
        m = _WEEK_RE.match(str(week).strip())
        if not m:
            raise ValueError("Week must look like 2026-W41.")
        start = _dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    win = scope_resolver.resolve("week", as_of=start)
    return win.start, win.end, win.label


def _months_touched(start: _dt.date, end: _dt.date) -> list[str]:
    months = [f"{start.year}-{start.month:02d}"]
    last = f"{end.year}-{end.month:02d}"
    if last != months[0]:
        months.append(last)
    return months


def _status_for(flag) -> str:
    if getattr(flag, "severity", "") == "Info":
        return "Info"
    return STATUS_MAPPING.get(getattr(flag, "remark_type", ""), "Needs review")


def _as_date(value):
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return None


def _flag_dates(flag):
    """(first, last) dates a flag stands for, or (None, None) if unknown."""
    d = _as_date(flag.date)
    if d:
        return d, d
    m = _SPAN_RE.match(getattr(flag, "span", "") or "")
    if m:
        return _dt.date.fromisoformat(m.group(1)), _dt.date.fromisoformat(m.group(2))
    return None, None


def _short_id(*parts) -> str:
    return "EX-" + hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


# ------------------------------------------------------------ month loading

def _load_month(month: str) -> dict:
    """Normalised view of one month. Never raises. Keys: month, state
    ("ok"|"partial"|"missing"|"failed"), message, rows, flags, names, outlets,
    notes."""
    def compute():
        base = {"month": month, "state": "failed", "message": "", "rows": [], "flags": [],
                "names": {}, "outlets": {}, "notes": []}
        try:
            if not (Path(daily.DROP_DIR) / month).is_dir():
                base.update(state="missing",
                            message=f"No data folder for {month} yet, so it is not included.")
                return base

            drop_dir = Path(daily.DROP_DIR)
            master_dir = Path(daily.MASTER_DATA_DIR)

            cached = diskcache.load(month, drop_dir, master_dir)
            if cached is not None and cached.get("status") not in (None, "FAILED") and "all_flags" in cached:
                res = cached
            else:
                res = daily.run_month(month, force=False)
                if res.get("status") == "NOTHING_NEW":
                    res = daily.run_month(month, force=True)
                if res.get("status") not in (None, "FAILED") and "all_flags" in res:
                    try:
                        diskcache.save(month, res, drop_dir, master_dir)
                    except Exception:  # noqa: BLE001
                        pass

            status = res.get("status")
            if status in (None, "FAILED") or "all_flags" not in res:
                base.update(state="failed",
                            message=f"{month} could not be read. Ask the Admin to check the files.")
                return base
            base["rows"] = list(res.get("day_check_rows") or [])
            base["flags"] = list(res.get("all_flags") or [])
            base["notes"] = [str(x) for x in (res.get("missing") or [])]
            md = res.get("master_data")
            rl = res.get("rules")
            if md is None or rl is None:
                from src.masterdata import load_master_data
                from src.rules import load_rules
                if md is None:
                    md = load_master_data(master_dir)
                if rl is None:
                    rl = load_rules(master_dir / "rules.toml")
            names, outlets = _staff_maps(md, rl)
            base["names"], base["outlets"] = names, outlets
            if status == "OK":
                base["state"] = "ok"
            else:
                base["state"] = "partial"
                base["message"] = f"{month} is partial: some input files are missing or look wrong."
            return base
        except Exception:  # noqa: BLE001 - the UI needs a sentence, not a traceback
            base["message"] = f"{month} could not be read. Ask the Admin to check the files."
            return base

    return _cached(("month", month, _month_signature(month)), compute)


def _excluded_ids(rules) -> set:
    try:
        return set(rules.get("excluded_staff_ids") or [])
    except Exception:  # noqa: BLE001
        return set()


def _staff_maps(master_data, rules):
    """({id: name}, {id: home outlet}) for non-excluded staff."""
    excluded = _excluded_ids(rules)
    names, outlets = {}, {}
    try:
        df = master_data.staff_aliases
        for _, r in df.iterrows():
            sid = str(r["id_initials"])
            if sid in excluded:
                continue
            names[sid] = str(r.get("full_name_on_tab") or "").strip() or sid
            outlets[sid] = str(r.get("home_outlet_tab") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return names, outlets


def _gather(start: _dt.date, end: _dt.date) -> dict:
    """Merge all months a window touches."""
    out = {"rows": [], "flags": [], "names": {}, "outlets": {}, "messages": [],
           "covered": [], "states": []}
    for month in _months_touched(start, end):
        m = _load_month(month)
        out["states"].append(m["state"])
        if m["message"]:
            out["messages"].append(m["message"])
        if m["state"] in ("ok", "partial"):
            y, mo = int(month[:4]), int(month[5:])
            first = _dt.date(y, mo, 1)
            last = (_dt.date(y + (mo == 12), mo % 12 + 1, 1)) - _dt.timedelta(days=1)
            out["covered"].append((max(first, start), min(last, end)))
            out["rows"].extend(r for r in m["rows"]
                               if _as_date(r.get("date")) and start <= _as_date(r["date"]) <= end)
            out["flags"].extend((month, f) for f in m["flags"])
            out["names"].update(m["names"])
            out["outlets"].update(m["outlets"])
    return out


def _flag_in_window(flag, start, end) -> bool:
    d = _as_date(flag.date)
    if d:
        return start <= d <= end
    first, last = _flag_dates(flag)
    if first:
        return first <= end and last >= start
    return getattr(flag, "severity", "") != "Info"  # month-level, no span


# ----------------------------------------------------------------- overview

def _pct(n, d):
    return None if not d else round(100.0 * n / d, 1)


def _empty_overview(label, start, end, status):
    return {
        "week_label": label, "start": start.isoformat(), "end": end.isoformat(),
        "covered": None,
        "kpis": {"attendance_rate": None, "late_arrivals": 0, "late_staff": 0,
                 "missing_punches": 0, "open_exceptions": 0, "older_than_week": 0},
        "by_outlet": [], "status": status,
    }


def overview(week: str | None = None) -> dict:
    try:
        start, end, label = parse_week(week)
    except Exception:  # noqa: BLE001
        today = _dt.date.today()
        return _empty_overview(str(week), today, today,
                               "That week is not valid. Use the form 2026-W41.")
    try:
        return _overview(start, end, label)
    except Exception:  # noqa: BLE001
        return _empty_overview(label, start, end, "This week could not be calculated.")


def _overview(start, end, label) -> dict:
    g = _gather(start, end)
    if not g["covered"]:
        msg = " ".join(g["messages"]) or "No data for this week."
        return _empty_overview(label, start, end, msg)

    no_clock = {f.person for _, f in g["flags"] if f.flag == NO_CLOCK_FLAG}
    excluded_free = [r for r in g["rows"] if r.get("type") == "WORK" and r["person"] not in no_clock]
    late_keys = {}
    for _, f in g["flags"]:
        d = _as_date(f.date)
        if d and start <= d <= end and str(f.flag).startswith(LATE_FLAG_PREFIX):
            late_keys[(f.person, d)] = True

    per_outlet: dict[str, dict] = {}
    totals = {"sched": 0, "present": 0, "late": 0, "missing": 0}
    late_people = set()
    for r in excluded_free:
        outlet = g["outlets"].get(r["person"], "") or "Unassigned"
        o = per_outlet.setdefault(outlet, {"sched": 0, "present": 0, "late": 0, "missing": 0})
        n_punch = len([p for p in str(r.get("punches") or "").split(",") if p.strip()])
        missing = n_punch < 2
        late = (not missing) and (r["person"], _as_date(r["date"])) in late_keys
        for t in (o, totals):
            t["sched"] += 1
            t["present"] += 1 if n_punch >= 1 else 0
            t["missing"] += 1 if missing else 0
            t["late"] += 1 if late else 0
        if late:
            late_people.add(r["person"])

    by_outlet = []
    for outlet in sorted(per_outlet):
        o = per_outlet[outlet]
        on_time = o["sched"] - o["late"] - o["missing"]
        by_outlet.append({
            "outlet": outlet, "on_time_pct": _pct(on_time, o["sched"]),
            "late_pct": _pct(o["late"], o["sched"]),
            "missing_pct": _pct(o["missing"], o["sched"]),
            "attendance_pct": _pct(o["present"], o["sched"]),
        })

    exc = _exceptions_in(g, start, end)
    open_ex = [e for e in exc if e["status"] in OPEN_STATUSES]
    cutoff = scope_resolver.today() -_dt.timedelta(days=7)
    older = [e for e in open_ex if e["date"] and _dt.date.fromisoformat(e["date"]) < cutoff]

    msgs = list(g["messages"])
    if not totals["sched"]:
        msgs.append("No scheduled work days with clock data in this week.")
    if "partial" in g["states"] or "missing" in g["states"] or "failed" in g["states"]:
        status = "PARTIAL: " + " ".join(msgs) if msgs else "PARTIAL"
    else:
        status = "OK" if totals["sched"] else " ".join(msgs)

    lo = min(c[0] for c in g["covered"])
    hi = max(c[1] for c in g["covered"])
    return {
        "week_label": label, "start": start.isoformat(), "end": end.isoformat(),
        "covered": {"start": lo.isoformat(), "end": hi.isoformat()},
        "kpis": {
            "attendance_rate": _pct(totals["present"], totals["sched"]),
            "late_arrivals": totals["late"], "late_staff": len(late_people),
            "missing_punches": totals["missing"], "open_exceptions": len(open_ex),
            "older_than_week": len(older),
        },
        "by_outlet": by_outlet, "status": status,
    }


# --------------------------------------------------------------- exceptions

def _exceptions_in(g, start, end) -> list[dict]:
    seen: dict[str, int] = {}
    out = []
    for month, f in g["flags"]:
        if not _flag_in_window(f, start, end):
            continue
        first, last = _flag_dates(f)
        shown = last
        if _as_date(f.date) is None and last and last > end:
            shown = end
        sid = str(f.person)
        base_id = _short_id(month, sid, f.flag, f.date, getattr(f, "span", ""), f.ref, f.details)
        n = seen.get(base_id, 0)
        seen[base_id] = n + 1
        out.append({
            "id": base_id if n == 0 else f"{base_id}-{n + 1}",
            "staff": g["names"].get(sid, sid), "staff_id": sid,
            "outlet": g["outlets"].get(sid, "") or str(f.home_outlet_tab or ""),
            "date": shown.isoformat() if shown else None,
            "issue": str(f.flag), "details": str(f.details),
            "days": int(getattr(f, "days", 1) or 1),
            "status": _status_for(f), "severity": str(f.severity),
        })
    out.sort(key=lambda e: (e["date"] or "", e["staff_id"], e["issue"]))
    return out


def exceptions(status: str | None = None, outlet: str | None = None,
               week: str | None = None) -> list[dict]:
    """Exceptions for the week (default: last completed week), newest last.
    Optional filters on status and outlet (exact, case-insensitive)."""
    try:
        start, end, _ = parse_week(week)
        g = _gather(start, end)
        rows = _exceptions_in(g, start, end)
    except Exception:  # noqa: BLE001
        return []
    if status:
        rows = [e for e in rows if e["status"].lower() == str(status).lower()]
    if outlet:
        rows = [e for e in rows if e["outlet"].lower() == str(outlet).lower()]
    return rows


# --------------------------------------------------------- staff / outlets

def _master():
    """(names, outlets, alias_status) from master data on disk."""
    def compute():
        try:
            from src.masterdata import load_master_data
            from src.rules import load_rules
            md = load_master_data(Path(daily.MASTER_DATA_DIR))
            try:
                rules = load_rules(Path(daily.MASTER_DATA_DIR) / "rules.toml")
            except Exception:  # noqa: BLE001
                rules = None
            excluded = _excluded_ids(rules) if rules is not None else set()
            rows = []
            for _, r in md.staff_aliases.iterrows():
                sid = str(r["id_initials"])
                if sid in excluded:
                    continue
                alias = str(r.get("status") or "").strip()
                rows.append({
                    "id": sid,
                    "name": str(r.get("full_name_on_tab") or "").strip() or sid,
                    "outlet": str(r.get("home_outlet_tab") or "").strip(),
                    "status": "Active" if alias in ("Matched", "Per your table", "Draft") else "Check details",
                })
            return rows
        except Exception:  # noqa: BLE001
            return []

    return _cached(("master", _month_signature("")), compute)


def staff() -> list[dict]:
    return [dict(r) for r in _master()]


def outlets() -> list[dict]:
    counts: dict[str, int] = {}
    for r in _master():
        key = r["outlet"] or "Unassigned"
        counts[key] = counts.get(key, 0) + 1
    return [{"outlet": k, "staff_count": counts[k]} for k in sorted(counts)]


# ------------------------------------------------------------------ reports

def _log_status(log_name: str) -> dict:
    """{window stem: latest non-skipped status} from a sibling log."""
    path = Path(daily.LOGS_DIR) / log_name
    result: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                st = (row.get("status") or "").strip()
                if st and st != "SKIPPED_EXISTS":
                    result[(row.get("window") or "").strip()] = st
    except OSError:
        pass
    return result


_WEEKLY_RE = re.compile(r"^Weekly_(\d{4}-W\d{2})(?:_DRAFT)?\.", re.I)
_MONTHLY_RE = re.compile(r"^Monthly_(\d{4}-\d{2})(?:_DRAFT)?\.", re.I)
_TRIAL_RE = re.compile(r"^(?:TRIAL_|Attendance_Output_Report_|Roster_Generated_).*?(\d{4}-\d{2})", re.I)
_REVIEW_RE = re.compile(r"^Review_", re.I)


def reports() -> list[dict]:
    root = Path(daily.REPORTS_DIR)
    out = []
    try:
        if not root.is_dir():
            return []
        weekly_log, monthly_log = _log_status("weekly_log.csv"), _log_status("monthly_log.csv")
        for month_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "LATEST"):
            for path in sorted(month_dir.iterdir()):
                if not path.is_file():
                    continue
                name = path.name
                kind, status = None, "Ready"
                m = _WEEKLY_RE.match(name)
                if m:
                    kind = "weekly"
                    if "_DRAFT" in name.upper():
                        status = "Draft"
                    elif weekly_log.get(f"{m.group(1)}_Weekly") == "PARTIAL":
                        status = "PARTIAL"
                m = _MONTHLY_RE.match(name)
                if m:
                    kind = "monthly"
                    if "_DRAFT" in name.upper():
                        status = "Draft"
                    elif monthly_log.get(f"{m.group(1)}_Monthly") == "PARTIAL":
                        status = "PARTIAL"
                if kind is None and (_TRIAL_RE.match(name) or _REVIEW_RE.match(name)):
                    kind = "trial"
                if kind is None:
                    continue
                st = path.stat()
                out.append({
                    "name": name, "kind": kind, "month": month_dir.name, "size": st.st_size,
                    "created": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "status": status, "path": f"{month_dir.name}/{name}",
                })
    except Exception:  # noqa: BLE001
        return out
    out.sort(key=lambda r: (r["created"], r["name"]), reverse=True)
    return out
