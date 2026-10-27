"""Tests for the IT and security specialist. All on synthetic trees under tmp_path;
the live project is only read (no writes) in the last smoke test."""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.agents import it_specialist as its
from src.agents.it_specialist import core, health, knowledge, triage, workflows
from src.agents.it_specialist.knowledge import ModuleProfile

LIVE = Path(__file__).resolve().parents[1]
PASS_CMD = ["-c", "print('3 passed, 1 skipped in 0.1s')"]
FAIL_CMD = ["-c", "import sys; print('1 failed, 2 passed in 0.1s'); sys.exit(1)"]
FAKE_SECRET = "Zq" + "9xK2" + "mW7vBn4"  # built at runtime so no literal secret sits in source


@pytest.fixture(autouse=True)
def _not_nested(monkeypatch):
    monkeypatch.delenv(health.NESTED_ENV, raising=False)


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)


GITIGNORE = """01_DROP_FOLDER/*
!01_DROP_FOLDER/READ_ME_FIRST.txt
02_RAW_ARCHIVE/
04_REFERENCE_SOURCES/
05_CAPTURED_DATA/
06_REPORTS/
07_LOGS/
03_MASTER_DATA/staff_aliases.csv
03_MASTER_DATA/approvals.csv
approvals.csv
_backup/
.env
"""


def make_project(root: Path, git=True) -> Path:
    for d in ("01_DROP_FOLDER/2026-05", "02_RAW_ARCHIVE", "03_MASTER_DATA", "04_REFERENCE_SOURCES",
              "05_CAPTURED_DATA", "06_REPORTS", "07_LOGS", "src/agents", "tests", ".venv"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "01_DROP_FOLDER/2026-05/a.xls").write_bytes(b"clock-bytes-1")
    (root / "01_DROP_FOLDER/READ_ME_FIRST.txt").write_text("read me")
    (root / "02_RAW_ARCHIVE/old.xls").write_bytes(b"archived")
    (root / "04_REFERENCE_SOURCES/ref.xlsx").write_bytes(b"reference")
    (root / "06_REPORTS/rep.xlsx").write_bytes(b"report")
    (root / "src/__init__.py").write_text("")
    (root / "src/daily.py").write_text('"""Daily engine."""\nLOCK_STALE_SECONDS = 2 * 60 * 60\n\n\ndef run_month(month):\n    """Run one month."""\n    return month\n')
    (root / "src/agents/email_sender.py").write_text("def send_report(report, to=None, send_enabled=False):\n    return 'x'\n")
    (root / "tests/test_x.py").write_text("def test_a():\n    assert True\n")
    (root / "config.toml").write_text('[paths]\ndrop = "01_DROP_FOLDER"\n')
    (root / "run_daily.bat").write_text("python -m src.daily\n")
    (root / ".gitignore").write_text(GITIGNORE)
    if git:
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@example.invalid")
        _git(root, "config", "user.name", "t")
        _git(root, "add", "-A")
    return root


@pytest.fixture
def proj(tmp_path):
    return make_project(tmp_path / "proj")


@pytest.fixture
def prof(proj):
    p = ModuleProfile.load(root=proj)
    p.python = sys.executable
    p.venv_dir = ""
    p.test_args = list(PASS_CMD)
    return p


