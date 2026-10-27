"""Read and edit 03_MASTER_DATA/rules.toml for the web app, through a validated path.

Edits are made as text on one rule block, so comments and the other rules stay
exactly as they are. Before anything is written the candidate file is parsed,
loaded with ``src.rules.load_rules`` (the same check as ``python -m src.rules
check``) and compared with the old file: only the named rule may differ. The old
file is copied to 07_LOGS/webapp/rules_backups first. Who/when goes into the
rule block (edited_by/edited_at), a [[changes]] entry and rules_audit.csv.

Only the server calls this, and only for roles with the ``rules_edit`` permission.
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
import tomllib
from pathlib import Path

from src.rules import RuleValidationError, load_rules

EDITABLE = ("label", "value", "status", "question", "source", "confirmed_on", "note")
STATUSES = ("CONFIRMED", "ASSUMED", "OPEN")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
_LOCK = threading.Lock()


class RuleEditError(Exception):
    """A refused edit. The message is safe to show to the user."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def list_rules(path: Path) -> list[dict]:
    try:
        data = tomllib.loads(_read(path))
    except (OSError, ValueError):
        return []
    rows = []
    for rid, raw in (data.get("rules") or {}).items():
        if not isinstance(raw, dict):
            continue
        rows.append({
            "id": rid, "label": raw.get("label", ""), "value": raw.get("value"),
            "status": raw.get("status", ""), "question": raw.get("question", ""),
            "source": raw.get("source", ""), "confirmed_on": raw.get("confirmed_on", ""),
            "used_in": raw.get("used_in", ""), "note": raw.get("note", ""),
            "options": raw.get("options"),
            "edited_by": raw.get("edited_by", ""), "edited_at": raw.get("edited_at", ""),
            "created_by": raw.get("created_by", ""), "created_at": raw.get("created_at", ""),
        })
    return rows


