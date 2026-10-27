"""Roles, user admin, rules editing, directory records, logs, report view, IT, S1/S2/S7/S11.

Created 2026-10-19, version 1. Supersedes nothing (new file).
Temporary folders only. Fake passwords only. Staff are referred to by ID.
"""
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

import src.daily as daily
from src.webapp import auth, registry, rules_edit, server
from tests.test_webapp_server import Client, PW, fake_module

ROOT = Path(__file__).resolve().parent.parent
REAL_RULES = ROOT / "03_MASTER_DATA" / "rules.toml"
NEW_PW = "Another-fake-pw-77"


@pytest.fixture
def env(tmp_path, monkeypatch):
    logs, reports, md = tmp_path / "logs", tmp_path / "reports", tmp_path / "md"
    (reports / "2026-05").mkdir(parents=True)
    md.mkdir()
    shutil.copy(REAL_RULES, md / "rules.toml")
    for role in auth.ROLES:
        auth.save_user(auth.users_path(logs), "master000" if role == "master" else role, role, PW)
    monkeypatch.setattr(daily, "LOGS_DIR", logs)
    monkeypatch.setattr(daily, "MASTER_DATA_DIR", md)
    srv = server.start_in_thread(logs_dir=logs, reports_dir=reports, static_dir=tmp_path / "static",
                                 rules_path=md / "rules.toml")
    yield types.SimpleNamespace(srv=srv, port=srv.server_address[1], logs=logs, reports=reports,
                                md=md, tmp=tmp_path)
    srv.shutdown()
    srv.server_close()


def login(env, role):
    return Client(env.port).login(role)


# -- permission table --------------------------------------------------------

def test_permission_table_least_privilege():
    assert not auth.can("viewer", "report_workbook") and auth.can("hrm", "report_workbook")
    assert auth.can("viewer", "report_review") and auth.can("viewer", "rules_view")
    for perm in ("staff_edit", "outlets_edit"):
        assert auth.PERMS[perm] == {"master", "admin"}
    assert auth.PERMS["rules_edit"] == {"master", "ceo"}
    assert auth.PERMS["lan_set"] == {"master"}
    assert auth.PERMS["users_manage"] == {"master", "ceo", "hrm"}
    assert "master" not in sum((list(v) for v in auth.ASSIGNABLE.values()), [])


def test_every_route_uses_a_known_permission():
    for (_m, _p), (perm, _fn) in server.ROUTES.items():
        assert perm in (None, "any") or perm in auth.PERMS, perm


def test_me_lists_permissions(env):
    st, me = login(env, "ceo").json("GET", "/api/me")
    assert me["role_label"] == "CEO" and "rules_edit" in me["perms"] and "staff_edit" not in me["perms"]
    st, me = login(env, "master").json("GET", "/api/me")
    assert me["is_master"] and me["username"] == "master000"


# -- migration -----------------------------------------------------------------

def test_old_admin_record_for_master000_becomes_master(tmp_path):
    path = auth.users_path(tmp_path)
    path.parent.mkdir(parents=True)
    rec = auth.hash_password(PW)
    rec["role"] = "admin"
    other = dict(rec, role="master")          # a second 'master' is demoted
    path.write_text(json.dumps({"users": {"master000": rec, "someone": other, "viewer": dict(rec, role="viewer")}}))
    users = auth.load_users(path)
    assert users["master000"]["role"] == "master"
    assert users["someone"]["role"] == "admin" and users["viewer"]["role"] == "viewer"
    assert auth.AuthManager(path).login("master000", PW)[0] == "ok"
    saved = json.loads(path.read_text())["users"]      # hash kept, nothing lost
    assert saved["master000"]["hash"] == rec["hash"] and "last_login" in saved["master000"]


def test_master_role_only_for_master_username(tmp_path):
    with pytest.raises(ValueError):
        auth.save_user(auth.users_path(tmp_path), "someone", "master", PW)


# -- users tab -------------------------------------------------------------------

