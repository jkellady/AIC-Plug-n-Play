"""Tests for the reply handler (synthetic workbooks, fake IDs, tmp_path only)."""
import json
import sys

import openpyxl
import pytest

import src.daily as daily
from src.agents import reply_handler as rh

HEADER = ["person", "date", "day", "flag_count"]


def _book(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Day check"
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


ORIGINAL_ROWS = [["S01", "2026-10-05", "Mon", 0], ["S01", "2026-10-06", "Tue", 1],
                 ["S02", "2026-10-05", "Mon", 0]]
NEW_ROWS = [["S01", "2026-10-05", "Mon", 0], ["S01", "2026-10-06", "Tue", 0],  # changed
            ["S03", "2026-10-07", "Wed", 2]]                                  # S02 removed, S03 added


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("REPORTS_DIR", "06_REPORTS"), ("LOGS_DIR", "07_LOGS"), ("DROP_DIR", "01_DROP_FOLDER")):
        monkeypatch.setattr(daily, name, tmp_path / sub)
    (tmp_path / "01_DROP_FOLDER").mkdir()
    original = tmp_path / "06_REPORTS" / "2026-10" / "Weekly_2026-W41.xlsx"
    _book(original, ORIGINAL_ROWS)
    reply = tmp_path / "01_DROP_FOLDER" / "2026-W41_Weekly_reply.xlsx"
    _book(reply, [["S01", "2026-10-06", "Tue", 0]])

    calls = []

    def fake_reporter(as_of=None, previous=True):
        calls.append((as_of, previous))
        out = daily.REPORTS_DIR / "2026-10" / "Weekly_2026-W41.xlsx"
        _book(out, NEW_ROWS)
        return {"status": "OK", "output_path": str(out)}

    monkeypatch.setattr(rh, "_load_reporter", lambda scope: (fake_reporter, ""))
    return tmp_path, original, reply, calls


