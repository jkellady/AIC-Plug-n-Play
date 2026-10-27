"""File-level checks for the static front end (no browser needed)."""
import json
import re
import struct
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "webapp" / "static"
FILES = ["index.html", "app.css", "app.js", "manifest.webmanifest", "sw.js",
         "icons/icon-192.png", "icons/icon-512.png", "icons/favicon.png"]


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", FILES)
def test_file_exists_and_not_empty(name):
    p = STATIC / name
    assert p.is_file(), name
    assert p.stat().st_size > 0, name


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_manifest_valid():
    m = json.loads(text("manifest.webmanifest"))
    assert m["name"] == "HR Attendance"
    assert m["display"] == "standalone"
    for key in ("short_name", "start_url", "theme_color", "background_color", "icons"):
        assert m.get(key), key
    sizes = set()
    for icon in m["icons"]:
        f = STATIC / icon["src"]
        assert f.is_file(), icon["src"]
        w, h = png_size(f)
        assert icon["sizes"] == f"{w}x{h}"
        sizes.add(w)
    assert {192, 512} <= sizes


def test_icon_sizes_and_small():
    assert png_size(STATIC / "icons/icon-192.png") == (192, 192)
    assert png_size(STATIC / "icons/icon-512.png") == (512, 512)
    w, h = png_size(STATIC / "icons/favicon.png")
    assert w == h and w <= 64
    for f in (STATIC / "icons").glob("*.png"):
        assert f.stat().st_size < 60_000


def test_index_only_local_references():
    html = text("index.html")
    assert not re.search(r"https?://", html, re.I)
    assert not re.search(r"(src|href)\s*=\s*[\"']//", html)
    for ref in re.findall(r"(?:src|href)\s*=\s*[\"']([^\"'#]+)[\"']", html):
        assert (STATIC / ref).is_file(), ref


def test_index_no_inline_script_style_or_handlers():
    html = text("index.html")
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        assert "src=" in m.group(1) and not m.group(2).strip()
    assert not re.search(r"<style\b", html, re.I)
    assert not re.search(r"\sstyle\s*=", html, re.I)
    assert not re.search(r"\son[a-z]+\s*=", html, re.I)
    assert "javascript:" not in html.lower()


def test_css_and_js_have_no_external_urls():
    for name in ("app.css", "app.js", "sw.js"):
        src = text(name)
        assert not re.search(r"https?://(?!demo\.invalid)", src), name
        assert "@import" not in src


def test_sw_never_caches_api_or_reports():
    sw = text("sw.js")
    shell = re.search(r"const SHELL = \[(.*?)\];", sw, re.S).group(1)
    assert "api" not in shell.lower()
    assert "report" not in shell.lower()
    assert "startsWith('/api')" in sw  # explicit bypass guard
    assert "method !== 'GET'" in sw


def test_app_js_no_unsafe_html_sinks():
    js = text("app.js")
    assert not re.search(r"innerHTML\s*[+]?=", js)
    assert "outerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "document.write" not in js
    assert not re.search(r"\beval\s*\(|new Function\s*\(", js)


def test_app_js_no_inline_style_attribute():
    # CSP style-src 'self' blocks style attributes; CSSOM (el.style.x) is fine.
    js = text("app.js")
    assert not re.search(r"setAttribute\(\s*['\"]style", js)
    assert not re.search(r"\bstyle\s*:\s*['\"]", js)


def test_demo_mode_is_url_flag_only_and_never_hits_network():
    js = text("app.js")
    assert "get('demo') === '1'" in js
    assert re.search(r"const DEMO\b", js)  # constant, cannot be flipped later
    assert not re.search(r"\bDEMO\s*=[^=]", js.replace("const DEMO =", ""))
    assert "if (DEMO) return demoApi" in js
    assert js.index("if (DEMO) return demoApi") < js.index("fetch(path")


def test_csrf_header_on_posts():
    assert "X-CSRF-Token" in text("app.js")


def test_all_api_paths_in_contract():
    allowed = {"login", "logout", "me", "overview", "exceptions", "staff", "staff/save", "outlets",
               "outlets/save", "reports", "reports/download", "reports/view", "reports/generate",
               "help", "scan/run", "upload", "health", "lan-info", "lan-set", "password", "profile",
               "users", "users/add", "users/update", "users/delete", "rules", "rules/update",
               "rules/add", "logs", "logs/view", "logs/download", "it/diagnose", "it/propose",
               "it/repair", "open-folder", "users/security", "users/unlock"}
    for p in set(re.findall(r"/api/([a-z/-]+)", text("app.js"))):
        assert p.rstrip("/") in allowed, p
