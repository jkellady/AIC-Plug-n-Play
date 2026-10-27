"""Local knowledge-base help search for the Client attendance web app.

Step 1 of a Q&A helper: retrieval only. No AI model, no network, stdlib only.

What it does
------------
Indexes a fixed allow-list of *documentation* files (rules, flag catalogue,
pending questions, README, the 00_START_HERE pages, docs/*.md), cuts them
into small passages, and ranks passages with a small BM25 written here.

What it never touches
---------------------
Staff data. Only the allow-listed sources below are ever opened, and the
folders/files in EXCLUDED_DIRS / EXCLUDED_NAMES are refused even if some
future change to the allow-list pointed at them. Source files are opened
read-only; this module writes nothing and prints nothing.

Public API
----------
search(q, limit=5)      -> list of hit dicts {title, source, snippet, score, ...}
search_full(q, limit=5) -> {"query", "results", "no_match", "suggestion"}
rebuild_index()         -> number of passages indexed (forces a rebuild)
answer_with_llm(...)    -> DISABLED extension point, always NotImplementedError

A hit has the keys title, source, snippet, score plus section, status and
no_match. `status` is CONFIRMED / ASSUMED / OPEN for rule passages (so an
answer can say "this rule is still ASSUMED"), PENDING for pending-question
passages, and "" otherwise. When nothing matches, search() returns ONE hit
with no_match=True, score 0 and a suggestion in `snippet`, never a guess.
All text is plain text; the front end must escape it.
"""
from __future__ import annotations

import csv
import io
import math
import re
import threading
import tomllib
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

# --------------------------------------------------------------------------
# Source policy
# --------------------------------------------------------------------------

# Folder names (any path component) that are never indexed: staff data,
# credentials and backups.
EXCLUDED_DIRS = frozenset({
    "01_drop_folder", "02_raw_archive", "04_reference_sources",
    "05_captured_data", "06_reports", "07_logs", "_backup",
    ".git", ".venv", "__pycache__", "node_modules",
})
# File names that are never indexed.
EXCLUDED_NAMES = frozenset({
    "staff_aliases.csv", "staff_aliases.example.csv", ".env",
})

_MAX_FILE_BYTES = 2_000_000
_MAX_QUERY_CHARS = 500
_MAX_QUERY_TOKENS = 40
_MAX_PASSAGE_CHARS = 900
_SNIPPET_CHARS = 260

_NO_MATCH_SUGGESTION = (
    "No matching help passage was found. Try fewer or simpler words, a rule "
    "name (for example 'late grace'), a question number (for example 'Q1'), "
    "or a flag name. If it is still unclear, ask JK Zentra."
)

_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have how i if in "
    "is it its me my of on or our so than that the their then there these "
    "this to us was we what when where which who why will with you your "
    "about into any should would could".split()
)

_STATUS_RE = re.compile(r"\b(CONFIRMED|ASSUMED|OPEN)\b")

# --------------------------------------------------------------------------
# Text normalising / tokenising
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x4DBF
            or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF)


def _fold(text: str) -> str:
    """Lower-case, NFKD, drop combining marks (so 'café' matches 'cafe')."""
    t = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


