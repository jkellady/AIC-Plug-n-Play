"""End-to-end wiring checks for the web app: real modules behind the real server.

Created 2026-10-18, version 1. Supersedes nothing (new file).
Everything runs against temporary folders; no staff data is read or written.
"""
import re
import sys
import types
from pathlib import Path

import pytest

import src.daily as daily
from src.webapp import auth, server
from tests.test_webapp_server import Client, PW, fake_module

STATIC = Path(__file__).resolve().parent.parent / "src" / "webapp" / "static"


@pytest.fixture
def real(tmp_path, monkeypatch):
    """Server wired to the REAL queries/help/agents modules, all folders temporary."""
    logs, reports, drop, md = (tmp_path / n for n in ("logs", "reports", "drop", "md"))
    for d in (reports, drop, md):
        d.mkdir()
    for role in auth.ROLES:
        auth.save_user(auth.users_path(logs), "master000" if role == "master" else role, role, PW)
    for name, val in (("LOGS_DIR", logs), ("REPORTS_DIR", reports),
                      ("DROP_DIR", drop), ("MASTER_DATA_DIR", md)):
        monkeypatch.setattr(daily, name, val)
    from src.webapp import queries
    queries.clear_cache()
    srv = server.start_in_thread(logs_dir=logs, reports_dir=reports,
                                 static_dir=server.Path(server.__file__).parent / "static")
    yield types.SimpleNamespace(port=srv.server_address[1], logs=logs, reports=reports)
    srv.shutdown()
    srv.server_close()
    queries.clear_cache()


def test_every_data_route_is_wired_not_503(real):
    """The 503 'not available yet' stub must never appear once all modules exist."""
    viewer, hrm, adm = (Client(real.port).login(r) for r in ("viewer", "hrm", "admin"))
    for path in ("/api/overview", "/api/reports", "/api/help?q=late"):
        for c in (viewer, hrm, adm):
            st, _ = c.json("GET", path)
            assert st == 200, (path, st)
    for path in ("/api/outlets", "/api/staff"):          # Admin screens
        assert adm.json("GET", path)[0] == 200, path
        assert hrm.json("GET", path)[0] == 403, path
        assert viewer.json("GET", path)[0] == 403, path
    assert hrm.json("GET", "/api/exceptions")[0] == 200
    assert adm.json("GET", "/api/exceptions")[0] == 200
    assert viewer.json("GET", "/api/exceptions")[0] == 403
    assert adm.json("GET", "/api/health")[0] == 200
    st, body = adm.json("POST", "/api/scan/run", {})
    assert st == 200 and "message" in body and body["status"] in ("OK", "NOTHING_NEW")


def test_real_empty_states_have_the_shape_the_screens_expect(real):
    hrm = Client(real.port).login("hrm")
    st, ov = hrm.json("GET", "/api/overview?week=2026-W20")
    assert st == 200 and set(ov) >= {"kpis", "by_outlet", "status", "week_label", "start", "end"}
    assert ov["by_outlet"] == [] and ov["kpis"]["attendance_rate"] is None
    assert hrm.json("GET", "/api/exceptions?week=2026-W20") == (200, [])
    assert hrm.json("GET", "/api/exceptions?week=nope")[0] == 400


def test_help_returns_a_list_with_title_and_snippet(real):
    st, hits = Client(real.port).login("viewer").json("GET", "/api/help?q=zzzqqqxx")
    assert st == 200 and isinstance(hits, list) and hits
    assert all("title" in h and "snippet" in h for h in hits)
    assert hits[0].get("no_match") is True


def test_exceptions_week_is_passed_to_queries(real, monkeypatch):
    seen = []
    fake_module(monkeypatch, "src.webapp.queries",
                exceptions=lambda s, o, w=None: seen.append((s, o, w)) or [])
    c = Client(real.port).login("hrm")
    assert c.json("GET", "/api/exceptions?status=Info&outlet=OUT4&week=2026-W20")[0] == 200
    assert c.json("GET", "/api/exceptions")[0] == 200
    assert seen == [("Info", "OUT4", "2026-W20"), (None, None, None)]