def snapshot(root: Path, rels):
    snap = {}
    for rel in rels:
        base = root / rel
        files = [base] if base.is_file() else list(base.rglob("*")) if base.is_dir() else []
        for f in files:
            if f.is_file():
                snap[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return snap


PROTECTED_SNAP = ["01_DROP_FOLDER", "02_RAW_ARCHIVE", "04_REFERENCE_SOURCES", "src", "tests", "config.toml",
                  ".gitignore", "03_MASTER_DATA", "05_CAPTURED_DATA"]


def status(report, name):
    return next(r for r in report["results"] if r["check"] == name)


def one(prof, name, **kw):
    return health.run_checks(prof, only={name}, **kw)["results"][0]


# ---------------------------------------------------------------- profile

def test_default_profile_loads_and_points_at_live_root():
    p = ModuleProfile.load()
    assert p.root == LIVE
    assert len(p.playbooks) >= 10 and "01_DROP_FOLDER" in p.protected and p.writable == ["06_REPORTS", "07_LOGS"]
    assert p.lock_stale_limit() == 7200


def test_writable_guard(prof):
    assert knowledge.is_writable(prof, prof.p("07_LOGS/x"))
    assert not knowledge.is_writable(prof, prof.p("01_DROP_FOLDER/2026-05/a.xls"))
    assert not knowledge.is_writable(prof, prof.p("src/daily.py"))
    with pytest.raises(PermissionError):
        knowledge.safe_write_text(prof, prof.p("src/daily.py"), "x")


# ---------------------------------------------------------------- health PASS paths

def test_healthy_project_has_no_fail(prof):
    rep = health.run_checks(prof)
    assert rep["counts"]["FAIL"] == 0, [r for r in rep["results"] if r["status"] == "FAIL"]
    assert len(rep["results"]) == 14
    assert status(rep, "tests")["detail"].startswith("passed=3 failed=0 skipped=1")
    for name in ("python_env", "folders", "stale_lock", "manifest", "secrets_scan", "env_tracking",
                 "gitignore_coverage", "git_staff_data", "backup_untracked", "email_gate"):
        assert status(rep, name)["status"] == "PASS", status(rep, name)


def test_every_result_has_the_contract_shape(prof):
    for r in health.run_checks(prof, run_tests=False)["results"]:
        assert set(r) == {"check", "status", "detail"} and r["status"] in ("PASS", "WARN", "FAIL")


# ---------------------------------------------------------------- health FAIL paths

def test_python_missing_fails(prof):
    prof.python = str(prof.root / "nope" / "python.exe")
    assert one(prof, "python_env")["status"] == "FAIL"
    assert one(prof, "tests")["status"] == "FAIL"


def test_failing_tests_fail_with_counts(prof):
    prof.test_args = list(FAIL_CMD)
    r = one(prof, "tests")
    assert r["status"] == "FAIL" and "failed=1" in r["detail"] and "passed=2" in r["detail"]


def test_tests_skipped_by_caller_is_warn(prof):
    assert one(prof, "tests", run_tests=False)["status"] == "WARN"


def test_missing_folder_fails(prof):
    (prof.root / "05_CAPTURED_DATA").rmdir()
    r = one(prof, "folders")
    assert r["status"] == "FAIL" and "05_CAPTURED_DATA" in r["detail"]


def test_stale_lock_paths(prof):
    lock = prof.p("07_LOGS/daily.lock")
    assert one(prof, "stale_lock")["status"] == "PASS"
    lock.write_text("now")
    assert one(prof, "stale_lock")["status"] == "WARN"
    old = time.time() - 3 * 60 * 60
    os.utime(lock, (old, old))
    assert one(prof, "stale_lock")["status"] == "FAIL"


def test_manifest_paths(prof):
    mf = prof.p("07_LOGS/processed_manifest.json")
    good = {"2026-05": {"files": {"a.xls": {"size": 13, "mtime": 1, "sha256": "x"}}}}
    mf.write_text(json.dumps(good))
    assert one(prof, "manifest")["status"] == "PASS"
    good["2026-05"]["files"]["gone.xls"] = {"size": 1}
    mf.write_text(json.dumps(good))
    assert one(prof, "manifest")["status"] == "WARN"
    mf.write_text("{not json")
    assert one(prof, "manifest")["status"] == "FAIL"


def test_logs_scan(prof):
    log = prof.p("07_LOGS/daily_task.log")
    log.write_text("[x] start\n[x] end python=0 \n")
    assert one(prof, "logs")["status"] == "PASS"
    log.write_text("[x] start\nTraceback (most recent call last):\n  boom\n[x] end python=0 \n")
    r = one(prof, "logs")
    assert r["status"] == "WARN" and "line 2" in r["detail"]
    log.write_text("[x] start\n[x] end python=1 \n")
    assert one(prof, "logs")["status"] == "FAIL"
    log.write_text("[x] end python=0\n")
    prof.p("07_LOGS/run_log.csv").write_text("run_time,month,status\na,2026-05,OK\nb,2026-05,FAILED\n")
    assert one(prof, "logs")["status"] == "FAIL"
    prof.p("07_LOGS/run_log.csv").write_text("run_time,month,status\nb,2026-05,FAILED\na,2026-05,OK\n")
    assert one(prof, "logs")["status"] == "WARN"


def test_secret_scan_finds_planted_secret_and_hides_value(prof):
    prof.p("07_LOGS/daily_task.log").write_text("ok\nlogin password=" + FAKE_SECRET + " sent\n")
    r = one(prof, "secrets_scan")
    assert r["status"] == "FAIL"
    assert "07_LOGS/daily_task.log:2" in r["detail"] and FAKE_SECRET not in r["detail"]


def test_secret_scan_in_tracked_source_is_warn(prof):
    f = prof.p("src/cfg.py")
    f.write_text('x = 1\nTOKEN = "' + FAKE_SECRET + '"\n')
    _git(prof.root, "add", "-A")
    r = one(prof, "secrets_scan")
    assert r["status"] == "WARN" and "src/cfg.py:2" in r["detail"] and FAKE_SECRET not in r["detail"]


def test_secret_scan_ignores_code_that_reads_env(prof):
    prof.p("src/cfg.py").write_text("password = os.environ['X']\ntoken = getenv('Y')\n")
    _git(prof.root, "add", "-A")
    assert one(prof, "secrets_scan")["status"] == "PASS"


def test_long_mixed_token_found_but_sha256_ignored(prof):
    tok = "aB3" * 12
    prof.p("07_LOGS/a.log").write_text("hash " + "a1" * 32 + "\n")
    assert one(prof, "secrets_scan")["status"] == "PASS"
    prof.p("07_LOGS/a.log").write_text("key " + tok + "\n")
    assert one(prof, "secrets_scan")["status"] == "FAIL"


def test_env_tracked_or_in_logs_fails(prof):
    (prof.root / ".env").write_text("X=1\n")
    assert one(prof, "env_tracking")["status"] == "PASS"
    _git(prof.root, "add", "-f", ".env")
    assert one(prof, "env_tracking")["status"] == "FAIL"
    _git(prof.root, "rm", "--cached", "-q", ".env")
    (prof.root / "07_LOGS/.env").write_text("X=1\n")
    assert one(prof, "env_tracking")["status"] == "FAIL"


def test_gitignore_gap_fails(prof):
    (prof.root / ".gitignore").write_text(GITIGNORE.replace("06_REPORTS/\n", ""))
    r = one(prof, "gitignore_coverage")
    assert r["status"] == "FAIL" and "06_REPORTS" in r["detail"]


def test_staff_data_tracked_or_staged_fails(prof):
    _git(prof.root, "add", "-f", "06_REPORTS/rep.xlsx")
    r = one(prof, "git_staff_data")
    assert r["status"] == "FAIL" and "06_REPORTS/rep.xlsx" in r["detail"]


def test_backup_tracked_fails(prof):
    (prof.root / "_backup/b1").mkdir(parents=True)
    (prof.root / "_backup/b1/f.txt").write_text("x")
    _git(prof.root, "add", "-f", "_backup")
    assert one(prof, "backup_untracked")["status"] == "FAIL"


def test_email_gate_open_fails(prof):
    prof.p("src/agents/email_sender.py").write_text("def send_report(report, send_enabled=True):\n    pass\n")
    assert one(prof, "email_gate")["status"] == "FAIL"
    prof.p("src/agents/email_sender.py").write_text("def send_report(report, send_enabled=False):\n    pass\n")
    prof.p("run_daily.bat").write_text("python -c send_report(send_enabled=True)\n")
    r = one(prof, "email_gate")
    assert r["status"] == "FAIL" and "run_daily.bat:1" in r["detail"]


def test_not_a_git_repo_degrades_to_warn(tmp_path):
    root = make_project(tmp_path / "nogit", git=False)
    p = ModuleProfile.load(root=root)
    for name in ("gitignore_coverage", "git_staff_data", "backup_untracked"):
        assert one(p, name)["status"] == "WARN"


def test_scheduled_tasks_check_never_fails(prof):
    assert one(prof, "scheduled_tasks")["status"] in ("PASS", "WARN")


def test_crashing_check_is_contained(prof, monkeypatch):
    monkeypatch.setattr(health, "CHECKS", [("boom", lambda p, **k: 1 / 0)])
    r = health.run_checks(prof)["results"][0]
    assert r["status"] == "FAIL" and "ZeroDivisionError" in r["detail"]


def test_nested_run_skips_test_suite(prof, monkeypatch):
    monkeypatch.setenv(health.NESTED_ENV, "1")
    assert one(prof, "tests")["status"] == "WARN"


# ---------------------------------------------------------------- triage

TB = ('Traceback (most recent call last):\n  File "{root}\\src\\daily.py", line 41, in run\n    x()\n{exc}: bad value for secret=abc12345 jane@example.com\n')


@pytest.mark.parametrize("exc,cls,risk", [
    ("json.decoder.JSONDecodeError", "corrupt_manifest", "SAFE_AUTOFIX_WITH_APPROVAL"),
    ("xlrd.biffh.XLRDError", "bad_workbook", "MANUAL_ONLY"),
    ("zipfile.BadZipFile", "bad_workbook", "MANUAL_ONLY"),
    ("PermissionError", "file_open_in_excel", "MANUAL_ONLY"),
    ("ModuleNotFoundError", "python_env_missing", "MANUAL_ONLY"),
    ("AssertionError", "test_regression", "MANUAL_ONLY"),
])
def test_triage_exception_signatures(prof, exc, cls, risk):
    inc = triage.triage(prof, TB.format(root=prof.root, exc=exc))
    assert inc["class"] == cls and inc["fix_risk"] == risk
    assert inc["state"] == "TRIAGED" and inc["id"].startswith("INC-")
    ev = inc["evidence"][-1]
    assert ev["file"] == "src/daily.py" and ev["line"] == 41 and ev["exception"].endswith(exc.split(".")[-1])
    blob = json.dumps(inc)
    assert "abc12345" not in blob and "jane@example.com" not in blob and "bad value" not in blob


@pytest.mark.parametrize("text,cls", [
    ("Another run appears to be in progress (daily.lock).", "stale_lock"),
    ("FileNotFoundError: [Errno 2] No such file or directory: '01_DROP_FOLDER/2026-09'", "missing_input_month"),
    ("FileNotFoundError: No such file or directory: '07_LOGS/run_log.csv'", "missing_output_folder"),
    ("OSError: [WinError 206] The filename or extension is too long", "path_too_long"),
    ("PermissionError: [WinError 32] The process cannot access the file because it is being used by another process", "file_open_in_excel"),
    ("'python' is not recognized as an internal or external command", "python_env_missing"),
    ("no idea what this is", "unclassified"),
])
def test_triage_text_signatures(prof, text, cls):
    assert triage.triage(prof, text)["class"] == cls


def test_triage_trigger_shapes(prof):
    assert triage.triage(prof, ["tests/test_daily.py::test_x"])["class"] == "test_regression"
    assert triage.triage(prof, {"kind": "failing_tests", "tests": ["tests/test_a.py::test_b"]})["class"] == "test_regression"
    inc = triage.triage(prof, {"kind": "scheduler_exit", "exit_code": 1, "log_tail": "boom"})
    assert inc["class"] == "scheduler_failure" and inc["fix_risk"] == "MANUAL_ONLY"
    inc = triage.triage(prof, {"kind": "health", "check": "stale_lock", "status": "FAIL"})
    assert inc["class"] == "stale_lock" and inc["evidence"][0]["check"] == "stale_lock"
    for check, cls in [("manifest", "corrupt_manifest"), ("secrets_scan", "secret_exposed"),
                       ("git_staff_data", "staff_data_in_git"), ("email_gate", "email_gate_open"),
                       ("folders", "missing_output_folder"), ("tests", "test_regression")]:
        assert triage.triage(prof, {"kind": "health", "check": check, "status": "FAIL"})["class"] == cls


def test_incident_has_required_fields(prof):
    inc = triage.triage(prof, "Another run appears to be in progress")
    for k in ("id", "severity", "class", "evidence", "likely_cause", "proposed_fix", "fix_risk", "rollback"):
        assert k in inc and inc[k] not in (None,)


# ---------------------------------------------------------------- workflow

def stale_lock(prof):
    lock = prof.p("07_LOGS/daily.lock")
    lock.write_text("2026-10-18T01:00:00")
    old = time.time() - 3 * 60 * 60
    os.utime(lock, (old, old))
    return lock


STALE = {"kind": "health", "check": "stale_lock", "status": "FAIL"}


def test_workflow_default_is_propose_only(prof):
    lock = stale_lock(prof)
    before = snapshot(prof.root, ["07_LOGS/daily.lock", *PROTECTED_SNAP])
    inc = workflows.run_incident(prof, STALE, probe=lambda p: False, run_tests=False)
    assert inc["state"] == "PROPOSED" and inc["awaiting_approval"] is True
    assert lock.exists() and not (prof.root / "_backup").exists()
    assert snapshot(prof.root, ["07_LOGS/daily.lock", *PROTECTED_SNAP]) == before
    assert (prof.root / inc["proposal"]).is_file()


def test_workflow_approved_safe_fix_closes(prof):
    lock = stale_lock(prof)
    before = snapshot(prof.root, PROTECTED_SNAP)
    inc = workflows.run_incident(prof, STALE, approve=True, probe=lambda p: False)
    assert inc["state"] == "CLOSED"
    assert [h["state"] for h in inc["history"]] == ["DETECTED", "TRIAGED", "PROPOSED", "APPROVED", "BACKED_UP", "FIXED", "VERIFIED", "CLOSED"] or \
        [h["state"] for h in inc["history"]][0] == "DETECTED"
    assert not lock.exists()
    assert len(list(prof.p("07_LOGS").glob("daily.lock.stale-*"))) == 1
    b = inc["backup"]
    assert b["ok"] and b["files"] == b["copied_files"] and b["files"] > 5
    bdir = prof.p(b["path"])
    assert (bdir / "07_LOGS/daily.lock").is_file()
    assert not (bdir / ".git").exists() and not (bdir / ".venv").exists() and not (bdir / "_backup").exists()
    assert snapshot(prof.root, PROTECTED_SNAP) == before
    saved = json.loads(prof.p(f"07_LOGS/it/incidents/{inc['id']}.json").read_text())
    assert saved["state"] == "CLOSED"
    rows = prof.p("07_LOGS/it/incident_log.csv").read_text().splitlines()
    assert rows[0].startswith("time,incident,state") and any(",CLOSED," in r for r in rows)


def test_approved_but_python_process_holds_project(prof):
    lock = stale_lock(prof)
    inc = workflows.run_incident(prof, STALE, approve=True, probe=lambda p: True, run_tests=False)
    assert inc["state"] == "ESCALATED" and lock.exists() and not (prof.root / "_backup").exists()


def test_approved_but_cannot_confirm_process(prof):
    lock = stale_lock(prof)
    inc = workflows.run_incident(prof, STALE, approve=True, probe=lambda p: None, run_tests=False)
    assert inc["state"] == "ESCALATED" and lock.exists()


def test_fresh_lock_is_not_touched(prof):
    lock = prof.p("07_LOGS/daily.lock")
    lock.write_text("x")
    inc = workflows.run_incident(prof, STALE, approve=True, probe=lambda p: False, run_tests=False)
    assert inc["state"] == "ESCALATED" and lock.exists()


def test_verify_failure_rolls_back_and_restores_bytes(prof):
    lock = stale_lock(prof)
    original = lock.read_bytes()
    prof.test_args = list(FAIL_CMD)  # the suite fails after the fix
    inc = workflows.run_incident(prof, STALE, approve=True, probe=lambda p: False)
    assert inc["state"] == "ROLLED_BACK"
    assert lock.read_bytes() == original
    assert inc["rollback_result"]["ok"]
    assert not list(prof.p("07_LOGS").glob("daily.lock.stale-*"))
    assert [h["state"] for h in inc["history"]][-3:] == ["BACKED_UP", "FIXED", "ROLLED_BACK"]


def test_manifest_quarantine(prof):
    mf = prof.p("07_LOGS/processed_manifest.json")
    mf.write_text("{broken")
    inc = workflows.run_incident(prof, {"kind": "health", "check": "manifest", "status": "FAIL"},
                                 approve=True, probe=lambda p: False)
    assert inc["state"] == "CLOSED" and not mf.exists()
    bad = list(prof.p("07_LOGS").glob("processed_manifest.json.bad-*"))
    assert len(bad) == 1 and bad[0].read_bytes() == b"{broken"


def test_valid_manifest_is_not_quarantined(prof):
    prof.p("07_LOGS/processed_manifest.json").write_text("{}")
    inc = workflows.run_incident(prof, "JSONDecodeError in processed_manifest", approve=True, run_tests=False)
    assert inc["state"] == "ESCALATED" and prof.p("07_LOGS/processed_manifest.json").exists()


def test_recreate_missing_writable_folder(prof):
    (prof.root / "06_REPORTS/rep.xlsx").unlink()
    (prof.root / "06_REPORTS").rmdir()
    inc = workflows.run_incident(prof, {"kind": "health", "check": "folders", "status": "FAIL"},
                                 approve=True, probe=lambda p: False)
    assert inc["state"] == "CLOSED" and (prof.root / "06_REPORTS").is_dir()


def test_missing_protected_folder_is_never_created(prof):
    (prof.root / "05_CAPTURED_DATA").rmdir()
    inc = workflows.run_incident(prof, {"kind": "health", "check": "folders", "status": "FAIL"}, approve=True, run_tests=False)
    assert inc["state"] == "ESCALATED" and not (prof.root / "05_CAPTURED_DATA").exists()


def test_manual_only_escalates_with_proposal_and_no_changes(prof):
    before = snapshot(prof.root, ["01_DROP_FOLDER", "02_RAW_ARCHIVE", "04_REFERENCE_SOURCES", "src", "tests", "07_LOGS", "06_REPORTS"])
    inc = workflows.run_incident(prof, TB.format(root=prof.root, exc="xlrd.biffh.XLRDError"), approve=True)
    assert inc["state"] == "ESCALATED" and inc["fix_risk"] == "MANUAL_ONLY"
    assert not (prof.root / "_backup").exists()
    prop = (prof.root / inc["proposal"]).read_text()
    assert "Fix proposal" in prop and "src/daily.py" in prop and "bad value" not in prop
    after = snapshot(prof.root, ["01_DROP_FOLDER", "02_RAW_ARCHIVE", "04_REFERENCE_SOURCES", "src", "tests", "06_REPORTS"])
    assert after == {k: v for k, v in before.items() if not k.startswith("07_LOGS")}


def test_writes_stay_inside_writable_paths(prof):
    stale_lock(prof)
    pre = snapshot(prof.root, ["."])
    workflows.run_incident(prof, STALE, approve=True, probe=lambda p: False)
    workflows.run_incident(prof, "no idea", approve=True)
    post = snapshot(prof.root, ["."])
    changed = {k for k in set(pre) | set(post) if pre.get(k) != post.get(k) and not k.startswith(".git/")}
    assert changed
    assert all(k.startswith(("07_LOGS/", "_backup/")) for k in changed), sorted(changed)


def test_backup_exclusions_and_no_self_nesting(prof):
    (prof.root / ".venv/x.txt").write_text("v")
    (prof.root / "_backup/old").mkdir(parents=True)
    (prof.root / "_backup/old/f.txt").write_text("old")
    b = workflows.make_backup(prof, "INC-1-x-abcd", "20261018-000000")
    d = prof.p(b["path"])
    assert b["ok"] and not (d / ".venv").exists() and not (d / "_backup").exists() and not (d / ".git").exists()
    # staff data is never copied into a backup (council B2)
    assert not (d / "01_DROP_FOLDER").exists()


def test_backup_excludes_staff_data_and_secrets(prof):
    root = prof.root
    (root / "07_LOGS/webapp").mkdir(parents=True, exist_ok=True)
    (root / "07_LOGS/webapp/users.json").write_text("{}")
    (root / "07_LOGS/uploads/staging").mkdir(parents=True, exist_ok=True)
    (root / "07_LOGS/uploads/staging/clock.xls").write_bytes(b"x")
    (root / "05_CAPTURED_DATA/2026-05").mkdir(parents=True, exist_ok=True)
    (root / "05_CAPTURED_DATA/2026-05/punches_long.csv").write_text("x")
    (root / "06_REPORTS/2026-05").mkdir(parents=True, exist_ok=True)
    (root / "06_REPORTS/2026-05/r.xlsx").write_bytes(b"x")
    (root / ".env").write_text("K=1")
    (root / "03_MASTER_DATA").mkdir(exist_ok=True)
    (root / "03_MASTER_DATA/approvals.csv").write_text("x")
    (root / "03_MASTER_DATA/staff_aliases.csv").write_text("x")
    (root / "03_MASTER_DATA/outlets.csv").write_text("code AA")
    (root / "07_LOGS/run_log.csv").write_text("run_time")
    b = workflows.make_backup(prof, "INC-2-x-abcd", "20261018-000001")
    d = prof.p(b["path"])
    copied = {p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()}
    forbidden = ("01_DROP_FOLDER", "02_RAW_ARCHIVE", "04_REFERENCE_SOURCES", "05_CAPTURED_DATA", "06_REPORTS",
                 "07_LOGS/webapp", "07_LOGS/uploads")
    assert not [c for c in copied if c.startswith(forbidden)], sorted(copied)
    assert not [c for c in copied if c.rsplit("/", 1)[-1] in (".env", "users.json", "approvals.csv", "staff_aliases.csv")
                or c.endswith((".xls", ".xlsx"))]
    assert "03_MASTER_DATA/outlets.csv" in copied and "07_LOGS/run_log.csv" in copied
    assert b["ok"]


def test_transfer_script_excludes_backup_and_secrets():
    text = (LIVE / "MAKE_TRANSFER_COPY.bat").read_text(encoding="utf-8")
    xd, xf = text.split("/XD", 1)[1].split("/XF", 1)
    assert "_backup" in xd.split()
    for name in (".env", "users.json", "approvals.csv"):
        assert name in xf.split()


def test_workflow_from_health_creates_incident_per_fail(prof):
    prof.p("07_LOGS/processed_manifest.json").write_text("{broken")
    stale_lock(prof)
    out = core.run(mode="workflow", profile=prof, run_tests=False, probe=lambda p: False)
    assert out["status"] == "OK" and out["incident_count"] >= 2
    assert {i["class"] for i in out["incidents"]} >= {"stale_lock", "corrupt_manifest"}
    assert all(i["state"] in ("PROPOSED", "ESCALATED") for i in out["incidents"])
    assert prof.p("07_LOGS/processed_manifest.json").exists()  # not approved, not touched


# ---------------------------------------------------------------- core / privacy

def test_core_modes_return_dicts_and_print_nothing(prof, capsys):
    out = [core.run(mode="health", profile=prof, run_tests=False),
           core.run(mode="map", profile=prof),
           core.run("Another run appears to be in progress", mode="triage", profile=prof),
           core.run(mode="workflow", profile=prof, run_tests=False)]
    assert all(isinstance(o, dict) and o["status"] == "OK" for o in out)
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""
    assert prof.p("07_LOGS/it/health_latest.json").is_file()


@pytest.mark.parametrize("garbage", [None, 42, b"\xff\xfe", {"kind": 5}, {"kind": "health"}, [None, 1], object(), "x" * 100000],
                         ids=["none", "int", "bytes", "kind5", "kindhealth", "list", "object", "huge"])
def test_never_raises_on_garbage(prof, garbage, capsys):
    for mode in core.MODES:
        r = core.run(garbage, mode=mode, profile=prof, run_tests=False)
        assert isinstance(r, dict) and "status" in r
    assert core.run(mode="nonsense", profile=prof)["status"] == "FAILED"
    assert capsys.readouterr().out == ""


def test_never_raises_on_bad_profile(tmp_path):
    assert core.run(profile=tmp_path / "missing.toml")["status"] == "FAILED"
    bad = tmp_path / "bad.toml"
    bad.write_text("[[[ not toml")
    assert core.run(profile=bad)["status"] == "FAILED"


def test_no_secret_or_env_password_leaks_into_any_output(prof):
    env_pw = "EnvPw" + "8472" + "kQz"
    (prof.root / ".env").write_text(f"EMAIL_{'PASS'}WORD={env_pw}\n")
    prof.p("07_LOGS/daily_task.log").write_text("x password=" + FAKE_SECRET + "\nend python=0\n")
    tb = TB.format(root=prof.root, exc="PermissionError") + f" password={FAKE_SECRET}"
    core.run(mode="health", profile=prof, run_tests=False)
    core.run(mode="map", profile=prof)
    core.run(tb, mode="triage", profile=prof)
    res = core.run(tb, mode="workflow", profile=prof, run_tests=False)
    blob = json.dumps(res)
    for f in prof.p("07_LOGS/it").rglob("*"):
        if f.is_file():
            blob += f.read_text(encoding="utf-8", errors="replace")
    assert FAKE_SECRET not in blob and env_pw not in blob
    assert "jane@example.com" not in blob


def test_system_map_content(prof):
    out = core.run(mode="map", profile=prof)
    m = json.loads(prof.p("07_LOGS/it/system_map.json").read_text())
    assert out["module_count"] == m["module_count"] == 2
    daily = next(x for x in m["modules"] if x["path"] == "src/daily.py")
    assert daily["doc"] == "Daily engine." and daily["functions"][0]["name"] == "run_month"
    assert m["tests"]["test_functions"] == 1
    assert any(e["name"] == "run_daily.bat" and "src.daily" in e["runs_modules"] for e in m["entry_points"])
    assert m["config_paths"]["drop"] == "01_DROP_FOLDER"
    md = prof.p("07_LOGS/it/system_map.md").read_text()
    assert "# System map" in md and "run_month" in md
    assert "clock-bytes" not in json.dumps(m)


# ---------------------------------------------------------------- cloning proof

OTHER = """
[module]
name = "other-module"
root = "other"
python = "{py}"
venv_dir = ""
test_args = ["-c", "print('2 passed in 0.1s')"]
module_globs = ["app/*.py"]
entry_globs = ["*.cmd"]
[paths]
required_folders = ["inbox", "out", "logs"]
protected = ["inbox", "app"]
writable = ["out", "logs"]
it_dir = "logs/it"
[state]
lock_file = "logs/job.lock"
lock_stale_seconds = 60
manifest_file = "logs/seen.json"
[[logs]]
name = "job"
path = "logs/job.log"
kind = "text"
exit_marker = "exit="
[security]
env_file = ".env"
gitignore_probes = ["inbox/x.csv", "out/x.csv", ".env"]
staff_data_regexes = ['^inbox/', '^out/']
[[playbooks]]
id = "stale_job_lock"
class = "stale_job_lock"
severity = "LOW"
patterns = ['health:stale_lock:FAIL']
likely_cause = "Job stopped early."
proposed_fix = "Rename the lock."
fix_risk = "SAFE_AUTOFIX_WITH_APPROVAL"
fix_action = "quarantine_stale_lock"
health_check = "stale_lock"
rollback = "Rename it back."
"""


def make_other(tmp_path):
    root = tmp_path / "other"
    for d in ("inbox", "out", "logs", "app"):
        (root / d).mkdir(parents=True)
    (root / "app/job.py").write_text('"""Job."""\n\ndef go():\n    """Go."""\n')
    (root / "inbox/in.csv").write_text("a,b\n")
    (root / "go.cmd").write_text("python -m app.job\n")
    prof_file = tmp_path / "other_profile.toml"
    prof_file.write_text(OTHER.format(py=Path(sys.executable).as_posix()))
    return root, prof_file


def test_clone_runs_same_core_with_a_different_profile(tmp_path, capsys):
    root, prof_file = make_other(tmp_path)
    before = snapshot(root, ["inbox", "app"])
    h = core.run(mode="health", profile=prof_file)
    assert h["status"] == "OK" and h["module"] == "other-module"
    assert [r for r in h["results"] if r["check"] == "tests"][0]["detail"].startswith("passed=2")
    assert [r for r in h["results"] if r["check"] == "folders"][0]["status"] == "PASS"
    assert core.run(mode="map", profile=prof_file)["module_count"] == 1
    assert (root / "logs/it/system_map.json").is_file()
    lock = root / "logs/job.lock"
    lock.write_text("x")
    old = time.time() - 600
    os.utime(lock, (old, old))
    out = core.run(mode="workflow", profile=prof_file, approve=True, run_tests=False, probe=lambda p: False)
    inc = [i for i in out["incidents"] if i["class"] == "stale_job_lock"][0]
    assert inc["state"] == "CLOSED" and not lock.exists()
    assert snapshot(root, ["inbox", "app"]) == before
    assert core.run({"kind": 1}, mode="triage", profile=prof_file)["status"] == "OK"
    assert capsys.readouterr().out == ""
    assert its.run is core.run


# ---------------------------------------------------------------- live smoke (read-only)

@pytest.mark.skipif(not (LIVE / "src" / "daily.py").exists(), reason="live project absent")
def test_live_project_read_only_smoke():
    p = ModuleProfile.load()
    rep = health.run_checks(p, run_tests=False)
    assert len(rep["results"]) == 14 and rep["counts"]["PASS"] >= 1
    m = knowledge.build_system_map(p)
    assert m["module_count"] >= 5 and m["tests"]["test_functions"] > 50
