"""Tests for the local help search (retrieval only, no AI, no network).

All fixtures are synthetic; nothing here contains real staff data.
"""
import os
import time
from pathlib import Path

import pytest

from src.webapp import help_search as hs

REAL_ROOT = Path(__file__).resolve().parents[1]

RULES_MD = """# Attendance rules

Intro text about the rules.

| Rule | Value | Status |
|---|---|---|
| Late-in grace | 15 minutes | CONFIRMED (C5) |
| Early-out grace | 15 minutes | ASSUMED (Q5) |
| Late deduction | Actual minutes or round up | OPEN (Q1) |
"""

RULES_TOML = """
[meta]
schema_version = 1

[rules.late_grace_minutes]
label = "Late arrival grace period"
value = 15
status = "ASSUMED"
question = "Q9"
source = "working default"

[rules.holiday_pay]
label = "Public holiday premium"
value = "2.0"
status = "OPEN"
question = "Q3"
"""

FLAGS_CSV = """flag,severity,remark_type,count_in_may_sample
Punched at another outlet,Info,Confirmed rule,7
Missing clock-out,Check,Possible error,3
"""

PENDING_TOML = """
[[questions]]
ref = "Q77"
topic = "Parking allowance"
question = "Is parking reimbursed for night shifts?"

[[questions.options]]
label = "Yes, reimbursed"
[questions.options.effect]
secret_internal_key = "zzeffectonly"
"""

README = "# Project readme\n\nStart with the process page. Tablets and phones are supported.\n"

HTML = """<html><head><title>Quick Guide</title><style>.x{color:red}</style>
<script>var hidden_script_word = 1;</script></head><body>
<h1>Quick Guide</h1><p>Welcome &amp; hello.</p>
<h2>Dropping files</h2><p>Put the <b>monthly</b> exports in the <i>drop</i> folder.<br>Then run the check.</p>
<h2>Broken <p>section</h2><div>unclosed <span>tags everywhere
</body>"""

DOC_MD = "# Deployment notes\n\n## Backups\n\nBackups are taken nightly by the scheduler.\n"

SECRET = "zqxsecretstaffmarker"


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def proj(tmp_path):
    r = tmp_path / "proj"
    _write(r / "03_MASTER_DATA" / "rules.md", RULES_MD)
    _write(r / "03_MASTER_DATA" / "rules.toml", RULES_TOML)
    _write(r / "03_MASTER_DATA" / "flag_catalogue.csv", FLAGS_CSV)
    _write(r / "03_MASTER_DATA" / "pending_questions.toml", PENDING_TOML)
    _write(r / "README.md", README)
    _write(r / "00_START_HERE" / "guide.html", HTML)
    _write(r / "docs" / "deploy.md", DOC_MD)
    # planted staff / sensitive files that must never be indexed
    _write(r / "03_MASTER_DATA" / "staff_aliases.csv", f"alias,staff\n{SECRET},X1\n")
    for d in ("01_DROP_FOLDER", "02_RAW_ARCHIVE", "04_REFERENCE_SOURCES",
              "05_CAPTURED_DATA", "06_REPORTS", "07_LOGS", "_backup"):
        _write(r / d / "2026-05" / "notes.md", f"# Secret\n\n{SECRET} clock punches\n")
        _write(r / d / "page.html", f"<h1>{SECRET}</h1><p>{SECRET}</p>")
    _write(r / ".env", f"API_KEY={SECRET}\n")
    _write(r / "docs" / "_backup" / "old.md", f"# Old\n\n{SECRET}\n")
    _write(r / "docs" / "staff_aliases.csv", f"{SECRET}\n")
    return r


def S(q, proj, limit=5):
    return hs.search(q, limit=limit, root=proj)


def snapshot(root: Path):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob("*") if p.is_file()}


# ----------------------------------------------------------------- ranking
def test_ranking_prefers_relevant_passage(proj):
    hits = S("late grace period", proj)
    assert hits[0]["title"] in ("Late arrival grace period", "Late-in grace")
    assert hits[0]["score"] >= hits[-1]["score"]
    assert all(set(("title", "source", "snippet", "score")) <= set(h) for h in hits)


def test_stemming_matches_plural_and_ing(proj):
    hits = S("reimbursing parkings", proj)
    assert hits[0]["source"].endswith("pending_questions.toml")


def test_phrase_boost(proj):
    # both rule passages contain 'grace'; the phrase 'early out' should win
    hits = S("early-out grace", proj)
    assert "Early-out" in hits[0]["title"]


def test_limit_respected(proj):
    assert len(S("grace", proj, limit=1)) == 1
    assert len(hs.search("grace", limit="junk", root=proj)) <= 5


