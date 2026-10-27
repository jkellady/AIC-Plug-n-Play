"""Monthly IT check for one YYYY-MM. Propose-only: reads, summarises, applies no fixes.

Sections: drop-folder summary, health and security, log review, input
completeness, incidents, trend vs last month's saved check. Output holds counts
and file paths only (no staff names, no data values) and is written to
``<it_dir>/monthly_check_<YYYY-MM>.json`` and ``.md``; an existing month file is
never overwritten (a numeric suffix is added).

The drop-folder summary classifies files with the drop scanner's own read-only
classifier. It does NOT call ``drop_scanner.run()``: that call rewrites the
scanner's last-seen state and would hide new files from the real daily scan.
"""
from __future__ import annotations

import csv
import datetime
import fnmatch
import json
import re
from collections import Counter
from pathlib import Path

from . import health
from .knowledge import ModuleProfile, build_system_map, rel_to_root, safe_write_text, scrub
from .triage import UNCLASSIFIED, match_playbook, _EXC

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
BAD_STATUSES = ("FAILED", "PARTIAL")
_DMY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_RAG_ORDER = {"GREEN": 0, "AMBER": 1, "RED": 2}


def previous_month_str(today: datetime.date | None = None) -> str:
    today = today or datetime.date.today()
    try:  # lazy: the resolver is another agent's file
        from src.agents.scope_resolver import previous_month
        start, _ = previous_month(today)
    except Exception:
        last = today.replace(day=1) - datetime.timedelta(days=1)
        start = last.replace(day=1)
    return f"{start.year}-{start.month:02d}"


def _month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:])
    start = datetime.date(y, m, 1)
    nxt = datetime.date(y + (m == 12), (m % 12) + 1, 1)
    return start, nxt - datetime.timedelta(days=1)


def _prev_of(month: str) -> str:
    start, _ = _month_bounds(month)
    last = start - datetime.timedelta(days=1)
    return f"{last.year}-{last.month:02d}"


# -- 1. drop folder --------------------------------------------------------


def _classify_file(path: Path) -> str:
    try:
        from src.agents import drop_scanner
        return drop_scanner._classify(path)
    except Exception:
        name = path.name
        if fnmatch.fnmatch(name.lower(), "*_reply.xlsx"):
            return "reply"
        return "unclassified"


def drop_summary(profile: ModuleProfile, month: str) -> dict:
    cfg = profile.monthly
    folder = profile.p(cfg.get("drop_root", "01_DROP_FOLDER")) / month
    if not folder.is_dir():
        return {"available": False, "folder": rel_to_root(profile, folder), "reason": "month folder missing",
                "classes": {}, "files": 0}
    files = [f for f in folder.iterdir() if f.is_file()]
    classes = Counter()
    for f in files:
        try:
            classes[_classify_file(f)] += 1
        except Exception:
            classes["unreadable"] += 1
    glob = cfg.get("clock_glob", "*-All_Report*.xls")
    clock = sum(1 for f in files if fnmatch.fnmatch(f.name, glob))
    if clock and classes.get("punches", 0) == 0:
        classes["punches"] = clock
    return {"available": True, "folder": rel_to_root(profile, folder), "files": len(files),
            "classes": dict(classes), "clock_files": clock}


# -- 3. logs -----------------------------------------------------------------


def _row_month(value: str) -> str:
    m = re.match(r"(\d{4}-\d{2})", value or "")
    return m.group(1) if m else ""


def _review_csv(profile: ModuleProfile, path: Path, month: str) -> dict:
    out = {"runs": 0, "by_status": {}, "failed": 0, "partial": 0, "repeated_failures": []}
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.reader(f))
    except OSError:
        return {"error": "unreadable", **out}
    if not rows:
        return out
    header = [h.strip().lower() for h in rows[0]]
    si = next((i for i, h in enumerate(header) if "status" in h or h == "state"), None)
    sig = Counter()
    by = Counter()
    for r in rows[1:]:
        if not r or _row_month(r[0]) != month:
            continue
        out["runs"] += 1
        st = (r[si].strip() if si is not None and si < len(r) else "UNKNOWN") or "UNKNOWN"
        by[st] += 1
        if st in BAD_STATUSES:
            text = " ".join(r)
            pb = match_playbook(profile, text) or UNCLASSIFIED
            sig[pb['class']] += 1
    out["by_status"] = dict(by)
    out["failed"], out["partial"] = by.get("FAILED", 0), by.get("PARTIAL", 0)
    out["repeated_failures"] = [{"signature": k, "count": v} for k, v in sig.most_common() if v >= 2]
    return out


