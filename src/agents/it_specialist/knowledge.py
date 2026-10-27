"""Module profile and read-only system map for the IT specialist.

``ModuleProfile`` is the only place project-specific facts live (loaded from a
TOML file such as ``playbooks.toml``). The shared helpers here (path guards,
scrubbing, safe writes) are used by every other file in the package.

``build_system_map`` inspects the repo with ``ast`` and the filesystem. It never
imports or runs project code and records structure only: names of modules,
functions, folders, and counts. No data values, no file contents.
"""
from __future__ import annotations

import ast
import datetime
import json
import re
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROFILE = Path(__file__).with_name("playbooks.toml")

# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


@dataclass
class ModuleProfile:
    name: str
    root: Path
    python: str = ".venv/Scripts/python.exe"
    venv_dir: str = ".venv"
    test_args: list = field(default_factory=lambda: ["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"])
    test_timeout: int = 900
    config_file: str = "config.toml"
    tests_dir: str = "tests"
    module_globs: list = field(default_factory=lambda: ["src/*.py"])
    entry_globs: list = field(default_factory=lambda: ["*.bat", "*.ps1"])
    doc_dirs: list = field(default_factory=list)
    data_flow: list = field(default_factory=list)
    required_folders: list = field(default_factory=list)
    protected: list = field(default_factory=list)
    writable: list = field(default_factory=lambda: ["07_LOGS"])
    it_dir: str = "07_LOGS/it"
    backup_dir: str = "_backup"
    backup_exclude: list = field(default_factory=lambda: [
        ".venv", ".git", "_backup", "__pycache__",
        "01_DROP_FOLDER", "02_RAW_ARCHIVE", "04_REFERENCE_SOURCES", "05_CAPTURED_DATA", "06_REPORTS",
        "07_LOGS/webapp", "07_LOGS/uploads", "07_LOGS/feedback", "03_MASTER_DATA/staff_aliases.csv",
        "*.xls", "*.xlsx", "*.xlsm", ".env", "users.json", "approvals.csv"])
    engine_files: list = field(default_factory=list)
    lock_file: str = ""
    lock_stale_seconds: int = 7200
    lock_stale_source: str = ""
    manifest_file: str = ""
    manifest_drop_root: str = ""
    process_markers: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    security: dict = field(default_factory=dict)
    playbooks: list = field(default_factory=list)
    monthly: dict = field(default_factory=dict)

    # -- loading ----------------------------------------------------------
    @classmethod
    def load(cls, path=None, root=None) -> "ModuleProfile":
        path = Path(path) if path else DEFAULT_PROFILE
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        mod, paths, state = data.get("module", {}), data.get("paths", {}), data.get("state", {})
        base = Path(root) if root is not None else (path.parent / mod.get("root", ".")).resolve()
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {}
        for src in (mod, paths, state):
            for key, value in src.items():
                if key in known and key != "root":
                    kwargs[key] = value
        return cls(
            name=mod.get("name", "module"), root=Path(base).resolve(),
            logs=data.get("logs", []), security=data.get("security", {}),
            playbooks=data.get("playbooks", []), monthly=data.get("monthly", {}),
            **{k: v for k, v in kwargs.items() if k != "name"},
        )

    # -- path helpers -----------------------------------------------------
    def p(self, rel: str) -> Path:
        return self.root / rel

    @property
    def it_path(self) -> Path:
        return self.p(self.it_dir)

    @property
    def python_path(self) -> Path:
        pp = Path(self.python)
        return pp if pp.is_absolute() else self.root / pp

    def lock_stale_limit(self) -> int:
        """Seconds after which a lock is stale; read from the engine source when the profile points at it."""
        if self.lock_stale_source and ":" in self.lock_stale_source:
            rel, name = self.lock_stale_source.rsplit(":", 1)
            value = _literal_constant(self.p(rel), name)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return int(self.lock_stale_seconds)


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        a, b = _eval_node(node.left), _eval_node(node.right)
        if a is None or b is None:
            return None
        ops = {ast.Add: lambda x, y: x + y, ast.Mult: lambda x, y: x * y, ast.Sub: lambda x, y: x - y}
        fn = ops.get(type(node.op))
        return fn(a, b) if fn else None
    return None


def _literal_constant(path: Path, name: str):
    """Read ``NAME = <number expression>`` from a source file without importing it."""
    try:
        tree = _parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return _eval_node(node.value)
    return None


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

_SECRET_RES = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)(\s*[=:]\s*)[\"']?[^\s\"',;]{4,}"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
]


def scrub(text: str) -> str:
    """Remove credential-looking strings and email addresses from text before it is written."""
    text = str(text)
    text = _SECRET_RES[0].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    for rx in _SECRET_RES[1:]:
        text = rx.sub("[REDACTED]", text)
    return text


