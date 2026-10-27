"""S9: launchers must not hard-code C:\\ paths (they find their own folder)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = sorted(list(ROOT.glob("*.bat")) + list(ROOT.glob("*.ps1")) + list((ROOT / "scripts").glob("*.ps1")))
DRIVE_PATH = re.compile(r"\b[A-Za-z]:\\")


def test_launchers_found():
    names = {p.name for p in LAUNCHERS}
    assert {"run_daily.bat", "RUN_NOW.bat", "MAKE_TRANSFER_COPY.bat", "setup_daily_task.ps1"} <= names


def test_no_hard_coded_drive_paths_in_launchers():
    bad = []
    for p in LAUNCHERS:
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            stripped = line.strip().lower()
            if stripped.startswith(("rem ", "#")):
                continue
            if DRIVE_PATH.search(line):
                bad.append(f"{p.name}:{n}")
    assert not bad, bad


def test_bat_launchers_cd_to_their_own_folder():
    for name in ("RUN_NOW.bat", "RUN_MONTH.bat", "RUN_MAY_TRIAL.bat", "RUN_SEPT.bat", "CREATE_FOLDERS.bat"):
        assert 'cd /d "%~dp0"' in (ROOT / name).read_text(encoding="utf-8"), name
    assert "%~dp0" in (ROOT / "run_daily.bat").read_text(encoding="utf-8")
    assert "$PSScriptRoot" in (ROOT / "setup_daily_task.ps1").read_text(encoding="utf-8")
