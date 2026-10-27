"""Public entry point of the IT and security specialist.

``run(trigger, approve, mode, profile)`` returns a dict, prints nothing and never
raises. Modes:

* ``health``   read-only checks; writes ``health_latest.json`` under the IT folder
* ``map``      writes ``system_map.json`` and ``system_map.md``
* ``triage``   classify a trigger into an incident record (no files except the incident log)
* ``monthly``  one propose-only check for a YYYY-MM (``month=``; default: last month)
* ``workflow`` trigger-to-fixed state machine; propose-only unless ``approve=True``

Nothing here creates a scheduled task. A scheduler can call ``main`` or ``run``.
"""
from __future__ import annotations

import datetime
import json
import sys
import traceback

from . import health, workflows
from .knowledge import (DEFAULT_PROFILE, ModuleProfile, rel_to_root, safe_write_text, scrub,
                        write_system_map)
from .triage import triage

MODES = ("health", "map", "triage", "workflow", "monthly")


def _error_log(profile, mode: str, exc: BaseException) -> None:
    """Record error class and the last frame's file and line only; never the message."""
    try:
        tb = traceback.extract_tb(exc.__traceback__)
        where = f"{rel_to_root(profile, tb[-1].filename)}:{tb[-1].lineno}" if tb else ""
        path = profile.it_path / "specialist_errors.log"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        line = f"{datetime.datetime.now().isoformat(timespec='seconds')} mode={mode} {type(exc).__name__} {where}\n"
        safe_write_text(profile, path, old + line)
    except Exception:
        pass


def _compact_incident(inc: dict) -> dict:
    keys = ("id", "state", "severity", "class", "fix_risk", "likely_cause", "proposed_fix", "evidence",
            "proposal", "awaiting_approval", "backup", "applied", "verification", "rollback_result", "plan")
    return {k: inc[k] for k in keys if k in inc}


def load_profile(profile=None, root=None) -> ModuleProfile:
    if isinstance(profile, ModuleProfile):
        return profile
    return ModuleProfile.load(profile or DEFAULT_PROFILE, root=root)


def run(trigger=None, approve: bool = False, mode: str = "health", profile=None, root=None,
        run_tests: bool = True, probe=None, month=None) -> dict:
    prof = None
    try:
        prof = load_profile(profile, root)
        if mode not in MODES:
            return {"status": "FAILED", "mode": str(mode), "error": "unknown mode"}
        if mode == "map":
            return {"status": "OK", "mode": mode, "module": prof.name, **write_system_map(prof)}
        if mode == "health":
            report = health.run_checks(prof, run_tests=run_tests)
            payload = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                       "module": prof.name, **report}
            safe_write_text(prof, prof.it_path / "health_latest.json", json.dumps(payload, indent=2))
            return {"status": "OK", "mode": mode, "module": prof.name, "overall": report["overall"],
                    "counts": report["counts"], "results": report["results"],
                    "report": rel_to_root(prof, prof.it_path / "health_latest.json")}
        if mode == "monthly":
            from .monthly import monthly_check
            return monthly_check(prof, month, run_tests=run_tests)
        if mode == "triage":
            inc = triage(prof, trigger)
            return {"status": "OK", "mode": mode, "module": prof.name, "incident": _compact_incident(inc)}
        # workflow
        if trigger is None:
            incs = workflows.run_from_health(prof, approve=approve, probe=probe, run_tests=run_tests)
        else:
            incs = [workflows.run_incident(prof, trigger, approve=approve, probe=probe, run_tests=run_tests)]
        return {"status": "OK", "mode": mode, "module": prof.name, "approved": bool(approve),
                "incident_count": len(incs), "incidents": [_compact_incident(i) for i in incs]}
    except BaseException as exc:  # noqa: BLE001 - the contract is "never raise"
        if prof is not None:
            _error_log(prof, str(mode), exc)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"status": "FAILED", "mode": str(mode), "error": type(exc).__name__}


def main(argv=None) -> int:
    """Command line for a person or a scheduler: ``python -m src.agents.it_specialist.core health|map|workflow [--approve]``."""
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv and argv[0] in MODES else "health"
    result = run(mode=mode, approve="--approve" in argv, run_tests="--no-tests" not in argv)
    summary = {k: v for k, v in result.items() if k != "results"}
    sys.stdout.write(scrub(json.dumps(summary, indent=2, default=str)) + "\n")
    return 0 if result.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
