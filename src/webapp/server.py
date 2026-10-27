"""HR Attendance attendance web app: localhost-only JSON API plus static files.

Stdlib only. Binds 127.0.0.1 and refuses anything else. Prints nothing.
Data endpoints lazily import modules built elsewhere; a missing module gives
503 {"error": "not available yet"}.
"""
from __future__ import annotations

import datetime
import importlib
import ipaddress
import json
import os
import re
import socket
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from src.webapp import auth, registry, rules_edit

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
COOKIE = "aic_session"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
UPLOAD_EXTS = {".xls", ".xlsx", ".csv"}
# Log viewer allow-list: file types, and names or folders that are never shown.
LOG_EXTS = {".log", ".csv", ".txt", ".json"}
LOG_DENY_NAMES = {"users.json", "first_login.txt", "registry.json", "server.pid"}
LOG_DENY_DIRS = {"edge-profile", "cache", "uploads", "rules_backups", "_backup"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
        ".txt": "text/plain; charset=utf-8", ".woff2": "font/woff2"}
# Reports shown inside the app run in a sandbox: no scripts, no network, inline styles only.
REPORT_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
MAX_VIEW_BYTES = 8 * 1024 * 1024
MAX_VIEW_SHEETS, MAX_VIEW_ROWS, MAX_VIEW_COLS = 8, 300, 30
PLACEHOLDER = (b"<!doctype html><meta charset=utf-8><title>Client</title>"
               b"<h1>Client Attendance</h1><p>The screens are not installed yet.</p>")