def _multipart(parts):
    b = "XBOUND"
    body = b"".join(
        (f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{n}"\r\n'
         "Content-Type: application/octet-stream\r\n\r\n").encode() + d + b"\r\n"
        for n, d in parts) + f"--{b}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={b}"}


def test_upload_saves_every_file_in_a_multi_file_form(real):
    adm = Client(real.port).login("admin")
    raw, hdr = _multipart([("a.csv", b"1"), ("b.xlsx", b"2")])
    st, body = adm.json("POST", "/api/upload", raw=raw, headers=hdr)
    assert st == 200 and body["saved"] == ["a.csv", "b.xlsx"] and "2 of 2" in body["message"]
    staging = real.logs / "uploads" / "admin"
    assert (staging / "a.csv").read_bytes() == b"1" and (staging / "b.xlsx").read_bytes() == b"2"
    # a repeat is refused with a readable message and nothing is overwritten
    st, body = adm.json("POST", "/api/upload", raw=raw, headers=hdr)
    assert st == 400 and "already exists" in body["message"]
    assert (staging / "a.csv").read_bytes() == b"1"


def test_upload_with_one_bad_type_saves_nothing(real):
    adm = Client(real.port).login("admin")
    raw, hdr = _multipart([("ok.csv", b"1"), ("bad.exe", b"2")])
    st, _ = adm.json("POST", "/api/upload", raw=raw, headers=hdr)
    assert st == 400
    assert not (real.logs / "uploads" / "admin" / "ok.csv").exists()


def test_scan_and_generate_return_a_message_for_the_screen(real, monkeypatch):
    fake_module(monkeypatch, "src.agents.drop_scanner",
                run=lambda: {"status": "OK", "scanned": 3, "new": 2, "alerts": ["watch out"]})
    fake_module(monkeypatch, "src.agents.dashboard",
                generate=lambda scope: {"status": "SKIPPED_EXISTS", "available": True,
                                        "window": "2026-W41"})
    adm = Client(real.port).login("admin")
    st, body = adm.json("POST", "/api/scan/run", {})
    assert st == 200 and "3 files" in body["message"] and "watch out" in body["message"]
    st, body = adm.json("POST", "/api/reports/generate", {"scope": "weekly"})
    assert st == 200 and "already exists" in body["message"]


def test_main_accepts_port_flag_used_by_launcher(monkeypatch):
    got = {}

    class Fake:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            got["closed"] = True

    monkeypatch.setattr(server, "create_server",
                        lambda host=server.HOST, port=server.DEFAULT_PORT, **k: got.update(port=port) or Fake())
    assert server.main(["--port", "9123"]) == 0
    assert got == {"port": 9123, "closed": True}
    server.main([])
    assert got["port"] == server.DEFAULT_PORT


# -- static front end vs server contract --------------------------------------

def test_app_js_has_no_control_characters():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", js)


def test_front_end_report_scopes_match_the_server():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "value: 'weekly'" in js and "value: 'monthly'" in js
    assert "value: 'week'," not in js and "value: 'month'," not in js


def test_front_end_handles_covered_object_and_passes_week():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "coveredText(" in js and "o.covered ? 'Clock files found: ' + o.covered" not in js
    assert js.count("qs({ week: state.week })") >= 2
    assert "api('/api/exceptions')" not in js


def test_service_worker_is_network_first_for_the_shell():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "return hit || net" not in sw and "shell-v1" not in sw


# -- opt-in office network (LAN) mode ------------------------------------------

@pytest.mark.parametrize("ip,ok", [("127.0.0.1", True), ("10.1.2.3", True), ("172.16.0.5", True),
                                   ("172.31.255.1", True), ("192.168.1.20", True),
                                   ("172.32.0.1", False), ("8.8.8.8", False), ("100.64.0.1", False),
                                   ("::1", False), ("not-an-ip", False), ("", False)])