def _lit(v) -> str:
    """A TOML literal for a JSON-like value."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        if "\n" in v or "\r" in v:
            raise RuleEditError("Text values must be on one line.")
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
        return "[" + ", ".join(_lit(x) for x in v) + "]"
    raise RuleEditError("That value type is not supported.")


def _depth_delta(line: str) -> int:
    """Bracket depth change of one TOML line, ignoring brackets inside strings."""
    d, in_s, esc = 0, False, False
    for ch in line:
        if in_s:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_s = False
        elif ch == '"':
            in_s = True
        elif ch == "#":
            break
        elif ch == "[":
            d += 1
        elif ch == "]":
            d -= 1
    return d


def _block_span(lines: list[str], rid: str) -> tuple[int, int]:
    head = f"[rules.{rid}]"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == head), None)
    if start is None:
        raise RuleEditError("That rule was not found.")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            end = i
            break
    return start, end


def _set_key(lines: list[str], rid: str, key: str, literal: str) -> None:
    s, e = _block_span(lines, rid)
    i = s + 1
    last_key_end = s
    while i < e:
        ln = lines[i]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m:
            j, depth = i, _depth_delta(ln.split("=", 1)[1])
            while depth > 0 and j + 1 < e:
                j += 1
                depth += _depth_delta(lines[j])
            if m.group(1) == key:
                lines[i:j + 1] = [f"{key} = {literal}"]
                return
            last_key_end = j
            i = j + 1
        else:
            i += 1
    lines.insert(last_key_end + 1, f"{key} = {literal}")


def _validate_and_write(path: Path, old_text: str, new_lines: list[str], rid: str,
                        backup_dir: Path, allow_new: bool) -> None:
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    try:
        old = tomllib.loads(old_text)
        new = tomllib.loads(new_text)
    except ValueError:
        raise RuleEditError("The change would break the rules file, so nothing was saved.") from None
    o_rules, n_rules = old.get("rules", {}), new.get("rules", {})
    others_same = all(n_rules.get(k) == v for k, v in o_rules.items() if k != rid)
    ids_ok = set(n_rules) == set(o_rules) | ({rid} if allow_new else set())
    if not (others_same and ids_ok and old.get("meta") == new.get("meta")):
        raise RuleEditError("The change would affect other rules, so nothing was saved.")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    cand = backup_dir / f"candidate_{stamp}.toml"
    cand.write_text(new_text, encoding="utf-8")
    try:
        load_rules(cand)                   # the same validation as `src.rules check`
    except RuleValidationError as exc:
        cand.unlink(missing_ok=True)
        raise RuleEditError("Not saved: " + "; ".join(str(exc).splitlines()[:3])) from None
    except Exception:  # noqa: BLE001
        cand.unlink(missing_ok=True)
        raise RuleEditError("The change would break the rules file, so nothing was saved.") from None
    cand.unlink(missing_ok=True)
    (backup_dir / f"rules_{stamp}.toml").write_text(old_text, encoding="utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)


def _audit(audit_csv: Path, who: str, rid: str, field: str, old, new) -> None:
    audit_csv.parent.mkdir(parents=True, exist_ok=True)
    fresh = not audit_csv.exists()
    with audit_csv.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(["when", "who", "rule", "field", "old", "new"])
        w.writerow([_now(), who, rid, field, json.dumps(old, ensure_ascii=False),
                    json.dumps(new, ensure_ascii=False)])


def _append_change(lines: list[str], rid: str, old_desc: str, new_desc: str, who: str) -> None:
    if lines and lines[-1].strip():
        lines.append("")
    lines += ["[[changes]]", f'date = "{time.strftime("%Y-%m-%d")}"', f"rule = {_lit(rid)}",
              f"old = {_lit(old_desc)}", f"new = {_lit(new_desc)}",
              f"source = {_lit('Web app edit by ' + who + ' at ' + _now())}"]


def _same_kind(old, new) -> bool:
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool)
    if isinstance(old, (int, float)):
        return isinstance(new, (int, float))
    return type(old) is type(new)


def update_rule(path: Path, rid: str, fields: dict, who: str, backup_dir: Path,
                audit_csv: Path) -> dict:
    """Change some of EDITABLE on one existing rule. Raises RuleEditError."""
    path = Path(path)
    if not isinstance(fields, dict) or not fields:
        raise RuleEditError("Nothing to change.")
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise RuleEditError("These fields cannot be edited here: " + ", ".join(sorted(unknown)))
    with _LOCK:
        old_text = _read(path)
        cur = (tomllib.loads(old_text).get("rules") or {}).get(rid)
        if cur is None:
            raise RuleEditError("That rule was not found.")
        lines = old_text.splitlines()
        changed = {}
        for k, v in fields.items():
            if k == "status" and v not in STATUSES:
                raise RuleEditError("Status must be CONFIRMED, ASSUMED or OPEN.")
            if k == "value" and not _same_kind(cur.get("value"), v):
                raise RuleEditError("The value must be the same kind as before (text, number, yes/no or list).")
            if k != "value" and not isinstance(v, str):
                raise RuleEditError(f"{k} must be text.")
            if v == cur.get(k, ""):
                continue
            changed[k] = v
        if not changed:
            raise RuleEditError("Nothing changed.")
        if changed.get("status") == "CONFIRMED" and "confirmed_on" not in changed \
                and not cur.get("confirmed_on"):
            changed["confirmed_on"] = time.strftime("%Y-%m-%d")
        for k, v in changed.items():
            _set_key(lines, rid, k, _lit(v))
        _set_key(lines, rid, "edited_by", _lit(who))
        _set_key(lines, rid, "edited_at", _lit(_now()))
        old_desc = ", ".join(f"{k} {json.dumps(cur.get(k), ensure_ascii=False)}" for k in changed)
        new_desc = ", ".join(f"{k} {json.dumps(v, ensure_ascii=False)}" for k, v in changed.items())
        _append_change(lines, rid, old_desc, new_desc, who)
        _validate_and_write(path, old_text, lines, rid, Path(backup_dir), allow_new=False)
        for k, v in changed.items():
            _audit(Path(audit_csv), who, rid, k, cur.get(k), v)
    return {"id": rid, "changed": sorted(changed)}


def add_rule(path: Path, rid: str, fields: dict, who: str, backup_dir: Path,
             audit_csv: Path) -> dict:
    """Add a new rule block. New rules start as ASSUMED or OPEN unless a source is given."""
    path = Path(path)
    if not isinstance(rid, str) or not ID_RE.match(rid):
        raise RuleEditError("The rule id must be lower-case letters, digits and underscores (3 to 61 characters).")
    label = fields.get("label")
    if not isinstance(label, str) or not label.strip():
        raise RuleEditError("A rule needs a label.")
    status = fields.get("status") or "ASSUMED"
    if status not in STATUSES:
        raise RuleEditError("Status must be CONFIRMED, ASSUMED or OPEN.")
    with _LOCK:
        old_text = _read(path)
        if rid in (tomllib.loads(old_text).get("rules") or {}):
            raise RuleEditError("A rule with that id already exists.")
        block = ["", f"[rules.{rid}]", f"label = {_lit(label.strip())}",
                 f"value = {_lit(fields.get('value', ''))}", f"status = {_lit(status)}",
                 f"question = {_lit(str(fields.get('question') or ''))}",
                 f"source = {_lit(str(fields.get('source') or ''))}",
                 f"confirmed_on = {_lit(time.strftime('%Y-%m-%d') if status == 'CONFIRMED' else '')}",
                 'used_in = "not built yet"']
        if fields.get("note"):
            block.append(f"note = {_lit(str(fields['note']))}")
        block += [f"created_by = {_lit(who)}", f"created_at = {_lit(_now())}"]
        lines = old_text.splitlines()
        at = next((i for i, ln in enumerate(lines) if re.match(r"^# -+ change log", ln)), None)
        if at is None:
            at = next((i for i, ln in enumerate(lines) if ln.strip() == "[[changes]]"), len(lines))
        lines[at:at] = block + [""]
        _append_change(lines, rid, "(new rule)", f"status {status}, value {json.dumps(fields.get('value', ''))}", who)
        _validate_and_write(path, old_text, lines, rid, Path(backup_dir), allow_new=True)
        _audit(Path(audit_csv), who, rid, "(new rule)", None, status)
    return {"id": rid}
