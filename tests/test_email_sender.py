"""Tests for src.agents.email_sender. Never touches the network."""
import pytest

import src.daily as daily
from src.agents import email_sender as es

FAKE_PW = "fake-app-password-0000"
FAKE_USER = "sender@example.invalid"
TO = ["a@example.invalid", "b@example.invalid"]


class ExplodingSMTP:
    def __init__(self, *a, **k):
        raise AssertionError("SMTP must not be constructed")


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, pw):
        self.calls.append("login")

    def send_message(self, msg):
        self.calls.append("send")
        self.sent = msg


@pytest.fixture
def env(tmp_path, monkeypatch):
    reports = tmp_path / "06_REPORTS"
    logs = tmp_path / "07_LOGS"
    (reports / "2026-09").mkdir(parents=True)
    monkeypatch.setattr(daily, "REPORTS_DIR", reports)
    monkeypatch.setattr(daily, "LOGS_DIR", logs)
    monkeypatch.setattr(daily, "PROJECT_ROOT", tmp_path)
    report = reports / "2026-09" / "Weekly_test.xlsx"
    report.write_bytes(b"PK-fake")
    FakeSMTP.instances = []
    monkeypatch.setattr(es, "SMTP_CLASS", FakeSMTP)
    return tmp_path, report


def write_env(root, text=None):
    p = root / ".env"
    p.write_text(text if text is not None else
                 f"AIC_SMTP_USER={FAKE_USER}\nAIC_SMTP_PASSWORD={FAKE_PW}\n",
                 encoding="utf-8")
    return p


def log_text(root):
    return (root / "07_LOGS" / "email_log.csv").read_text(encoding="utf-8")


def test_disabled_by_default_sends_nothing(env, monkeypatch):
    root, report = env
    write_env(root)
    monkeypatch.setattr(es, "SMTP_CLASS", ExplodingSMTP)
    real_import = __import__

    def guard(name, *a, **k):
        if name == "smtplib":
            raise AssertionError("smtplib imported while disabled")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", guard)
    r = es.send_report(report, TO)
    monkeypatch.undo()
    assert r["status"] == "DISABLED"
    assert r["recipients_count"] == 2
    assert FakeSMTP.instances == []


def test_disabled_does_not_read_env(env, monkeypatch):
    root, report = env
    monkeypatch.setattr(es, "read_env", lambda p: pytest.fail("env read while disabled"))
    assert es.send_report(report, TO)["status"] == "DISABLED"


def test_no_recipients(env):
    root, report = env
    write_env(root)
    assert es.send_report(report, send_enabled=True)["status"] == "NO_RECIPIENTS"
    assert es.send_report(report, [], send_enabled=True)["status"] == "NO_RECIPIENTS"
    assert FakeSMTP.instances == []


def test_no_credentials(env):
    root, report = env
    r = es.send_report(report, TO, send_enabled=True)  # no .env at all
    assert r["status"] == "NO_CREDENTIALS"
    write_env(root, f"AIC_SMTP_USER={FAKE_USER}\n")
    assert es.send_report(report, TO, send_enabled=True)["status"] == "NO_CREDENTIALS"
    assert FakeSMTP.instances == []
    assert not (root / ".env").read_text().count(FAKE_PW)


def test_env_parsing(tmp_path):
    p = tmp_path / "x.env"
    p.write_text(
        "# comment\n\nA=plain\nB=\"quoted value\"\nC='single'\nD = spaced \n"
        "E=with # inline\nexport F=exported\nnoequals\nG=a=b\n", encoding="utf-8")
    d = es.read_env(p)
    assert d == {"A": "plain", "B": "quoted value", "C": "single", "D": "spaced",
                 "E": "with", "F": "exported", "G": "a=b"}
    assert es.read_env(tmp_path / "missing.env") == {}


def test_successful_send_with_fake_smtp(env):
    root, report = env
    write_env(root, f"AIC_SMTP_USER={FAKE_USER}\nAIC_SMTP_PASSWORD='{FAKE_PW}'\n"
                    "AIC_SMTP_PORT=2525\n")
    r = es.send_report(report, TO, send_enabled=True)
    assert r == {"status": "SENT", "recipients_count": 2,
                 "attachment": "Weekly_test.xlsx", "error": None}
    s = FakeSMTP.instances[0]
    assert (s.host, s.port) == ("smtp.gmail.com", 2525)
    assert s.calls == ["starttls", "login", "send"]
    atts = list(s.sent.iter_attachments())
    assert [a.get_filename() for a in atts] == ["Weekly_test.xlsx"]


def test_password_never_leaks(env, monkeypatch, capsys):
    root, report = env
    write_env(root)

    class BoomSMTP(FakeSMTP):
        def login(self, user, pw):
            raise RuntimeError(f"auth failed for {pw}")

    monkeypatch.setattr(es, "SMTP_CLASS", BoomSMTP)
    r = es.send_report(report, TO, send_enabled=True)
    assert r["status"] == "FAILED"
    assert FAKE_PW not in str(r)
    assert FAKE_PW not in log_text(root)
    assert "example.invalid" not in log_text(root)
    ok = es.send_report(report, TO, send_enabled=False)
    assert FAKE_PW not in str(ok)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_attachment_outside_reports_refused(env, tmp_path):
    root, report = env
    write_env(root)
    outside = root / "other.xlsx"
    outside.write_bytes(b"x")
    r = es.send_report(outside, TO, send_enabled=True)
    assert r["status"] == "FAILED"
    assert "06_REPORTS" in r["error"]
    r2 = es.send_report(report.parent / ".." / ".." / "other.xlsx", TO, send_enabled=True)
    assert r2["status"] == "FAILED"
    r3 = es.send_report(report.parent / "nope.xlsx", TO, send_enabled=True)
    assert r3["status"] == "FAILED"
    assert FakeSMTP.instances == []


def test_dry_run(env, monkeypatch):
    root, report = env
    monkeypatch.setattr(es, "SMTP_CLASS", ExplodingSMTP)
    r = es.send_report(report, TO, dry_run=True)
    assert r == {"status": "DRY_RUN", "recipients_count": 2,
                 "attachment": "Weekly_test.xlsx", "error": None}
    assert es.send_report(report, [], dry_run=True)["status"] == "NO_RECIPIENTS"


def test_header_injection_refused(env):
    root, report = env
    write_env(root)
    r = es.send_report(report, ["a@example.invalid\nBcc: x@example.invalid"], send_enabled=True)
    assert r["status"] == "FAILED"
    assert FakeSMTP.instances == []


def test_log_only_counts_and_name_and_writes_only_logs(env, capsys):
    root, report = env
    before = {p for p in root.rglob("*") if p.is_file()}
    es.send_report(report, TO)
    es.send_report(report, TO, dry_run=True)
    es.send_report(report, [], send_enabled=True)
    after = {p for p in root.rglob("*") if p.is_file()}
    new = after - before
    assert new == {root / "07_LOGS" / "email_log.csv"}
    lines = log_text(root).strip().splitlines()
    assert lines[0] == "run_time,status,recipients_count,attachment"
    assert [l.split(",")[1] for l in lines[1:]] == ["DISABLED", "DRY_RUN", "NO_RECIPIENTS"]
    assert "@" not in log_text(root)
    assert "Weekly_test.xlsx" in log_text(root)
    assert capsys.readouterr().out == ""
    assert not (root / ".env").exists()


def test_main_returns_zero_when_disabled(env):
    assert es.main() == 0


def test_main_returns_one_on_failure(env, monkeypatch):
    monkeypatch.setattr(es, "send_report", lambda *a, **k: {"status": "FAILED"})
    assert es.main() == 1
