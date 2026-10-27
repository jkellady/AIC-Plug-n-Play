import http.client
import json
import sys
import types

import pytest

import src.daily as daily
from src.webapp import auth, server

PW = "fake-test-password-1"


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = None
        self.csrf = None

    def req(self, method, path, body=None, headers=None, raw=None):
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if method == "POST":
            h.setdefault("Origin", f"http://127.0.0.1:{self.port}")
        if method == "POST" and self.csrf and "X-CSRF-Token" not in h:
            h["X-CSRF-Token"] = self.csrf
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        c.close()
        return r, payload

    def json(self, *a, **k):
        r, p = self.req(*a, **k)
        return r.status, json.loads(p or b"{}")

    def login(self, user):
        user = "master000" if user == "master" else user
        r, p = self.req("POST", "/api/login", {"username": user, "password": PW})
        assert r.status == 200, p
        self.cookie = r.getheader("Set-Cookie").split(";")[0]
        self.csrf = json.loads(p)["csrf"]
        return self


@pytest.fixture
def env(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    reports = tmp_path / "reports"
    static = tmp_path / "static"
    (reports / "2026-05").mkdir(parents=True)
    (reports / "2026-05" / "r.xlsx").write_bytes(b"PK-fake")
    (reports / "2026-05" / "r.html").write_text("x")
    (tmp_path / "secret.xlsx").write_bytes(b"secret")
    for role in auth.ROLES:
        auth.save_user(auth.users_path(logs), "master000" if role == "master" else role, role, PW)
    monkeypatch.setattr(daily, "LOGS_DIR", logs)
    srv = server.start_in_thread(logs_dir=logs, reports_dir=reports, static_dir=static)
    yield types.SimpleNamespace(srv=srv, port=srv.server_address[1], logs=logs,
                                tmp=tmp_path, static=static)
    srv.shutdown()
    srv.server_close()


def fake_module(monkeypatch, name, **funcs):
    mod = types.ModuleType(name)
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)


def test_refuses_non_loopback():
    with pytest.raises(ValueError):
        server.create_server(host="0.0.0.0", port=0)


