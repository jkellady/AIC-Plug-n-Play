"""Tests for the IT specialist's monthly mode (synthetic temp tree only)."""
import datetime
import json
import os
import sys
import time
from pathlib import Path

import pytest

from src.agents.it_specialist import core, health, monthly
from src.agents.it_specialist.knowledge import ModuleProfile
from test_it_specialist import PASS_CMD, PROTECTED_SNAP, FAKE_SECRET, make_project, snapshot

TODAY = datetime.date(2026, 10, 18)
MONTH = "2026-09"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(health.NESTED_ENV, raising=False)

    def fake_classify(path):
        n = path.name.lower()
        return "roster" if "roster" in n else "hr" if "hr_" in n else "punches" if "all_report" in n else "unknown"

    monkeypatch.setattr(monthly, "_classify_file", fake_classify)


@pytest.fixture
def prof(tmp_path):
    p = ModuleProfile.load(root=make_project(tmp_path / "proj"))
    p.python, p.venv_dir, p.test_args = sys.executable, "", list(PASS_CMD)
    return p


def add_inputs(prof, month=MONTH):
    d = prof.root / "01_DROP_FOLDER" / month
    d.mkdir(parents=True, exist_ok=True)
    for n in ("OUT3-All_Report_x.xls", "Management_roster.xlsx", "HR_record.xlsx"):
        (d / n).write_bytes(b"x")


def add_reports(prof, month=MONTH):
    rep = prof.root / "06_REPORTS" / month
    rep.mkdir(parents=True, exist_ok=True)
    y, m = int(month[:4]), int(month[5:])
    (rep / f"Monthly_{y}-{m:02d}.xlsx").write_bytes(b"x")
    for w in monthly.expected_weeks(month, TODAY):
        e = w["end"]
        wd = prof.root / "06_REPORTS" / f"{e.year}-{e.month:02d}"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / f"Weekly_{w['iso_year']}-W{w['iso_week']:02d}.xlsx").write_bytes(b"x")


def check(prof, month=MONTH, **kw):
    return monthly.monthly_check(prof, month, run_tests=False, today=TODAY, **kw)


def saved(prof, name):
    return json.loads((prof.it_path / name).read_text())


def test_default_month_is_previous_month():
    assert monthly.previous_month_str(datetime.date(2026, 1, 5)) == "2025-12"
    assert monthly.previous_month_str(datetime.date(2026, 10, 18)) == "2026-09"


def test_expected_weeks_use_end_of_week_rule():
    weeks = monthly.expected_weeks("2026-09", TODAY)
    assert [w["end"].month for w in weeks] == [9] * len(weeks) and len(weeks) == 4
    assert all(w["end"] < TODAY for w in weeks)
    assert monthly.expected_weeks("2026-10", TODAY) == [w for w in monthly.expected_weeks("2026-10", TODAY) if w["end"] < TODAY]


def test_complete_month_is_green(prof):
    add_inputs(prof)
    add_reports(prof)
    out = check(prof)
    assert out["status"] == "OK" and out["rag"] == "GREEN", out["actions"]
    assert out["actions"] == ["No action needed this month."] and out["applied_fixes"] == 0
    d = saved(prof, "monthly_check_2026-09.json")
    assert d["drop"]["classes"] == {"punches": 1, "roster": 1, "hr": 1}
    assert d["inputs"]["missing"] == [] and d["inputs"]["expected_weekly_reports"] == 4
    md = (prof.it_path / "monthly_check_2026-09.md").read_text()
    assert "Monthly IT check 2026-09" in md and "GREEN" in md


def test_missing_inputs_and_reports_are_amber(prof):
    (prof.root / "01_DROP_FOLDER/2026-09").mkdir()
    (prof.root / "01_DROP_FOLDER/2026-09/OUT3-All_Report_x.xls").write_bytes(b"x")
    out = check(prof)
    assert out["rag"] == "AMBER"
    assert set(out["missing_inputs"]) == {"roster", "hr_workbook"}
    text = " ".join(out["actions"])
    assert "roster" in text and "weekly report" in text and "Month report" in text
    assert len(out["actions"]) <= 5


