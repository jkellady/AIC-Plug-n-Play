"""Authentication for the local web app: scrypt users, sessions, CSRF, lockout.

Stdlib only. Users live ONLY in 07_LOGS/webapp/users.json as salted scrypt
hashes. There are no default accounts. Nothing here prints or logs secrets.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

ROLES = ("master", "ceo", "admin", "hrm", "viewer")
ROLE_LABELS = {"master": "Master Admin", "ceo": "CEO", "admin": "Admin", "hrm": "HRM", "viewer": "Viewer"}
MASTER_USERNAME = "master000"
# Who may create, reset or delete accounts of which role (least privilege).
# The master account is never in any list: nobody manages it but itself.
ASSIGNABLE = {
    "master": ("ceo", "admin", "hrm", "viewer"),
    "ceo": ("admin", "hrm", "viewer"),
    "hrm": ("admin", "viewer"),
}
# One permission table for the whole app. The server checks it on every call.
PERMS = {
    "overview": {"master", "ceo", "admin", "hrm", "viewer"},
    "reports_list": {"master", "ceo", "admin", "hrm", "viewer"},
    "report_review": {"master", "ceo", "admin", "hrm", "viewer"},   # Review_*.html (names hidden)
    "report_workbook": {"master", "ceo", "admin", "hrm"},        # .xlsx, per-person flags
    "exceptions": {"master", "ceo", "admin", "hrm"},
    "staff_view": {"master", "ceo", "admin"},
    "staff_edit": {"master", "admin"},
    "outlets_view": {"master", "ceo", "admin"},
    "outlets_edit": {"master", "admin"},
    "scan": {"master", "admin"},
    "health": {"master", "admin"},
    "logs": {"master", "admin"},
    "rules_view": {"master", "ceo", "admin", "hrm", "viewer"},
    "rules_edit": {"master", "ceo"},
    "users_manage": {"master", "ceo", "hrm"},
    "users_security": {"master"},         # read-only security view (no hashes, ever)
    "lan_view": {"master", "ceo", "admin"},
    "lan_set": {"master"},
    "help": {"master", "ceo", "admin", "hrm", "viewer"},
    "open_folder": {"master", "admin"},   # opens Explorer on the server PC itself
    "it_diagnose": {"master", "admin"},
    "it_repair": {"master", "admin"},
}


def can(role, perm: str) -> bool:
    return role in PERMS.get(perm, ())


def can_manage(actor_role, target_role) -> bool:
    return target_role in ASSIGNABLE.get(actor_role, ())
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
IDLE_TIMEOUT = 30 * 60
ABSOLUTE_TIMEOUT = 12 * 60 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 5 * 60
MAX_USERNAME_LEN = 64
MAX_PASSWORD_LEN = 256
MIN_PASSWORD_LEN = 10


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          dklen=32, maxmem=SCRYPT_MAXMEM)


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R,
                  p: int = SCRYPT_P) -> dict:
    """Return a storable record (salt and hash as hex). Never the password."""
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "hash": _scrypt(password, salt, n, r, p).hex(),
            "n": n, "r": r, "p": p}


def verify_password(password: str, record: dict) -> bool:
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
        got = _scrypt(password, salt, int(record["n"]), int(record["r"]), int(record["p"]))
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


# A dummy record so unknown usernames cost the same time as known ones.
_DUMMY = hash_password("not-a-real-password")


def users_path(logs_dir: Path | str) -> Path:
    return Path(logs_dir) / "webapp" / "users.json"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _normalise(users: dict) -> dict:
    """Migration in memory: the master account is master000 and only master000.
    Older files stored it as 'admin'; any other 'master' is demoted to admin."""
    for name, rec in users.items():
        if not isinstance(rec, dict):
            continue
        if name == MASTER_USERNAME:
            rec["role"] = "master"
        elif rec.get("role") == "master":
            rec["role"] = "admin"
    return users


def load_users(path: Path | str) -> dict:
    """Return {username: record}. Missing or unreadable file -> {}."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        users = data.get("users", {})
        return _normalise(users) if isinstance(users, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def audit(users_file: Path, who: str, action: str, target: str = "", detail: str = "") -> None:
    """Append who/when/what to user_audit.csv. Never any password, hash or salt."""
    try:
        f = Path(users_file).parent / "user_audit.csv"
        fresh = not f.exists()
        with f.open("a", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if fresh:
                w.writerow(["when", "who", "action", "target", "detail"])
            w.writerow([_now_iso(), who, action, target, detail])
    except OSError:
        pass


def _write_file(path: Path, users: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save_user(path: Path | str, username: str, role: str, password: str,
              must_change: bool = False) -> None:
    """Add or replace one user. Used by scripts/set_user_password.py (a human types
    the password there). The master role belongs to MASTER_USERNAME only."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    username = username.strip().lower()
    if not username or len(username) > MAX_USERNAME_LEN:
        raise ValueError("bad username")
    if username == MASTER_USERNAME:
        role = "master"
    elif role == "master":
        raise ValueError("the master role belongs to one account only")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    users = load_users(path)
    old = users.get(username, {})
    rec = hash_password(password)
    rec["role"] = role
    for k in ("display_name", "created_by", "created_at", "last_login"):
        if k in old:
            rec[k] = old[k]
    rec.setdefault("created_at", _now_iso())
    rec.setdefault("created_by", "set_user_password.py")
    rec["password_changed_at"] = _now_iso()
    if must_change:
        rec["must_change"] = True
    users[username] = rec
    _write_file(path, users)


class AuthManager:
    """Login, lockout and server-side sessions. Thread safe."""

    def __init__(self, users_file: Path | str, clock=time.time):
        self.users_file = Path(users_file)
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._failures: dict[str, list] = {}   # username -> [count, locked_until]

    def has_users(self) -> bool:
        return bool(load_users(self.users_file))

    # -- login ---------------------------------------------------------
    def login(self, username, password) -> tuple[str, object]:
        """Return (status, session_token_or_None).

        status: ok | no_users | bad | locked | invalid
        """
        if not isinstance(username, str) or not isinstance(password, str):
            return "invalid", None
        if not username or len(username) > MAX_USERNAME_LEN or len(password) > MAX_PASSWORD_LEN:
            return "invalid", None
        key = username.strip().lower()
        users = load_users(self.users_file)
        if not users:
            return "no_users", None
        now = self._clock()
        with self._lock:
            fail = self._failures.get(key)
            if fail and fail[1] > now:
                return "locked", None
            if fail and fail[1] and fail[1] <= now:
                self._failures.pop(key, None)
        rec = users.get(key)
        ok = verify_password(password, rec if rec else _DUMMY)
        if not (ok and rec and rec.get("role") in ROLES):
            with self._lock:
                fail = self._failures.setdefault(key, [0, 0.0])
                fail[0] += 1
                if fail[0] >= MAX_FAILURES:
                    fail[1] = now + LOCKOUT_SECONDS
                    fail[0] = 0
                if len(self._failures) > 5000:   # bound memory
                    self._failures.pop(next(iter(self._failures)))
            return "bad", None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._failures.pop(key, None)
            self._sessions[token] = {"username": key, "role": rec["role"],
                                     "csrf": secrets.token_urlsafe(32),
                                     "must_change": bool(rec.get("must_change")),
                                     "is_master": rec["role"] == "master",
                                     "display_name": rec.get("display_name") or key,
                                     "created": now, "last": now}
            fresh = load_users(self.users_file)
            if key in fresh:
                fresh[key]["last_login"] = _now_iso()
                try:
                    _write_file(self.users_file, fresh)
                except OSError:
                    pass
        return "ok", token

    # -- sessions ------------------------------------------------------
    def get_session(self, token) -> dict | None:
        if not token:
            return None
        now = self._clock()
        with self._lock:
            s = self._sessions.get(token)
            if s is None:
                return None
            if now - s["last"] > IDLE_TIMEOUT or now - s["created"] > ABSOLUTE_TIMEOUT:
                self._sessions.pop(token, None)
                return None
            s["last"] = now
            return dict(s)

    def check_password(self, username, password) -> bool:
        """Re-check a signed-in user's password (for sensitive settings)."""
        if not isinstance(username, str) or not isinstance(password, str) \
                or len(password) > MAX_PASSWORD_LEN:
            return False
        rec = load_users(self.users_file).get(username.strip().lower())
        return verify_password(password, rec if rec else _DUMMY) and bool(rec)

    def change_password(self, username, old, new, keep_token=None) -> str:
        """Return ok | bad_old | weak | same. On success the hash is replaced, the
        must_change flag cleared and every OTHER session of that user ended."""
        if not isinstance(new, str) or len(new) < MIN_PASSWORD_LEN or len(new) > MAX_PASSWORD_LEN:
            return "weak"
        if not self.check_password(username, old):
            return "bad_old"
        if old == new:
            return "same"
        key = username.strip().lower()
        with self._lock:
            users = load_users(self.users_file)
            rec = users.get(key)
            if rec is None:
                return "bad_old"
            fresh = hash_password(new)
            for k, v in rec.items():
                if k not in ("salt", "hash", "n", "r", "p", "must_change"):
                    fresh[k] = v
            fresh["updated_by"], fresh["updated_at"] = key, _now_iso()
            fresh["password_changed_at"] = fresh["updated_at"]
            users[key] = fresh
            _write_file(self.users_file, users)
            audit(self.users_file, key, "password_changed_by_self", key)
            for tok in [t for t, s in self._sessions.items()
                        if s["username"] == key and t != keep_token]:
                self._sessions.pop(tok, None)
            if keep_token in self._sessions:
                self._sessions[keep_token]["must_change"] = False
        return "ok"

    def logout(self, token) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    # -- user administration (the server checks the permission first) --------
    def list_users(self) -> list[dict]:
        """No hashes, ever. password_set is True when a hash exists."""
        out = []
        for u, r in load_users(self.users_file).items():
            out.append({"username": u, "role": r.get("role", ""),
                        "display_name": r.get("display_name") or "",
                        "is_master": r.get("role") == "master",
                        "password_set": bool(r.get("hash")),
                        "must_change": bool(r.get("must_change")),
                        "created_at": r.get("created_at") or "",
                        "created_by": r.get("created_by") or "",
                        "updated_at": r.get("updated_at") or "",
                        "updated_by": r.get("updated_by") or "",
                        "last_login": r.get("last_login") or "",
                        "password_changed_at": r.get("password_changed_at") or "",
                        "locked": self.is_locked(u)})
        order = {r: i for i, r in enumerate(ROLES)}
        out.sort(key=lambda x: (order.get(x["role"], 9), x["username"]))
        return out

    def is_locked(self, username: str) -> bool:
        with self._lock:
            fail = self._failures.get(str(username).strip().lower())
            return bool(fail and fail[1] > self._clock())

    def unlock(self, username: str, actor: str, actor_role: str) -> str:
        """Clear a sign-in lockout. The master may unlock anyone; managers only the
        roles they manage."""
        username = str(username).strip().lower()
        rec = load_users(self.users_file).get(username)
        if rec is None:
            return "not_found"
        if actor_role != "master" and not can_manage(actor_role, rec.get("role")):
            return "forbidden"
        with self._lock:
            self._failures.pop(username, None)
        audit(self.users_file, actor, "user_unlocked", username)
        return "ok"

    def add_user(self, username: str, role: str, password: str, actor: str,
                 actor_role: str, display_name: str = "") -> str:
        if not isinstance(username, str) or not isinstance(password, str):
            return "bad_username"
        if role not in ROLES:
            return "bad_role"
        if not can_manage(actor_role, role):
            return "forbidden"
        if len(password) < MIN_PASSWORD_LEN or len(password) > MAX_PASSWORD_LEN:
            return "weak"
        username = username.strip().lower()
        if not username or len(username) > MAX_USERNAME_LEN or username == MASTER_USERNAME:
            return "bad_username"
        with self._lock:
            users = load_users(self.users_file)
            if username in users:
                return "exists"
            rec = hash_password(password)
            rec.update(role=role, must_change=True, created_by=actor, created_at=_now_iso(),
                       password_changed_at=_now_iso(),
                       display_name=str(display_name or "").strip()[:80])
            users[username] = rec
            _write_file(self.users_file, users)
        audit(self.users_file, actor, "user_added", username, "role=" + role)
        return "ok"

    def update_user(self, username: str, actor: str, actor_role: str,
                    role: str | None = None, password: str | None = None,
                    display_name: str | None = None) -> str:
        """Change role, display name or set a new initial password (forces a change
        at next sign-in). The master account and your own account are refused here."""
        username = str(username).strip().lower()
        with self._lock:
            users = load_users(self.users_file)
            rec = users.get(username)
            if rec is None:
                return "not_found"
            if username == actor or not can_manage(actor_role, rec.get("role")):
                return "forbidden"
            if role is not None:
                if role not in ROLES:
                    return "bad_role"
                if not can_manage(actor_role, role):
                    return "forbidden"
                rec["role"] = role
            if display_name is not None:
                rec["display_name"] = str(display_name).strip()[:80]
            if password is not None:
                if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN \
                        or len(password) > MAX_PASSWORD_LEN:
                    return "weak"
                fresh = hash_password(password)
                for k, v in rec.items():
                    if k not in ("salt", "hash", "n", "r", "p", "must_change"):
                        fresh[k] = v
                fresh["must_change"] = True
                fresh["password_changed_at"] = _now_iso()
                rec = fresh
            rec["updated_by"], rec["updated_at"] = actor, _now_iso()
            users[username] = rec
            _write_file(self.users_file, users)
            for tok in [t for t, s in self._sessions.items() if s["username"] == username]:
                self._sessions.pop(tok, None)
            self._failures.pop(username, None)
        what = [k for k, v in (("role", role), ("name", display_name), ("password_reset", password)) if v is not None]
        audit(self.users_file, actor, "user_updated", username, ",".join(what))
        return "ok"

    def update_master_name(self, actor: str, actor_role: str, display_name: str) -> str:
        """Only the master account edits its own display name."""
        if actor_role != "master":
            return "forbidden"
        with self._lock:
            users = load_users(self.users_file)
            rec = users.get(actor)
            if rec is None or rec.get("role") != "master":
                return "forbidden"
            rec["display_name"] = str(display_name).strip()[:80]
            rec["updated_by"], rec["updated_at"] = actor, _now_iso()
            _write_file(self.users_file, users)
            for s in self._sessions.values():
                if s["username"] == actor:
                    s["display_name"] = rec["display_name"] or actor
        return "ok"

    def delete_user(self, username: str, actor: str, actor_role: str) -> str:
        username = str(username).strip().lower()
        with self._lock:
            users = load_users(self.users_file)
            rec = users.get(username)
            if rec is None:
                return "not_found"
            if username == actor or not can_manage(actor_role, rec.get("role")):
                return "forbidden"
            del users[username]
            _write_file(self.users_file, users)
            audit(self.users_file, actor, "user_deleted", username)
            for tok in [t for t, s in self._sessions.items() if s["username"] == username]:
                self._sessions.pop(tok, None)
        return "ok"

    def check_csrf(self, session: dict, supplied) -> bool:
        return isinstance(supplied, str) and hmac.compare_digest(
            supplied.encode("utf-8"), session["csrf"].encode("utf-8"))