def rel_to_root(profile: ModuleProfile, path) -> str:
    try:
        return Path(path).resolve().relative_to(profile.root).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def _under(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


def is_protected(profile: ModuleProfile, path) -> bool:
    return any(_under(Path(path), profile.p(rel)) for rel in profile.protected)


def is_writable(profile: ModuleProfile, path) -> bool:
    """True when ``path`` is inside a writable folder (or the backup folder) and not protected."""
    path = Path(path)
    if is_protected(profile, path):
        return False
    allowed = list(profile.writable) + [profile.backup_dir]
    return any(_under(path, profile.p(rel)) for rel in allowed)


def safe_write_text(profile: ModuleProfile, path, text: str) -> Path:
    path = Path(path)
    if not is_writable(profile, path):
        raise PermissionError(f"write refused outside writable paths: {rel_to_root(profile, path)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def now_stamp(now=None) -> str:
    return (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------
# System map
# --------------------------------------------------------------------------


def _parse(text: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(text)


def _first_line(doc) -> str:
    return (doc or "").strip().splitlines()[0][:160] if doc and doc.strip() else ""


def _inspect_module(profile: ModuleProfile, path: Path) -> dict:
    info = {"path": rel_to_root(profile, path), "doc": "", "functions": [], "classes": [], "imports": []}
    try:
        tree = _parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        info["error"] = "unparseable"
        return info
    info["doc"] = _first_line(ast.get_docstring(tree))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            info["functions"].append({"name": node.name, "doc": _first_line(ast.get_docstring(node)),
                                      "args": len(node.args.args)})
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            info["classes"].append({"name": node.name, "doc": _first_line(ast.get_docstring(node))})
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == profile.root.name.lower():
            info["imports"].append(node.module)
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            info["imports"].append(node.module)
        elif isinstance(node, ast.Import):
            info["imports"] += [a.name for a in node.names if a.name.startswith("src")]
    info["imports"] = sorted(set(info["imports"]))
    return info


def _count_tests(profile: ModuleProfile) -> dict:
    files, total = {}, 0
    tdir = profile.p(profile.tests_dir)
    if tdir.is_dir():
        for f in sorted(tdir.glob("test_*.py")):
            try:
                tree = _parse(f.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
                files[f.name] = -1
                continue
            n = sum(1 for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"))
            files[f.name] = n
            total += n
    return {"files": files, "file_count": len(files), "test_functions": total}


def _entry_points(profile: ModuleProfile) -> list:
    out = []
    seen = set()
    for pattern in profile.entry_globs:
        for f in sorted(profile.root.glob(pattern)):
            if f.name in seen or not f.is_file():
                continue
            seen.add(f.name)
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            out.append({"name": f.name, "runs_modules": sorted(set(re.findall(r"-m\s+([\w.]+)", text)))})
    return out


def _folder_map(profile: ModuleProfile) -> list:
    out = []
    for d in sorted(profile.root.iterdir()):
        if d.is_dir() and d.name not in (".git", ".venv", "__pycache__"):
            try:
                count = sum(1 for x in d.rglob("*") if x.is_file()) if d.name != "_backup" else -1
            except OSError:
                count = -1
            out.append({"folder": d.name, "files": count,
                        "role": "protected" if is_protected(profile, d) else
                        ("writable" if is_writable(profile, d) else "other")})
    return out


def _config_paths(profile: ModuleProfile) -> dict:
    cfg = profile.p(profile.config_file)
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {k: v for k, v in data.get("paths", {}).items() if isinstance(v, str)}


def build_system_map(profile: ModuleProfile) -> dict:
    modules = []
    seen = set()
    for pattern in profile.module_globs:
        for f in sorted(profile.root.glob(pattern)):
            if f.is_file() and f.suffix == ".py" and f not in seen and f.name != "__init__.py":
                seen.add(f)
                modules.append(_inspect_module(profile, f))
    tests = _count_tests(profile)
    return {
        "module": profile.name,
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "modules": modules,
        "module_count": len(modules),
        "config_paths": _config_paths(profile),
        "folders": _folder_map(profile),
        "tests": tests,
        "entry_points": _entry_points(profile),
        "docs": {d: sum(1 for _ in profile.p(d).glob("*")) for d in profile.doc_dirs if profile.p(d).is_dir()},
        "data_flow": list(profile.data_flow),
        "protected": list(profile.protected),
        "writable": list(profile.writable),
        "engine_files": list(profile.engine_files),
        "playbooks": [{"id": pb.get("id"), "class": pb.get("class"), "fix_risk": pb.get("fix_risk")}
                      for pb in profile.playbooks],
    }


def render_markdown(m: dict) -> str:
    lines = [f"# System map: {m['module']}", "", f"Generated {m['generated']}. Structure only, no data values.", ""]
    lines += ["## Data flow", ""] + [f"{i}. {s}" for i, s in enumerate(m["data_flow"], 1)] + [""]
    lines += ["## Folders", "", "| Folder | Files | Role |", "|---|---|---|"]
    lines += [f"| {f['folder']} | {f['files']} | {f['role']} |" for f in m["folders"]] + [""]
    lines += [f"## Modules ({m['module_count']})", ""]
    for mod in m["modules"]:
        lines.append(f"### {mod['path']}")
        if mod["doc"]:
            lines.append(mod["doc"])
        for fn in mod["functions"]:
            lines.append(f"- `{fn['name']}` {fn['doc']}".rstrip())
        if mod["imports"]:
            lines.append(f"- imports: {', '.join(mod['imports'])}")
        lines.append("")
    lines += ["## Entry points", ""]
    lines += [f"- {e['name']} ({', '.join(e['runs_modules']) or 'no python module'})" for e in m["entry_points"]]
    lines += ["", "## Tests", "", f"{m['tests']['test_functions']} test functions in {m['tests']['file_count']} files.", ""]
    lines += ["## Config paths", ""] + [f"- {k} = {v}" for k, v in m["config_paths"].items()] + [""]
    lines += ["## Playbooks", ""] + [f"- {p['id']}: {p['fix_risk']}" for p in m["playbooks"]] + [""]
    return "\n".join(lines)


def write_system_map(profile: ModuleProfile) -> dict:
    m = build_system_map(profile)
    jpath = safe_write_text(profile, profile.it_path / "system_map.json", json.dumps(m, indent=2))
    mpath = safe_write_text(profile, profile.it_path / "system_map.md", render_markdown(m))
    return {"status": "OK", "module_count": m["module_count"], "test_functions": m["tests"]["test_functions"],
            "folders": len(m["folders"]), "entry_points": len(m["entry_points"]),
            "json": rel_to_root(profile, jpath), "markdown": rel_to_root(profile, mpath)}
