"""Classify a trigger against the profile's playbooks into an incident record.

A trigger is a crash log tail (str), a failing test list (list of ids or a
``{"kind": "failing_tests", ...}`` dict), a scheduler exit
(``{"kind": "scheduler_exit", "exit_code": n, "log_tail": ...}``) or a health
result (``{"kind": "health", "check": ..., "status": ...}``).

Evidence is file + line + exception class name only. Message text is dropped on
purpose because exception messages can carry data values.
"""
from __future__ import annotations

import datetime
import hashlib
import re

from .knowledge import ModuleProfile, scrub

_TB_FILE = re.compile(r'File "([^"]+)", line (\d+)')
_EXC = re.compile(r"^\s*((?:[A-Za-z_]\w*\.)*[A-Z]\w*(?:Error|Exception|Warning|Exit|Interrupt|File|Exists))", re.M)
_FAILED_ID = re.compile(r"FAILED\s+([\w./\\-]+\.py)(?:::([\w\[\]-]+))?")

UNCLASSIFIED = {
    "id": "unclassified", "class": "unclassified", "severity": "MEDIUM",
    "likely_cause": "No playbook signature matches this trigger.",
    "proposed_fix": "Read the evidence (file, line, exception class) and add a playbook once the cause is known.",
    "fix_risk": "MANUAL_ONLY", "rollback": "Not applicable: no change is made.",
}


def normalise_trigger(trigger) -> dict:
    """Turn any trigger shape into {kind, text, health_check, health_status}. Never raises."""
    try:
        if trigger is None:
            return {"kind": "none", "text": ""}
        if isinstance(trigger, str):
            return {"kind": "crash_log", "text": trigger}
        if isinstance(trigger, (list, tuple, set)):
            items = [str(x) for x in trigger]
            return {"kind": "failing_tests", "text": "\n".join(f"FAILED {i}" if "::" in i or ".py" in i else i for i in items)}
        if isinstance(trigger, dict):
            kind = str(trigger.get("kind", "crash_log"))
            if kind == "health":
                check, status = str(trigger.get("check", "")), str(trigger.get("status", ""))
                return {"kind": "health", "text": f"health:{check}:{status}",
                        "health_check": check, "health_status": status}
            if kind == "scheduler_exit":
                code = trigger.get("exit_code", "")
                return {"kind": kind, "text": f"scheduler_exit code={code}\n{trigger.get('log_tail', '')}"}
            if kind == "failing_tests":
                ids = trigger.get("tests") or []
                return {"kind": kind, "text": "\n".join(f"FAILED {i}" for i in ids) + "\n" + str(trigger.get("output", ""))}
            return {"kind": kind, "text": str(trigger.get("text") or trigger.get("log_tail") or "")}
        return {"kind": "unknown", "text": str(trigger)}
    except Exception:
        return {"kind": "unknown", "text": ""}


def extract_evidence(profile: ModuleProfile, text: str, limit: int = 8) -> list:
    """Return [{file, line, exception}] from a traceback-like text, paths made project-relative."""
    ev, excs = [], _EXC.findall(text)
    root = str(profile.root).replace("\\", "/").lower()
    for m in _TB_FILE.finditer(text):
        path = m.group(1).replace("\\", "/")
        if path.lower().startswith(root):
            path = path[len(root):].lstrip("/")
        elif ":/" in path[:3] or path.startswith("/"):
            path = path.rsplit("/", 1)[-1]
        ev.append({"file": scrub(path), "line": int(m.group(2))})
    for m in _FAILED_ID.finditer(text):
        ev.append({"file": scrub(m.group(1).replace("\\", "/")), "line": 0, "test": scrub(m.group(2) or "")})
    ev = ev[-limit:]
    if excs:
        if ev:
            ev[-1]["exception"] = excs[-1]
        else:
            ev.append({"file": "", "line": 0, "exception": excs[-1]})
    return ev


def match_playbook(profile: ModuleProfile, text: str) -> dict | None:
    for pb in profile.playbooks:
        for pat in pb.get("patterns", []):
            try:
                if re.search(pat, text, re.I | re.M):
                    return pb
            except re.error:
                continue
    return None


def make_incident_id(kind_class: str, text: str, now=None) -> str:
    now = now or datetime.datetime.now()
    digest = hashlib.sha1((kind_class + text + now.isoformat()).encode("utf-8", "replace")).hexdigest()[:4]
    return f"INC-{now.strftime('%Y%m%d-%H%M%S')}-{kind_class}-{digest}"


def triage(profile: ModuleProfile, trigger, now=None) -> dict:
    """Return an incident dict (state TRIAGED). Never raises."""
    try:
        norm = normalise_trigger(trigger)
        text = norm["text"]
        pb = match_playbook(profile, text) or UNCLASSIFIED
        evidence = extract_evidence(profile, text)
        if norm["kind"] == "health":
            evidence = [{"file": "", "line": 0, "check": norm["health_check"], "status": norm["health_status"]}]
        incident = {
            "id": make_incident_id(pb["class"], text, now),
            "state": "TRIAGED",
            "trigger_kind": norm["kind"],
            "severity": pb.get("severity", "MEDIUM"),
            "class": pb["class"],
            "playbook": pb.get("id", pb["class"]),
            "evidence": evidence,
            "likely_cause": pb.get("likely_cause", ""),
            "proposed_fix": pb.get("proposed_fix", ""),
            "fix_risk": pb.get("fix_risk", "MANUAL_ONLY"),
            "fix_action": pb.get("fix_action", ""),
            "health_check": pb.get("health_check", ""),
            "rollback": pb.get("rollback", ""),
            "created": (now or datetime.datetime.now()).isoformat(timespec="seconds"),
            "history": [],
        }
        if incident["fix_risk"] == "SAFE_AUTOFIX_WITH_APPROVAL" and not incident["fix_action"]:
            incident["fix_risk"] = "MANUAL_ONLY"
        return incident
    except Exception as exc:
        return {"id": make_incident_id("triage_error", "", now), "state": "TRIAGED", "trigger_kind": "unknown",
                "severity": "MEDIUM", "class": "triage_error", "playbook": "", "evidence": [],
                "likely_cause": f"triage failed: {type(exc).__name__}", "proposed_fix": "Inspect manually.",
                "fix_risk": "MANUAL_ONLY", "fix_action": "", "health_check": "", "rollback": "",
                "created": datetime.datetime.now().isoformat(timespec="seconds"), "history": []}