def test_user_list_has_no_secrets_and_shows_status(env):
    st, body = login(env, "ceo").json("GET", "/api/users")
    assert st == 200
    text = json.dumps(body)
    raw = json.loads(auth.users_path(env.logs).read_text())["users"]["viewer"]
    for needle in (raw["hash"], raw["salt"], PW, '"hash"', '"salt"'):
        assert needle not in text
    row = next(u for u in body["users"] if u["username"] == "viewer")
    assert row["password_set"] is True and row["role"] == "viewer" and "created_at" in row
    assert next(u for u in body["users"] if u["is_master"])["can_manage"] is False


@pytest.mark.parametrize("actor,allowed", [("master", {"ceo", "admin", "hrm", "viewer"}),
                                           ("ceo", {"admin", "hrm", "viewer"}), ("hrm", {"admin", "viewer"})])
def test_who_may_create_which_role(env, actor, allowed):
    c = login(env, actor)
    for i, role in enumerate(auth.ROLES):
        st, _ = c.json("POST", "/api/users/add", {"username": f"new{i}", "role": role, "password": NEW_PW})
        assert (st == 200) == (role in allowed), (actor, role, st)


def test_others_cannot_add_users(env):
    for role in ("viewer", "admin"):
        assert login(env, role).json("POST", "/api/users/add",
                                     {"username": "x1", "role": "viewer", "password": NEW_PW})[0] == 403


def test_new_user_must_change_password_and_creator_recorded(env):
    c = login(env, "hrm")
    assert c.json("POST", "/api/users/add", {"username": "new1", "role": "viewer", "password": NEW_PW,
                                             "display_name": "Person One"})[0] == 200
    rec = auth.load_users(auth.users_path(env.logs))["new1"]
    assert rec["must_change"] and rec["created_by"] == "hrm" and rec["created_at"]
    assert c.json("POST", "/api/users/add", {"username": "new1", "role": "viewer", "password": NEW_PW})[0] == 409
    assert c.json("POST", "/api/users/add", {"username": "new2", "role": "viewer", "password": "short"})[0] == 400
    nc = Client(env.port)
    r, p = nc.req("POST", "/api/login", {"username": "new1", "password": NEW_PW})
    assert r.status == 200 and json.loads(p)["must_change"] is True


def test_reset_password_form_sets_initial_password_never_shows_it(env):
    c = login(env, "ceo")
    st, _ = c.json("POST", "/api/users/update", {"username": "viewer", "password": NEW_PW})
    assert st == 200
    assert Client(env.port).req("POST", "/api/login", {"username": "viewer", "password": PW})[0].status == 401
    r, p = Client(env.port).req("POST", "/api/login", {"username": "viewer", "password": NEW_PW})
    assert r.status == 200 and json.loads(p)["must_change"] is True
    assert NEW_PW not in (env.logs / "webapp" / "access.log").read_text()
    assert c.json("POST", "/api/users/update", {"username": "viewer", "password": "short"})[0] == 400


def test_master_account_is_protected(env):
    for actor in ("ceo", "hrm"):
        c = login(env, actor)
        for path, body in (("/api/users/update", {"username": "master000", "password": NEW_PW}),
                           ("/api/users/update", {"username": "master000", "display_name": "x"}),
                           ("/api/users/update", {"username": "master000", "role": "viewer"}),
                           ("/api/users/delete", {"username": "master000"})):
            assert c.json("POST", path, body)[0] == 403, (actor, path)
    m = login(env, "master")
    assert m.json("POST", "/api/users/delete", {"username": "master000"})[0] == 403    # not even self
    assert m.json("POST", "/api/users/update", {"username": "master000", "role": "viewer"})[0] == 403
    assert auth.load_users(auth.users_path(env.logs))["master000"]["role"] == "master"


def test_cannot_manage_same_or_higher_role_or_self(env):
    hrm = login(env, "hrm")
    assert hrm.json("POST", "/api/users/update", {"username": "ceo", "password": NEW_PW})[0] == 403
    assert hrm.json("POST", "/api/users/update", {"username": "hrm", "role": "admin"})[0] == 403   # self
    assert hrm.json("POST", "/api/users/delete", {"username": "hrm"})[0] == 403
    assert hrm.json("POST", "/api/users/update", {"username": "viewer", "role": "ceo"})[0] == 403      # escalation
    assert login(env, "ceo").json("POST", "/api/users/delete", {"username": "viewer"})[0] == 200


