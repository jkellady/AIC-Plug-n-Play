"""Trigger-to-fixed workflow for one incident.

States: DETECTED -> TRIAGED -> PROPOSED -> (human approval) -> BACKED_UP ->
FIXED -> VERIFIED -> CLOSED, with ROLLED_BACK when verification fails and
ESCALATED when the playbook is MANUAL_ONLY (or a SAFE fix cannot run safely).

Hard limits, enforced in code and not only by convention:
* A fix is applied only if ``approve=True`` is passed. The default proposes.
* Only whitelisted actions in ``ACTIONS`` exist. Each one only renames or
  creates inside the profile's writable paths. Nothing is ever deleted, no
  source or input file is touched, nothing is committed, pushed or sent.
* Every apply is preceded by a verified dated backup under the backup folder.
"""
from __future__ import annotations

import csv
import datetime
import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path

from . import health
from .knowledge import ModuleProfile, is_writable, now_stamp, rel_to_root, safe_write_text, scrub
from .triage import triage

LOG_HEADER = ["time", "incident", "state", "class", "severity", "fix_risk", "note"]


class FixRefused(Exception):
    pass


# -- persistence -----------------------------------------------------------


def _persist(profile: ModuleProfile, inc: dict) -> None:
    path = profile.it_path / "incidents" / f"{inc['id']}.json"
    safe_write_text(profile, path, json.dumps(inc, indent=2, default=str))


def _log_row(profile: ModuleProfile, inc: dict, note: str) -> None:
    path = profile.it_path / "incident_log.csv"
    if not is_writable(profile, path):
        raise PermissionError("incident log outside writable paths")
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_HEADER)
        w.writerow([datetime.datetime.now().isoformat(timespec="seconds"), inc["id"], inc["state"],
                    inc["class"], inc["severity"], inc["fix_risk"], scrub(note)[:200]])


def advance(profile: ModuleProfile, inc: dict, state: str, note: str = "") -> None:
    inc["state"] = state
    inc["history"].append({"state": state, "time": datetime.datetime.now().isoformat(timespec="seconds"),
                           "note": scrub(note)[:200]})
    _persist(profile, inc)
    _log_row(profile, inc, note)


# -- process probe ---------------------------------------------------------