def _review_text_log(path: Path, month: str) -> dict:
    out = {"runs": 0, "by_status": {}, "failed": 0, "partial": 0, "tracebacks": 0, "repeated_failures": []}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"error": "unreadable", **out}
    y, mth = int(month[:4]), int(month[5:])
    in_month = False
    exits = Counter()
    sig = Counter()
    for ln in lines:
        m = _DMY.search(ln[:40]) if ln.startswith("[") else None
        if m:
            in_month = (int(m.group(3)), int(m.group(2))) == (y, mth)
        if not in_month:
            continue
        if re.search(r"\bstart\b", ln) and ln.startswith("["):
            out["runs"] += 1
        e = re.search(r"end python=(\d+)", ln)
        if e:
            exits["OK" if e.group(1) == "0" else "FAILED"] += 1
        if "Traceback (most recent call last)" in ln:
            out["tracebacks"] += 1
        x = _EXC.match(ln) if not ln.startswith("[") else None
        if x:
            sig[x.group(1)] += 1
    out["by_status"] = dict(exits)
    out["failed"] = exits.get("FAILED", 0)
    out["repeated_failures"] = [{"signature": k, "count": v} for k, v in sig.most_common() if v >= 2]
    return out


def review_logs(profile: ModuleProfile, month: str) -> dict:
    result = {}
    for rel in profile.monthly.get("log_files", []):
        p = profile.p(rel)
        key = p.name
        if not p.is_file():
            result[key] = {"present": False}
            continue
        info = _review_text_log(p, month) if p.suffix == ".log" else _review_csv(profile, p, month)
        result[key] = {"present": True, **info}
    return result


# -- 4. inputs ---------------------------------------------------------------


def expected_weeks(month: str, today: datetime.date, week_start: int = 0):
    """ISO weeks whose last day falls in the month and has already ended (weekly report folder rule)."""
    first, last = _month_bounds(month)
    d = first - datetime.timedelta(days=(first.weekday() - week_start) % 7)
    weeks = []
    while d <= last:
        end = d + datetime.timedelta(days=6)
        if first <= end <= last and end < today:
            iso = (d + datetime.timedelta(days=3)).isocalendar()
            weeks.append({"iso_year": iso.year, "iso_week": iso.week, "end": end})
        d += datetime.timedelta(days=7)
    return weeks


def check_inputs(profile: ModuleProfile, month: str, drop: dict, today: datetime.date) -> dict:
    cfg = profile.monthly
    y, m = int(month[:4]), int(month[5:])
    cls = drop.get("classes", {})
    present = {"clock_files": drop.get("clock_files", 0) > 0 or cls.get("punches", 0) > 0,
               "roster": cls.get("roster", 0) > 0, "hr_workbook": cls.get("hr", 0) > 0}
    missing = [k for k, v in present.items() if not v]
    rep_dir = profile.p(cfg.get("reports_root", "06_REPORTS")) / month
    month_report = any((rep_dir / n.format(year=y, month=m)).is_file() for n in cfg.get("monthly_names", []))
    weeks, missing_weeks = [], []
    for w in expected_weeks(month, today, int(cfg.get("week_start", 0))):
        name = cfg.get("weekly_name", "Weekly_{iso_year}-W{iso_week:02d}.xlsx").format(**w)
        end_month = f"{w['end'].year}-{w['end'].month:02d}"
        ok = (profile.p(cfg.get("reports_root", "06_REPORTS")) / end_month / name).is_file()
        weeks.append(name)
        if not ok:
            missing_weeks.append(name)
    return {"present": present, "missing": missing, "month_report_present": month_report,
            "expected_weekly_reports": len(weeks), "missing_weekly_reports": missing_weeks,
            "month_ended": today > _month_bounds(month)[1]}