def test_only_master_edits_own_name(env):
    m = login(env, "master")
    assert m.json("POST", "/api/profile", {"display_name": "JK Zentra"})[0] == 200
    assert auth.load_users(auth.users_path(env.logs))["master000"]["display_name"] == "JK Zentra"
    assert m.json("GET", "/api/me")[1]["display_name"] == "JK Zentra"
    for role in ("ceo", "hrm", "admin", "viewer"):
        assert login(env, role).json("POST", "/api/profile", {"display_name": "x"})[0] == 403
    assert m.json("POST", "/api/profile", {"display_name": ""})[0] == 400


# -- rules ---------------------------------------------------------------------------

def rule_ids(env):
    return {r["id"] for r in rules_edit.list_rules(env.md / "rules.toml")}


def test_rules_table_visible_to_all_editable_by_master_and_ceo(env):
    for role in auth.ROLES:
        st, rows = login(env, role).json("GET", "/api/rules")
        assert st == 200 and len(rows) >= 20
    row = next(r for r in rows if r["id"] == "late_grace_minutes")
    assert row["status"] == "CONFIRMED" and row["value"] == 15
    body = {"id": "late_grace_minutes", "fields": {"value": 20, "source": "Test source"}}
    for role in ("admin", "hrm", "viewer"):
        assert login(env, role).json("POST", "/api/rules/update", body)[0] == 403
    assert login(env, "ceo").json("POST", "/api/rules/update", body)[0] == 200


def test_rule_edit_is_validated_audited_and_leaves_the_rest_alone(env):
    path = env.md / "rules.toml"
    before = path.read_text(encoding="utf-8")
    m = login(env, "master")
    st, _ = m.json("POST", "/api/rules/update", {"id": "late_grace_minutes", "fields": {"value": 20}})
    assert st == 200
    after = path.read_text(encoding="utf-8")
    assert after.startswith(before.split("[[changes]]")[0][:2000])           # comments and order kept
    row = next(r for r in rules_edit.list_rules(path) if r["id"] == "late_grace_minutes")
    assert row["value"] == 20 and row["edited_by"] == "master000" and row["edited_at"]
    assert "Web app edit by master000" in after
    audit = (env.logs / "webapp" / "rules_audit.csv").read_text()
    assert "late_grace_minutes" in audit and "master000" in audit
    assert list((env.logs / "webapp" / "rules_backups").glob("rules_*.toml"))
    from src import rules as rules_mod
    assert rules_mod._check(path) == 0                                          # `src.rules check`
    others = {r["id"]: r["value"] for r in rules_edit.list_rules(path) if r["id"] != "late_grace_minutes"}
    old = {r["id"]: r["value"] for r in rules_edit.list_rules(REAL_RULES) if r["id"] != "late_grace_minutes"}
    assert others == old


def test_rule_edit_refusals_keep_file_unchanged(env):
    path = env.md / "rules.toml"
    before = path.read_bytes()
    m = login(env, "master")
    cases = [
        {"id": "late_grace_minutes", "fields": {"value": "text"}},                 # wrong kind
        {"id": "late_grace_minutes", "fields": {"status": "MAYBE"}},               # bad status
        {"id": "late_grace_minutes", "fields": {"used_in": "x"}},                  # not editable
        {"id": "late_grace_minutes", "fields": {"source": "a\nb"}},                # newline
        {"id": "nope_rule", "fields": {"value": 1}},                               # unknown
        {"id": "late_grace_minutes", "fields": {}},
    ]
    for body in cases:
        assert m.json("POST", "/api/rules/update", body)[0] == 400, body
    assumed = next(r["id"] for r in rules_edit.list_rules(path) if r["status"] == "ASSUMED")
    st, body = m.json("POST", "/api/rules/update", {"id": assumed, "fields": {"status": "CONFIRMED", "source": ""}})
    cur = next(r for r in rules_edit.list_rules(path) if r["id"] == assumed)
    if not cur["source"]:
        assert st == 400 and "source" in body["error"]                              # CONFIRMED needs a source
    assert path.read_bytes() == before


