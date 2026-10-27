"""Read-only health and security checks for a module profile.

Every check returns ``{"check", "status" (PASS|WARN|FAIL), "detail"}``. Details
carry counts, file paths and line numbers only: never file contents, never a
matched secret value. Nothing here writes anywhere; the caller decides what to
record under the profile's IT folder.
"""
from __future__ import annotations

import ast
import csv
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .knowledge import ModuleProfile, _parse, rel_to_root

NESTED_ENV = "IT_SPECIALIST_ACTIVE"
GIT_TIMEOUT = 30


def _res(check: str, status: str, detail: str) -> dict:
    return {"check": check, "status": status, "detail": detail}


# -- subprocess helpers ----------------------------------------------------


def _run(cmd, cwd, timeout, env_extra=None):
    env = dict(os.environ)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "GIT_OPTIONAL_LOCKS": "0"})
    env.update(env_extra or {})
    try:
        cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                            env=env, encoding="utf-8", errors="replace")
        return cp.returncode, cp.stdout or "", cp.stderr or ""
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except (OSError, ValueError):
        return None, "", "unavailable"


def _git(profile: ModuleProfile, *args):
    return _run(["git", *args], profile.root, GIT_TIMEOUT)


def _is_git_repo(profile: ModuleProfile) -> bool:
    rc, out, _ = _git(profile, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def _tracked_files(profile: ModuleProfile):
    rc, out, _ = _git(profile, "ls-files", "-z")
    if rc != 0:
        return None
    return [x for x in out.split("\0") if x]


# -- checks ----------------------------------------------------------------


def check_python_env(profile: ModuleProfile, **_) -> dict:
    py = profile.python_path
    problems = []
    if not py.is_file():
        problems.append("python executable missing")
    if profile.venv_dir and not profile.p(profile.venv_dir).is_dir():
        problems.append("venv folder missing")
    if problems:
        return _res("python_env", "FAIL", "; ".join(problems))
    rc, out, _e = _run([str(py), "--version"], profile.root, 20)
    if rc != 0:
        return _res("python_env", "FAIL", "python does not start")
    return _res("python_env", "PASS", out.strip() or "python starts")


_COUNT_RES = {k: re.compile(rf"(\d+) {k}") for k in ("passed", "failed", "skipped", "error")}


def parse_pytest_summary(text: str) -> dict:
    tail = "\n".join(text.strip().splitlines()[-6:])
    counts = {k: 0 for k in _COUNT_RES}
    for k, rx in _COUNT_RES.items():
        m = rx.search(tail)
        if m:
            counts[k] = int(m.group(1))
    return counts


def check_tests(profile: ModuleProfile, run_tests: bool = True, **_) -> dict:
    if not run_tests:
        return _res("tests", "WARN", "test run skipped by caller")
    if os.environ.get(NESTED_ENV):
        return _res("tests", "WARN", "test run skipped: already inside a specialist run")
    py = profile.python_path
    if not py.is_file():
        return _res("tests", "FAIL", "python executable missing, cannot run tests")
    rc, out, err = _run([str(py), *profile.test_args], profile.root, profile.test_timeout,
                        {NESTED_ENV: "1"})
    if rc is None:
        return _res("tests", "FAIL", f"test run did not finish ({err})")
    c = parse_pytest_summary(out + "\n" + err)
    detail = f"passed={c['passed']} failed={c['failed']} skipped={c['skipped']} errors={c['error']}"
    if rc != 0 or c["failed"] or c["error"]:
        return _res("tests", "FAIL", detail + f" exit={rc}")
    if c["passed"] == 0:
        return _res("tests", "WARN", detail + " (no tests counted)")
    return _res("tests", "PASS", detail)


def check_folders(profile: ModuleProfile, **_) -> dict:
    missing = [f for f in profile.required_folders if not profile.p(f).is_dir()]
    if missing:
        return _res("folders", "FAIL", f"{len(missing)} missing: " + ", ".join(missing))
    return _res("folders", "PASS", f"{len(profile.required_folders)} required folders present")


def check_stale_lock(profile: ModuleProfile, **_) -> dict:
    if not profile.lock_file:
        return _res("stale_lock", "PASS", "no lock file configured")
    lock = profile.p(profile.lock_file)
    if not lock.exists():
        return _res("stale_lock", "PASS", "no lock file present")
    age = time.time() - lock.stat().st_mtime
    limit = profile.lock_stale_limit()
    if age > limit:
        return _res("stale_lock", "FAIL", f"lock is {int(age)}s old, stale after {limit}s")
    return _res("stale_lock", "WARN", f"fresh lock ({int(age)}s old): a run may be in progress")


def check_manifest(profile: ModuleProfile, **_) -> dict:
    if not profile.manifest_file:
        return _res("manifest", "PASS", "no manifest configured")
    mf = profile.p(profile.manifest_file)
    if not mf.exists():
        return _res("manifest", "PASS", "no manifest yet (first run will create it)")
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _res("manifest", "FAIL", "manifest is not valid JSON")
    if not isinstance(data, dict):
        return _res("manifest", "FAIL", "manifest top level is not an object")
    tracked = missing = changed = 0
    for month, entry in data.items():
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, dict):
            return _res("manifest", "FAIL", "manifest entry has no files table")
        for name, sig in files.items():
            tracked += 1
            path = profile.p(profile.manifest_drop_root) / month / name if profile.manifest_drop_root else None
            if path is None or not path.is_file():
                missing += 1
            elif isinstance(sig, dict) and "size" in sig and path.stat().st_size != sig["size"]:
                changed += 1
    if missing or changed:
        return _res("manifest", "WARN",
                    f"{tracked} tracked files, {missing} not on disk, {changed} changed size (next run will re-process)")
    return _res("manifest", "PASS", f"{len(data)} months, {tracked} tracked files match disk")