def test_missing_month_folder_is_reported(prof):
    out = check(prof, "2026-08")
    d = saved(prof, "monthly_check_2026-08.json")
    assert d["drop"]["available"] is False and out["rag"] == "AMBER"
    assert "Drop folder for this month is missing." in out["actions"]


def test_failed_runs_counted_and_repeated_signature_found(prof):
    add_inputs(prof); add_reports(prof)
    (prof.root / "07_LOGS/run_log.csv").write_text(
        "run_time,month,status,detail\n"
        "2026-09-03T01:00:00,2026-09,OK,\n"
        "2026-09-04T01:00:00,2026-09,FAILED,PermissionError WinError 32\n"
        "2026-09-05T01:00:00,2026-09,FAILED,PermissionError WinError 32\n"
        "2026-09-06T01:00:00,2026-09,PARTIAL,\n"
        "2026-10-01T01:00:00,2026-10,FAILED,other month\n")
    out = check(prof)
    info = saved(prof, "monthly_check_2026-09.json")["logs"]["run_log.csv"]
    assert info["runs"] == 4 and info["failed"] == 2 and info["partial"] == 1
    assert info["by_status"] == {"OK": 1, "FAILED": 2, "PARTIAL": 1}
    assert info["repeated_failures"] == [{"signature": "file_open_in_excel", "count": 2}]
    assert out["rag"] == "RED"
    assert any("PARTIAL" in a for a in out["actions"]) or any("FAILED" in a for a in out["actions"])


def test_text_log_review_counts_month_only(prof):
    add_inputs(prof); add_reports(prof)
    (prof.root / "07_LOGS/daily_task.log").write_text(
        "[Mon 12/10/2026  6:40:16.20] start \n[Mon 12/10/2026  6:40:19.36] end python=1 \n"
        "[Tue 08/09/2026  6:00:00.00] start \nTraceback (most recent call last):\n  File \"x.py\", line 1\nValueError: boom\n"
        "[Tue 08/09/2026  6:00:02.00] end python=1 \n"
        "[Wed 09/09/2026  6:00:00.00] start \n[Wed 09/09/2026  6:00:02.00] end python=0 \n")
    check(prof)
    info = saved(prof, "monthly_check_2026-09.json")["logs"]["daily_task.log"]
    assert info["runs"] == 2 and info["failed"] == 1 and info["tracebacks"] == 1
    assert info["by_status"] == {"FAILED": 1, "OK": 1}


def test_incident_summary(prof):
    add_inputs(prof); add_reports(prof)
    log = prof.root / "07_LOGS/it/incident_log.csv"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("time,incident,state,class,severity,fix_risk,note\n"
                   "2026-09-02T10:00:00,A,TRIAGED,stale_lock,MEDIUM,SAFE,x\n2026-09-02T10:05:00,A,CLOSED,stale_lock,MEDIUM,SAFE,x\n"
                   "2026-09-10T10:00:00,B,TRIAGED,secret_exposed,CRITICAL,MANUAL_ONLY,x\n2026-09-10T10:01:00,B,ESCALATED,secret_exposed,CRITICAL,MANUAL_ONLY,x\n"
                   "2026-10-02T10:00:00,C,ESCALATED,x,LOW,MANUAL_ONLY,x\n")
    out = check(prof)
    assert out["incidents"]["opened"] == 2 and out["incidents"]["closed"] == 1
    assert out["incidents"]["still_open"] == 1 and out["incidents"]["open_ids"] == ["B"]
    assert out["rag"] == "RED"