def test_rule_status_change_to_confirmed_with_source(env):
    path = env.md / "rules.toml"
    rid = next(r["id"] for r in rules_edit.list_rules(path) if r["status"] == "ASSUMED")
    st, _ = login(env, "ceo").json("POST", "/api/rules/update",
                                   {"id": rid, "fields": {"status": "CONFIRMED", "source": "Test: Client reply"}})
    assert st == 200
    row = next(r for r in rules_edit.list_rules(path) if r["id"] == rid)
    assert row["status"] == "CONFIRMED" and row["confirmed_on"]
    from src import rules as rules_mod
    assert rules_mod._check(path) == 0


def test_rule_add(env):
    path = env.md / "rules.toml"
    m = login(env, "master")
    body = {"id": "test_new_rule", "fields": {"label": "A test rule", "value": 5, "status": "OPEN", "question": "Q99"}}
    assert m.json("POST", "/api/rules/add", body)[0] == 200
    assert m.json("POST", "/api/rules/add", body)[0] == 400                          # exists
    assert m.json("POST", "/api/rules/add", {"id": "Bad Id", "fields": {"label": "x"}})[0] == 400
    assert m.json("POST", "/api/rules/add", {"id": "no_label", "fields": {}})[0] == 400
    row = next(r for r in rules_edit.list_rules(path) if r["id"] == "test_new_rule")
    assert row["status"] == "OPEN" and row["created_by"] == "master000" and row["used_in"] == "not built yet"
    from src import rules as rules_mod
    assert rules_mod._check(path) == 0
    assert login(env, "admin").json("POST", "/api/rules/add", body)[0] == 403


# -- staff / outlets directory --------------------------------------------------------

@pytest.fixture
def fake_master(monkeypatch):
    fake_module(monkeypatch, "src.webapp.queries",
                staff=lambda: [{"id": "S001", "name": "Staff One", "outlet": "O1", "status": "Active"}],
                outlets=lambda: [{"outlet": "O1", "staff_count": 1}], clear_cache=lambda: None)


def test_staff_and_outlets_admin_only_with_audit_fields(env, fake_master):
    adm = login(env, "admin")
    assert adm.json("POST", "/api/staff/save", {"create": True, "id": "S900", "name": "Staff Nine", "outlet": "O1"})[0] == 200
    st, rows = adm.json("GET", "/api/staff")
    new = next(r for r in rows if r["id"] == "S900")
    assert new["created_by"] == "admin" and new["created_at"] and new["source"] == "added in app"
    assert adm.json("POST", "/api/staff/save", {"id": "S900", "name": "Staff Nine B", "status": "On leave"})[0] == 200
    new = next(r for r in adm.json("GET", "/api/staff")[1] if r["id"] == "S900")
    assert new["edited_by"] == "admin" and new["edited_at"] and new["name"] == "Staff Nine B"
    assert adm.json("POST", "/api/staff/save", {"create": True, "id": "S900", "name": "x"})[0] == 400   # exists
    assert adm.json("POST", "/api/staff/save", {"create": True, "id": "S001", "name": "x"})[0] == 400   # master row
    assert adm.json("POST", "/api/staff/save", {"id": "S001", "name": "Staff One B"})[0] == 200          # override
    one = next(r for r in adm.json("GET", "/api/staff")[1] if r["id"] == "S001")
    assert one["name"] == "Staff One B" and one["source"] == "master data + edit"
    assert adm.json("POST", "/api/staff/save", {"create": True, "id": "../x", "name": "x"})[0] == 400
    assert adm.json("POST", "/api/outlets/save", {"create": True, "id": "O9", "name": "Outlet Nine"})[0] == 200
    outs = adm.json("GET", "/api/outlets")[1]
    o9 = next(o for o in outs if o["id"] == "O9")
    assert o9["created_by"] == "admin" and o9["created_at"]
    assert adm.json("POST", "/api/outlets/save", {"id": "O9", "name": "Outlet Nine B"})[0] == 200
    assert next(o for o in adm.json("GET", "/api/outlets")[1] if o["id"] == "O9")["edited_by"] == "admin"
    for role in ("hrm", "viewer"):
        c = login(env, role)
        assert c.json("GET", "/api/staff")[0] == 403 and c.json("GET", "/api/outlets")[0] == 403
        assert c.json("POST", "/api/staff/save", {"id": "S001", "name": "x"})[0] == 403
    assert login(env, "ceo").json("GET", "/api/staff")[0] == 200
    assert login(env, "ceo").json("POST", "/api/staff/save", {"id": "S001", "name": "x"})[0] == 403


