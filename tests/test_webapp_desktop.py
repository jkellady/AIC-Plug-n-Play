"""Desktop icon and launcher for the web app: icon structure and print-only script output."""
import importlib.util
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
POWERSHELL = shutil.which("powershell")
needs_ps = pytest.mark.skipif(POWERSHELL is None, reason="powershell not available")


def _load_make_icon():
    spec = importlib.util.spec_from_file_location("make_icon", SCRIPTS / "make_icon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ps(script, *args):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPTS / script), *args],
        capture_output=True, text=True, timeout=60)


def _check_ico(data):
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, 4)
    sizes = []
    for i in range(count):
        w, h, _c, _r, planes, bpp, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert w == h and planes == 1 and bpp == 32
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert data[offset + length - 12:offset + length - 8] == b"\x00\x00\x00\x00"  # IEND length
        sizes.append(256 if w == 0 else w)
    return sizes


def test_make_icon_structure(tmp_path):
    out = tmp_path / "x" / "icon.ico"
    _load_make_icon().build_ico(out)
    assert _check_ico(out.read_bytes()) == [16, 32, 48, 256]


def test_committed_icon_is_valid():
    assert _check_ico((SCRIPTS / "assets" / "hr_attendance.ico").read_bytes()) == [16, 32, 48, 256]


@needs_ps
def test_launch_app_whatif_with_spaces(tmp_path):
    root = tmp_path / "Client App Root"
    root.mkdir()
    r = _ps("launch_app.ps1", "-WhatIf", "-ProjectRoot", str(root))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "PRINT-ONLY" in out
    assert f'"{root}\\.venv\\Scripts\\python.exe" -m src.webapp.server' in out
    assert "http://127.0.0.1:8765/" in out
    assert f'--user-data-dir="{root}\\07_LOGS\\webapp\\edge-profile"' in out
    assert "--app=http://127.0.0.1:8765/" in out
    assert "server.pid" in out
    assert not (root / "07_LOGS").exists()  # nothing created
    assert "password" not in out.lower()


@needs_ps
def test_launch_app_stop_whatif(tmp_path):
    r = _ps("launch_app.ps1", "-Stop", "-WhatIf", "-ProjectRoot", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "server.pid" in r.stdout and "would stop" in r.stdout


@needs_ps
def test_shortcut_print_only_creates_nothing(tmp_path):
    root = tmp_path / "Client App Root"
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    r = _ps("make_desktop_shortcut.ps1", "-ProjectRoot", str(root), "-DesktopDir", str(desktop))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "PRINT-ONLY" in out
    assert "HR Attendance.lnk" in out
    assert f'-WindowStyle Hidden -ExecutionPolicy Bypass -File "{root}\\scripts\\launch_app.ps1"' in out
    assert "hr_attendance.ico" in out
    assert "password" not in out.lower()
    assert list(desktop.iterdir()) == []


@needs_ps
def test_shortcut_default_desktop_unchanged():
    # Real Desktop: with no switches no .lnk may appear.
    desktop = Path(subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True).stdout.strip())
    before = sorted(p.name for p in desktop.glob("*.lnk")) if desktop.is_dir() else []
    r = _ps("make_desktop_shortcut.ps1")
    after = sorted(p.name for p in desktop.glob("*.lnk")) if desktop.is_dir() else []
    assert r.returncode == 0 and before == after
