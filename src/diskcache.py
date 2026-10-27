"""Per-month disk cache for engine results (day_check_rows + all_flags).

Saves to 07_LOGS/cache/<month>.json after a successful run_month().
Includes an input-file signature so the cache auto-invalidates when
drop-folder contents or master-data files change.

The in-memory cache in queries.py sits in front of this; this layer
survives server restarts.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import src.daily as _daily
from src.dayengine import DayFlag


def _cache_dir() -> Path:
    return Path(_daily.LOGS_DIR) / "cache"


def _input_signature(month: str, drop_dir: Path, master_dir: Path) -> list:
    parts = []
    folder = drop_dir / month
    try:
        for p in sorted(folder.glob("*")):
            if p.is_file():
                st = p.stat()
                parts.append([p.name, st.st_size, st.st_mtime_ns])
    except OSError:
        pass
    for name in ("staff_aliases.csv", "outlets.csv", "rules.toml", "approvals.csv"):
        fp = master_dir / name
        try:
            st = fp.stat()
            parts.append([name, st.st_size, st.st_mtime_ns])
        except OSError:
            parts.append([name, None, None])
    return parts


def _flag_to_dict(f: DayFlag) -> dict:
    date_val = f.date if isinstance(f.date, str) else f.date.isoformat()
    return {
        "severity": f.severity, "flag": f.flag, "person": f.person,
        "home_outlet_tab": f.home_outlet_tab, "date": date_val,
        "details": f.details, "remark": f.remark, "remark_type": f.remark_type,
        "ref": f.ref, "days": f.days, "span": f.span,
        "flag_id": f.flag_id,
    }


def _dict_to_flag(d: dict) -> DayFlag:
    date_val = d["date"]
    if date_val != "Month":
        date_val = datetime.date.fromisoformat(date_val)
    return DayFlag(
        severity=d["severity"], flag=d["flag"], person=d["person"],
        home_outlet_tab=d["home_outlet_tab"], date=date_val,
        details=d["details"], remark=d["remark"], remark_type=d["remark_type"],
        ref=d["ref"], days=d.get("days", 1), span=d.get("span", ""),
    )


def _row_to_dict(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (datetime.date, datetime.datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _dict_to_row(d: dict) -> dict:
    out = dict(d)
    if "date" in out and isinstance(out["date"], str):
        try:
            out["date"] = datetime.date.fromisoformat(out["date"])
        except ValueError:
            pass
    return out


def save(month: str, result: dict, drop_dir: Path, master_dir: Path) -> Path:
    sig = _input_signature(month, drop_dir, master_dir)
    payload = {
        "month": month,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "input_signature": sig,
        "status": result.get("status", ""),
        "missing": result.get("missing", []),
        "staff_total": result.get("staff_total", 0),
        "staff_with_clock_file": result.get("staff_with_clock_file", 0),
        "day_check_rows": [_row_to_dict(r) for r in result.get("day_check_rows", [])],
        "all_flags": [_flag_to_dict(f) for f in result.get("all_flags", [])],
        "hr_skipped": result.get("hr_skipped", []),
    }
    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{month}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load(month: str, drop_dir: Path, master_dir: Path) -> dict | None:
    path = _cache_dir() / f"{month}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    current_sig = _input_signature(month, drop_dir, master_dir)
    if raw.get("input_signature") != current_sig:
        return None
    return {
        "status": raw["status"],
        "month": raw["month"],
        "missing": raw.get("missing", []),
        "staff_total": raw.get("staff_total", 0),
        "staff_with_clock_file": raw.get("staff_with_clock_file", 0),
        "day_check_rows": [_dict_to_row(r) for r in raw.get("day_check_rows", [])],
        "all_flags": [_dict_to_flag(f) for f in raw.get("all_flags", [])],
        "hr_skipped": raw.get("hr_skipped", []),
    }