def check_logs(profile: ModuleProfile, **_) -> dict:
    fails, warns, scanned = [], [], 0
    for log in profile.logs:
        path = profile.p(log["path"])
        if not path.is_file():
            continue
        scanned += 1
        try:
            if log.get("kind") == "csv":
                with open(path, newline="", encoding="utf-8", errors="replace") as f:
                    rows = list(csv.DictReader(f))[-50:]
                col, bad = log.get("status_column", "status"), set(log.get("fail_values", ["FAILED"]))
                hits = [i for i, r in enumerate(rows) if (r.get(col) or "").strip() in bad]
                if hits and hits[-1] == len(rows) - 1:
                    fails.append(f"{log['name']}: latest run FAILED")
                elif hits:
                    warns.append(f"{log['name']}: {len(hits)} FAILED in last {len(rows)} runs")
            else:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                n = int(log.get("tail_lines", 400))
                base = max(0, len(lines) - n)
                tail = lines[base:]
                tb = [base + i + 1 for i, ln in enumerate(tail) if "Traceback (most recent call last)" in ln]
                marker = log.get("exit_marker")
                last_exit = None
                if marker:
                    for ln in tail:
                        m = re.search(re.escape(marker) + r"(\d+)", ln)
                        if m:
                            last_exit = int(m.group(1))
                if last_exit not in (None, 0):
                    fails.append(f"{log['name']}: last run exited {last_exit}")
                elif tb:
                    warns.append(f"{log['name']}: {len(tb)} traceback(s), last at line {tb[-1]}")
        except OSError:
            warns.append(f"{log['name']}: unreadable")
    if fails:
        return _res("logs", "FAIL", "; ".join(fails + warns))
    if warns:
        return _res("logs", "WARN", "; ".join(warns))
    return _res("logs", "PASS", f"{scanned} log files scanned, no failures")


# -- security ----------------------------------------------------------------

