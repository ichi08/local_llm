#!/usr/bin/env python3
"""Capture all page-document text, keeping navigation and other page noise."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.message import Message
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

EXTRACTOR = "html-body-text-with-noise-v1"


class PageTextParser(HTMLParser):
    """No article selection, summary, deduplication, or length limit."""

    BLOCKS = {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
              "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
              "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul"}
    NON_TEXT = {"script", "style", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.saw_body = False
        self.in_title = False
        self.ignored = []
        self.parts = []
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "body":
            self.in_body = self.saw_body = True
        if not self.in_body:
            return
        if self.ignored:
            if tag in self.NON_TEXT:
                self.ignored.append(tag)
            return
        if tag in self.NON_TEXT:
            self.ignored.append(tag)
        elif tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")
        elif tag == "img":
            # Keep textual alternatives already supplied by the page, without OCR.
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append("\n" + alt + "\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if self.in_body and tag in self.BLOCKS:
            self.parts.append("\n")
        if tag == "body":
            self.in_body = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.in_body and not self.ignored:
            self.parts.append(data)


def extract_page_text(html: str) -> tuple[str, str]:
    parser = PageTextParser()
    parser.feed(html)
    parser.close()
    if not parser.saw_body:
        raise ValueError("取得したHTMLにbodyがありません。入力を作成しません。")
    # Only normalize HTML layout whitespace; preserve every text occurrence in order.
    lines = [re.sub(r"[\t\r\f\v ]+", " ", line).strip() for line in "".join(parser.parts).split("\n")]
    text = "\n".join(line for line in lines if line) + "\n"
    if not text.strip():
        raise ValueError("ページの文章が空です。入力を作成しません。")
    title = " ".join("".join(parser.title_parts).split())
    return text, title


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_snapshot(cache_dir: Path, url: str, *, method: str = "static") -> tuple[str, dict]:
    index = json.loads((cache_dir / "index.json").read_text(encoding="utf-8"))
    snapshot_id = index.get("snapshot_id", "")
    if not re.fullmatch(r"[a-f0-9]{64}", snapshot_id):
        raise ValueError("ページ入力のsnapshot_idが不正です。")
    directory = cache_dir / snapshot_id
    metadata = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    if metadata.get("url") != url or metadata.get("extractor") != EXTRACTOR or metadata.get("capture_method", "static") != method:
        raise ValueError("ページ入力のURLまたは抽出方式が一致しません。")
    raw, text_bytes = (directory / "page.html").read_bytes(), (directory / "page.txt").read_bytes()
    if digest(raw) != metadata.get("html_sha256") or digest(text_bytes) != metadata.get("text_sha256"):
        raise ValueError("ページ入力のSHA-256が一致しません。保存済み入力を確認してください。")
    return text_bytes.decode("utf-8"), {**metadata, "snapshot_dir": str(directory.resolve())}


def fetch_html(url: str) -> tuple[bytes, str, str]:
    # Use the same curl/TLS trust store as model downloads; never disable certificate checks.
    with TemporaryDirectory(prefix="local-llm-page-") as temporary:
        path = Path(temporary) / "page.html"
        result = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                                 "--connect-timeout", "30", "--max-time", "60", "--output", str(path),
                                 "--write-out", "%{json}", "--url", url], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"ページ取得に失敗（curl終了コード{result.returncode}）: {result.stderr.strip()}")
        details = json.loads(result.stdout)
        headers = Message()
        headers["Content-Type"] = details.get("content_type") or ""
        if headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
            raise ValueError(f"入力ページがHTMLではありません: {headers.get_content_type()}")
        return path.read_bytes(), details["url_effective"], headers.get_content_charset() or "utf-8"


def fetch_rendered_html(url: str):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise ValueError('ブラウザー取得には ./setup.sh --browser が必要です。') from error
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':1440,'height':900}, locale='ja-JP')
            response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
            if not response or response.status >= 400:
                raise ValueError('ブラウザーのページ取得に失敗しました。')
            page.wait_for_timeout(3000)
            page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            page.wait_for_timeout(2000)
            return page.content().encode('utf-8'), page.url, 'utf-8', {'engine':'chromium','version':browser.version,
                    'viewport':{'width':1440,'height':900},'wait':'domcontentloaded + 3秒、最下部へスクロール + 2秒'}
        finally:
            browser.close()


def capture_page(url: str, cache_dir: Path, *, refresh: bool = False, method: str = "static") -> tuple[str, dict]:
    """Fetch once outside measurement; later runs reuse the immutable snapshot."""
    if not refresh and (cache_dir / "index.json").exists():
        return read_snapshot(cache_dir, url, method=method)
    if not url.startswith(("https://", "http://")):
        raise ValueError("入力ページはhttp/https URLで指定してください。")
    if method == 'browser':
        raw, effective_url, encoding, browser_info = fetch_rendered_html(url)
    elif method == 'static':
        raw, effective_url, encoding = fetch_html(url)
        browser_info = None
    else:
        raise ValueError('ページ取得方式はstaticまたはbrowserです。')
    try:
        html = raw.decode(encoding)
    except (LookupError, UnicodeError) as error:
        raise ValueError("ページの文字コードを解釈できません。文字を置換せず停止します。") from error
    text, title = extract_page_text(html)
    text_bytes = text.encode("utf-8")
    snapshot_id = digest(raw + EXTRACTOR.encode("ascii") + (b"browser" if method == "browser" else b""))
    directory = cache_dir / snapshot_id
    metadata = {"url": url, "effective_url": effective_url,
                "fetched_at": datetime.now(timezone.utc).isoformat(), "title": title,
                "encoding": encoding, "extractor": EXTRACTOR, "capture_method": method, "browser": browser_info,
                "html_sha256": digest(raw), "text_sha256": digest(text_bytes),
                "html_bytes": len(raw), "text_bytes": len(text_bytes), "text_characters": len(text)}
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "snapshot.json").exists():
        (directory / "page.html").write_bytes(raw)
        (directory / "page.txt").write_bytes(text_bytes)
        (directory / "snapshot.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary = cache_dir / "index.tmp"
    temporary.write_text(json.dumps({"snapshot_id": snapshot_id}) + "\n", encoding="utf-8")
    temporary.replace(cache_dir / "index.json")
    return read_snapshot(cache_dir, url, method=method)


def main() -> int:
    parser = argparse.ArgumentParser(description="URLのページ全文をノイズ込みで保存します。本文抽出・要約・重複除去はしません。")
    parser.add_argument("url")
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true", help="新しい取得結果を保存して既定入力を更新する。過去の取得結果は残す")
    parser.add_argument("--method", choices=("static","browser"), default="static")
    args = parser.parse_args()
    try:
        _, metadata = capture_page(args.url, args.cache_dir, refresh=args.refresh, method=args.method)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f"入力ページの取得・検証に失敗: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
