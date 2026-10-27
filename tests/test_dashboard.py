import importlib
import sys

import pytest

import src.daily as daily
from src.agents import dashboard as dash


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    logs, reports = tmp_path / "07_LOGS", tmp_path / "06_REPORTS"
    logs.mkdir()
    reports.mkdir()
    monkeypatch.setattr(daily, "LOGS_DIR", logs)
    monkeypatch.setattr(daily, "REPORTS_DIR", reports)
    return logs, reports


def test_launch_command_localhost_only():
    cmd = dash.launch_command()
    assert cmd == ["streamlit", "run", "src/agents/dashboard/app.py",
                   "--server.address", "127.0.0.1", "--server.headless", "true"]
    assert "0.0.0.0" not in " ".join(cmd)


def test_generate_unknown_scope():
    assert dash.generate("nope")["status"] == "UNKNOWN_SCOPE"


def test_generate_missing_agent(monkeypatch):
    monkeypatch.setitem(dash.SCOPES, "monthly", "src.agents.does_not_exist")
    res = dash.generate("monthly")
    assert res["status"] == "NOT_AVAILABLE" and res["available"] is False


def test_generate_calls_run_and_catches_errors(monkeypatch):
    class Fake:
        @staticmethod
        def run(**kw):
            return {"status": "OK", "kw": kw}

    class Bad:
        @staticmethod
        def run(**kw):
            raise ValueError("boom")

    real = importlib.import_module
    monkeypatch.setattr(dash.importlib, "import_module",
                        lambda n: Fake if n == "fake.ok" else Bad if n == "fake.bad" else real(n))
    monkeypatch.setitem(dash.SCOPES, "weekly", "fake.ok")
    assert dash.generate("weekly", x=1)["kw"] == {"x": 1}
    monkeypatch.setitem(dash.SCOPES, "weekly", "fake.bad")
    assert dash.generate("weekly")["status"] == "FAILED"


def test_list_archive(dirs):
    _, reports = dirs
    assert dash.list_archive() == []
    (reports / "2026-05").mkdir()
    (reports / "2026-05" / "a.xlsx").write_bytes(b"12345")
    (reports / "2026-05" / "skip.bin").write_bytes(b"x")
    rows = dash.list_archive()
    assert len(rows) == 1
    assert rows[0]["name"] == "a.xlsx" and rows[0]["size_bytes"] == 5
    assert "content" not in rows[0]


def test_save_upload_ok_and_confined(dirs, tmp_path):
    logs, _ = dirs
    res = dash.save_upload("clock.xls", b"abc", "week1")
    assert res["status"] == "OK"
    assert (logs / "uploads" / "week1" / "clock.xls").read_bytes() == b"abc"
    assert dash.save_upload("clock.xls", b"z", "week1")["status"] == "REFUSED"
    written = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert all((logs / "uploads") in p.parents for p in written)


@pytest.mark.parametrize("name", ["../evil.xls", "..\\evil.xls", "a/b.xls", "C:\\x.xls",
                                  "/abs.xls", "..", "", "x.exe", "a\x00.xls"])
def test_save_upload_bad_name(dirs, name):
    assert dash.save_upload(name, b"x")["status"] == "REFUSED"


@pytest.mark.parametrize("staging", ["..", "../x", "a/b", "", "C:\\"])
def test_save_upload_bad_staging(dirs, staging, tmp_path):
    assert dash.save_upload("ok.csv", b"x", staging)["status"] == "REFUSED"
    assert not (tmp_path / "ok.csv").exists()


def test_save_upload_never_drop_folder(dirs):
    dash.save_upload("ok.csv", b"x")
    assert not (daily.DROP_DIR / "staging" / "ok.csv").exists()


def test_read_log(dirs):
    logs, _ = dirs
    assert dash.read_log("weekly_log.csv")["status"] == "MISSING"
    assert dash.read_log("../secret.csv")["status"] == "REFUSED"
    (logs / "weekly_log.csv").write_text("a,b\n" + "".join(f"{i},x\n" for i in range(10)))
    res = dash.read_log("weekly_log.csv", limit=3)
    assert res["header"] == ["a", "b"] and len(res["rows"]) == 3
    assert res["rows"][-1][0] == "9" and res["total_rows"] == 10
    assert set(dash.read_all_logs()) == set(dash.LOG_FILES)


def test_get_recipients_read_only():
    res = dash.get_recipients()
    assert res["email_sending"] == "OFF"
    assert "OFF" in res["note"]
    assert all(isinstance(v, int) for v in res["counts"].values())


def test_app_imports_without_streamlit_and_prints_nothing(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "streamlit", None)  # forces ImportError
    sys.modules.pop("src.agents.dashboard.app", None)
    mod = importlib.import_module("src.agents.dashboard.app")
    assert callable(mod.main)
    with pytest.raises(ImportError):
        mod.main()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
