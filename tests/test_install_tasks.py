"""Tests for scripts/install_tasks.ps1. Only the safe print-only mode is ever run."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_tasks.ps1"
PREFIX = "AIC-Attendance-Agent-"

pytestmark = pytest.mark.skipif(
    shutil.which("powershell.exe") is None or shutil.which("schtasks.exe") is None,
    reason="needs Windows PowerShell and schtasks",
)


def _run(*args):
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True, text=True, timeout=120,
    )


def _agent_tasks():
    out = subprocess.run(["schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, timeout=120).stdout
    return [l for l in out.splitlines() if PREFIX in l]


@pytest.fixture(scope="module")
def printed():
    before = _agent_tasks()
    r = _run()
    after = _agent_tasks()
    return r, before, after


def test_exit_zero_and_nothing_registered(printed):
    r, before, after = printed
    assert r.returncode == 0, r.stderr
    assert before == after


def test_names_times_days(printed):
    out = printed[0].stdout
    for n in ("Scan", "Weekly", "Monthly"):
        assert f'/TN "{PREFIX}{n}"' in out
    assert "/SC DAILY /ST 12:00" in out
    assert "/SC WEEKLY /D MON /ST 09:00" in out
    assert "/SC MONTHLY /D 1 /ST 09:00" in out
    assert "src.agents.drop_scanner" in out
    assert "src.agents.weekly_report" in out
    assert "src.agents.monthly_report" in out
    assert "PRINT-ONLY" in out


def test_no_password_and_current_user_only(printed):
    out = printed[0].stdout
    assert "/RP" not in out and "/RU SYSTEM" not in out and "SYSTEM" not in out
    assert "password" not in out.lower().replace("no password stored", "")
    assert "/IT" in out and "/RU" in out


def test_logs_and_workdir(printed):
    out = printed[0].stdout
    assert "task_Scan.log" in out and "task_Weekly.log" in out and "task_Monthly.log" in out
    assert "cd /d" in out and "2>&1" in out


def test_uses_module_invocation_not_import(printed):
    out = printed[0].stdout
    assert "-m src.agents.drop_scanner" in out
    assert "-m src.agents.weekly_report" in out
    assert "-m src.agents.monthly_report" in out
    assert "import run" not in out


def test_project_root_with_spaces_is_quoted(tmp_path):
    root = tmp_path / "My Client App"
    root.mkdir()
    r = _run("-ProjectRoot", str(root), "-ScanTime", "13:30")
    assert r.returncode == 0, r.stderr
    assert f'\\"{root}\\"' in r.stdout
    assert f'\\"{root}\\.venv\\Scripts\\python.exe\\"' in r.stdout
    assert "/ST 13:30" in r.stdout
    assert not (root / "07_LOGS").exists()  # print-only creates nothing


def test_unregister_without_confirm_changes_nothing():
    before = _agent_tasks()
    r = _run("-Unregister")
    assert r.returncode == 0, r.stderr
    assert "/Delete" not in r.stdout or "[print-only]" in r.stdout
    assert _agent_tasks() == before
