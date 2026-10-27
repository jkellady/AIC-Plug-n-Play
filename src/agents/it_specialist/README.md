# IT and security specialist

Resident IT owner for a module. Propose-first: it never edits source, never touches inputs, never deletes, never commits, pushes or sends anything.

| File | Job |
|---|---|
| `playbooks.toml` | The module profile: paths, protected/writable folders, logs, security rules, failure signatures and playbooks. Only file to change for another module. |
| `knowledge.py` | Profile loader, path guards, scrubbing, and the read-only system map (`07_LOGS/it/system_map.json` and `.md`). |
| `health.py` | 14 read-only checks (PASS/WARN/FAIL), including the test suite and security checks. |
| `triage.py` | Trigger (log tail, failing tests, scheduler exit, health FAIL) to incident. |
| `workflows.py` | DETECTED, TRIAGED, PROPOSED, approval, BACKED_UP, FIXED, VERIFIED, CLOSED; ROLLED_BACK on verify failure; ESCALATED for manual fixes. |
| `core.py` | `run(trigger=None, approve=False, mode="health"\|"map"\|"triage"\|"workflow")`. Returns a dict, prints nothing, never raises. |

```
.venv\Scripts\python.exe -m src.agents.it_specialist.core health --no-tests
.venv\Scripts\python.exe -m src.agents.it_specialist.core map
```

Safe fixes (only with `approve=True`): quarantine a stale `daily.lock`, quarantine a corrupt `processed_manifest.json` (rename to `*.bad-<timestamp>`), recreate a missing `06_REPORTS` or `07_LOGS`. Anything else becomes a written proposal in `07_LOGS/it/proposals/`.

Output goes to `07_LOGS/it/` only (plus a dated `_backup/` copy when a fix is applied). Incident files hold file paths, line numbers, counts and error class names, never data values.

To clone for another module: copy `playbooks.toml`, edit it, and call `run(profile="path/to/profile.toml")`.