def python_holds_project(profile: ModuleProfile):
    """True/False whether another python process looks like it is running this project; None if unknown."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
           f"Get-CimInstance Win32_Process -Filter \"Name like 'python%' and ProcessId <> {os.getpid()}\" "
           "| Select-Object -ExpandProperty CommandLine"]
    rc, out, _e = health._run(cmd, profile.root, 30)
    if rc != 0:
        return None
    root = str(profile.root).lower()
    markers = [m.lower() for m in profile.process_markers]
    for line in out.splitlines():
        low = line.lower()
        if root in low or any(m in low for m in markers):
            return True
    return False


# -- whitelisted actions ---------------------------------------------------
# Each action: (profile, apply, probe, stamp) -> {"ok", "reason", "changes"}.
# With apply=False it only checks preconditions and previews the changes.


def _rename_plan(profile, src: Path, suffix_word: str, stamp: str):
    dst = src.with_name(f"{src.name}.{suffix_word}-{stamp}")
    if not (is_writable(profile, src) and is_writable(profile, dst)):
        raise FixRefused("target is outside writable paths")
    return {"op": "rename", "from": rel_to_root(profile, src), "to": rel_to_root(profile, dst)}


def act_quarantine_stale_lock(profile, apply, probe, stamp):
    lock = profile.p(profile.lock_file) if profile.lock_file else None
    if lock is None or not lock.exists():
        return {"ok": False, "reason": "no lock file present", "changes": []}
    import time
    if time.time() - lock.stat().st_mtime <= profile.lock_stale_limit():
        return {"ok": False, "reason": "lock is not stale yet", "changes": []}
    held = probe(profile)
    if held is None:
        return {"ok": False, "reason": "cannot confirm that no python process holds the project", "changes": []}
    if held:
        return {"ok": False, "reason": "a python process appears to be running the project", "changes": []}
    change = _rename_plan(profile, lock, "stale", stamp)
    if apply:
        lock.rename(profile.p(change["to"]))
    return {"ok": True, "reason": "lock is stale and no python process holds the project", "changes": [change]}


def act_quarantine_manifest(profile, apply, probe, stamp):
    mf = profile.p(profile.manifest_file) if profile.manifest_file else None
    if mf is None or not mf.exists():
        return {"ok": False, "reason": "no manifest present", "changes": []}
    try:
        json.loads(mf.read_text(encoding="utf-8"))
        return {"ok": False, "reason": "manifest parses fine; nothing to quarantine", "changes": []}
    except (OSError, ValueError):
        pass
    change = _rename_plan(profile, mf, "bad", stamp)
    if apply:
        mf.rename(profile.p(change["to"]))
    return {"ok": True, "reason": "manifest is not valid JSON", "changes": [change]}


def act_recreate_writable_folders(profile, apply, probe, stamp):
    missing = [f for f in profile.required_folders if not profile.p(f).exists()]
    creatable = [f for f in missing if is_writable(profile, profile.p(f))]
    if not creatable:
        return {"ok": False, "reason": "no missing folder is inside the writable paths", "changes": []}
    changes = [{"op": "mkdir", "path": f} for f in creatable]
    if apply:
        for f in creatable:
            profile.p(f).mkdir(parents=True, exist_ok=True)
    return {"ok": True, "reason": f"{len(creatable)} writable folder(s) missing", "changes": changes}


ACTIONS = {
    "quarantine_stale_lock": act_quarantine_stale_lock,
    "quarantine_manifest": act_quarantine_manifest,
    "recreate_writable_folders": act_recreate_writable_folders,
}


# -- backup and rollback -----------------------------------------------------


def _walk_files(profile: ModuleProfile, base: Path):
    """Files to back up. ``backup_exclude`` entries are: a bare name (a folder
    name at any depth, or a file name), a glob such as ``*.xls*`` (file names),
    or a relative path such as ``07_LOGS/webapp`` (that folder or file, from the root).
    """
    entries = [e.replace("\\", "/").strip("/") for e in profile.backup_exclude]
    entries.append(Path(profile.backup_dir).name)
    names = {e for e in entries if "/" not in e}
    rel_dirs = {e for e in entries if "/" in e}
    for dirpath, dirs, files in os.walk(base):
        here = Path(dirpath)
        rel_here = here.relative_to(base).as_posix()
        dirs[:] = [
            d for d in dirs
            if d not in names and (f"{rel_here}/{d}" if rel_here != "." else d) not in rel_dirs
        ]
        for name in files:
            if any(fnmatch.fnmatch(name, pat) for pat in names):
                continue
            if (f"{rel_here}/{name}" if rel_here != "." else name) in rel_dirs:
                continue
            yield here / name


def make_backup(profile: ModuleProfile, incident_id: str, stamp: str) -> dict:
    dest = profile.p(profile.backup_dir) / f"{stamp}_it_{incident_id.rsplit('-', 1)[-1]}"
    if not is_writable(profile, dest):
        raise FixRefused("backup folder not writable by policy")
    if dest.exists():
        dest = dest.with_name(dest.name + "_b")
    src_count = src_bytes = 0
    for f in _walk_files(profile, profile.root):
        rel = f.relative_to(profile.root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        src_count += 1
        src_bytes += f.stat().st_size
    dst_count = dst_bytes = 0
    for f in dest.rglob("*"):
        if f.is_file():
            dst_count += 1
            dst_bytes += f.stat().st_size
    ok = (src_count == dst_count) and (src_bytes == dst_bytes)
    return {"ok": ok, "path": rel_to_root(profile, dest), "files": src_count, "bytes": src_bytes,
            "copied_files": dst_count}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rollback(profile: ModuleProfile, changes: list, backup_rel: str, stamp: str) -> dict:
    restored = failed = 0
    backup = profile.p(backup_rel) if backup_rel else None
    for ch in reversed(changes):
        try:
            if ch["op"] == "rename":
                orig, moved = profile.p(ch["from"]), profile.p(ch["to"])
                if orig.exists():  # something regenerated it: set it aside, do not overwrite
                    orig.rename(orig.with_name(orig.name + f".rollback-{stamp}"))
                if moved.exists():
                    moved.rename(orig)
                if backup is not None and (backup / ch["from"]).is_file():
                    if not orig.exists() or _sha(orig) != _sha(backup / ch["from"]):
                        shutil.copy2(backup / ch["from"], orig)
                ok = orig.exists() and (backup is None or not (backup / ch["from"]).is_file()
                                        or _sha(orig) == _sha(backup / ch["from"]))
            elif ch["op"] == "mkdir":
                d = profile.p(ch["path"])
                if d.exists() and not any(d.iterdir()):
                    d.rename(d.with_name(d.name + f".rolledback-{stamp}"))
                ok = True
            else:
                ok = False
            restored += 1 if ok else 0
            failed += 0 if ok else 1
        except OSError:
            failed += 1
    return {"restored": restored, "failed": failed, "ok": failed == 0}


# -- proposals -------------------------------------------------------------


def write_proposal(profile: ModuleProfile, inc: dict, extra: str = "") -> str:
    lines = [f"# Fix proposal {inc['id']}", "",
             f"- Class: {inc['class']}  Severity: {inc['severity']}  Fix risk: {inc['fix_risk']}",
             f"- Trigger: {inc['trigger_kind']}", "", "## Evidence (file, line, error class only)", ""]
    for e in inc["evidence"]:
        bits = [f"{k}={v}" for k, v in e.items() if v not in ("", 0, None)]
        lines.append("- " + (", ".join(bits) or "none"))
    lines += ["", "## Likely cause", "", inc["likely_cause"], "", "## Proposed fix", "", inc["proposed_fix"], "",
              "## Rollback", "", inc["rollback"], ""]
    if inc["fix_risk"] == "MANUAL_ONLY":
        lines += ["## Who", "", "Needs the builder or a person. The IT specialist does not edit source, inputs or reference files.", ""]
    if extra:
        lines += ["## Notes", "", extra, ""]
    path = profile.it_path / "proposals" / f"{inc['id']}.md"
    safe_write_text(profile, path, scrub("\n".join(lines)))
    return rel_to_root(profile, path)


# -- verification ------------------------------------------------------------


def verify(profile: ModuleProfile, inc: dict, run_tests: bool) -> dict:
    names = {"tests"}
    if inc.get("health_check"):
        names.add(inc["health_check"])
    report = health.run_checks(profile, only=names, run_tests=run_tests)
    fails = [r["check"] for r in report["results"] if r["status"] == "FAIL"]
    return {"ok": not fails, "failed_checks": fails, "results": report["results"]}


# -- the state machine ---------------------------------------------------------


def run_incident(profile: ModuleProfile, trigger, approve: bool = False, probe=None,
                 run_tests: bool = True, now=None, incident: dict | None = None) -> dict:
    probe = probe or python_holds_project
    stamp = now_stamp(now)
    inc = incident or triage(profile, trigger, now)
    inc["history"] = [{"state": "DETECTED", "time": inc["created"], "note": inc["trigger_kind"]}] + inc["history"]
    advance(profile, inc, "TRIAGED", f"{inc['class']} / {inc['fix_risk']}")

    manual = inc["fix_risk"] != "SAFE_AUTOFIX_WITH_APPROVAL" or inc["fix_action"] not in ACTIONS
    inc["proposal"] = write_proposal(profile, inc)
    advance(profile, inc, "PROPOSED", inc["proposal"])
    if manual:
        inc["fix_risk"] = "MANUAL_ONLY"
        advance(profile, inc, "ESCALATED", "manual fix required; proposal written")
        return inc

    action = ACTIONS[inc["fix_action"]]
    try:
        plan = action(profile, False, probe, stamp)
    except FixRefused as exc:
        plan = {"ok": False, "reason": str(exc), "changes": []}
    inc["plan"] = plan
    if not plan["ok"]:
        advance(profile, inc, "ESCALATED", f"fix precondition failed: {plan['reason']}")
        return inc
    if not approve:
        inc["awaiting_approval"] = True
        advance(profile, inc, "PROPOSED", "awaiting approval; nothing changed")
        return inc

    inc["awaiting_approval"] = False
    inc["history"].append({"state": "APPROVED", "time": datetime.datetime.now().isoformat(timespec="seconds"), "note": "approve=True"})
    try:
        backup = make_backup(profile, inc["id"], stamp)
    except (OSError, FixRefused) as exc:
        advance(profile, inc, "ESCALATED", f"backup failed: {type(exc).__name__}; nothing changed")
        return inc
    inc["backup"] = backup
    if not backup["ok"]:
        advance(profile, inc, "ESCALATED", "backup count check failed; nothing changed")
        return inc
    advance(profile, inc, "BACKED_UP", f"{backup['files']} files")

    try:
        result = action(profile, True, probe, stamp)
    except (OSError, FixRefused) as exc:
        result = {"ok": False, "reason": type(exc).__name__, "changes": plan["changes"]}
    inc["applied"] = result
    if not result["ok"]:
        inc["rollback_result"] = rollback(profile, plan["changes"], backup["path"], stamp)
        advance(profile, inc, "ROLLED_BACK", f"apply failed: {result['reason']}")
        return inc
    advance(profile, inc, "FIXED", f"{len(result['changes'])} change(s)")

    v = verify(profile, inc, run_tests)
    inc["verification"] = {"ok": v["ok"], "failed_checks": v["failed_checks"],
                           "results": [{"check": r["check"], "status": r["status"]} for r in v["results"]]}
    if v["ok"]:
        advance(profile, inc, "VERIFIED", "tests and relevant check ok")
        advance(profile, inc, "CLOSED", "fixed and verified")
    else:
        inc["rollback_result"] = rollback(profile, result["changes"], backup["path"], stamp)
        advance(profile, inc, "ROLLED_BACK", "verification failed: " + ",".join(v["failed_checks"]))
    return inc


def run_from_health(profile: ModuleProfile, approve: bool = False, probe=None, run_tests: bool = True,
                    now=None) -> list:
    """Create one incident per health FAIL and take each through the workflow."""
    report = health.run_checks(profile, run_tests=run_tests)
    incidents = []
    for r in report["results"]:
        if r["status"] == "FAIL":
            incidents.append(run_incident(profile, {"kind": "health", "check": r["check"], "status": "FAIL"},
                                          approve=approve, probe=probe, run_tests=run_tests, now=now))
    return incidents