_SECRET_PATTERNS = [
    ("credential_assignment",
     re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\s*[=:]\s*[\"']?(?!\s)[^\s\"',;)\]]{4,}")),
    ("app_password",
     re.compile(r"(?i)(pass|pwd|smtp|app)[^\n]{0,40}\b[a-z]{4}( [a-z]{4}){3}\b")),
    ("long_token", re.compile(r"\b(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*[a-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}\b")),
]
# A "value" that is clearly code, not a literal secret.
_CODE_VALUE = re.compile(r"(?i)^(os\.|self\.|env|getenv|config|cfg|none|null|true|false|args|kwargs|\{|\(|\[|<|%|\$)")


def _scan_file(path: Path, max_bytes: int):
    """Yield (line_number, pattern_name). Never yields the matched text."""
    try:
        if path.stat().st_size > max_bytes:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in _SECRET_PATTERNS:
            m = rx.search(line)
            if not m:
                continue
            if name == "long_token":
                tail = line[m.end():m.end() + 6]
                if re.match(r"\.\w{2,5}\b", tail) or m.group(0).count("_") >= 3:
                    continue  # a file name, not a token
            if name == "credential_assignment":
                value = line[m.end(1):].lstrip(" \t=:\"'")
                if _CODE_VALUE.match(value) or "REDACTED" in value:
                    continue
            yield n, name
            break


def check_secrets_scan(profile: ModuleProfile, **_) -> dict:
    sec = profile.security
    exts = set(sec.get("secret_source_exts", [".py", ".toml", ".csv", ".log", ".txt", ".md"]))
    max_bytes = int(sec.get("secret_max_bytes", 2_000_000))
    env_name = sec.get("env_file", ".env")
    log_hits, src_hits = [], []
    tracked = _tracked_files(profile)
    if tracked is not None:
        for rel in tracked:
            p = profile.p(rel)
            if p.suffix.lower() in exts and p.name != Path(env_name).name and p.is_file():
                src_hits += [(rel, n, k) for n, k in _scan_file(p, max_bytes)]
    else:
        for folder in ("src", profile.tests_dir):
            for p in profile.p(folder).rglob("*") if profile.p(folder).is_dir() else []:
                if p.is_file() and p.suffix.lower() in exts:
                    src_hits += [(rel_to_root(profile, p), n, k) for n, k in _scan_file(p, max_bytes)]
    for rel in sec.get("secret_log_roots", ["07_LOGS"]):
        base = profile.p(rel)
        for p in base.rglob("*") if base.is_dir() else []:
            if p.is_file() and p.suffix.lower() in exts and p.name != Path(env_name).name:
                log_hits += [(rel_to_root(profile, p), n, k) for n, k in _scan_file(p, max_bytes)]
    if log_hits:
        where = "; ".join(f"{f}:{n} ({k})" for f, n, k in log_hits[:10])
        return _res("secrets_scan", "FAIL", f"{len(log_hits)} credential-like hit(s) in logs/reports: {where}")
    if src_hits:
        where = "; ".join(f"{f}:{n} ({k})" for f, n, k in src_hits[:10])
        return _res("secrets_scan", "WARN", f"{len(src_hits)} credential-like hit(s) in tracked source: {where}")
    return _res("secrets_scan", "PASS", "no credential-looking strings in tracked source or logs")


def check_env_tracking(profile: ModuleProfile, **_) -> dict:
    env_name = profile.security.get("env_file", ".env")
    problems = []
    tracked = _tracked_files(profile)
    if tracked is None:
        git_note = "git unavailable"
    else:
        git_note = ""
        if any(Path(t).name == Path(env_name).name for t in tracked):
            problems.append("env file is tracked by git")
    outputs = list(profile.writable) + [profile.it_dir]
    for rel in outputs:
        base = profile.p(rel)
        if base.is_dir() and any(True for _ in base.rglob(Path(env_name).name + "*")):
            problems.append(f"env-like file under {rel}")
    if problems:
        return _res("env_tracking", "FAIL", "; ".join(problems))
    if git_note:
        return _res("env_tracking", "WARN", git_note + "; no env file found in output folders")
    return _res("env_tracking", "PASS", "env file not tracked and not under any report or log path")


