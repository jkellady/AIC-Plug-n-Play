import json

import pytest

from src.webapp import auth

PW = "fake-test-password-1"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def mgr(tmp_path):
    path = auth.users_path(tmp_path)
    auth.save_user(path, "viewer", "viewer", PW)
    clock = Clock()
    return auth.AuthManager(path, clock=clock), clock, path


def test_hash_is_salted_and_not_plaintext(tmp_path):
    path = auth.users_path(tmp_path)
    auth.save_user(path, "a", "viewer", PW)
    auth.save_user(path, "b", "viewer", PW)
    text = path.read_text()
    assert PW not in text
    users = json.loads(text)["users"]
    assert users["a"]["salt"] != users["b"]["salt"]
    assert users["a"]["hash"] != users["b"]["hash"]
    assert auth.verify_password(PW, users["a"])
    assert not auth.verify_password("wrong", users["a"])


def test_bad_role_rejected(tmp_path):
    with pytest.raises(ValueError):
        auth.save_user(auth.users_path(tmp_path), "x", "boss", PW)


def test_no_users_file(tmp_path):
    m = auth.AuthManager(auth.users_path(tmp_path))
    assert m.login("viewer", PW) == ("no_users", None)


def test_login_ok_and_bad(mgr):
    m, _, _ = mgr
    status, token = m.login("Viewer", PW)
    assert status == "ok" and len(token) >= 40
    assert m.get_session(token)["role"] == "viewer"
    assert m.login("viewer", "nope")[0] == "bad"
    assert m.login("ghost", PW)[0] == "bad"
    assert m.login(None, PW)[0] == "invalid"


def test_lockout_and_release(mgr):
    m, clock, _ = mgr
    for _ in range(5):
        assert m.login("viewer", "bad")[0] == "bad"
    assert m.login("viewer", PW)[0] == "locked"
    clock.t += 301
    assert m.login("viewer", PW)[0] == "ok"


def test_idle_and_absolute_expiry(mgr):
    m, clock, _ = mgr
    _, t1 = m.login("viewer", PW)
    clock.t += 31 * 60
    assert m.get_session(t1) is None
    _, t2 = m.login("viewer", PW)
    for _ in range(30):           # stay active every 29 min
        clock.t += 29 * 60
        if m.get_session(t2) is None:
            break
    assert clock.t - 1000 < 13 * 3600
    assert m.get_session(t2) is None  # absolute 12 h cap hit


def test_logout_and_csrf(mgr):
    m, _, _ = mgr
    _, t = m.login("viewer", PW)
    s = m.get_session(t)
    assert m.check_csrf(s, s["csrf"])
    assert not m.check_csrf(s, "x") and not m.check_csrf(s, None)
    m.logout(t)
    assert m.get_session(t) is None