def test_registry_never_touches_master_data(env, fake_master):
    (env.md / "staff_aliases.csv").write_text("id_initials\nS001\n")
    before = (env.md / "staff_aliases.csv").read_bytes()
    login(env, "admin").json("POST", "/api/staff/save", {"id": "S001", "name": "Edited"})
    assert (env.md / "staff_aliases.csv").read_bytes() == before
    assert (env.logs / "webapp" / "registry.json").is_file()


# -- reports (S2, in-app view) ------------------------------------------------------

def test_reports_view_and_download_by_role(env):
    (env.reports / "2026-05" / "Review_May-26.html").write_text("<h1>Review</h1><script>x()</script>")
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Flags"
    wb.active.append(["Person", "Flag"])
    wb.active.append(["S001", "Late"])
    wb.save(env.reports / "2026-05" / "TRIAL_x_2026-05.xlsx")
    for path in ("/api/reports/download?path=2026-05/TRIAL_x_2026-05.xlsx",
                 "/api/reports/view?path=2026-05/TRIAL_x_2026-05.xlsx"):
        assert login(env, "viewer").json("GET", path)[0] == 403            # S2: no per-person workbook for Viewer
        assert login(env, "hrm").req("GET", path)[0].status == 200
    viewer = login(env, "viewer")
    r, p = viewer.req("GET", "/api/reports/view?path=2026-05/Review_May-26.html")
    assert r.status == 200 and b"Review" in p
    csp = r.getheader("Content-Security-Policy")
    assert "sandbox" in csp and "script-src" not in csp and "default-src 'none'" in csp
    assert r.getheader("X-Frame-Options") == "SAMEORIGIN"
    st, body = login(env, "hrm").json("GET", "/api/reports/view?path=2026-05/TRIAL_x_2026-05.xlsx")
    assert body["sheets"][0]["rows"] == [["Person", "Flag"], ["S001", "Late"]]
    assert viewer.req("GET", "/api/reports/download?path=2026-05/Review_May-26.html")[0].status == 200
    assert viewer.json("GET", "/api/reports/view?path=../x.html")[0] == 404
    assert viewer.json("GET", "/api/reports/view?path=2026-05/a.txt")[0] == 400


def test_reports_list_marks_what_each_role_may_open(env, monkeypatch):
    fake_module(monkeypatch, "src.webapp.queries", reports=lambda: [
        {"name": "a.xlsx", "path": "2026-05/a.xlsx"}, {"name": "Review_a.html", "path": "2026-05/Review_a.html"}])
    rows = login(env, "viewer").json("GET", "/api/reports")[1]
    assert [r["can_open"] for r in rows] == [False, True]
    assert [r["can_open"] for r in login(env, "hrm").json("GET", "/api/reports")[1]] == [True, True]


# -- logs ---------------------------------------------------------------------------