def test_no_users_message(tmp_path):
    srv = server.start_in_thread(logs_dir=tmp_path / "l", reports_dir=tmp_path,
                                 static_dir=tmp_path)
    try:
        st, body = Client(srv.server_address[1]).json(
            "POST", "/api/login", {"username": "viewer", "password": PW})
        assert st == 503 and "no users" in body["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_login_me_logout(env):
    c = Client(env.port)
    assert c.json("GET", "/api/me")[0] == 401
    r, _ = c.req("POST", "/api/login", {"username": "hrm", "password": PW})
    cookie = r.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    c.login("hrm")
    st, me = c.json("GET", "/api/me")
    assert st == 200 and me["role"] == "hrm" and me["csrf"] == c.csrf
    assert c.json("POST", "/api/logout")[0] == 200
    assert c.json("GET", "/api/me")[0] == 401


def test_login_fail_and_lockout(env):
    c = Client(env.port)
    for _ in range(5):
        assert c.json("POST", "/api/login", {"username": "viewer", "password": "bad"})[0] == 401
    assert c.json("POST", "/api/login", {"username": "viewer", "password": PW})[0] == 429
    assert c.json("POST", "/api/login", {"username": "hrm", "password": PW})[0] == 200


def test_session_expiry(env):
    clock = types.SimpleNamespace(t=1000.0)
    env.srv.auth._clock = lambda: clock.t
    c = Client(env.port).login("viewer")
    assert c.json("GET", "/api/me")[0] == 200
    clock.t += 31 * 60
    assert c.json("GET", "/api/me")[0] == 401


def test_csrf_required(env):
    c = Client(env.port).login("admin")
    assert c.json("POST", "/api/scan/run", {}, headers={"X-CSRF-Token": "wrong"})[0] == 403
    c.csrf = None
    assert c.json("POST", "/api/scan/run", {})[0] == 403
    assert c.json("POST", "/api/logout")[0] == 403


def test_role_guards(env, monkeypatch):
    fake_module(monkeypatch, "src.webapp.queries", overview=lambda w: {"w": w},
                exceptions=lambda s, o, w=None: [], staff=lambda: [], outlets=lambda: [],
                reports=lambda: [])
    fake_module(monkeypatch, "src.webapp.help_search", search=lambda q: [])
    viewer, hrm, adm = (Client(env.port).login(r) for r in ("viewer", "hrm", "admin"))
    for path in ("/api/overview", "/api/reports", "/api/help?q=x"):
        assert viewer.json("GET", path)[0] == 200
    for path in ("/api/staff", "/api/outlets", "/api/exceptions", "/api/health"):
        assert viewer.json("GET", path)[0] == 403
    assert hrm.json("GET", "/api/staff")[0] == 403 and hrm.json("GET", "/api/outlets")[0] == 403
    assert adm.json("GET", "/api/staff")[0] == 200 and adm.json("GET", "/api/outlets")[0] == 200
    assert hrm.json("GET", "/api/exceptions?status=open&outlet=OUT4")[0] == 200
    assert hrm.json("GET", "/api/health")[0] == 403
    for path in ("/api/scan/run", "/api/upload?filename=a.csv", "/api/reports/generate"):
        assert viewer.json("POST", path, {"scope": "weekly"})[0] == 403
        assert hrm.json("POST", path, {"scope": "weekly"})[0] == 403
    assert adm.json("GET", "/api/health")[0] == 200
    assert adm.json("GET", "/api/overview?week=2026-W41")[1] == {"w": "2026-W41"}
    assert adm.json("GET", "/api/overview?week=bad")[0] == 400


def test_unauthenticated_is_401(env):
    c = Client(env.port)
    for path in ("/api/overview", "/api/staff", "/api/reports/download?path=a.xlsx"):
        assert c.json("GET", path)[0] == 401


def test_503_when_modules_missing(env, monkeypatch):
    for name in ("src.webapp.queries", "src.webapp.help_search", "src.agents.drop_scanner",
                 "src.agents.dashboard"):
        monkeypatch.setitem(sys.modules, name, None)   # forces ImportError
    adm = Client(env.port).login("admin")
    for method, path in [("GET", "/api/overview"), ("GET", "/api/staff"),
                         ("GET", "/api/outlets"), ("GET", "/api/reports"),
                         ("GET", "/api/exceptions"), ("GET", "/api/help?q=a"),
                         ("POST", "/api/scan/run"),
                         ("POST", "/api/reports/generate"),
                         ("POST", "/api/upload?filename=a.csv")]:
        body = {"scope": "weekly"} if path.endswith("generate") else None
        raw = b"x" if path.startswith("/api/upload") else None
        st, resp = adm.json(method, path, body, raw=raw)
        assert (st, resp) == (503, {"error": "not available yet"}), path


def test_internal_error_has_no_trace(env, monkeypatch):
    def boom(w):
        raise RuntimeError("secret detail")
    fake_module(monkeypatch, "src.webapp.queries", overview=boom)
    st, body = Client(env.port).login("viewer").json("GET", "/api/overview")
    assert st == 500 and body == {"error": "server error"}


def test_download_safe_paths(env):
    assert Client(env.port).login("viewer").json("GET", "/api/reports/download?path=2026-05/r.xlsx")[0] == 403   # S2
    c = Client(env.port).login("hrm")
    r, p = c.req("GET", "/api/reports/download?path=2026-05/r.xlsx")
    assert r.status == 200 and p == b"PK-fake"
    assert "attachment" in r.getheader("Content-Disposition")
    for bad in ("../secret.xlsx", "..%2Fsecret.xlsx",
                "2026-05/../../secret.xlsx", "C:/Windows/x.xlsx", "..%5Csecret.xlsx",
                "/etc/passwd.xlsx", "2026-05/missing.xlsx", ""):
        r, p = c.req("GET", "/api/reports/download?path=" + bad)
        assert r.status in (400, 404), bad
        assert b"secret" not in p


def test_static_placeholder_headers_and_traversal(env):
    c = Client(env.port)
    r, p = c.req("GET", "/")
    assert r.status == 200 and b"Client" in p
    assert "default-src 'self'" in r.getheader("Content-Security-Policy")
    assert r.getheader("X-Frame-Options") == "DENY"
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    ra, _ = c.req("GET", "/api/me")
    assert ra.getheader("Cache-Control") == "no-store"
    env.static.mkdir()
    (env.static / "app.js").write_text("var a=1;")
    (env.tmp / "outside.txt").write_text("nope")
    assert c.req("GET", "/app.js")[1] == b"var a=1;"
    for bad in ("/../outside.txt", "/%2e%2e/outside.txt", "/..%5coutside.txt",
                "/%2e%2e%5coutside.txt", "/nofile.js", "/C:/Windows/win.ini"):
        r, p = c.req("GET", bad)
        assert r.status in (400, 404) and b"nope" not in p, bad


def test_upload_rules(env):
    adm = Client(env.port).login("admin")
    st, body = adm.json("POST", "/api/upload?filename=clock.csv", raw=b"a,b\n1,2\n")
    assert st == 200 and body["status"] == "OK"
    assert (env.logs / "uploads" / "admin" / "clock.csv").read_bytes() == b"a,b\n1,2\n"
    assert adm.json("POST", "/api/upload?filename=clock.csv", raw=b"x")[0] == 400   # exists
    for bad in ("evil.exe", "x.pdf", "x.txt", "noext", "x.xlsx.bat"):
        assert adm.json("POST", f"/api/upload?filename={bad}", raw=b"x")[0] == 400, bad
    adm.json("POST", "/api/upload?filename=../../evil.csv", raw=b"x")
    assert not (env.logs.parent / "evil.csv").exists()
    boundary = "XBOUND"
    mp = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"m.xlsx\"\r\n"
          f"Content-Type: application/octet-stream\r\n\r\nDATA\r\n--{boundary}--\r\n").encode()
    st, body = adm.json("POST", "/api/upload", raw=mp,
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert st == 200 and (env.logs / "uploads" / "admin" / "m.xlsx").read_bytes() == b"DATA"


def test_upload_size_limit(env):
    adm = Client(env.port).login("admin")
    big = b"0" * (server.MAX_UPLOAD_BYTES + 1)
    st, _ = adm.json("POST", "/api/upload?filename=big.csv", raw=big)
    assert st == 413
    assert not (env.logs / "uploads" / "admin" / "big.csv").exists()


def test_no_secrets_in_logs_or_responses(env, capsys):
    c = Client(env.port)
    r, p = c.req("POST", "/api/login", {"username": "viewer", "password": PW})
    c.req("POST", "/api/login", {"username": "viewer", "password": "other-fake-pw"})
    c.login("viewer")
    rec = json.loads(auth.users_path(env.logs).read_text())["users"]["viewer"]
    c.req("GET", "/api/me?secret=queryvalue")
    log = (env.logs / "webapp" / "access.log").read_text()
    for needle in (PW, "other-fake-pw", rec["hash"], rec["salt"], "queryvalue", c.csrf):
        assert needle not in log
    for needle in (PW, rec["hash"], rec["salt"]):
        assert needle.encode() not in p
    assert "GET /api/me 200 viewer" in log
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_bad_host_header_refused(env):
    c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=10)
    c.request("GET", "/api/me", headers={"Host": "evil.example"})
    assert c.getresponse().status == 400