PRIVATE_NETS = [ipaddress.ip_network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")]
LAN_MARKER = Path("webapp") / "lan.enabled"
IP_MAX_FAILURES = 10
IP_LOCKOUT_SECONDS = 5 * 60


def is_private_ip(ip: str) -> bool:
    """True for RFC1918 and loopback IPv4 only."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and any(addr in n for n in PRIVATE_NETS)


def detect_lan_ip() -> str | None:
    """The PC's private LAN IPv4, or None. Sends no packet (UDP connect only)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return ip if is_private_ip(ip) and not ip.startswith("127.") else None


def lan_requested(logs_dir, argv_flag: bool = False) -> bool:
    """LAN mode is OFF unless --lan, AIC_LAN=1 or the marker file says so."""
    if argv_flag or os.environ.get("AIC_LAN") == "1":
        return True
    return (Path(logs_dir) / LAN_MARKER).is_file()


def open_folder(path: Path) -> None:
    """Open a folder in Explorer. Only called with one of three fixed folders."""
    os.startfile(str(path))  # noqa: S606 - Windows only; path is never user-supplied


class NotAvailable(Exception):
    pass


def _call(module: str, func: str, *args):
    """Lazy import + call. Missing module/function -> NotAvailable."""
    try:
        fn = getattr(importlib.import_module(module), func)
    except (ImportError, AttributeError):
        raise NotAvailable from None
    return fn(*args)


def safe_join(root: Path, rel: str) -> Path | None:
    """Resolve rel under root, or None if it escapes or looks odd."""
    if not rel or "\x00" in rel or "\\" in rel or ":" in rel:
        return None
    root = Path(root).resolve()
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if target != root and root not in target.parents:
        return None
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "Client"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    # -- plumbing ------------------------------------------------------
    def log_message(self, *a, **k):  # silence stdout/stderr
        pass

    def _log_access(self, status: int):
        try:
            d = self.server.logs_dir / "webapp"
            d.mkdir(parents=True, exist_ok=True)
            path = urlsplit(self.path).path[:200]
            role = self._session["role"] if getattr(self, "_session", None) else "-"
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            line = f"{stamp} {self.client_address[0]} {self.command} {path} {status} {role}\n"
            with self.server.log_lock, (d / "access.log").open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    def _send(self, status: int, body: bytes, ctype: str, api: bool = False, extra=(), csp: str = None):
        self._log_access(status)   # before the reply, so the log is never behind the client
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", csp or CSP)
        self.send_header("X-Frame-Options", "SAMEORIGIN" if csp else "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if api:
            self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj, extra=()):
        self._send(status, json.dumps(obj, default=str).encode("utf-8"),
                   "application/json", api=True, extra=extra)

    def _err(self, status: int, msg: str):
        self._json(status, {"error": msg})

    # -- request entry -------------------------------------------------
    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_PUT(self):
        self._session = None
        self._err(405, "method not allowed")

    do_DELETE = do_PATCH = do_PUT

    def _dispatch(self):
        self._session = None
        try:
            if not self._client_ok():
                return self._err(403, "this address is not allowed")
            if not self._host_ok():
                return self._err(400, "bad host")
            parts = urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                self._api(path, parse_qs(parts.query))
            elif self.command in ("GET", "HEAD"):
                self._static(path)
            else:
                self._err(405, "method not allowed")
        except NotAvailable:
            self._err(503, "not available yet")
        except Exception:  # noqa: BLE001 - never leak a stack trace
            try:
                self._err(500, "server error")
            except Exception:  # noqa: BLE001
                pass

    def _origin_ok(self) -> bool:
        """POSTs must come from this app's own page (S1). Browsers always send Origin
        on a POST; Sec-Fetch-Site is accepted as the alternative."""
        origin = self.headers.get("Origin")
        if origin:
            host = urlsplit(origin)
            return host.scheme == "http" and (host.hostname or "").lower() in self.server.allowed_hosts                 and (host.port or 80) == self.server.server_address[1]
        return (self.headers.get("Sec-Fetch-Site") or "").lower() == "same-origin"

    def _client_ok(self) -> bool:
        return is_private_ip(self.client_address[0])

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        # Literal IPs only (plus localhost): a rebinding name never matches.
        return host.rsplit(":", 1)[0] in self.server.allowed_hosts

    # -- static --------------------------------------------------------
    def _static(self, path: str):
        rel = unquote(path).lstrip("/") or "index.html"
        root = self.server.static_dir
        target = safe_join(root, rel) if root.is_dir() else None
        if target is not None and target.is_file():
            ctype = MIME.get(target.suffix.lower(), "application/octet-stream")
            return self._send(200, target.read_bytes(), ctype)
        if rel == "index.html":
            return self._send(200, PLACEHOLDER, MIME[".html"])
        self._err(404, "not found")

    # -- API -----------------------------------------------------------
    def _cookie_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            morsel = SimpleCookie(raw).get(COOKIE)
        except Exception:  # noqa: BLE001
            return None
        return morsel.value if morsel else None

    def _read_body(self, limit: int):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            self._err(400, "bad length")
            return None
        if n > limit:
            left = min(n, limit + 4 * 1024 * 1024)   # drain so the client can finish
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
            self._err(413, "too large")
            return None
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._err(415, "JSON content type required")
            return None
        body = self._read_body(MAX_JSON_BYTES)
        if body is None:
            return None
        try:
            obj = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            self._err(400, "bad json")
            return None
        if not isinstance(obj, dict):
            self._err(400, "bad json")
            return None
        return obj

    def _api(self, path: str, q: dict):
        method = "GET" if self.command == "HEAD" else self.command
        route = ROUTES.get((method, path))
        if route is None:
            if any(p == path for (_m, p) in ROUTES):
                return self._err(405, "method not allowed")
            return self._err(404, "not found")
        perm, fn = route
        if perm is not None:
            if method == "POST" and not self._origin_ok():
                return self._err(403, "bad origin")
            sess = self.server.auth.get_session(self._cookie_token())
            if sess is None:
                return self._err(401, "login required")
            self._session = sess
            if method == "POST" and not self.server.auth.check_csrf(
                    sess, self.headers.get("X-CSRF-Token")):
                return self._err(403, "bad csrf token")
            if sess.get("must_change") and path not in ("/api/me", "/api/logout", "/api/password"):
                return self._err(403, "password change required")
            if perm != "any" and not auth.can(sess["role"], perm):
                return self._err(403, "not allowed for your role")
        elif method == "POST" and not self._origin_ok():
            return self._err(403, "bad origin")
        fn(self, q)

    @staticmethod
    def _q(q, name, default=""):
        return q.get(name, [default])[0][:200]

    # -- handlers ------------------------------------------------------
    def h_login(self, q):
        body = self._json_body()
        if body is None:
            return
        ip = self.client_address[0]
        now = self.server.auth._clock()
        with self.server.log_lock:
            fail = self.server.ip_failures.get(ip)
            blocked = bool(fail and fail[1] > now)
        if blocked:   # reply outside the lock: _send logs under the same lock
            return self._err(429, "too many attempts, try again in 5 minutes")
        status, token = self.server.auth.login(body.get("username"), body.get("password"))
        if status in ("bad", "locked"):
            with self.server.log_lock:
                fail = self.server.ip_failures.setdefault(ip, [0, 0.0])
                fail[0] += 1
                if fail[0] >= IP_MAX_FAILURES:
                    fail[0], fail[1] = 0, now + IP_LOCKOUT_SECONDS
                if len(self.server.ip_failures) > 5000:
                    self.server.ip_failures.pop(next(iter(self.server.ip_failures)))
        elif status == "ok":
            with self.server.log_lock:
                self.server.ip_failures.pop(ip, None)
        if status == "no_users":
            return self._err(503, "no users set up")
        if status == "invalid":
            return self._err(400, "username and password required")
        if status == "locked":
            return self._err(429, "too many attempts, try again in 5 minutes")
        if status != "ok":
            return self._err(401, "wrong username or password")
        sess = self.server.auth.get_session(token)
        cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"
        self._json(200, self._me_payload(sess), extra=[("Set-Cookie", cookie)])

    def h_logout(self, q):
        self.server.auth.logout(self._cookie_token())
        cookie = f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        self._json(200, {"ok": True}, extra=[("Set-Cookie", cookie)])

    def _me_payload(self, s):
        return {"username": s["username"], "role": s["role"],
                "role_label": auth.ROLE_LABELS.get(s["role"], s["role"]),
                "display_name": s.get("display_name") or s["username"],
                "csrf": s["csrf"], "must_change": s.get("must_change", False),
                "is_master": s["role"] == "master",
                "perms": sorted(p for p in auth.PERMS if auth.can(s["role"], p))}

    def h_me(self, q):
        self._json(200, self._me_payload(self._session))

    def h_password(self, q):
        body = self._json_body()
        if body is None:
            return
        res = self.server.auth.change_password(
            self._session["username"], body.get("old"), body.get("new"), self._cookie_token())
        if res == "weak":
            return self._err(400, f"The new password must have at least {auth.MIN_PASSWORD_LEN} characters.")
        if res == "same":
            return self._err(400, "The new password must be different from the old one.")
        if res != "ok":
            return self._err(403, "The current password is not right.")
        self._json(200, {"ok": True, "message": "Password changed. Other sign-ins of yours were ended."})

    def h_profile(self, q):
        """Master admin edits their own display name. Nobody else may."""
        body = self._json_body()
        if body is None:
            return
        name = body.get("display_name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            return self._err(400, "A name of 1 to 80 characters is needed.")
        s = self._session
        if self.server.auth.update_master_name(s["username"], s["role"], name) != "ok":
            return self._err(403, "Only the master admin can edit their own name.")
        self._json(200, {"ok": True})

    def h_overview(self, q):
        week = self._q(q, "week")
        if week and not WEEK_RE.match(week):
            return self._err(400, "week must look like 2026-W41")
        self._json(200, _call("src.webapp.queries", "overview", week or None))

    def h_exceptions(self, q):
        week = self._q(q, "week")
        if week and not WEEK_RE.match(week):
            return self._err(400, "week must look like 2026-W41")
        self._json(200, _call("src.webapp.queries", "exceptions",
                              self._q(q, "status") or None, self._q(q, "outlet") or None,
                              week or None))

    # -- staff and outlets (Admin screens) -----------------------------------
    def _staff_rows(self):
        return registry.merge_staff(_call("src.webapp.queries", "staff"), self.server.registry_path)

    def h_staff(self, q):
        self._json(200, self._staff_rows())

    def h_outlets(self, q):
        staff_rows = self._staff_rows()
        master = _call("src.webapp.queries", "outlets")
        self._json(200, registry.merge_outlets(master, staff_rows, self.server.registry_path))

    def h_staff_save(self, q):
        body = self._json_body()
        if body is None:
            return
        create = bool(body.get("create"))
        try:
            ids = {r["id"] for r in _call("src.webapp.queries", "staff")}
            res = registry.save_staff(self.server.registry_path, body.get("id"), body, self._session["username"],
                                      create, ids)
        except registry.RegistryError as exc:
            return self._err(400, str(exc))
        self._json(200, {"ok": True, **res})

    def h_outlet_save(self, q):
        body = self._json_body()
        if body is None:
            return
        create = bool(body.get("create"))
        try:
            ids = {r["outlet"] for r in _call("src.webapp.queries", "outlets")}
            res = registry.save_outlet(self.server.registry_path, body.get("id"), body, self._session["username"],
                                       create, ids)
        except registry.RegistryError as exc:
            return self._err(400, str(exc))
        self._json(200, {"ok": True, **res})

    # -- reports -------------------------------------------------------------
    @staticmethod
    def _report_perm(rel: str):
        low = rel.lower()
        if low.endswith(".xlsx"):
            return "report_workbook"
        if low.endswith(".html"):
            return "report_review"
        return None

    def h_reports(self, q):
        role = self._session["role"]
        rows = _call("src.webapp.queries", "reports")
        for r in rows:
            perm = self._report_perm(str(r.get("path", "")))
            r["can_open"] = bool(perm and auth.can(role, perm))
        self._json(200, rows)

    def h_help(self, q):
        self._json(200, _call("src.webapp.help_search", "search", self._q(q, "q")))

    def _report_target(self, rel: str):
        """(target, perm) for an allowed report file, or sends the error and returns None."""
        perm = self._report_perm(rel)
        if perm is None:
            self._err(400, "only .xlsx and .html reports can be opened")
            return None
        if not auth.can(self._session["role"], perm):
            self._err(403, "not allowed for your role")
            return None
        target = safe_join(self.server.reports_dir, rel)
        if target is None or not target.is_file():
            self._err(404, "not found")
            return None
        return target

    def h_download(self, q):
        target = self._report_target(self._q(q, "path"))
        if target is None:
            return
        name = target.name.replace('"', "")
        ctype = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                 if target.suffix.lower() == ".xlsx" else "text/html; charset=utf-8")
        self._send(200, target.read_bytes(), ctype, api=True,
                   extra=[("Content-Disposition", f'attachment; filename="{name}"')])

    def h_report_view(self, q):
        """View a report inside the app. .html is served sandboxed (no scripts) for an
        iframe; .xlsx is returned as plain rows (first sheets and rows only)."""
        target = self._report_target(self._q(q, "path"))
        if target is None:
            return
        if target.suffix.lower() == ".html":
            return self._send(200, target.read_bytes(), "text/html; charset=utf-8", api=True,
                              csp=REPORT_CSP)
        if target.stat().st_size > MAX_VIEW_BYTES:
            return self._err(413, "too large to view here; download it instead")
        try:
            import openpyxl
            wb = openpyxl.load_workbook(target, read_only=True, data_only=True)
            sheets = []
            for ws in list(wb.worksheets)[:MAX_VIEW_SHEETS]:
                rows, cut = [], False
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= MAX_VIEW_ROWS:
                        cut = True
                        break
                    rows.append(["" if v is None else str(v)[:200] for v in row[:MAX_VIEW_COLS]])
                sheets.append({"name": ws.title, "rows": rows, "truncated": cut})
            wb.close()
        except Exception:  # noqa: BLE001 - never send exception text to the browser
            return self._err(500, "server error")
        self._json(200, {"name": target.name, "sheets": sheets})

    def h_scan(self, q):
        if self._read_body(MAX_JSON_BYTES) is None:
            return
        res = _call("src.agents.drop_scanner", "run")
        res = res if isinstance(res, dict) else {"result": res}
        if "message" not in res:
            if res.get("status") == "NOTHING_NEW":
                res["message"] = f"Scan finished: {res.get('scanned', 0)} files, nothing new since last time."
            else:
                res["message"] = (f"Scan finished: {res.get('scanned', 0)} files, "
                                  f"{res.get('new', 0)} new.")
            if res.get("alerts"):
                res["message"] += " " + " ".join(str(a) for a in res["alerts"])
        self._json(200, res)

    def h_generate(self, q):
        body = self._json_body()
        if body is None:
            return
        scope = body.get("scope")
        if scope not in ("weekly", "monthly"):
            return self._err(400, "scope must be weekly or monthly")
        res = _call("src.agents.dashboard", "generate", scope)
        if isinstance(res, dict) and res.get("available") is False:
            raise NotAvailable
        if isinstance(res, dict) and res.get("status") == "FAILED":
            res["detail"] = "See the log files for the reason."   # never send exception text (S11)
        if isinstance(res, dict) and "message" not in res:
            status = str(res.get("status", ""))
            window = res.get("window") or scope
            if status == "SKIPPED_EXISTS":
                res["message"] = f"The {scope} report for {window} already exists, so nothing was replaced."
            elif status == "FAILED":
                res["message"] = f"The {scope} report could not be created. {res.get('detail', '')}".strip()
            else:
                res["message"] = f"The {scope} report for {window} was created ({status or 'OK'})."
        self._json(200, res)

    def h_upload(self, q):
        body = self._read_body(MAX_UPLOAD_BYTES)
        if body is None:
            return
        ctype = self.headers.get("Content-Type") or ""
        files = [((self._q(q, "filename") or self.headers.get("X-Filename")), body)]
        if ctype.lower().startswith("multipart/form-data"):
            msg = BytesParser(policy=HTTP).parsebytes(
                b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
            files = [(part.get_filename(), part.get_payload(decode=True) or b"")
                     for part in msg.iter_parts() if part.get_filename()]
        if not files:
            return self._err(400, "file name required")
        clean = []
        for filename, data in files:
            if not filename or not isinstance(filename, str):
                return self._err(400, "file name required")
            filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
            if Path(filename).suffix.lower() not in UPLOAD_EXTS:
                return self._err(400, "only .xls, .xlsx and .csv files are allowed")
            clean.append((filename, data))
        who = re.sub(r"[^A-Za-z0-9_-]", "_", self._session["username"])[:60] or "user"
        results = [_call("src.agents.dashboard", "save_upload", n, d, who) for n, d in clean]
        good = [r for r in results if r.get("status") == "OK"]
        res = dict(results[0]) if len(results) == 1 else {
            "status": "OK" if len(good) == len(results) else "PARTIAL",
            "saved": [r["saved"] for r in good]}
        res["message"] = (f"{len(good)} of {len(results)} file(s) saved to your upload folder. "
                          "They are not in the drop folder until Admin moves them there.")
        refused = [str(r.get("detail")) for r in results if r.get("status") != "OK"]
        if refused:
            res["message"] += " Not saved: " + "; ".join(refused)
        self._json(200 if len(good) == len(results) else 400, res)

    def _lan_state(self):
        srv = self.server
        marker = (srv.logs_dir / LAN_MARKER).is_file()
        host = srv.server_address[0]
        return {"running": srv.lan, "setting": marker,
                "url": f"http://{host}:{srv.server_address[1]}/" if srv.lan else None,
                "restart_needed": srv.lan != marker and os.environ.get("AIC_LAN") != "1",
                "can_change": auth.can(self._session["role"], "lan_set"),
                "warning": "Plain HTTP: passwords cross the office network unencrypted."}

    def h_lan_info(self, q):
        self._json(200, self._lan_state())

    def h_lan_set(self, q):
        """Master admin re-types their own password, then the lan.enabled marker is written
        or removed. The change applies the next time the server starts."""
        body = self._json_body()
        if body is None:
            return
        if not isinstance(body.get("enabled"), bool):
            return self._err(400, "enabled must be true or false")
        if not self.server.auth.check_password(self._session["username"], body.get("password")):
            return self._err(403, "wrong password")
        marker = self.server.logs_dir / LAN_MARKER
        try:
            if body["enabled"]:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("on\n", encoding="utf-8")
            else:
                marker.unlink(missing_ok=True)
        except OSError:
            return self._err(500, "server error")
        self._json(200, self._lan_state())

    def h_health(self, q):
        f = self.server.logs_dir / "it" / "health_latest.json"
        try:
            self._json(200, json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            self._json(200, {"status": "unknown", "detail": "no health file yet"})

    # -- open a folder in Windows Explorer (fixed list, this PC only) -----------
    def h_open_folder(self, q):
        body = self._json_body()
        if body is None:
            return
        import src.daily as daily
        folders = {"reports": self.server.reports_dir, "logs": self.server.logs_dir,
                   "drop": Path(daily.DROP_DIR)}
        target = folders.get(body.get("which"))
        if target is None:
            return self._err(400, "which must be reports, logs or drop")
        if not self.client_address[0].startswith("127."):
            return self._err(403, "Folders can only be opened on the PC that runs the app.")
        if not target.is_dir():
            return self._err(404, "That folder does not exist yet.")
        try:
            open_folder(target)
        except OSError:
            return self._err(500, "server error")
        self._json(200, {"ok": True, "message": "Opened in File Explorer on this PC."})

    # -- IT diagnosis and repair (specialist is propose-first; repairs are whitelisted) --
    @staticmethod
    def _it_run(**kw):
        try:
            core = importlib.import_module("src.agents.it_specialist.core")
        except ImportError:
            raise NotAvailable from None
        res = core.run(run_tests=False, **kw)
        return res if isinstance(res, dict) else {"status": "FAILED"}

    def h_it_diagnose(self, q):
        if self._read_body(MAX_JSON_BYTES) is None:
            return
        res = self._it_run(mode="health")
        self._json(200, {"status": res.get("status"), "overall": res.get("overall"),
                         "counts": res.get("counts"),
                         "results": [{"check": r.get("check"), "status": r.get("status"),
                                      "detail": r.get("detail")} for r in res.get("results", [])]})

    def h_it_propose(self, q):
        if self._read_body(MAX_JSON_BYTES) is None:
            return
        res = self._it_run(mode="workflow", approve=False)
        self._json(200, {"status": res.get("status"), "incidents": res.get("incidents", []),
                         "approved": False})

    def h_it_repair(self, q):
        """Applies only the specialist's whitelisted safe fixes, after the user confirms
        with their own password. Anything else stays a written proposal."""
        body = self._json_body()
        if body is None:
            return
        if body.get("confirm") is not True:
            return self._err(400, "Please confirm the repair first.")
        if not self.server.auth.check_password(self._session["username"], body.get("password")):
            return self._err(403, "wrong password")
        res = self._it_run(mode="workflow", approve=True)
        self._json(200, {"status": res.get("status"), "incidents": res.get("incidents", []),
                         "approved": True})

    # -- user management (master / CEO / HRM; see auth.ASSIGNABLE) ------------
    def _user_result(self, res):
        msgs = {"bad_role": (400, "That role is not valid."),
                "forbidden": (403, "You cannot do that to this account or role."),
                "weak": (400, f"The password must have at least {auth.MIN_PASSWORD_LEN} characters."),
                "bad_username": (400, "Choose a different username."),
                "exists": (409, "That username already exists."),
                "not_found": (404, "User not found.")}
        if res == "ok":
            return self._json(200, {"ok": True})
        status, text = msgs.get(res, (400, "Not saved."))
        self._err(status, text)

    def h_users_list(self, q):
        s = self._session
        users = self.server.auth.list_users()
        for u in users:
            u["can_manage"] = u["username"] != s["username"] and auth.can_manage(s["role"], u["role"])
        self._json(200, {"users": users, "assignable": list(auth.ASSIGNABLE.get(s["role"], ())),
                         "role_labels": auth.ROLE_LABELS})

    def h_users_add(self, q):
        body = self._json_body()
        if body is None:
            return
        s = self._session
        self._user_result(self.server.auth.add_user(
            body.get("username", ""), body.get("role", ""), body.get("password", ""),
            s["username"], s["role"], body.get("display_name", "")))

    def h_users_update(self, q):
        """Change role or display name, or set a new initial password (the person must
        change it at next sign-in). The password is typed by a human; it is never shown."""
        body = self._json_body()
        if body is None:
            return
        s = self._session
        self._user_result(self.server.auth.update_user(
            body.get("username", ""), s["username"], s["role"], role=body.get("role"),
            password=body.get("password"), display_name=body.get("display_name")))

    def h_users_security(self, q):
        """Master only. Read-only: role, password set, last password change, last
        sign-in, lock-out and must-change. No hash or salt is ever returned."""
        keep = ("username", "display_name", "role", "password_set", "password_changed_at",
                "last_login", "locked", "must_change", "created_at", "created_by")
        rows = [{k: u[k] for k in keep} for u in self.server.auth.list_users()]
        self._json(200, {"users": rows, "note": "Passwords are stored only as salted hashes and cannot be shown."})

    def h_users_unlock(self, q):
        body = self._json_body()
        if body is None:
            return
        s = self._session
        self._user_result(self.server.auth.unlock(body.get("username", ""), s["username"], s["role"]))

    def h_users_delete(self, q):
        body = self._json_body()
        if body is None:
            return
        s = self._session
        self._user_result(self.server.auth.delete_user(body.get("username", ""), s["username"], s["role"]))

    # -- rules (read: all, write: master / CEO) --------------------------------
    def h_rules_list(self, q):
        self._json(200, rules_edit.list_rules(self.server.rules_path))

    def _rules_edit(self, fn):
        body = self._json_body()
        if body is None:
            return
        s = self._session
        srv = self.server
        try:
            res = fn(srv.rules_path, body.get("id"), body.get("fields") or {}, s["username"],
                     srv.logs_dir / "webapp" / "rules_backups",
                     srv.logs_dir / "webapp" / "rules_audit.csv")
        except rules_edit.RuleEditError as exc:
            return self._err(400, str(exc))
        try:
            _call("src.webapp.queries", "clear_cache")
        except NotAvailable:
            pass
        self._json(200, {"ok": True, **res})

    def h_rules_update(self, q):
        self._rules_edit(rules_edit.update_rule)

    def h_rules_add(self, q):
        self._rules_edit(rules_edit.add_rule)

    # -- logs viewer (Admin) ---------------------------------------------------
    def _log_file_ok(self, p: Path) -> bool:
        try:
            rel = p.relative_to(self.server.logs_dir.resolve())
        except ValueError:
            return False
        return (p.suffix.lower() in LOG_EXTS and p.name.lower() not in LOG_DENY_NAMES
                and not any(part.lower() in LOG_DENY_DIRS for part in rel.parts[:-1]))

    def h_logs_list(self, q):
        start, end = self._q(q, "start"), self._q(q, "end")
        for d in (start, end):
            if d and not DATE_RE.match(d):
                return self._err(400, "dates must look like 2026-10-19")
        root = self.server.logs_dir.resolve()
        files = []
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in LOG_DENY_DIRS]
            for n in names:
                p = Path(dirpath) / n
                if not self._log_file_ok(p):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                if (start and mtime[:10] < start) or (end and mtime[:10] > end):
                    continue
                files.append({"name": n, "path": p.relative_to(root).as_posix(),
                              "size": st.st_size, "modified": mtime})
        files.sort(key=lambda f: f["modified"], reverse=True)
        self._json(200, files[:300])

    def _log_target(self, q):
        rel = self._q(q, "path").replace("\\", "/")
        if not rel:
            self._err(400, "path required")
            return None
        target = safe_join(self.server.logs_dir, rel)
        if target is None or not target.is_file() or not self._log_file_ok(target):
            self._err(404, "not found")
            return None
        return target

    def h_logs_view(self, q):
        target = self._log_target(q)
        if target is None:
            return
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return self._err(500, "server error")
        if len(text) > 500_000:
            text = "... (showing the last 500,000 characters) ...\n" + text[-500_000:]
        self._json(200, {"name": target.name, "content": text})

    def h_logs_download(self, q):
        target = self._log_target(q)
        if target is None:
            return
        name = target.name.replace('"', "")
        ctype = "application/json" if target.suffix.lower() == ".json" else "text/plain; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, api=True,
                   extra=[("Content-Disposition", f'attachment; filename="{name}"')])


ROUTES = {
    ("POST", "/api/login"): (None, Handler.h_login),
    ("POST", "/api/logout"): ("any", Handler.h_logout),
    ("GET", "/api/me"): ("any", Handler.h_me),
    ("POST", "/api/password"): ("any", Handler.h_password),
    ("POST", "/api/profile"): ("any", Handler.h_profile),
    ("GET", "/api/overview"): ("overview", Handler.h_overview),
    ("GET", "/api/outlets"): ("outlets_view", Handler.h_outlets),
    ("POST", "/api/outlets/save"): ("outlets_edit", Handler.h_outlet_save),
    ("GET", "/api/staff"): ("staff_view", Handler.h_staff),
    ("POST", "/api/staff/save"): ("staff_edit", Handler.h_staff_save),
    ("GET", "/api/reports"): ("reports_list", Handler.h_reports),
    ("GET", "/api/reports/download"): ("reports_list", Handler.h_download),
    ("GET", "/api/reports/view"): ("reports_list", Handler.h_report_view),
    ("POST", "/api/reports/generate"): ("scan", Handler.h_generate),
    ("GET", "/api/help"): ("help", Handler.h_help),
    ("GET", "/api/exceptions"): ("exceptions", Handler.h_exceptions),
    ("POST", "/api/scan/run"): ("scan", Handler.h_scan),
    ("POST", "/api/upload"): ("scan", Handler.h_upload),
    ("GET", "/api/health"): ("health", Handler.h_health),
    ("GET", "/api/lan-info"): ("lan_view", Handler.h_lan_info),
    ("POST", "/api/lan-set"): ("lan_set", Handler.h_lan_set),
    ("POST", "/api/open-folder"): ("open_folder", Handler.h_open_folder),
    ("POST", "/api/it/diagnose"): ("it_diagnose", Handler.h_it_diagnose),
    ("POST", "/api/it/propose"): ("it_diagnose", Handler.h_it_propose),
    ("POST", "/api/it/repair"): ("it_repair", Handler.h_it_repair),
    ("GET", "/api/users"): ("users_manage", Handler.h_users_list),
    ("POST", "/api/users/add"): ("users_manage", Handler.h_users_add),
    ("POST", "/api/users/update"): ("users_manage", Handler.h_users_update),
    ("GET", "/api/users/security"): ("users_security", Handler.h_users_security),
    ("POST", "/api/users/unlock"): ("users_manage", Handler.h_users_unlock),
    ("POST", "/api/users/delete"): ("users_manage", Handler.h_users_delete),
    ("GET", "/api/rules"): ("rules_view", Handler.h_rules_list),
    ("POST", "/api/rules/update"): ("rules_edit", Handler.h_rules_update),
    ("POST", "/api/rules/add"): ("rules_edit", Handler.h_rules_add),
    ("GET", "/api/logs"): ("logs", Handler.h_logs_list),
    ("GET", "/api/logs/view"): ("logs", Handler.h_logs_view),
    ("GET", "/api/logs/download"): ("logs", Handler.h_logs_download),
}


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def create_server(host: str = HOST, port: int = DEFAULT_PORT, *, logs_dir=None,
                  reports_dir=None, static_dir=None, clock=None, lan: bool = False,
                  rules_path=None) -> AppServer:
    """lan=True binds the PC's private LAN IPv4 (host=None detects it). Default: 127.0.0.1."""
    if lan:
        host = host if host not in (None, HOST) else detect_lan_ip()
        if not host or not is_private_ip(host):
            raise ValueError("LAN mode needs a private (10/8, 172.16/12, 192.168/16) IPv4 address")
    elif host != HOST:
        raise ValueError("The web app only binds 127.0.0.1")
    import src.daily as daily
    srv = AppServer((host, port), Handler)
    assert srv.server_address[0] == host
    srv.lan = bool(lan)
    srv.allowed_hosts = {"127.0.0.1", "localhost"} | ({host} if lan else set())
    srv.ip_failures = {}
    srv.logs_dir = Path(logs_dir if logs_dir is not None else daily.LOGS_DIR)
    srv.reports_dir = Path(reports_dir if reports_dir is not None else daily.REPORTS_DIR)
    srv.static_dir = Path(static_dir if static_dir is not None
                          else Path(__file__).parent / "static")
    srv.registry_path = srv.logs_dir / "webapp" / "registry.json"
    srv.rules_path = Path(rules_path if rules_path is not None
                          else Path(daily.MASTER_DATA_DIR) / "rules.toml")
    srv.log_lock = threading.Lock()
    kwargs = {"clock": clock} if clock else {}
    srv.auth = auth.AuthManager(auth.users_path(srv.logs_dir), **kwargs)
    return srv


def start_in_thread(port: int = 0, **kwargs) -> AppServer:
    """Test helper: serve on an OS-chosen port in a daemon thread."""
    srv = create_server(port=port, **kwargs)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="HR Attendance web app (127.0.0.1 only)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--lan", action="store_true", help="also serve the private office network")
    args = ap.parse_args(argv)
    import src.daily as daily
    lan = lan_requested(daily.LOGS_DIR, args.lan)
    srv = create_server(host=None if lan else HOST, port=args.port, lan=lan)
    if lan:
        print(f"LAN access ON: http://{srv.server_address[0]}:{srv.server_address[1]}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