def test_logs_date_range_view_download_and_deny_list(env):
    import os
    d = env.logs
    (d / "webapp").mkdir(parents=True, exist_ok=True)
    (d / "run_log.csv").write_text("a,b\n1,2\n")
    (d / "old.log").write_text("old line")
    os.utime(d / "old.log", (1_700_000_000, 1_700_000_000))        # 2023-11-14
    (d / "webapp" / "FIRST_LOGIN.txt").write_text("secret note")
    (d / "webapp" / "registry.json").write_text("{}")
    (d / "uploads").mkdir()
    (d / "uploads" / "clock.csv").write_text("raw")
    (d / "edge-profile").mkdir()
    (d / "edge-profile" / "x.log").write_text("noise")
    adm = login(env, "admin")
    names = {f["path"] for f in adm.json("GET", "/api/logs")[1]}
    assert {"run_log.csv", "old.log", "webapp/access.log"} <= names or {"run_log.csv", "old.log"} <= names
    for hidden in ("webapp/users.json", "webapp/FIRST_LOGIN.txt", "webapp/registry.json",
                   "uploads/clock.csv", "edge-profile/x.log"):
        assert hidden not in names
        assert adm.json("GET", "/api/logs/view?path=" + hidden)[0] == 404
        assert adm.json("GET", "/api/logs/download?path=" + hidden)[0] == 404
    only_old = {f["path"] for f in adm.json("GET", "/api/logs?start=2023-11-01&end=2023-11-30")[1]}
    assert only_old == {"old.log"}
    assert adm.json("GET", "/api/logs?start=bad")[0] == 400
    assert adm.json("GET", "/api/logs/view?path=old.log")[1]["content"] == "old line"
    r, p = adm.req("GET", "/api/logs/download?path=old.log")
    assert r.status == 200 and p == b"old line" and "attachment" in r.getheader("Content-Disposition")
    assert adm.json("GET", "/api/logs/view?path=../secret.log")[0] == 404
    for role in ("viewer", "hrm", "ceo"):
        assert login(env, role).json("GET", "/api/logs")[0] == 403


# -- IT diagnosis / repair ---------------------------------------------------------------

def test_it_endpoints_gated_and_repair_needs_password(env, monkeypatch):
    calls = []

    def fake_run(**kw):
        calls.append(kw)
        return {"status": "OK", "overall": "PASS", "counts": {"PASS": 1}, "results": [
            {"check": "disk", "status": "PASS", "detail": "ok"}], "incidents": []}
    core = types.ModuleType("src.agents.it_specialist.core")
    core.run = fake_run
    monkeypatch.setitem(sys.modules, "src.agents.it_specialist.core", core)
    adm = login(env, "admin")
    st, body = adm.json("POST", "/api/it/diagnose", {})
    assert st == 200 and body["results"][0]["check"] == "disk" and calls[-1]["mode"] == "health"
    assert adm.json("POST", "/api/it/propose", {})[0] == 200 and calls[-1]["approve"] is False
    assert adm.json("POST", "/api/it/repair", {"confirm": True, "password": "wrong-password-1"})[0] == 403
    assert adm.json("POST", "/api/it/repair", {"password": PW})[0] == 400          # no confirm
    n = len(calls)
    assert adm.json("POST", "/api/it/repair", {"confirm": True, "password": PW})[0] == 200
    assert calls[n]["approve"] is True and len(calls) == n + 1
    for role in ("viewer", "hrm", "ceo"):
        c = login(env, role)
        for path in ("/api/it/diagnose", "/api/it/propose", "/api/it/repair"):
            assert c.json("POST", path, {"confirm": True, "password": PW})[0] == 403


# -- LAN, S1, S7, S11 ---------------------------------------------------------------------------

def test_lan_switch_is_master_only_and_ceo_can_look(env):
    assert login(env, "ceo").json("GET", "/api/lan-info")[1]["can_change"] is False
    assert login(env, "master").json("GET", "/api/lan-info")[1]["can_change"] is True
    assert login(env, "admin").json("POST", "/api/lan-set", {"enabled": True, "password": PW})[0] == 403
    assert login(env, "viewer").json("GET", "/api/lan-info")[0] == 403


