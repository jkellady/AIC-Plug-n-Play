"""Local dashboard logic (B-dash).

All behaviour lives here as plain functions so it is testable without
streamlit. ``app.py`` is a thin UI over these. Every function returns a dict
(or list of dicts) and prints nothing.

Safety:
* Uploads go only to ``07_LOGS/uploads/<staging>/``, never to 01_DROP_FOLDER.
* Archive and log views list/tail files; they never edit or delete.
* Recipients is read-only counts. Email sending is OFF.
* The launcher command binds to 127.0.0.1 only.

Dependency to add later (separately approved): ``streamlit==1.39.0``.
"""
from __future__ import annotations

import csv
import datetime
import importlib
import re
from pathlib import Path

import src.daily as daily

HOST = "127.0.0.1"
STREAMLIT_VERSION = "1.39.0"
UPLOADS_SUBDIR = "uploads"
DEFAULT_STAGING = "staging"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_UPLOAD_EXTS = {".xls", ".xlsx", ".csv", ".pdf", ".txt"}
LOG_FILES = ["weekly_log.csv", "monthly_log.csv", "scan_log.csv", "email_log.csv",
             "reply_log.csv", "run_log.csv"]
REPORT_EXTS = {".xlsx", ".html", ".txt", ".json", ".pdf"}
SCOPES = {
    "weekly": "src.agents.weekly_report",
    "monthly": "src.agents.monthly_report",
    "scan": "src.agents.drop_scanner",
}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ \-]{0,127}$")
_STAGING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


def launch_command() -> list[str]:
    """Command line to start the dashboard. Never executed here."""
    return ["streamlit", "run", "src/agents/dashboard/app.py",
            "--server.address", HOST, "--server.headless", "true"]


def generate(scope: str, **kwargs) -> dict:
    """Call the matching agent's run(); return a plain dict, never raise."""
    module_name = SCOPES.get(scope)
    if module_name is None:
        return {"status": "UNKNOWN_SCOPE", "scope": scope, "available": False,
                "detail": f"Scope must be one of {sorted(SCOPES)}."}
    try:
        module = importlib.import_module(module_name)
        run = getattr(module, "run")
    except (ImportError, AttributeError):
        return {"status": "NOT_AVAILABLE", "scope": scope, "available": False,
                "detail": f"The {scope} agent is not available yet."}
    try:
        result = run(**kwargs)
    except Exception as exc:  # noqa: BLE001 - the UI must show an error, not crash
        return {"status": "FAILED", "scope": scope, "available": True,
                "detail": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict):
        return {"scope": scope, "available": True, **result}
    return {"status": "OK", "scope": scope, "available": True, "result": result}


def list_archive() -> list[dict]:
    """Report files under 06_REPORTS: name, size, modified. No contents."""
    root = Path(daily.REPORTS_DIR)
    if not root.is_dir():
        return []
    rows = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in REPORT_EXTS:
            stat = path.stat()
            rows.append({
                "name": path.name,
                "folder": path.parent.relative_to(root).as_posix(),
                "size_bytes": stat.st_size,
                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
    rows.sort(key=lambda r: r["modified"], reverse=True)
    return rows


def save_upload(filename: str, data: bytes, staging: str = DEFAULT_STAGING) -> dict:
    """Save bytes into 07_LOGS/uploads/<staging>/<filename>. Refuses path tricks."""
    if not isinstance(filename, str) or not _NAME_RE.match(filename) or ".." in filename:
        return {"status": "REFUSED", "detail": "Unsafe file name."}
    if Path(filename).suffix.lower() not in ALLOWED_UPLOAD_EXTS:
        return {"status": "REFUSED", "detail": "File type not allowed."}
    if not isinstance(staging, str) or not _STAGING_RE.match(staging):
        return {"status": "REFUSED", "detail": "Unsafe staging folder name."}
    if len(data) > MAX_UPLOAD_BYTES:
        return {"status": "REFUSED", "detail": "File too large."}
    base = (Path(daily.LOGS_DIR) / UPLOADS_SUBDIR).resolve()
    target_dir = (base / staging).resolve()
    target = (target_dir / filename).resolve()
    if base not in target.parents or target.parent != target_dir:
        return {"status": "REFUSED", "detail": "Path outside the uploads folder."}
    if target.exists():
        return {"status": "REFUSED", "detail": "A file with that name already exists."}
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"status": "OK", "saved": target.name, "staging": staging, "size_bytes": len(data)}


def read_log(name: str, limit: int = 50) -> dict:
    """Tail one known log CSV from 07_LOGS (last ``limit`` data rows)."""
    if name not in LOG_FILES:
        return {"status": "REFUSED", "name": name, "rows": [], "detail": "Unknown log."}
    path = Path(daily.LOGS_DIR) / name
    if not path.is_file():
        return {"status": "MISSING", "name": name, "rows": []}
    limit = max(1, min(int(limit), 1000))
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.reader(fh))
    header, body = (rows[0], rows[1:]) if rows else ([], [])
    return {"status": "OK", "name": name, "header": header, "rows": body[-limit:],
            "total_rows": len(body)}


def read_all_logs(limit: int = 50) -> dict:
    return {name: read_log(name, limit) for name in LOG_FILES}


def get_recipients() -> dict:
    """Read-only recipient counts. Lists are unset and email is OFF."""
    counts = {"weekly": 0, "monthly": 0}
    try:
        mod = importlib.import_module("src.agents.email_sender")
        reader = getattr(mod, "recipient_counts", None)
        if callable(reader):
            got = reader()
            if isinstance(got, dict):
                counts.update({k: v for k, v in got.items() if isinstance(v, int)})
    except Exception:  # noqa: BLE001 - counts only, never fail the UI
        pass
    return {"counts": counts, "email_sending": "OFF",
            "note": "Email sending is OFF. Recipient lists are unset. Read-only."}