def _snapshot(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_disabled_by_default_does_nothing(env):
    tmp, _, reply, calls = env
    before = _snapshot(tmp)
    res = rh.run(reply)
    assert res["status"] == "DISABLED"
    assert calls == []
    assert _snapshot(tmp) == before


def test_bad_filename(env):
    tmp, _, _, _ = env
    bad = tmp / "01_DROP_FOLDER" / "notes.xlsx"
    bad.write_bytes(b"x")
    assert rh.run(bad, enabled=True)["status"] == "FAILED"
    bad2 = tmp / "01_DROP_FOLDER" / "2026-W99_Weekly_reply.xlsx"
    bad2.write_bytes(b"x")
    assert rh.run(bad2, enabled=True)["status"] == "FAILED"
    bad3 = tmp / "01_DROP_FOLDER" / "hello_reply.xlsx"
    bad3.write_bytes(b"x")
    assert rh.run(bad3, enabled=True)["status"] == "FAILED"


def test_no_matching_original(env):
    tmp, original, _, calls = env
    other = tmp / "01_DROP_FOLDER" / "2026-W30_Weekly_reply.xlsx"
    other.write_bytes(b"x")
    res = rh.run(other, enabled=True)
    assert res["status"] == "NO_MATCH"
    assert res["scope"] == "week"
    assert calls == []


def test_ok_diff_counts_and_week_window(env):
    _, original, reply, calls = env
    res = rh.run(reply, enabled=True)
    assert res["status"] == "OK", res
    assert res["scope"] == "week"
    assert res["window"] == "2026-10-05 to 2026-10-11"
    assert res["diff_counts"] == {
        "rows_added": 1, "rows_removed": 1, "rows_changed": 1,
        "flagged_person_days_before": 1, "flagged_person_days_after": 1,
        "comparable": True,
    }
    assert str(res["revision_path"]).endswith("Weekly_2026-W41_v2.xlsx")
    assert calls[0][0].isoformat() == "2026-10-05" and calls[0][1] is False


def test_revision_numbering_and_no_overwrite(env):
    tmp, original, reply, _ = env
    orig_bytes = original.read_bytes()
    r2 = rh.run(reply, enabled=True)
    v2_bytes = open(r2["revision_path"], "rb").read()
    r3 = rh.run(reply, enabled=True)
    assert r2["revision_path"].endswith("_v2.xlsx")
    assert r3["revision_path"].endswith("_v3.xlsx")
    assert original.read_bytes() == orig_bytes
    assert open(r2["revision_path"], "rb").read() == v2_bytes


def test_existing_v2_is_skipped_not_overwritten(env):
    _, original, reply, _ = env
    v2 = original.with_name("Weekly_2026-W41_v2.xlsx")
    v2.write_bytes(b"precious")
    res = rh.run(reply, enabled=True)
    assert res["revision_path"].endswith("_v3.xlsx")
    assert v2.read_bytes() == b"precious"


def test_reply_untouched_drop_untouched_and_writes_only_reports_and_logs(env):
    tmp, _, reply, _ = env
    before = _snapshot(tmp)
    res = rh.run(reply, enabled=True)
    assert res["status"] == "OK"
    after = _snapshot(tmp)
    for p, data in before.items():
        assert after[p] == data  # nothing existing was changed or removed
    new = set(after) - set(before)
    assert new
    for p in new:
        assert p.relative_to(tmp).parts[0] in ("06_REPORTS", "07_LOGS"), p
    assert not any("_reply_tmp_" in str(p) for p in after)  # scratch cleaned up
    assert daily.REPORTS_DIR == tmp / "06_REPORTS"  # restored


def test_summary_json_and_csv_are_count_only(env):
    tmp, _, reply, _ = env
    rh.run(reply, enabled=True)
    logs = tmp / "07_LOGS"
    files = list(logs.glob("reply_2026-W41_Weekly_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["diff_counts"]["rows_added"] == 1
    text = files[0].read_text(encoding="utf-8")
    for fake_id in ("S01", "S02", "S03"):
        assert fake_id not in text
    csv = (logs / "reply_log.csv").read_text(encoding="utf-8").splitlines()
    assert csv[0] == "run_time,stem,status,detail"
    assert ",2026-W41_Weekly,OK," in csv[1]


def test_on_disk_stem_name_accepted(env):
    tmp, _, _, _ = env
    reply = tmp / "01_DROP_FOLDER" / "Weekly_2026-W41_reply.xlsx"
    reply.write_bytes(b"x")
    assert rh.run(reply, enabled=True)["status"] == "OK"


def test_monthly_reporter_missing_is_blocked(env, monkeypatch):
    tmp, _, _, _ = env
    monkeypatch.undo()  # drop the fake loader; keep tmp dirs below
    monkeypatch.setattr(daily, "REPORTS_DIR", tmp / "06_REPORTS")
    monkeypatch.setattr(daily, "LOGS_DIR", tmp / "07_LOGS")
    _book(tmp / "06_REPORTS" / "2026-09" / "Monthly_2026-09.xlsx", ORIGINAL_ROWS)
    reply = tmp / "01_DROP_FOLDER" / "2026-09_Monthly_reply.xlsx"
    reply.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "src.agents.monthly_report", None)  # import raises
    res = rh.run(reply, enabled=True)
    assert res["status"] == "BLOCKED"
    assert res["scope"] == "month" and res["window"] == "2026-09-01 to 2026-09-30"
    assert res["error"]


def test_monthly_ok_with_fake_reporter(env, monkeypatch):
    tmp, _, _, _ = env
    _book(tmp / "06_REPORTS" / "2026-09" / "Monthly_2026-09.xlsx", ORIGINAL_ROWS)

    def fake_monthly(as_of=None):  # no `previous` argument: TypeError fallback path
        out = daily.REPORTS_DIR / "2026-09" / "Monthly_2026-09.xlsx"
        _book(out, NEW_ROWS)
        return {"status": "OK", "output_path": str(out)}

    monkeypatch.setattr(rh, "_load_reporter", lambda scope: (fake_monthly, ""))
    reply = tmp / "01_DROP_FOLDER" / "2026-09_Monthly_reply.xlsx"
    reply.write_bytes(b"x")
    res = rh.run(reply, enabled=True)
    assert res["status"] == "OK"
    assert res["revision_path"].endswith("Monthly_2026-09_v2.xlsx")


def test_reporter_skipped_exists_is_failed(env, monkeypatch):
    _, _, reply, _ = env
    monkeypatch.setattr(rh, "_load_reporter",
                        lambda scope: (lambda **kw: {"status": "SKIPPED_EXISTS", "output_path": None}, ""))
    res = rh.run(reply, enabled=True)
    assert res["status"] == "FAILED"
    assert res["revision_path"] is None


def test_prints_nothing(env, capsys):
    _, _, reply, _ = env
    rh.run(reply)
    rh.run(reply, enabled=True)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_main_returns_zero_when_disabled(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["reply_handler"])
    assert rh.main() == 0


def test_main_returns_one_on_failure(env, monkeypatch):
    monkeypatch.setattr(rh, "run", lambda *a, **k: {"status": "FAILED"})
    monkeypatch.setattr(sys, "argv", ["reply_handler", "fake.xlsx"])
    assert rh.main() == 1