def test_s1_post_needs_same_origin_and_json_content_type(env):
    c = Client(env.port)
    body = {"username": "viewer", "password": PW}
    st, _ = c.json("POST", "/api/login", body, headers={"Origin": "http://evil.example"})
    assert st == 403
    st, _ = c.json("POST", "/api/login", body, headers={"Origin": f"http://127.0.0.1:{env.port + 1}"})
    assert st == 403
    r, _ = c.req("POST", "/api/login", raw=json.dumps(body).encode(), headers={"Origin": f"http://127.0.0.1:{env.port}"})
    assert r.status == 415                                                       # no JSON content type
    r, _ = c.req("POST", "/api/login", raw=json.dumps(body).encode(),
                 headers={"Content-Type": "text/plain", "Origin": f"http://localhost:{env.port}"})
    assert r.status == 415
    r, _ = c.req("POST", "/api/login", raw=b"{}", headers={"Content-Type": "application/json", "Origin": ""})
    assert r.status == 403                                                       # neither Origin nor Fetch-Site
    r, _ = c.req("POST", "/api/login", body, headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    assert r.status == 403                                                       # Origin present and wrong wins
    r, _ = c.req("POST", "/api/login", body, headers={"Sec-Fetch-Site": "same-origin", "Origin": ""})
    assert r.status == 200
    bad = c.json("POST", "/api/login", body, headers={"Origin": "http://evil.example"})
    assert bad[0] == 403
    # a cross-site page cannot burn the lockout counter
    for _ in range(6):
        Client(env.port).json("POST", "/api/login", {"username": "hrm", "password": "x"},
                              headers={"Origin": "http://evil.example"})
    assert Client(env.port).login("hrm")


def test_s7_main_accepts_port(monkeypatch, tmp_path):
    seen = {}

    class Fake:
        server_address = ("127.0.0.1", 0)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    def fake_create(host=None, port=None, **kw):
        seen["port"] = port
        return Fake()
    monkeypatch.setattr(server, "create_server", fake_create)
    monkeypatch.setattr(server, "lan_requested", lambda *a, **k: False)
    assert server.main(["--port", "9123"]) == 0 and seen["port"] == 9123 and seen["closed"]


def test_s11_generate_failure_has_no_exception_text(env, monkeypatch):
    fake_module(monkeypatch, "src.agents.dashboard",
                generate=lambda scope: {"status": "FAILED", "available": True,
                                        "detail": "KeyError: 'sheet S001 Staff Name'"})
    st, body = login(env, "admin").json("POST", "/api/reports/generate", {"scope": "weekly"})
    assert st == 200 and "KeyError" not in json.dumps(body) and "S001" not in json.dumps(body)


def test_server_error_text_is_generic(env, monkeypatch):
    def boom():
        raise RuntimeError("secret detail S001")
    fake_module(monkeypatch, "src.webapp.queries", staff=boom)
    st, body = login(env, "admin").json("GET", "/api/staff")
    assert st == 500 and body == {"error": "server error"}


def test_guides_have_document_control_and_permission_table():
    md = (ROOT / "docs" / "WEBAPP_GUIDE.md").read_text(encoding="utf-8")
    html = (ROOT / "00_START_HERE" / "WebApp_Guide.html").read_text(encoding="utf-8")
    for text in (md, html):
        assert "Date of creation" in text and "Version" in text and "Supersedes" in text
    for label in auth.ROLE_LABELS.values():
        assert label in md and label in html
    assert "Rules: add / edit | yes | yes | no | no | no" in md       # matches PERMS["rules_edit"]


def test_open_folder_fixed_list_roles_and_local_only(env, monkeypatch):
    opened = []
    monkeypatch.setattr(server, "open_folder", lambda p: opened.append(Path(p)))
    drop = env.tmp / "drop"
    drop.mkdir()
    monkeypatch.setattr(daily, "DROP_DIR", drop)
    adm = login(env, "admin")
    for which, expect in (("reports", env.reports), ("logs", env.logs), ("drop", drop)):
        assert adm.json("POST", "/api/open-folder", {"which": which})[0] == 200
        assert opened[-1] == expect
    n = len(opened)
    for bad in ("../x", "C:/Windows", "", None, "uploads"):
        assert adm.json("POST", "/api/open-folder", {"which": bad})[0] == 400
    assert len(opened) == n
    for role in ("viewer", "hrm", "ceo"):
        assert login(env, role).json("POST", "/api/open-folder", {"which": "logs"})[0] == 403
    assert login(env, "master").json("POST", "/api/open-folder", {"which": "logs"})[0] == 200


# -- forgot password, security view, audit ---------------------------------------------------

def test_login_page_has_forgot_password_steps():
    html = (ROOT / "src" / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Forgot password?" in html and "set_user_password.py" in html


def test_security_view_master_only_and_no_secrets(env):
    m = login(env, "master")
    st, body = m.json("GET", "/api/users/security")
    assert st == 200
    raw = json.loads(auth.users_path(env.logs).read_text())["users"]["viewer"]
    text = json.dumps(body)
    for needle in (raw["hash"], raw["salt"], PW, '"salt"', '"hash"'):
        assert needle not in text
    row = next(u for u in body["users"] if u["username"] == "viewer")
    assert set(row) >= {"role", "password_set", "password_changed_at", "last_login", "locked", "must_change"}
    assert row["password_set"] is True and row["password_changed_at"]
    for role in ("ceo", "hrm", "admin", "viewer"):
        assert login(env, role).json("GET", "/api/users/security")[0] == 403
    # users.json cannot be fetched through the log viewer or any download route
    adm = login(env, "admin")
    assert adm.json("GET", "/api/logs/view?path=webapp/users.json")[0] == 404
    assert adm.req("GET", "/api/logs/download?path=webapp/users.json")[0].status == 404


def test_lockout_shown_and_unlock(env):
    for _ in range(5):
        Client(env.port).json("POST", "/api/login", {"username": "viewer", "password": "wrong-pass-1"})
    m = login(env, "master")
    assert next(u for u in m.json("GET", "/api/users/security")[1]["users"] if u["username"] == "viewer")["locked"] is True
    assert Client(env.port).json("POST", "/api/login", {"username": "viewer", "password": PW})[0] == 429
    assert login(env, "admin").json("POST", "/api/users/unlock", {"username": "viewer"})[0] == 403
    assert login(env, "hrm").json("POST", "/api/users/unlock", {"username": "ceo"})[0] == 403    # not manageable by HRM
    assert login(env, "hrm").json("POST", "/api/users/unlock", {"username": "viewer"})[0] == 200
    assert Client(env.port).login("viewer")
    assert next(u for u in m.json("GET", "/api/users/security")[1]["users"] if u["username"] == "viewer")["locked"] is False


def test_user_audit_log_has_who_when_never_secrets(env):
    c = login(env, "ceo")
    c.json("POST", "/api/users/add", {"username": "aud1", "role": "viewer", "password": NEW_PW})
    c.json("POST", "/api/users/update", {"username": "aud1", "password": "Other-fake-pw-88"})
    c.json("POST", "/api/users/unlock", {"username": "aud1"})
    c.json("POST", "/api/users/delete", {"username": "aud1"})
    log = (env.logs / "webapp" / "user_audit.csv").read_text()
    for action in ("user_added", "user_updated", "user_unlocked", "user_deleted"):
        assert action in log
    assert "ceo" in log and "aud1" in log
    for needle in (NEW_PW, "Other-fake-pw-88", PW):
        assert needle not in log
    assert "password_reset" in log


def test_forgot_password_flow_end_to_end(env):
    """User forgets -> HRM resets with a typed temporary password -> user must change it."""
    hrm = login(env, "hrm")
    assert hrm.json("POST", "/api/users/update", {"username": "viewer", "password": "Temp-typed-pw-55"})[0] == 200
    u = Client(env.port)
    r, p = u.req("POST", "/api/login", {"username": "viewer", "password": "Temp-typed-pw-55"})
    assert r.status == 200 and json.loads(p)["must_change"] is True
    u.cookie, u.csrf = r.getheader("Set-Cookie").split(";")[0], json.loads(p)["csrf"]
    assert u.json("GET", "/api/overview")[0] == 403
    assert u.json("POST", "/api/password", {"old": "Temp-typed-pw-55", "new": "My-own-new-pw-66"})[0] == 200
    assert Client(env.port).req("POST", "/api/login", {"username": "viewer", "password": "My-own-new-pw-66"})[0].status == 200
    # the master account cannot be reset by managers
    assert hrm.json("POST", "/api/users/update", {"username": "master000", "password": "Temp-typed-pw-55"})[0] == 403


def test_every_script_py_compiles():
    scripts = sorted((ROOT / "scripts").glob("*.py"))
    assert scripts
    for f in scripts:
        compile(f.read_text(encoding="utf-8"), str(f), "exec")   # no files written