def _stem(w: str) -> str:
    """Very small suffix stemmer. Leaves short words and anything with a
    digit (rule refs such as 'q1', times such as '10') alone."""
    if len(w) <= 3 or any(c.isdigit() for c in w) or not w.isascii():
        return w
    for suf, rep in (("sses", "ss"), ("ies", "y"), ("ing", ""), ("edly", ""),
                     ("ed", ""), ("ly", ""), ("es", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) + len(rep) >= 3:
            if suf == "s" and w.endswith(("ss", "us", "is")):
                continue
            w = w[: len(w) - len(suf)] + rep
            break
    # undouble a trailing consonant left by -ing/-ed (running -> run)
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeioulsz":
        w = w[:-1]
    if w.endswith("e") and len(w) > 4:  # late/lates, rule/rules converge
        w = w[:-1]
    return w


def _tokens(text: str, keep_stop: bool = False) -> list[str]:
    """Stemmed tokens in order. CJK runs become one token per character."""
    out: list[str] = []
    for raw in _WORD_RE.findall(_fold(text)):
        if any(_is_cjk(c) for c in raw):
            out.extend(c for c in raw if _is_cjk(c))
            continue
        raw = raw.strip("_")
        if not raw:
            continue
        if not keep_stop and raw in _STOPWORDS:
            continue
        out.append(_stem(raw))
    return out


# --------------------------------------------------------------------------
# Passages
# --------------------------------------------------------------------------

def _passage(title: str, source: str, section: str, text: str,
             status: str = "") -> dict:
    return {"title": title.strip() or section or source, "source": source,
            "section": section.strip(), "text": text.strip(), "status": status}


def _split_long(text: str) -> list[str]:
    """Cut a long block into <= _MAX_PASSAGE_CHARS pieces on paragraph /
    sentence boundaries."""
    text = text.strip()
    if len(text) <= _MAX_PASSAGE_CHARS:
        return [text] if text else []
    pieces: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n|(?<=[.!?])\s+", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > _MAX_PASSAGE_CHARS:  # unbroken giant run
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(para[:_MAX_PASSAGE_CHARS])
            para = para[_MAX_PASSAGE_CHARS:]
        if cur and len(cur) + len(para) + 1 > _MAX_PASSAGE_CHARS:
            pieces.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def _status_of(text: str) -> str:
    m = _STATUS_RE.search(text)
    return m.group(1) if m else ""


def _read_text(path: Path) -> str:
    with open(path, "rb") as fh:  # read-only
        data = fh.read(_MAX_FILE_BYTES)
    return data.decode("utf-8-sig", errors="replace")


def _md_table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _parse_markdown(text: str, source: str, doc_title: str) -> list[dict]:
    passages: list[dict] = []
    heading = doc_title
    buf: list[str] = []
    header_cells: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        for piece in _split_long(body):
            passages.append(_passage(heading, source, heading, piece,
                                     _status_of(piece) if source.lower().endswith("rules.md") else ""))

    for line in text.splitlines():
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            header_cells = []
            heading = m.group(2) or doc_title
            if m.group(1) == "#" and doc_title == source:
                doc_title = heading
            continue
        if line.lstrip().startswith("|"):
            cells = _md_table_cells(line)
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
                continue  # separator row
            if not header_cells:
                header_cells = cells
                continue
            # one table row = one passage, titled by its first cell
            flush()
            pairs = [f"{h}: {c}" if h else c for h, c in zip(header_cells, cells)]
            body = "; ".join(pairs)
            passages.append(_passage(cells[0] if cells and cells[0] else heading,
                                     source, heading, body,
                                     _status_of(body) if source.lower().endswith("rules.md") else ""))
            continue
        header_cells = [] if not line.strip() else header_cells
        buf.append(line)
    flush()
    return [p for p in passages if p["text"]]


class _HtmlText(HTMLParser):
    """Collects (heading, text) blocks from HTML. Never raises on bad markup."""

    _BLOCK = {"p", "div", "li", "tr", "br", "table", "ul", "ol", "section",
              "article", "header", "footer", "pre", "td", "th", "dd", "dt"}
    _SKIP = {"script", "style", "head", "template", "noscript"}
    _HEAD = {"h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.sections: list[tuple[str, str]] = []
        self._skip = 0
        self._in_title = False
        self._in_head_tag = False
        self._head_buf: list[str] = []
        self._heading = ""
        self._buf: list[str] = []

    def _flush(self) -> None:
        body = re.sub(r"[ \t]+", " ", "".join(self._buf))
        body = re.sub(r"\n\s*\n+", "\n", body).strip()
        if body:
            self.sections.append((self._heading, body))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP and tag != "head":
            self._skip += 1
        if tag in self._HEAD:
            self._flush()
            self._in_head_tag = True
            self._head_buf = []
        elif tag in self._BLOCK:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and tag != "head" and self._skip:
            self._skip -= 1
        if tag in self._HEAD and self._in_head_tag:
            self._in_head_tag = False
            self._heading = " ".join("".join(self._head_buf).split())
        elif tag in self._BLOCK:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._in_head_tag:
            self._head_buf.append(data)
        self._buf.append(data)

    def finish(self) -> None:
        self.close()
        self._flush()


def _parse_html(text: str, source: str) -> list[dict]:
    p = _HtmlText()
    try:
        p.feed(text)
        p.finish()
    except Exception:  # malformed markup: keep whatever was collected
        p._flush()
    title = " ".join(p.title.split()) or source
    out: list[dict] = []
    for heading, body in p.sections:
        # A heading's own text is the first line of its section; drop duplicate
        sec = heading or title
        for piece in _split_long(body):
            out.append(_passage(sec, source, sec, piece))
    return out


def _parse_rules_toml(text: str, source: str) -> list[dict]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    out: list[dict] = []
    for rid, rule in (data.get("rules") or {}).items():
        if not isinstance(rule, dict):
            continue
        status = str(rule.get("status", "")).upper()
        label = str(rule.get("label", rid))
        bits = [f"Rule id: {rid}", f"Label: {label}"]
        for key in ("value", "status", "question", "source", "confirmed_on", "used_in", "note", "notes"):
            if rule.get(key) not in (None, ""):
                bits.append(f"{key.replace('_', ' ').capitalize()}: {rule[key]}")
        out.append(_passage(label, source, rid, "\n".join(bits), status))
    return out


def _parse_pending_toml(text: str, source: str) -> list[dict]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    out: list[dict] = []
    for q in data.get("questions") or []:
        if not isinstance(q, dict):
            continue
        ref = str(q.get("ref", ""))
        topic = str(q.get("topic", ""))
        bits = [f"Question {ref}: {q.get('question', '')}"]
        if topic:
            bits.insert(0, f"Topic: {topic}")
        # option labels are plain-language; their `effect` tables are internal
        for opt in q.get("options") or []:
            if isinstance(opt, dict) and opt.get("label"):
                bits.append(f"Option: {opt['label']}")
        out.append(_passage(f"{ref} {topic}".strip(), source, ref,
                            "\n".join(bits), "PENDING"))
    return out


def _parse_csv(text: str, source: str) -> list[dict]:
    out: list[dict] = []
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None)
    if not header:
        return out
    header = [h.strip() for h in header]
    for row in rows:
        if not any(c.strip() for c in row):
            continue
        pairs = [f"{h}: {c.strip()}" for h, c in zip(header, row) if c.strip()]
        out.append(_passage(row[0].strip(), source, "Flag catalogue", "; ".join(pairs)))
    return out


# --------------------------------------------------------------------------
# Source discovery
# --------------------------------------------------------------------------

def _default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _allowed(root: Path, path: Path) -> bool:
    """True only for a real file inside root that avoids every excluded name."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    if path.name.lower() in EXCLUDED_NAMES:
        return False
    if any(part.lower() in EXCLUDED_DIRS for part in rel.parts[:-1]):
        return False
    return path.is_file()


def _discover(root: Path) -> list[Path]:
    cands: list[Path] = [
        root / "03_MASTER_DATA" / "rules.md",
        root / "03_MASTER_DATA" / "rules.toml",
        root / "03_MASTER_DATA" / "flag_catalogue.csv",
        root / "03_MASTER_DATA" / "pending_questions.toml",
        root / "README.md",
    ]
    for folder, pattern in (("00_START_HERE", "*.html"), ("docs", "*.md")):
        d = root / folder
        if d.is_dir():
            cands.extend(sorted(d.glob(pattern)))
            if folder == "docs":
                cands.extend(sorted(p for p in d.rglob("*.md") if p.parent != d))
    seen, out = set(), []
    for p in cands:
        if p not in seen and _allowed(root, p):
            seen.add(p)
            out.append(p)
    return out


def _signature(files: list[Path]) -> tuple:
    sig = []
    for p in files:
        try:
            st = p.stat()
            sig.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(p), -1, -1))
    return tuple(sig)


def _passages_for(root: Path, path: Path) -> list[dict]:
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    try:
        text = _read_text(path)
    except OSError:
        return []
    suffix = path.suffix.lower()
    name = path.name.lower()
    if name == "rules.toml":
        return _parse_rules_toml(text, rel)
    if name == "pending_questions.toml":
        return _parse_pending_toml(text, rel)
    if suffix == ".csv":
        return _parse_csv(text, rel)
    if suffix in (".html", ".htm"):
        return _parse_html(text, rel)
    return _parse_markdown(text, rel, rel)


# --------------------------------------------------------------------------
# Index (BM25)
# --------------------------------------------------------------------------

class _Index:
    K1 = 1.5
    B = 0.75

    def __init__(self, passages: list[dict], signature: tuple) -> None:
        self.signature = signature
        self.passages = passages
        self.tf: list[Counter] = []
        self.seq: list[list[str]] = []
        self.lens: list[int] = []
        df: Counter = Counter()
        for p in passages:
            title_toks = _tokens(f"{p['title']} {p['section']}")
            body = _tokens(p["text"])
            toks = body + title_toks * 2  # title words count extra
            c = Counter(toks)
            self.tf.append(c)
            self.seq.append(body)
            self.lens.append(len(toks))
            df.update(c.keys())
        n = len(passages)
        self.avg = (sum(self.lens) / n) if n else 0.0
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def rank(self, q_tokens: list[str], limit: int) -> list[tuple[float, int]]:
        if not self.passages or not q_tokens:
            return []
        uniq = list(dict.fromkeys(q_tokens))
        bigrams = {(a, b) for a, b in zip(q_tokens, q_tokens[1:]) if a != b}
        scored: list[tuple[float, int]] = []
        for i, c in enumerate(self.tf):
            s = 0.0
            hit_terms = 0
            for t in uniq:
                f = c.get(t)
                if not f:
                    continue
                hit_terms += 1
                denom = f + self.K1 * (1 - self.B + self.B * self.lens[i] / (self.avg or 1))
                s += self.idf.get(t, 0.0) * f * (self.K1 + 1) / denom
            if not hit_terms:
                continue
            # phrase boost: adjacent query words appearing adjacent in text
            if bigrams:
                seq = self.seq[i]
                found = sum(1 for a, b in zip(seq, seq[1:]) if (a, b) in bigrams)
                s *= 1 + min(found, 3) * 0.35
            # coverage boost: passages that match more of the query words
            s *= 0.6 + 0.4 * hit_terms / len(uniq)
            scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[:limit]


_lock = threading.RLock()
_cache: dict[str, _Index] = {}


def _build(root: Path) -> _Index:
    files = _discover(root)
    passages: list[dict] = []
    for f in files:
        passages.extend(_passages_for(root, f))
    return _Index(passages, _signature(files))


def _get_index(root: Path, force: bool = False) -> _Index:
    key = str(root.resolve())
    with _lock:
        idx = _cache.get(key)
        if not force and idx is not None:
            if _signature(_discover(root)) == idx.signature:
                return idx
        idx = _build(root)
        _cache[key] = idx
        return idx


def rebuild_index(root: str | Path | None = None) -> int:
    """Force a rebuild now. Returns the number of passages indexed."""
    r = Path(root) if root else _default_root()
    return len(_get_index(r, force=True).passages)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _snippet(text: str, q_tokens: list[str], status: str) -> str:
    flat = " ".join(text.split())
    want = set(q_tokens)
    start = 0
    for m in _WORD_RE.finditer(flat):
        if any(_stem(_fold(m.group(0))) == t or _fold(m.group(0)) == t for t in want):
            start = max(0, m.start() - 60)
            break
    if start > 0:
        sp = flat.find(" ", start)
        start = sp + 1 if 0 <= sp < start + 30 else start
    chunk = flat[start:start + _SNIPPET_CHARS]
    if start + _SNIPPET_CHARS < len(flat):
        chunk = chunk.rsplit(" ", 1)[0] + "..." if " " in chunk else chunk + "..."
    if start > 0:
        chunk = "..." + chunk
    return f"[{status}] {chunk}" if status else chunk


def _no_match(query: str) -> dict:
    return {"title": "No match", "source": "", "section": "", "status": "",
            "snippet": _NO_MATCH_SUGGESTION, "score": 0.0, "no_match": True}


def search_full(q, limit: int = 5, root: str | Path | None = None) -> dict:
    query = q if isinstance(q, str) else ("" if q is None else str(q))
    query = query.strip()[:_MAX_QUERY_CHARS]
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 5
    q_tokens = _tokens(query)[:_MAX_QUERY_TOKENS]
    if not q_tokens:
        # nothing searchable (empty, only stopwords, only punctuation)
        return {"query": query, "results": [], "no_match": True,
                "suggestion": _NO_MATCH_SUGGESTION}
    idx = _get_index(Path(root) if root else _default_root())
    results = []
    for score, i in idx.rank(q_tokens, limit):
        p = idx.passages[i]
        results.append({
            "title": p["title"], "source": p["source"], "section": p["section"],
            "status": p["status"], "score": round(score, 4), "no_match": False,
            "snippet": _snippet(p["text"], q_tokens, p["status"]),
        })
    return {"query": query, "results": results, "no_match": not results,
            "suggestion": "" if results else _NO_MATCH_SUGGESTION}


def search(q, limit: int = 5, root: str | Path | None = None) -> list[dict]:
    """Ranked passages for q. If nothing matches, returns a single hit with
    no_match=True whose snippet is a suggestion (never a guessed answer)."""
    full = search_full(q, limit, root)
    return full["results"] or [_no_match(full["query"])]


# --------------------------------------------------------------------------
# Step 2 extension point: DISABLED. Not implemented on purpose.
# --------------------------------------------------------------------------

def answer_with_llm(question: str, passages: list[dict], *args, **kwargs):
    """DISABLED extension point for a later step 2. Always raises.

    If JK Zentra approves step 2, this would send ONLY the question and the
    retrieved documentation passages (rules, flag catalogue, manuals) to a
    language model, never staff data, clock punches, reports, names or IDs
    from the data folders. It would need an API key stored in .env (never in
    code or git) and JK Zentra's explicit approval first. Until then it does
    nothing and no network code exists in this module.
    """
    raise NotImplementedError(
        "answer_with_llm is disabled: step 2 needs an API key in .env and "
        "JK Zentra's approval. Use search() for retrieval-only help."
    )
