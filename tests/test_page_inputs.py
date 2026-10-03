"""Full-page capture regressions; all HTTP responses are fixtures."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import benchmark_models as bench
from scripts import page_inputs

URL = "https://example.test/article"
HTML = """<!doctype html><html><head><title>Test article</title></head><body>
<nav>Menu <a>Search</a></nav><nav hidden>Menu <a>Search</a></nav>
<aside>AI-generated summary</aside><article><h1>Test article</h1>
<p>First &amp; second <b>inline</b> words.</p><img alt="Chart description">
<p>最後の段落を省略しない。</p></article><aside>Related article</aside>
<footer>Privacy Terms FOOTER-END</footer>
<script>do_not_include_script()</script><style>do_not_include_css</style>
<template>do_not_include_template</template></body></html>"""


class PageInputTests(unittest.TestCase):
    def test_all_body_text_noise_duplicates_and_tail_are_preserved(self):
        text, title = page_inputs.extract_page_text(HTML)
        self.assertEqual(title, "Test article")
        self.assertEqual(text.count("Menu Search"), 2)
        for expected in ("AI-generated summary", "First & second inline words.", "Chart description",
                         "最後の段落を省略しない。", "Related article", "Privacy Terms FOOTER-END"):
            self.assertIn(expected, text)
        self.assertTrue(text.endswith("FOOTER-END\n"))
        self.assertNotIn("do_not_include", text)

    def test_long_page_is_not_shortened(self):
        text, _ = page_inputs.extract_page_text("<body>" + "長文" * 50000 + "最終行</body>")
        self.assertEqual(text, "長文" * 50000 + "最終行\n")

    def test_raw_html_and_text_are_hashed_and_reused_without_network(self):
        with TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with patch.object(page_inputs, "fetch_html", return_value=(HTML.encode(), URL, "utf-8")) as fetch:
                text, snapshot = page_inputs.capture_page(URL, cache)
            fetch.assert_called_once()
            self.assertEqual(snapshot["html_sha256"], hashlib.sha256(HTML.encode()).hexdigest())
            self.assertEqual(snapshot["text_sha256"], hashlib.sha256(text.encode()).hexdigest())
            with patch.object(page_inputs, "fetch_html", side_effect=AssertionError("must stay offline")):
                self.assertEqual(page_inputs.capture_page(URL, cache), (text, snapshot))
            (Path(snapshot["snapshot_dir"]) / "page.txt").write_text("modified", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                page_inputs.read_snapshot(cache, URL)

    def test_refresh_keeps_old_capture(self):
        with TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with patch.object(page_inputs, "fetch_html", return_value=(HTML.encode(), URL, "utf-8")):
                _, old = page_inputs.capture_page(URL, cache)
            with patch.object(page_inputs, "fetch_html", return_value=(HTML.replace("First", "Updated").encode(), URL, "utf-8")):
                text, new = page_inputs.capture_page(URL, cache, refresh=True)
            self.assertNotEqual(old["snapshot_dir"], new["snapshot_dir"])
            self.assertTrue((Path(old["snapshot_dir"]) / "page.html").is_file())
            self.assertIn("Updated", text)

    def test_missing_snapshot_can_be_planned_offline_but_never_used_as_a_prompt(self):
        with TemporaryDirectory() as temporary:
            suite_path = Path(temporary) / "suite.json"
            suite = {"material_sources": {"page": {"url": URL, "cache_dir": "cache"}},
                     "questions": [{"id": "q", "category": "要約", "material_key": "page", "instruction": "全文を読む"}]}
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            with patch.object(page_inputs, "fetch_html", side_effect=AssertionError("plan must stay offline")):
                _, pending = bench.load_question_suite(suite_path, allow_missing_pages=True)
                self.assertTrue(pending[0]["input_pending"])
                self.assertNotIn("prompt", pending[0])
                with self.assertRaisesRegex(ValueError, "未取得"):
                    bench.load_question_suite(suite_path)
            with patch.object(page_inputs, "fetch_html", return_value=(HTML.encode(), URL, "utf-8")):
                resolved, questions = bench.load_question_suite(suite_path, fetch_pages=True)
            text = resolved["materials"]["page"]
            self.assertEqual(questions[0]["prompt"], "全文を読む\n\n資料：\n" + text)
            self.assertIn("FOOTER-END", questions[0]["prompt"])

    def test_error_page_title_is_not_accepted_as_article(self):
        with TemporaryDirectory() as temporary:
            suite_path = Path(temporary) / "suite.json"
            suite = {"material_sources": {"page": {"url": URL, "cache_dir": "cache", "expected_title": "Expected article"}},
                     "questions": [{"id": "q", "category": "要約", "material_key": "page", "instruction": "読む"}]}
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            with patch.object(page_inputs, "fetch_html", return_value=(HTML.encode(), URL, "utf-8")):
                with self.assertRaisesRegex(ValueError, "タイトル"):
                    bench.load_question_suite(suite_path, fetch_pages=True)


if __name__ == "__main__":
    unittest.main()