def check_gitignore_coverage(profile: ModuleProfile, **_) -> dict:
    if not _is_git_repo(profile):
        return _res("gitignore_coverage", "WARN", "not a git repository")
    probes = profile.security.get("gitignore_probes", [])
    uncovered = []
    for probe in probes:
        rc, _o, _e = _git(profile, "check-ignore", "-q", "--no-index", probe)
        if rc == 1:
            uncovered.append(probe)
        elif rc not in (0, 1):
            return _res("gitignore_coverage", "WARN", "git check-ignore failed")
    if uncovered:
        return _res("gitignore_coverage", "FAIL", f"{len(uncovered)} path(s) not ignored: " + ", ".join(uncovered))
    return _res("gitignore_coverage", "PASS", f"{len(probes)} staff-data probe paths are ignored")


def _staff_matches(profile: ModuleProfile, paths):
    rxs = [re.compile(r) for r in profile.security.get("staff_data_regexes", [])]
    return [p for p in paths if any(r.search(p) for r in rxs)]


def check_git_staff_data(profile: ModuleProfile, **_) -> dict:
    tracked = _tracked_files(profile)
    if tracked is None:
        return _res("git_staff_data", "WARN", "not a git repository or git unavailable")
    rc, out, _e = _git(profile, "diff", "--cached", "--name-only", "-z")
    staged = [x for x in out.split("\0") if x] if rc == 0 else []
    bad_t, bad_s = _staff_matches(profile, tracked), _staff_matches(profile, staged)
    if bad_t or bad_s:
        sample = ", ".join(sorted(set(bad_t + bad_s))[:5])
        return _res("git_staff_data", "FAIL", f"{len(bad_t)} tracked and {len(bad_s)} staged staff-data file(s): {sample}")
    return _res("git_staff_data", "PASS", f"{len(tracked)} tracked files, none are staff data; nothing staged")


def check_backup_untracked(profile: ModuleProfile, **_) -> dict:
    tracked = _tracked_files(profile)
    if tracked is None:
        return _res("backup_untracked", "WARN", "not a git repository or git unavailable")
    rx = re.compile(profile.security.get("backup_regex", "^" + re.escape(profile.backup_dir) + "/"))
    n = sum(1 for t in tracked if rx.search(t))
    if n:
        return _res("backup_untracked", "FAIL", f"{n} backup file(s) are tracked by git")
    return _res("backup_untracked", "PASS", "backup copies are not tracked")