# -- 5. incidents --------------------------------------------------------------


def review_incidents(profile: ModuleProfile, month: str) -> dict:
    path = profile.p(profile.monthly.get("incident_log", "07_LOGS/it/incident_log.csv"))
    out = {"opened": 0, "closed": 0, "still_open": 0, "open_ids": [], "open_severities": {}}
    if not path.is_file():
        return out
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return out
    last, first_seen = {}, {}
    for r in rows:
        iid = r.get("incident", "")
        first_seen.setdefault(iid, _row_month(r.get("time", "")))
        last[iid] = r
    for iid, r in last.items():
        opened_this = first_seen[iid] == month
        state = r.get("state", "")
        closed = state == "CLOSED"
        if opened_this:
            out["opened"] += 1
        if closed and _row_month(r.get("time", "")) == month:
            out["closed"] += 1
        if not closed and first_seen[iid] <= month:
            out["still_open"] += 1
            out["open_ids"].append(iid)
            sev = r.get("severity", "")
            out["open_severities"][sev] = out["open_severities"].get(sev, 0) + 1
    return out


# -- 6. trend ------------------------------------------------------------------


def _saved_checks(profile: ModuleProfile, month: str):
    return sorted(profile.it_path.glob(f"monthly_check_{month}*.json"))


def trend(profile: ModuleProfile, month: str, counts: dict, test_functions: int) -> dict:
    prev = _prev_of(month)
    files = _saved_checks(profile, prev)
    if not files:
        return {"previous_month": prev, "available": False}
    try:
        old = json.loads(files[-1].read_text(encoding="utf-8"))
        oc, ot = old["health"]["counts"], old["health"].get("test_functions")
    except (OSError, ValueError, KeyError):
        return {"previous_month": prev, "available": False}
    return {"previous_month": prev, "available": True, "previous_file": rel_to_root(profile, files[-1]),
            "health_delta": {k: counts.get(k, 0) - oc.get(k, 0) for k in ("PASS", "WARN", "FAIL")},
            "test_functions_delta": (test_functions - ot) if isinstance(ot, int) else None}


# -- RAG and actions -----------------------------------------------------------


def assess(health_report, logs, inputs, incidents, tr, drop, tests_skipped: bool):
    rag, actions = "GREEN", []

    def bump(level, priority, text):
        nonlocal rag
        if _RAG_ORDER[level] > _RAG_ORDER[rag]:
            rag = level
        actions.append((priority, text))

    for r in health_report["results"]:
        if r["status"] == "FAIL":
            bump("RED", 10, f"Health check '{r['check']}' FAILS. Run workflow mode to get a proposal.")
        elif r["status"] == "WARN" and r["check"] not in ("scheduled_tasks", "tests"):
            bump("AMBER", 40, f"Health check '{r['check']}' warns; review it.")
        elif r["status"] == "WARN" and r["check"] == "tests" and not tests_skipped:
            bump("AMBER", 40, "Test run gave a warning; review it.")
    for name, info in logs.items():
        if info.get("failed"):
            bump("RED" if name in ("run_log.csv", "daily_task.log") and info["failed"] >= 2 else "AMBER", 20,
                 f"{name}: {info['failed']} FAILED run(s) this month.")
        if info.get("partial"):
            bump("AMBER", 30, f"{name}: {info['partial']} PARTIAL run(s) this month.")
        for rep in info.get("repeated_failures", []):
            bump("AMBER", 25, f"{name}: failure signature '{rep['signature']}' repeated {rep['count']} times.")
    if inputs["month_ended"] and inputs["missing"]:
        bump("AMBER", 15, "Month inputs missing: " + ", ".join(inputs["missing"]) + ".")
    elif inputs["missing"]:
        bump("AMBER", 45, "Month inputs not complete yet: " + ", ".join(inputs["missing"]) + ".")
    if inputs["missing_weekly_reports"]:
        bump("AMBER", 35, f"{len(inputs['missing_weekly_reports'])} expected weekly report(s) not found.")
    if inputs["month_ended"] and not inputs["month_report_present"]:
        bump("AMBER", 18, "Month report not found for the ended month.")
    if not drop.get("available"):
        bump("AMBER", 16, "Drop folder for this month is missing.")
    if incidents["still_open"]:
        sev = incidents["open_severities"]
        level = "RED" if sev.get("CRITICAL") or sev.get("HIGH") else "AMBER"
        bump(level, 12 if level == "RED" else 28, f"{incidents['still_open']} incident(s) still open.")
    if tr.get("available") and tr["health_delta"]["FAIL"] > 0:
        bump("AMBER", 22, "More health FAILs than last month.")
    if tr.get("available") and (tr.get("test_functions_delta") or 0) < 0:
        bump("AMBER", 24, "Fewer tests than last month.")
    actions.sort(key=lambda a: a[0])
    top = [t for _p, t in actions][:5]
    return rag, top or ["No action needed this month."]