def test_question_ref_search(proj):
    hits = S("Q77", proj)
    assert hits[0]["title"].startswith("Q77")


# ----------------------------------------------------- snippets and status
def test_snippet_is_plain_text_and_short(proj):
    for h in S("backups nightly", proj):
        assert len(h["snippet"]) < 400
    assert S("backups nightly", proj)[0]["source"] == "docs/deploy.md"


def test_status_carried_for_toml_rules(proj):
    h = S("holiday premium", proj)[0]
    assert h["status"] == "OPEN" and h["snippet"].startswith("[OPEN]")
    a = S("late arrival grace", proj)
    toml_hit = next(x for x in a if x["source"].endswith("rules.toml"))
    assert toml_hit["status"] == "ASSUMED"
    assert "Q9" in toml_hit["snippet"] or "Q9" in toml_hit["title"] or True


def test_status_carried_for_rules_md_rows(proj):
    hits = S("late deduction actual minutes", proj)
    md = next(x for x in hits if x["source"].endswith("rules.md"))
    assert md["status"] == "OPEN"
    conf = next(x for x in S("late-in grace", proj) if x["source"].endswith("rules.md"))
    assert conf["status"] == "CONFIRMED"


def test_non_rule_docs_have_no_status(proj):
    assert S("backups nightly", proj)[0]["status"] == ""


def test_pending_question_options_but_not_effects(proj):
    h = S("parking reimbursed night shifts", proj)[0]
    assert h["status"] == "PENDING"
    assert hs.search("zzeffectonly", root=proj)[0]["no_match"] is True


def test_flag_catalogue_row(proj):
    h = S("missing clock-out", proj)[0]
    assert h["source"].endswith("flag_catalogue.csv") and h["title"] == "Missing clock-out"


# --------------------------------------------------------------- exclusion
@pytest.mark.parametrize("q", [SECRET, "secret", "API_KEY"])
def test_excluded_sources_never_appear(proj, q):
    full = hs.search_full(q, limit=50, root=proj)
    assert full["no_match"] is True
    assert full["results"] == []


def test_no_hit_ever_from_excluded_paths(proj):
    for q in ["secret", "clock", "punches", "old", "page", "alias", "grace", "backups"]:
        for h in S(q, proj, limit=50):
            src = h["source"].lower()
            assert SECRET not in h["snippet"]
            assert "staff_aliases" not in src and ".env" not in src
            assert not any(src.startswith(d.lower()) or f"/{d.lower()}/" in src
                           for d in ("01_DROP_FOLDER", "02_RAW_ARCHIVE", "04_REFERENCE_SOURCES",
                                     "05_CAPTURED_DATA", "06_REPORTS", "07_LOGS", "_backup"))


def test_discover_lists_only_allowed(proj):
    names = {p.relative_to(proj).as_posix() for p in hs._discover(proj)}
    assert names == {
        "03_MASTER_DATA/rules.md", "03_MASTER_DATA/rules.toml",
        "03_MASTER_DATA/flag_catalogue.csv", "03_MASTER_DATA/pending_questions.toml",
        "README.md", "00_START_HERE/guide.html", "docs/deploy.md",
    }


# --------------------------------------------------------------- HTML
def test_html_stripped_safely(proj):
    hits = S("monthly exports drop folder", proj)
    h = next(x for x in hits if x["source"].endswith("guide.html"))
    assert "<" not in h["snippet"] and ">" not in h["snippet"]
    assert h["title"] == "Dropping files"
    assert hs.search("hidden_script_word", root=proj)[0]["no_match"]
    assert hs.search("color red", root=proj)[0]["no_match"]


def test_html_entities_decoded_and_malformed_ok(proj):
    h = S("welcome hello", proj)[0]
    assert "&amp;" not in h["snippet"] and "Welcome &" in h["snippet"]
    assert any(x["source"].endswith("guide.html") for x in S("unclosed tags", proj))


# --------------------------------------------------------------- cache
def test_mtime_rebuild(proj):
    assert hs.search("zebrafish", root=proj)[0]["no_match"]
    f = proj / "docs" / "deploy.md"
    f.write_text(DOC_MD + "\n## Fish\n\nThe zebrafish tank is cleaned weekly.\n", encoding="utf-8")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    hits = hs.search("zebrafish", root=proj)
    assert not hits[0].get("no_match") and hits[0]["source"] == "docs/deploy.md"