def check_email_gate(profile: ModuleProfile, **_) -> dict:
    sec = profile.security
    rel = sec.get("email_gate_file")
    if not rel:
        return _res("email_gate", "PASS", "no email gate configured")
    path = profile.p(rel)
    if not path.is_file():
        return _res("email_gate", "PASS", "email sender not present")
    try:
        tree = _parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return _res("email_gate", "WARN", "email sender unparseable")
    fn_name, arg = sec.get("email_gate_function", "send_report"), sec.get("email_gate_arg", "send_enabled")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            a = node.args
            names = [x.arg for x in a.args + a.kwonlyargs]
            defaults = [None] * (len(a.args) - len(a.defaults)) + list(a.defaults) + list(a.kw_defaults)
            for name, d in zip(names, defaults):
                if name == arg:
                    if isinstance(d, ast.Constant) and d.value is False:
                        break
                    return _res("email_gate", "FAIL", f"{arg} does not default to False in {rel}")
            else:
                return _res("email_gate", "WARN", f"{arg} argument not found in {fn_name}")
            break
    else:
        return _res("email_gate", "WARN", f"{fn_name} not found in {rel}")
    forced = []
    for p in list(profile.p("src").rglob("*.py")) + list(profile.root.glob("*.bat")) + list(profile.root.glob("*.ps1")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if p.suffix == ".py":
                if "tests" in p.parts:
                    continue
                for node in ast.walk(_parse(text)):
                    if isinstance(node, ast.Call):
                        for kw in node.keywords:
                            if kw.arg == arg and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                forced.append(f"{rel_to_root(profile, p)}:{node.lineno}")
            else:
                for n, line in enumerate(text.splitlines(), 1):
                    if re.search(rf"{re.escape(arg)}\s*=\s*True", line):
                        forced.append(f"{rel_to_root(profile, p)}:{n}")
        except (OSError, SyntaxError, ValueError):
            pass
    if forced:
        return _res("email_gate", "FAIL", f"{arg}=True forced at " + ", ".join(forced[:5]))
    return _res("email_gate", "PASS", f"{arg} defaults to False and is not forced on anywhere")


def check_scheduled_tasks(profile: ModuleProfile, **_) -> dict:
    prefixes = profile.security.get("scheduler_prefixes", [])
    rc, out, _e = _run(["schtasks", "/Query", "/FO", "CSV", "/NH"], profile.root, 30)
    if rc != 0:
        return _res("scheduled_tasks", "WARN", "scheduler could not be queried")
    names = [row[0].lstrip("\\") for row in csv.reader(out.splitlines()) if row]
    found = sorted({n for n in names if any(n.startswith(p) for p in prefixes)})
    if found:
        return _res("scheduled_tasks", "PASS", f"{len(found)} matching task(s): " + ", ".join(found))
    return _res("scheduled_tasks", "WARN", "no matching scheduled task registered")


def check_permissions(profile: ModuleProfile, **_) -> dict:
    notes = []
    base = profile.it_path if profile.it_path.exists() else profile.it_path.parent
    if base.exists() and not os.access(base, os.W_OK):
        notes.append("IT folder not writable")
    unreadable = [f for f in profile.protected if profile.p(f).exists() and not os.access(profile.p(f), os.R_OK)]
    if unreadable:
        notes.append(f"{len(unreadable)} protected path(s) unreadable")
    env = profile.p(profile.security.get("env_file", ".env"))
    if env.is_file() and os.name == "nt":
        rc, out, _e = _run(["icacls", str(env)], profile.root, 15)
        if rc == 0 and re.search(r"(?i)\b(Everyone|BUILTIN\\Users|Authenticated Users):", out):
            notes.append("env file readable by broad groups")
    if notes:
        return _res("permissions", "WARN", "; ".join(notes))
    return _res("permissions", "PASS", "permissions look sane")


CHECKS = [
    ("python_env", check_python_env), ("tests", check_tests), ("folders", check_folders),
    ("stale_lock", check_stale_lock), ("manifest", check_manifest), ("logs", check_logs),
    ("secrets_scan", check_secrets_scan), ("env_tracking", check_env_tracking),
    ("gitignore_coverage", check_gitignore_coverage), ("git_staff_data", check_git_staff_data),
    ("backup_untracked", check_backup_untracked), ("email_gate", check_email_gate),
    ("scheduled_tasks", check_scheduled_tasks), ("permissions", check_permissions),
]


def run_checks(profile: ModuleProfile, only=None, run_tests: bool = True) -> dict:
    results = []
    for name, fn in CHECKS:
        if only is not None and name not in only:
            continue
        try:
            results.append(fn(profile, run_tests=run_tests))
        except Exception as exc:  # a check must never take the run down
            results.append(_res(name, "FAIL", f"check crashed: {type(exc).__name__}"))
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    overall = "FAIL" if counts["FAIL"] else ("WARN" if counts["WARN"] else "PASS")
    return {"overall": overall, "counts": counts, "results": results}