# -- writing ---------------------------------------------------------------------


def _free_path(base: Path, stem: str) -> tuple[Path, Path]:
    n = 1
    while True:
        suffix = "" if n == 1 else f"_{n}"
        j, m = base / f"{stem}{suffix}.json", base / f"{stem}{suffix}.md"
        if not j.exists() and not m.exists():
            return j, m
        n += 1


def render_md(d: dict) -> str:
    L = [f"# Monthly IT check {d['month']}", "", f"Overall: **{d['rag']}**. Generated {d['generated']}. Propose-only; counts and paths only.", "",
         "## Top actions", ""] + [f"{i}. {a}" for i, a in enumerate(d["actions"], 1)]
    h = d["health"]
    L += ["", "## Health and security", "", f"PASS {h['counts']['PASS']}, WARN {h['counts']['WARN']}, FAIL {h['counts']['FAIL']}; test functions {h['test_functions']}.", ""]
    L += [f"- {r['check']}: {r['status']}" for r in h["results"]]
    L += ["", "## Drop folder", "", f"{d['drop']}", "", "## Inputs", "", f"{d['inputs']}", "", "## Logs", ""]
    L += [f"- {k}: {v}" for k, v in d["logs"].items()]
    L += ["", "## Incidents", "", f"{d['incidents']}", "", "## Trend", "", f"{d['trend']}", ""]
    return scrub("\n".join(L))


def monthly_check(profile: ModuleProfile, month: str | None = None, run_tests: bool = True,
                  today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    month = month or previous_month_str(today)
    if not MONTH_RE.match(str(month)):
        return {"status": "FAILED", "mode": "monthly", "error": "month must be YYYY-MM"}
    drop = drop_summary(profile, month)
    report = health.run_checks(profile, run_tests=run_tests)
    test_functions = build_system_map(profile)["tests"]["test_functions"]
    logs = review_logs(profile, month)
    inputs = check_inputs(profile, month, drop, today)
    incidents = review_incidents(profile, month)
    tr = trend(profile, month, report["counts"], test_functions)
    tests_skipped = not run_tests
    rag, actions = assess(report, logs, inputs, incidents, tr, drop, tests_skipped)
    data = {"month": month, "generated": datetime.datetime.now().isoformat(timespec="seconds"), "rag": rag,
            "actions": actions, "drop": drop,
            "health": {"counts": report["counts"], "overall": report["overall"], "test_functions": test_functions,
                       "results": [{"check": r["check"], "status": r["status"]} for r in report["results"]]},
            "logs": logs, "inputs": inputs, "incidents": incidents, "trend": tr}
    jpath, mpath = _free_path(profile.it_path, f"monthly_check_{month}")
    safe_write_text(profile, jpath, scrub(json.dumps(data, indent=2, default=str)))
    safe_write_text(profile, mpath, render_md(data))
    return {"status": "OK", "mode": "monthly", "module": profile.name, "month": month, "rag": rag,
            "actions": actions, "health_counts": report["counts"], "incidents": incidents,
            "missing_inputs": inputs["missing"], "files": [rel_to_root(profile, jpath), rel_to_root(profile, mpath)],
            "applied_fixes": 0}