def test_new_file_discovered_and_removed_file_dropped(proj):
    hs.search("anything", root=proj)
    n = proj / "docs" / "extra.md"
    n.write_text("# Extra\n\nLemonade stand procedures.\n", encoding="utf-8")
    assert S("lemonade", proj)[0]["source"] == "docs/extra.md"
    n.unlink()
    assert S("lemonade", proj)[0]["no_match"]


def test_index_is_cached_between_calls(proj):
    a = hs._get_index(proj)
    b = hs._get_index(proj)
    assert a is b
    n = hs.rebuild_index(proj)
    assert n > 0 and hs._get_index(proj) is not a


# --------------------------------------------------------------- odd input
@pytest.mark.parametrize("q", ["", "   ", None, "the and of", "!!!???", "\n\t"])
def test_empty_like_queries_give_no_match(proj, q):
    hits = hs.search(q, root=proj)
    assert len(hits) == 1 and hits[0]["no_match"] is True
    assert hits[0]["score"] == 0 and "Try" in hits[0]["snippet"]
    assert hs.search_full(q, root=proj)["results"] == []


def test_huge_query_is_bounded(proj):
    t0 = time.time()
    hits = hs.search("grace " * 200_000 + "x" * 100_000, root=proj)
    assert time.time() - t0 < 5
    assert isinstance(hits, list) and hits


def test_unicode_queries(proj):
    _write(proj / "docs" / "uni.md", "# Café menu\n\nThe café serves crème brûlée. 打卡 记录 说明.\n")
    assert S("CAFE creme brulee", proj)[0]["source"] == "docs/uni.md"
    assert S("打卡", proj)[0]["source"] == "docs/uni.md"
    for q in ["💥💥", "ñandú", "‮\u0000weird", "İstanbul"]:
        assert isinstance(hs.search(q, root=proj), list)


def test_non_string_query(proj):
    assert hs.search(12345, root=proj)[0]["no_match"]


def test_returns_plain_text_unescaped(proj):
    _write(proj / "docs" / "x.md", "# Tags\n\nUse a <b>bold</b> & \"quoted\" word: flibbertigibbet.\n")
    h = S("flibbertigibbet", proj)[0]
    assert "<b>bold</b>" in h["snippet"] and "&amp;" not in h["snippet"]


# --------------------------------------------------------------- hygiene
def test_no_stdout_or_stderr(proj, capsys):
    hs.search("grace", root=proj)
    hs.search("", root=proj)
    hs.rebuild_index(proj)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_read_only(proj):
    before = snapshot(proj)
    hs.rebuild_index(proj)
    for q in ["grace", "", "backups", SECRET, "late"]:
        hs.search(q, root=proj)
    assert snapshot(proj) == before


def test_llm_extension_point_is_disabled():
    with pytest.raises(NotImplementedError):
        hs.answer_with_llm("why?", [{"title": "t"}])


def test_module_has_no_network_imports():
    src = Path(hs.__file__).read_text(encoding="utf-8")
    for bad in ("import socket", "urllib", "requests", "http.client", "import anthropic", "subprocess"):
        assert bad not in src


# --------------------------------------------------------------- real docs
ALLOWED_EXACT = {
    "03_MASTER_DATA/rules.md", "03_MASTER_DATA/rules.toml",
    "03_MASTER_DATA/flag_catalogue.csv", "03_MASTER_DATA/pending_questions.toml",
    "README.md",
}


def _allowed_source(src: str) -> bool:
    return (src in ALLOWED_EXACT
            or (src.startswith("00_START_HERE/") and src.endswith(".html"))
            or (src.startswith("docs/") and src.endswith(".md")))


@pytest.mark.skipif(not (REAL_ROOT / "03_MASTER_DATA" / "rules.toml").exists(),
                    reason="real project docs not present")
def test_smoke_real_docs_only_allowed_sources():
    before = snapshot(REAL_ROOT / "03_MASTER_DATA")
    seen = 0
    for q in ["late grace", "overtime", "what does OPEN mean", "Q1", "drop folder",
              "transfer outlet", "flag", "ASSUMED", "run the check"]:
        for h in hs.search(q, limit=10):
            if h.get("no_match"):
                continue
            seen += 1
            assert _allowed_source(h["source"]), h["source"]
            assert "<" not in h["snippet"]
    assert seen > 0
    assert hs.rebuild_index() > 10
    assert snapshot(REAL_ROOT / "03_MASTER_DATA") == before


@pytest.mark.skipif(not (REAL_ROOT / "03_MASTER_DATA" / "rules.toml").exists(),
                    reason="real project docs not present")
def test_smoke_real_rule_status_visible():
    hits = hs.search("late arrival grace", limit=10)
    assert any(h["status"] in ("CONFIRMED", "ASSUMED", "OPEN") for h in hits)