def test_trend_against_previous_month(prof):
    add_inputs(prof, "2026-08")
    check(prof, "2026-08")
    out = check(prof, MONTH)
    t = saved(prof, "monthly_check_2026-09.json")["trend"]
    assert t["available"] and t["previous_month"] == "2026-08"
    assert t["health_delta"] == {"PASS": 0, "WARN": 0, "FAIL": 0} and t["test_functions_delta"] == 0
    (prof.root / "tests/test_y.py").write_text("def test_b():\n    assert True\n")
    check(prof, MONTH)
    t2 = saved(prof, "monthly_check_2026-09_2.json")["trend"]
    assert t2["test_functions_delta"] == 1
    assert saved(prof, "monthly_check_2026-09.json")["trend"]["test_functions_delta"] == 0


def test_never_overwrites_existing_month_file(prof):
    check(prof)
    first = (prof.it_path / "monthly_check_2026-09.json").read_bytes()
    out = check(prof)
    check(prof)
    assert (prof.it_path / "monthly_check_2026-09.json").read_bytes() == first
    assert out["files"] == ["07_LOGS/it/monthly_check_2026-09_2.json", "07_LOGS/it/monthly_check_2026-09_2.md"]
    assert (prof.it_path / "monthly_check_2026-09_3.md").is_file()


def test_propose_only_changes_nothing_but_it_folder(prof):
    add_inputs(prof)
    lock = prof.root / "07_LOGS/daily.lock"
    lock.write_text("x")
    old = time.time() - 3 * 3600
    os.utime(lock, (old, old))
    (prof.root / "07_LOGS/processed_manifest.json").write_text("{broken")
    pre = snapshot(prof.root, ["."])
    out = check(prof)
    post = snapshot(prof.root, ["."])
    changed = {k for k in set(pre) | set(post) if pre.get(k) != post.get(k) and not k.startswith(".git/")}
    assert changed and all(k.startswith("07_LOGS/it/") for k in changed), sorted(changed)
    assert lock.exists() and (prof.root / "07_LOGS/processed_manifest.json").exists()
    assert out["rag"] == "RED" and not (prof.root / "07_LOGS/last_scan.json").exists()
    assert not (prof.root / "_backup").exists()


def test_privacy_scrub(prof):
    add_inputs(prof); add_reports(prof)
    (prof.root / "07_LOGS/run_log.csv").write_text(
        "run_time,month,status,detail\n2026-09-04T01:00:00,2026-09,FAILED,password=" + FAKE_SECRET + " jane@example.com\n")
    (prof.root / "07_LOGS/daily_task.log").write_text("[Tue 08/09/2026 6:00:00.00] start\nPermissionError: secret=" + FAKE_SECRET + "\n")
    core.run(mode="monthly", profile=prof, month=MONTH, run_tests=False)
    blob = "".join(f.read_text(errors="replace") for f in prof.it_path.rglob("monthly_check_*"))
    assert blob and FAKE_SECRET not in blob and "jane@example.com" not in blob


def test_core_monthly_prints_nothing_and_never_raises(prof, capsys):
    for kw in ({}, {"month": MONTH}, {"month": "garbage"}, {"month": None}, {"month": 42}):
        r = core.run(mode="monthly", profile=prof, run_tests=False, **kw)
        assert isinstance(r, dict) and r["status"] in ("OK", "FAILED")
    assert core.run(mode="monthly", profile=prof, month="2026-13", run_tests=False)["status"] == "FAILED"
    assert capsys.readouterr().out == ""


def test_core_monthly_result_shape(prof):
    add_inputs(prof)
    r = core.run(mode="monthly", profile=prof, month=MONTH, run_tests=False)
    assert r["status"] == "OK" and r["rag"] in ("GREEN", "AMBER", "RED")
    assert 1 <= len(r["actions"]) <= 5 and set(r["health_counts"]) == {"PASS", "WARN", "FAIL"}


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "src" / "daily.py").exists(), reason="live project absent")
def test_live_summaries_are_read_only():
    p = ModuleProfile.load()
    d = monthly.drop_summary(p, "2026-05")
    assert "available" in d
    assert monthly.review_logs(p, "2026-05")
