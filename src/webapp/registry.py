"""Staff and outlet directory records added or edited in the web app (Admin screens).

Stored in 07_LOGS/webapp/registry.json with created_by/created_at and
edited_by/edited_at on every record. This is a directory only: it never writes
master data (03_MASTER_DATA), the HR workbook or the schedule, and the attendance
check does not read it. Records from master data are shown beside it; editing one
saves an override here and leaves the master file untouched.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
STAFF_STATUS = ("Active", "On leave", "Left", "Check details")
_LOCK = threading.Lock()


class RegistryError(Exception):
    """A refused change. The message is safe to show."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {"staff": dict(data.get("staff") or {}), "outlets": dict(data.get("outlets") or {})}
    except (OSError, ValueError, AttributeError):
        return {"staff": {}, "outlets": {}}


def _save(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _text(v, field: str, limit: int, required: bool = False) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise RegistryError(f"{field} must be text.")
    v = v.strip()
    if required and not v:
        raise RegistryError(f"{field} is required.")
    if len(v) > limit or any(c in v for c in "\r\n\t\x00"):
        raise RegistryError(f"{field} is too long or has a bad character.")
    return v


def merge_staff(master_rows: list[dict], path: Path) -> list[dict]:
    reg = _load(path)["staff"]
    out, seen = [], set()
    for r in master_rows:
        rec = reg.get(r["id"])
        row = {"id": r["id"], "name": r.get("name", ""), "outlet": r.get("outlet", ""),
               "status": r.get("status", ""), "source": "master data",
               "created_by": "", "created_at": "", "edited_by": "", "edited_at": ""}
        if rec:
            row.update({k: rec.get(k, row[k]) for k in ("name", "outlet", "status")})
            row.update(source="master data + edit", edited_by=rec.get("edited_by", ""),
                       edited_at=rec.get("edited_at", ""), created_by=rec.get("created_by", ""),
                       created_at=rec.get("created_at", ""))
        out.append(row)
        seen.add(r["id"])
    for sid, rec in reg.items():
        if sid not in seen:
            out.append({"id": sid, "name": rec.get("name", ""), "outlet": rec.get("outlet", ""),
                        "status": rec.get("status", "Active"), "source": "added in app",
                        "created_by": rec.get("created_by", ""), "created_at": rec.get("created_at", ""),
                        "edited_by": rec.get("edited_by", ""), "edited_at": rec.get("edited_at", "")})
    return out


def merge_outlets(master_rows: list[dict], staff_rows: list[dict], path: Path) -> list[dict]:
    reg = _load(path)["outlets"]
    counts: dict[str, int] = {}
    for s in staff_rows:
        key = s.get("outlet") or "Unassigned"
        counts[key] = counts.get(key, 0) + 1
    names = {r["outlet"] for r in master_rows} | set(reg) | set(counts)
    out = []
    for oid in sorted(names):
        rec = reg.get(oid)
        in_master = any(r["outlet"] == oid for r in master_rows)
        out.append({"id": oid, "outlet": oid, "name": (rec or {}).get("name", ""),
                    "note": (rec or {}).get("note", ""), "staff_count": counts.get(oid, 0),
                    "source": ("master data + edit" if rec and in_master else
                               "added in app" if rec else "master data"),
                    "created_by": (rec or {}).get("created_by", ""),
                    "created_at": (rec or {}).get("created_at", ""),
                    "edited_by": (rec or {}).get("edited_by", ""),
                    "edited_at": (rec or {}).get("edited_at", "")})
    return out


def save_staff(path: Path, sid, fields: dict, who: str, create: bool, existing_ids: set) -> dict:
    sid = _text(sid, "Staff ID", 32, True)
    if not ID_RE.match(sid):
        raise RegistryError("Staff ID may use letters, digits, dot, dash and underscore.")
    name = _text(fields.get("name"), "Name", 80, True)
    outlet = _text(fields.get("outlet"), "Outlet", 40)
    status = fields.get("status") or "Active"
    if status not in STAFF_STATUS:
        raise RegistryError("Status must be one of: " + ", ".join(STAFF_STATUS) + ".")
    with _LOCK:
        data = _load(path)
        exists = sid in existing_ids or sid in data["staff"]
        if create and exists:
            raise RegistryError("That staff ID already exists. Use edit instead.")
        if not create and not exists:
            raise RegistryError("That staff ID was not found.")
        rec = data["staff"].get(sid, {})
        rec.update(name=name, outlet=outlet, status=status)
        if create:
            rec.update(created_by=who, created_at=_now())
        else:
            rec.update(edited_by=who, edited_at=_now())
        data["staff"][sid] = rec
        _save(path, data)
    return {"id": sid}


def save_outlet(path: Path, oid, fields: dict, who: str, create: bool, existing_ids: set) -> dict:
    oid = _text(oid, "Outlet ID", 32, True)
    if not ID_RE.match(oid):
        raise RegistryError("Outlet ID may use letters, digits, dot, dash and underscore.")
    name = _text(fields.get("name"), "Name", 80, True)
    note = _text(fields.get("note"), "Note", 200)
    with _LOCK:
        data = _load(path)
        exists = oid in existing_ids or oid in data["outlets"]
        if create and exists:
            raise RegistryError("That outlet ID already exists. Use edit instead.")
        if not create and not exists:
            raise RegistryError("That outlet ID was not found.")
        rec = data["outlets"].get(oid, {})
        rec.update(name=name, note=note)
        if create:
            rec.update(created_by=who, created_at=_now())
        else:
            rec.update(edited_by=who, edited_at=_now())
        data["outlets"][oid] = rec
        _save(path, data)
    return {"id": oid}
