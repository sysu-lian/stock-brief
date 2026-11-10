"""PDF 正文抽取测试：清理、大小上限、重试、失败包装、截断、缓存（不依赖网络）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src import pdf_text
from src.pdf_text import (
    MAX_BYTES, PdfExtractError, _clean, _download, _is_garbled, extract_text,
)


class FakeResponse:
    def __init__(self, body: bytes = b"", headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TestClean(unittest.TestCase):

    def test_strips_private_use_and_normalizes(self):
        raw = "标题\uf06c 内容\r\n\r\n  第二行  \u3000 尾"
        self.assertEqual(_clean(raw), "标题 内容\n第二行 尾")

    def test_empty(self):
        self.assertEqual(_clean(""), "")
        self.assertEqual(_clean(None), "")


class TestDownload(unittest.TestCase):

    def test_declared_size_limit_rejected(self):
        response = FakeResponse(headers={"Content-Length": str(MAX_BYTES + 1)})
        with patch("src.pdf_text.urllib.request.urlopen", lambda *a, **k: response), \
                self.assertRaises(PdfExtractError):
            _download("https://x/1.pdf")

    def test_read_size_limit_rejected(self):
        response = FakeResponse(body=b"x" * 11, headers={})
        with patch("src.pdf_text.urllib.request.urlopen", lambda *a, **k: response), \
                self.assertRaises(PdfExtractError):
            _download("https://x/1.pdf", max_bytes=10)

    def test_small_body_ok(self):
        response = FakeResponse(body=b"%PDF-1.7", headers={})
        with patch("src.pdf_text.urllib.request.urlopen", lambda *a, **k: response):
            self.assertEqual(_download("https://x/1.pdf"), b"%PDF-1.7")


class TestExtractText(unittest.TestCase):

    def setUp(self):
        extract_text.cache_clear()

    def tearDown(self):
        extract_text.cache_clear()

    def test_retries_once_then_succeeds(self):
        calls = []

        def flaky(url, **_kw):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("连接提前断开")
            return b"%PDF-1.7"

        with patch("src.pdf_text._download", flaky), \
                patch("src.pdf_text._extract", lambda data, **_k: "正文内容"), \
                patch("src.pdf_text.time.sleep", lambda _s: None):
            self.assertEqual(extract_text("https://x/1.pdf"), "正文内容")
        self.assertEqual(len(calls), 2)

    def test_raises_after_two_failures(self):
        def boom(_url, **_kw):
            raise OSError("一直断")

        with patch("src.pdf_text._download", boom), \
                patch("src.pdf_text.time.sleep", lambda _s: None), \
                self.assertRaises(PdfExtractError):
            extract_text("https://x/1.pdf")

    def test_size_error_not_retried(self):
        calls = []

        def too_big(_url, **_kw):
            calls.append(1)
            raise PdfExtractError("过大")

        with patch("src.pdf_text._download", too_big), \
                self.assertRaises(PdfExtractError):
            extract_text("https://x/1.pdf")
        self.assertEqual(len(calls), 1)

    def test_empty_text_is_visible(self):
        with patch("src.pdf_text._download", lambda _u, **_k: b"%PDF"), \
                patch("src.pdf_text._extract", lambda _d, **_k: "   \n  "), \
                self.assertRaises(PdfExtractError):
            extract_text("https://x/1.pdf")

    def test_truncates_to_max_chars(self):
        with patch("src.pdf_text._download", lambda _u, **_k: b"%PDF"), \
                patch("src.pdf_text._extract", lambda _d, **_k: "字" * 50):
            self.assertEqual(len(extract_text("https://x/1.pdf", max_chars=20)), 20)

    def test_cached_by_url(self):
        calls = []

        def dl(url, **_kw):
            calls.append(url)
            return b"%PDF"

        with patch("src.pdf_text._download", dl), \
                patch("src.pdf_text._extract", lambda _d, **_k: "正文"):
            extract_text("https://x/1.pdf")
            extract_text("https://x/1.pdf")
        self.assertEqual(len(calls), 1)


class TestGarbled(unittest.TestCase):

    GOOD = ("A股代码：688981 A股简称：中芯国际 公告编号：2026-033\n"
            "关于召开2026年第三季度业绩说明会的预告公告\n"
            "会议召开时间：2026年11月13日（星期五）上午8:30-9:30")
    # 真实巨潮港股月报表抽取出的乱码片段（CID 字体编码损坏）
    BAD = ("FF301\nW\x01\x12\x01ֻ∉\x01\x01\x11\x12\x01܋∉ \x12\x0f\x13\x0f\x12\n"
           "ٺܢứྛದ۴ࠣῌuഈ൧ἲ≣vֻ\x01ࣴᅣഈ൧֥ཐۖ≁䨷⊈ಊứྛದ֥⊈ಊṉọᄅḸі")

    def test_good_text_is_not_garbled(self):
        self.assertFalse(_is_garbled(self.GOOD))

    def test_broken_font_is_garbled(self):
        self.assertTrue(_is_garbled(self.BAD))

    def test_empty_is_garbled(self):
        self.assertTrue(_is_garbled(""))

    def test_extract_text_rejects_garbled(self):
        extract_text.cache_clear()
        with patch("src.pdf_text._download", lambda _u, **_k: b"%PDF"), \
                patch("src.pdf_text._extract", lambda _d, **_k: self.BAD), \
                self.assertRaises(PdfExtractError) as ctx:
            extract_text("https://x/bad.pdf")
        self.assertIn("乱码", str(ctx.exception))
        extract_text.cache_clear()


if __name__ == "__main__":
    unittest.main()