def test_private_ip_allow_list(ip, ok):
    assert server.is_private_ip(ip) is ok


def test_lan_is_off_by_default(real, monkeypatch):
    monkeypatch.delenv("AIC_LAN", raising=False)
    assert server.lan_requested(real.logs) is False
    assert server.lan_requested(real.logs, argv_flag=True) is True
    adm = Client(real.port).login("admin")
    st, info = adm.json("GET", "/api/lan-info")
    assert st == 200 and info["running"] is False and info["setting"] is False and info["url"] is None
    with pytest.raises(ValueError):
        server.create_server(host="0.0.0.0", port=0)
    with pytest.raises(ValueError):
        server.create_server(host="192.168.1.5", port=0)


def test_lan_marker_and_env_switch_it_on(tmp_path, monkeypatch):
    monkeypatch.delenv("AIC_LAN", raising=False)
    (tmp_path / "webapp").mkdir()
    (tmp_path / "webapp" / "lan.enabled").write_text("on")
    assert server.lan_requested(tmp_path) is True
    (tmp_path / "webapp" / "lan.enabled").unlink()
    monkeypatch.setenv("AIC_LAN", "1")
    assert server.lan_requested(tmp_path) is True


def test_lan_refuses_non_private_bind():
    with pytest.raises(ValueError):
        server.create_server(host="8.8.8.8", port=0, lan=True)


