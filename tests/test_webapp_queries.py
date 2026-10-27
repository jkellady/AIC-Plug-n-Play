"""Tests for src.webapp.queries. Synthetic data, fake names only (FAKEONE...)."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import src.daily as daily
from src.agents import scope_resolver
from src.dayengine import DayFlag
from src.webapp import queries

REAL_ROOT = Path(daily.PROJECT_ROOT)
D = dt.date


class FakeRules:
    def __init__(self, excluded=()):
        self._ex = list(excluded)

    def get(self, rule_id):
        return self._ex if rule_id == "excluded_staff_ids" else None


def make_md():
    df = pd.DataFrame([
        {"id_initials": "F1", "full_name_on_tab": "FAKEONE A", "home_outlet_tab": "OUT3", "status": "Matched"},
        {"id_initials": "F2", "full_name_on_tab": "FAKETWO B", "home_outlet_tab": "OUT3", "status": "Conflict"},
        {"id_initials": "F3", "full_name_on_tab": "FAKETHREE C", "home_outlet_tab": "OUT4", "status": "Draft"},
        {"id_initials": "F4", "full_name_on_tab": "FAKEFOUR D", "home_outlet_tab": "OUT4", "status": "Matched"},
    ])
    return SimpleNamespace(staff_aliases=df, outlets=pd.DataFrame())


def row(person, date, punches, wtype="WORK"):
    return {"person": person, "date": date, "day": date.strftime("%a"), "schedule_code": "FULL",
            "type": wtype, "shift_start": "10:00", "shift_end": "22:00", "punches": punches,
            "clock_outlet": None, "system_in": None, "system_out": None, "hq_in": None,
            "hq_out": None, "flag_refs": [0]}


def flag(person, date, name, severity="Check", remark_type="Pending question", **kw):
    return DayFlag(severity=severity, flag=name, person=person, home_outlet_tab="OUT3", date=date,
                   details=kw.pop("details", "detail"), remark="", remark_type=remark_type,
                   ref=kw.pop("ref", ""), **kw)


def month_result(rows, flags, status="OK", rules=None):
    return {"status": status, "month": "x", "missing": [] if status == "OK" else ["a file is missing"],
            "rules": rules or FakeRules(), "all_flags": flags, "day_check_rows": rows,
            "master_data": make_md()}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for sub in ("01_DROP_FOLDER", "03_MASTER_DATA", "06_REPORTS", "07_LOGS"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(daily, "DROP_DIR", tmp_path / "01_DROP_FOLDER")
    monkeypatch.setattr(daily, "MASTER_DATA_DIR", tmp_path / "03_MASTER_DATA")
    monkeypatch.setattr(daily, "REPORTS_DIR", tmp_path / "06_REPORTS")
    monkeypatch.setattr(daily, "LOGS_DIR", tmp_path / "07_LOGS")
    monkeypatch.setattr(scope_resolver, "today", lambda: D(2026, 10, 19))
    queries.clear_cache()
    yield tmp_path
    queries.clear_cache()


def snapshot(root: Path):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in root.rglob("*") if p.is_file())


def patch_run(monkeypatch, results: dict, calls=None):
    def fake(month, force=False, _test_hook=None):
        if calls is not None:
            calls.append((month, force))
        return results[month]
    monkeypatch.setattr(daily, "run_month", fake)


# W40 = 2026-09-28 .. 2026-10-04 (spans Sep/Oct). W41 = Oct 5..11.
def standard_results():
    sep_rows = [row("F1", D(2026, 9, 28), "10:00, 22:00"), row("F3", D(2026, 9, 29), "10:30, 22:00"),
                row("F1", D(2026, 9, 30), None)]
    sep_flags = [flag("F3", D(2026, 9, 29), "Late in (over 15 min)", remark_type="Pending question"),
                 flag("F1", "Month", "Missing clock-in and clock-out", remark_type="Possible error",
                      days=1, span="2026-09-30 to 2026-09-30")]
    oct_rows = [row("F1", D(2026, 10, 1), "10:00, 22:00"), row("F2", D(2026, 10, 2), "10:20"),
                row("F4", D(2026, 10, 2), "10:00, 22:00"), row("F1", D(2026, 10, 3), None, "OFF"),
                row("F4", D(2026, 10, 6), "10:00, 22:00")]
    oct_flags = [flag("F2", D(2026, 10, 2), "Late in (over 15 min)", remark_type="Confirmed rule"),
                 flag("F4", D(2026, 10, 2), "Punched at another outlet", severity="Info"),
                 flag("F2", "Month", "Missing clock-out", remark_type="Possible error", days=1,
                      span="2026-10-02 to 2026-10-02"),
                 flag("F1", "Month", "No clock file in batch", severity="Info"),
                 flag("F4", "Month", "Tab name differs", remark_type="Possible error")]
    return {"2026-09": month_result(sep_rows, sep_flags), "2026-10": month_result(oct_rows, oct_flags)}


def make_months(env, *months):
    for m in months:
        (env / "01_DROP_FOLDER" / m).mkdir(exist_ok=True)


# ------------------------------------------------------------------- tests

def test_status_mapping_table():
    assert queries.STATUS_MAPPING == {
        "Possible error": "Needs review", "Pending question": "Pending question",
        "Confirmed rule": "Confirmed rule"}
    cases = [
        (flag("F1", D(2026, 10, 1), "x", "Check", "Possible error"), "Needs review"),
        (flag("F1", D(2026, 10, 1), "x", "Blocker", "Possible error"), "Needs review"),
        (flag("F1", D(2026, 10, 1), "x", "Check", "Pending question"), "Pending question"),
        (flag("F1", D(2026, 10, 1), "x", "Check", "Confirmed rule"), "Confirmed rule"),
        (flag("F1", D(2026, 10, 1), "x", "Info", "Possible error"), "Info"),
        (flag("F1", D(2026, 10, 1), "x", "Info", "Confirmed rule"), "Info"),
        (flag("F1", D(2026, 10, 1), "x", "Check", "Weird"), "Needs review"),
    ]
    for f, want in cases:
        assert queries._status_for(f) == want


def test_week_boundaries(env):
    assert queries.parse_week("2026-W41")[:2] == (D(2026, 10, 5), D(2026, 10, 11))
    assert queries.parse_week("2026-W40")[:2] == (D(2026, 9, 28), D(2026, 10, 4))
    assert queries.parse_week("2026-W01")[0] == D(2025, 12, 29)  # ISO year start
    # default = last completed week relative to today (Mon 2026-10-19)
    assert queries.parse_week(None)[:2] == (D(2026, 10, 12), D(2026, 10, 18))
    assert queries.parse_week("2026-W41")[2].startswith("2026-W41")
    with pytest.raises(ValueError):
        queries.parse_week("nonsense")


def test_invalid_week_never_raises(env):
    out = queries.overview("bogus")
    assert "not valid" in out["status"]
    assert out["kpis"]["open_exceptions"] == 0
    assert queries.exceptions(week="bogus") == []


def test_overview_single_month_kpis(env, monkeypatch):
    make_months(env, "2026-10")
    patch_run(monkeypatch, standard_results())
    out = queries.overview("2026-W41")
    json.dumps(out)
    assert out["start"] == "2026-10-05" and out["end"] == "2026-10-11"
    assert out["status"] == "OK"
    # F4 on Oct 6 only: 1 scheduled, present, on time. F1 has no clock file -> excluded.
    assert out["kpis"]["attendance_rate"] == 100.0
    assert out["kpis"]["late_arrivals"] == 0
    assert out["by_outlet"] == [{"outlet": "OUT4", "on_time_pct": 100.0, "late_pct": 0.0,
                                 "missing_pct": 0.0, "attendance_pct": 100.0}]


def test_spanning_month_merge(env, monkeypatch):
    make_months(env, "2026-09", "2026-10")
    calls = []
    patch_run(monkeypatch, standard_results(), calls)
    out = queries.overview("2026-W40")
    assert {c[0] for c in calls} == {"2026-09", "2026-10"}
    assert out["status"] == "OK"
    assert out["covered"] == {"start": "2026-09-28", "end": "2026-10-04"}
    k = out["kpis"]
    # tracked scheduled WORK days: F1 Sep28, F1 Sep30 are F1 (no clock file) -> excluded.
    # F3 Sep29 (late), F2 Oct2 (1 punch -> missing), F4 Oct2 (on time).
    assert k["late_arrivals"] == 1 and k["late_staff"] == 1
    assert k["missing_punches"] == 1
    assert k["attendance_rate"] == 100.0
    outlets = {o["outlet"]: o for o in out["by_outlet"]}
    assert outlets["OUT3"]["missing_pct"] == 100.0 and outlets["OUT3"]["on_time_pct"] == 0.0
    assert outlets["OUT4"]["late_pct"] == 50.0 and outlets["OUT4"]["on_time_pct"] == 50.0
    # exceptions from both months, Info included, not outside the week
    ex = queries.exceptions(week="2026-W40")
    assert {e["issue"] for e in ex} >= {"Late in (over 15 min)", "Missing clock-out",
                                        "Punched at another outlet"}
    assert k["open_exceptions"] == len([e for e in ex if e["status"] in queries.OPEN_STATUSES])


def test_force_retry_on_nothing_new(env, monkeypatch):
    make_months(env, "2026-10")
    calls = []

    def fake(month, force=False, _test_hook=None):
        calls.append(force)
        return {"status": "NOTHING_NEW", "month": month} if not force else standard_results()[month]
    monkeypatch.setattr(daily, "run_month", fake)
    out = queries.overview("2026-W41")
    assert calls == [False, True] and out["status"] == "OK"


def test_missing_month_folder_graceful(env, monkeypatch):
    make_months(env, "2026-10")  # September folder absent
    patch_run(monkeypatch, standard_results())
    out = queries.overview("2026-W40")
    assert out["status"].startswith("PARTIAL") and "2026-09" in out["status"]
    assert out["covered"] == {"start": "2026-10-01", "end": "2026-10-04"}
    none = queries.overview("2026-W30")  # no folders at all
    assert none["covered"] is None and "No data folder" in none["status"]
    assert none["kpis"]["attendance_rate"] is None and none["by_outlet"] == []


def test_partial_and_failed_and_empty(env, monkeypatch):
    make_months(env, "2026-10")
    res = standard_results()
    res["2026-10"]["status"] = "PARTIAL"
    patch_run(monkeypatch, res)
    assert queries.overview("2026-W41")["status"].startswith("PARTIAL")
    queries.clear_cache()
    patch_run(monkeypatch, {"2026-10": {"status": "FAILED", "error": "FAKEONE exploded"}})
    out = queries.overview("2026-W41")
    assert out["covered"] is None and "FAKEONE" not in out["status"]
    queries.clear_cache()
    patch_run(monkeypatch, {"2026-10": month_result([], [])})
    out = queries.overview("2026-W41")
    assert out["kpis"]["attendance_rate"] is None and "No scheduled" in out["status"]
    queries.clear_cache()

    def boom(month, force=False, _test_hook=None):
        raise RuntimeError("FAKEONE secret")
    monkeypatch.setattr(daily, "run_month", boom)
    out = queries.overview("2026-W41")
    assert "FAKEONE" not in json.dumps(out["status"])
    assert queries.exceptions(week="2026-W41") == []


def test_excluded_staff_not_listed(env, monkeypatch):
    make_months(env, "2026-10")
    res = standard_results()
    res["2026-10"]["rules"] = FakeRules(excluded=["F4"])
    # engine already removes excluded flags; names map must also drop them
    res["2026-10"]["all_flags"] = [f for f in res["2026-10"]["all_flags"] if f.person != "F4"]
    patch_run(monkeypatch, res)
    ex = queries.exceptions(week="2026-W40")
    assert all(e["staff_id"] != "F4" for e in ex)
    assert queries._load_month("2026-10")["names"].get("F4") is None


def test_exceptions_filters_and_shape(env, monkeypatch):
    make_months(env, "2026-09", "2026-10")
    patch_run(monkeypatch, standard_results())
    ex = queries.exceptions(week="2026-W40")
    json.dumps(ex)
    assert ex and set(ex[0]) >= {"id", "staff", "outlet", "date", "issue", "status", "severity"}
    assert len({e["id"] for e in ex}) == len(ex)
    assert {e["status"] for e in ex} <= {"Needs review", "Pending question", "Info", "Confirmed rule"}
    late = [e for e in ex if e["issue"].startswith("Late")]
    assert {e["status"] for e in late} == {"Pending question", "Confirmed rule"}
    assert all(e["status"] == "Info" for e in queries.exceptions("Info", week="2026-W40"))
    assert all(e["outlet"] == "OUT4" for e in queries.exceptions(outlet="sg", week="2026-W40"))
    assert queries.exceptions("Needs review", "OUT4", week="2026-W40")
    # month-level Info without span ("No clock file") is not listed; non-Info one is
    issues = {e["issue"] for e in ex}
    assert "No clock file in batch" not in issues and "Tab name differs" in issues
    # a rolled flag whose span is outside the week is dropped
    assert not [e for e in queries.exceptions(week="2026-W41") if e["issue"] == "Missing clock-out"]
    # default week = last completed week; no crash
    assert isinstance(queries.exceptions(), list)


def test_older_than_week(env, monkeypatch):
    make_months(env, "2026-10")
    monkeypatch.setattr(scope_resolver, "today", lambda: D(2026, 10, 28))
    patch_run(monkeypatch, standard_results())
    k = queries.overview("2026-W40")["kpis"]
    assert k["older_than_week"] >= 1 and k["older_than_week"] <= k["open_exceptions"]


def test_staff_and_outlets(env, monkeypatch):
    md = env / "03_MASTER_DATA"
    for name in ("outlets.csv", "schedule_codes.csv"):
        shutil.copy(REAL_ROOT / "03_MASTER_DATA" / name, md / name)
    (md / "staff_aliases.csv").write_text(
        "id_initials,record_tab,full_name_on_tab,clock_file_names,home_outlet_tab,schedule_block,evidence,status,client_confirm\n"
        "F1,t,FAKEONE A,,OUT3,,,Matched,\nF2,t,FAKETWO B,,OUT3,,,Conflict,\n"
        "F3,t,FAKETHREE C,,OUT4,,,Draft,\nF4,t,FAKEFOUR D,,,,,Matched,\n", encoding="utf-8")
    import src.rules
    monkeypatch.setattr(src.rules, "load_rules", lambda p: FakeRules(excluded=["F3"]))
    s = queries.staff()
    json.dumps(s)
    assert [r["id"] for r in s] == ["F1", "F2", "F4"]
    assert {r["id"]: r["status"] for r in s}["F2"] == "Check details"
    assert queries.outlets() == [{"outlet": "OUT3", "staff_count": 2},
                                 {"outlet": "Unassigned", "staff_count": 1}]


def test_staff_missing_master_data(env):
    assert queries.staff() == [] and queries.outlets() == []


def test_reports_listing(env):
    rep, logs = env / "06_REPORTS", env / "07_LOGS"
    (rep / "2026-10").mkdir()
    (rep / "2026-05").mkdir()
    (rep / "LATEST").mkdir()
    (rep / "2026-10" / "Weekly_2026-W41.xlsx").write_bytes(b"x" * 10)
    (rep / "2026-10" / "Weekly_2026-W40.xlsx").write_bytes(b"x" * 5)
    (rep / "2026-10" / "Monthly_2026-09.xlsx").write_bytes(b"y")
    (rep / "2026-05" / "Attendance_Output_Report_2026-05.xlsx").write_bytes(b"z")
    (rep / "2026-05" / "AGGREGATE_SUMMARY.json").write_text("{}")
    (rep / "LATEST" / "Review_May-26.html").write_text("<html>")
    (logs / "weekly_log.csv").write_text(
        "run_time,window,status,detail\n"
        "t,2026-W41_Weekly,PARTIAL,x\nt,2026-W40_Weekly,OK,x\nt,2026-W40_Weekly,SKIPPED_EXISTS,x\n",
        encoding="utf-8")
    r = queries.reports()
    json.dumps(r)
    by = {x["name"]: x for x in r}
    assert set(by) == {"Weekly_2026-W41.xlsx", "Weekly_2026-W40.xlsx", "Monthly_2026-09.xlsx",
                       "Attendance_Output_Report_2026-05.xlsx"}
    assert by["Weekly_2026-W41.xlsx"]["status"] == "PARTIAL"
    assert by["Weekly_2026-W40.xlsx"]["status"] == "Ready"
    assert by["Weekly_2026-W41.xlsx"]["kind"] == "weekly"
    assert by["Monthly_2026-09.xlsx"]["kind"] == "monthly"
    assert by["Attendance_Output_Report_2026-05.xlsx"]["kind"] == "trial"
    assert by["Weekly_2026-W41.xlsx"]["path"] == "2026-10/Weekly_2026-W41.xlsx"
    assert by["Weekly_2026-W41.xlsx"]["size"] == 10


def test_reports_missing_dir(env, monkeypatch):
    monkeypatch.setattr(daily, "REPORTS_DIR", env / "nope")
    assert queries.reports() == []


def test_cache_avoids_second_engine_call(env, monkeypatch):
    make_months(env, "2026-10")
    calls = []
    patch_run(monkeypatch, standard_results(), calls)
    queries.overview("2026-W41")
    queries.overview("2026-W41")
    queries.exceptions(week="2026-W41")
    assert calls.count(("2026-10", False)) == 1


def test_read_only_and_silent(env, monkeypatch, capsys):
    make_months(env, "2026-09", "2026-10")
    (env / "06_REPORTS" / "2026-10").mkdir()
    (env / "06_REPORTS" / "2026-10" / "Weekly_2026-W41.xlsx").write_bytes(b"x")
    patch_run(monkeypatch, standard_results())
    before = snapshot(env)
    queries.overview("2026-W40")
    queries.overview()
    queries.exceptions()
    queries.staff()
    queries.outlets()
    queries.reports()
    after = snapshot(env)
    cache_dir = str(Path("07_LOGS") / "cache")
    after_no_cache = [e for e in after if cache_dir not in e[0]]
    before_no_cache = [e for e in before if cache_dir not in e[0]]
    assert after_no_cache == before_no_cache
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""


# ------------------------------------------------------- real-engine test

REAL_MAY = REAL_ROOT / "01_DROP_FOLDER" / "2026-05"
REAL_MASTER = REAL_ROOT / "03_MASTER_DATA"


@pytest.mark.skipif(not (REAL_MAY.is_dir() and (REAL_MASTER / "staff_aliases.csv").exists()
                         and any(REAL_MAY.glob("*-All_Report*.xls"))),
                    reason="May 2026 sample not present")
def test_real_engine_on_temp_copy_of_may(tmp_path, monkeypatch):
    drop, md = tmp_path / "01_DROP_FOLDER", tmp_path / "03_MASTER_DATA"
    shutil.copytree(REAL_MAY, drop / "2026-05")
    shutil.copytree(REAL_MASTER, md)
    for sub in ("06_REPORTS", "07_LOGS"):
        (tmp_path / sub).mkdir()
    logs = tmp_path / "07_LOGS"
    for attr, val in {
        "DROP_DIR": drop, "MASTER_DATA_DIR": md, "REPORTS_DIR": tmp_path / "06_REPORTS",
        "LOGS_DIR": logs, "MANIFEST_PATH": logs / "processed_manifest.json",
        "RUN_LOG_PATH": logs / "run_log.csv", "DAILY_TASK_LOG_PATH": logs / "daily_task.log",
        "APPROVALS_PATH": md / "approvals.csv",
        "PENDING_QUESTIONS_PATH": md / "pending_questions.toml",
    }.items():
        monkeypatch.setattr(daily, attr, val)
    try:
        from src import run_lock
        monkeypatch.setattr(run_lock, "_LOCK_PATH", logs / "engine.lock")
    except (ImportError, AttributeError):
        pass
    queries.clear_cache()
    drop_before = snapshot(drop)
    out = queries.overview("2026-W19")  # 2026-05-04 .. 2026-05-10
    json.dumps(out)
    assert out["start"] == "2026-05-04" and out["end"] == "2026-05-10"
    assert out["kpis"]["attendance_rate"] is not None
    assert out["by_outlet"]
    ex = queries.exceptions(week="2026-W19")
    json.dumps(ex)
    assert all(e["status"] in ("Needs review", "Pending question", "Info", "Confirmed rule") for e in ex)
    # week spanning April/May: April folder absent -> graceful, May merged
    span = queries.overview("2026-W18")
    assert span["status"].startswith("PARTIAL") and "2026-04" in span["status"]
    assert queries.staff() and queries.outlets()
    assert snapshot(drop) == drop_before  # inputs untouched
    queries.clear_cache()