@pytest.fixture
def lan_srv(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    for role in auth.ROLES:
        auth.save_user(auth.users_path(logs), "master000" if role == "master" else role, role, PW)
    monkeypatch.setattr(daily, "LOGS_DIR", logs)
    monkeypatch.setattr(server, "detect_lan_ip", lambda: "127.0.0.1")   # stand-in LAN address
    srv = server.start_in_thread(logs_dir=logs, reports_dir=tmp_path, static_dir=tmp_path, lan=True)
    yield types.SimpleNamespace(port=srv.server_address[1], srv=srv, logs=logs)
    srv.shutdown()
    srv.server_close()


def test_lan_mode_reports_state_and_requires_login(lan_srv):
    c = Client(lan_srv.port)
    assert c.json("GET", "/api/overview")[0] == 401
    assert c.json("GET", "/api/staff")[0] == 401
    adm = Client(lan_srv.port).login("admin")
    st, info = adm.json("GET", "/api/lan-info")
    assert info["running"] is True and info["url"].startswith("http://127.0.0.1:") and info["warning"]


def test_host_header_must_be_a_literal_allowed_address(lan_srv):
    c = Client(lan_srv.port)
    for bad in ("evil.example.com", "evil.example.com:8765", "192.168.9.9", ""):
        r, _ = c.req("GET", "/api/me", headers={"Host": bad})
        assert r.status == 400, bad
    r, _ = c.req("GET", "/api/me", headers={"Host": "127.0.0.1:1"})
    assert r.status == 401


def test_client_outside_private_ranges_gets_403(lan_srv, monkeypatch):
    monkeypatch.setattr(server, "is_private_ip", lambda ip: False)
    r, _ = Client(lan_srv.port).req("GET", "/")
    assert r.status == 403
    r, _ = Client(lan_srv.port).req("POST", "/api/login", {"username": "a", "password": "b"})
    assert r.status == 403


def test_per_ip_login_lockout_spans_usernames(lan_srv):
    c = Client(lan_srv.port)
    for i in range(server.IP_MAX_FAILURES):
        r, _ = c.req("POST", "/api/login", {"username": f"nobody{i}", "password": "wrong-pw-123"})
        assert r.status == 401
    # the real, correct account is now refused from this address
    r, _ = c.req("POST", "/api/login", {"username": "admin", "password": PW})
    assert r.status == 429


def test_access_log_has_ip_role_route_but_no_query(lan_srv):
    adm = Client(lan_srv.port).login("admin")
    adm.json("GET", "/api/help?q=secretname")
    log = (lan_srv.logs / "webapp" / "access.log").read_text()
    assert "127.0.0.1" in log and "/api/help" in log and "secretname" not in log


def test_only_master_can_switch_lan_with_own_password(real):
    adm = Client(real.port).login("master")
    assert Client(real.port).login("admin").json("POST", "/api/lan-set", {"enabled": True, "password": PW})[0] == 403
    marker = real.logs / "webapp" / "lan.enabled"
    st, _ = adm.json("POST", "/api/lan-set", {"enabled": True, "password": "wrong-password-1"})
    assert st == 403 and not marker.exists()
    st, body = adm.json("POST", "/api/lan-set", {"enabled": True, "password": PW})
    assert st == 200 and marker.exists() and body["setting"] is True and body["restart_needed"] is True
    st, body = adm.json("POST", "/api/lan-set", {"enabled": False, "password": PW})
    assert st == 200 and not marker.exists()
    assert adm.json("POST", "/api/lan-set", {"enabled": "yes", "password": PW})[0] == 400
    for role in ("viewer", "hrm"):
        assert Client(real.port).login(role).json("GET", "/api/lan-info")[0] == 403
        assert Client(real.port).login(role).json("POST", "/api/lan-set", {"enabled": True, "password": PW})[0] == 403


def test_login_page_warns_when_not_local():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="lan-warning"' in html and "lan-warning" in js


# -- change password / first login ---------------------------------------------

def test_first_login_forces_password_change(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    auth.save_user(auth.users_path(logs), "viewer", "viewer", "Temp-pass-0001", must_change=True)
    monkeypatch.setattr(daily, "LOGS_DIR", logs)
    srv = server.start_in_thread(logs_dir=logs, reports_dir=tmp_path, static_dir=tmp_path)
    try:
        c = Client(srv.server_address[1])
        r, p = c.req("POST", "/api/login", {"username": "viewer", "password": "Temp-pass-0001"})
        assert r.status == 200
        import json as _j
        assert _j.loads(p)["must_change"] is True
        c.cookie, c.csrf = r.getheader("Set-Cookie").split(";")[0], _j.loads(p)["csrf"]
        assert c.json("GET", "/api/me")[1]["must_change"] is True
        assert c.json("GET", "/api/overview")[0] == 403          # blocked until changed
        assert c.json("POST", "/api/password", {"old": "wrong", "new": "Brand-new-pass-1"})[0] == 403
        assert c.json("POST", "/api/password", {"old": "Temp-pass-0001", "new": "short"})[0] == 400
        assert c.json("POST", "/api/password", {"old": "Temp-pass-0001", "new": "Temp-pass-0001"})[0] == 400
        assert c.json("POST", "/api/password", {"old": "Temp-pass-0001", "new": "Brand-new-pass-1"})[0] == 200
        assert c.json("GET", "/api/me")[1]["must_change"] is False
        assert c.json("GET", "/api/overview")[0] != 403
        assert "must_change" not in auth.load_users(auth.users_path(logs))["viewer"]
        # old password no longer works, new one does
        assert Client(srv.server_address[1]).req("POST", "/api/login", {"username": "viewer", "password": "Temp-pass-0001"})[0].status == 401
        assert Client(srv.server_address[1]).req("POST", "/api/login", {"username": "viewer", "password": "Brand-new-pass-1"})[0].status == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_change_password_ends_other_sessions_and_needs_csrf(real):
    a, b = Client(real.port).login("hrm"), Client(real.port).login("hrm")
    assert a.json("POST", "/api/password", {"old": PW, "new": "Another-good-pw-2"},
                  headers={"X-CSRF-Token": "bad"})[0] == 403
    assert a.json("POST", "/api/password", {"old": PW, "new": "Another-good-pw-2"})[0] == 200
    assert a.json("GET", "/api/me")[0] == 200
    assert b.json("GET", "/api/me")[0] == 401


def test_front_end_has_change_password_screen():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "/api/password" in js and "must_change" in js and "vSettings" in js and "vPassword" not in js
