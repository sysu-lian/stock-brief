"""巨潮公告直连测试：全程断网、HTTP 层可注入，覆盖映射/查询/空结果/失败可见/去重/缓存。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src import fetch_announcements as fa
from src.fetch_announcements import (
    DEFAULT_DAYS_BACK,
    MAX_ITEMS,
    AnnouncementFetchError,
    _column_for,
    _fmt_time,
    fetch_announcements,
)
from src.models import NewsItem
from src.pdf_text import PdfExtractError

DAY = "2026-11-10"

MAP_ROWS = [
    {"code": "688981", "orgId": "gshk0000981", "zwjc": "中芯国际"},
    {"code": "300750", "orgId": "GD165627", "zwjc": "宁德时代"},
    {"code": "", "orgId": "ghost", "zwjc": "无代码"},       # 应跳过
    {"code": "000001", "orgId": "", "zwjc": "无orgId"},     # 应跳过
]

QUERY_RESPONSE = {
    "totalAnnouncement": 3,
    "announcements": [
        {"announcementTitle": "三季度报告", "announcementTime": 1793894400000,
         "adjunctUrl": "finalpage/2026-11-06/1225660175.PDF"},
        {"announcementTitle": "董事会决议公告", "announcementTime": 1793808000000,
         "adjunctUrl": "/finalpage/2026-11-05/2.PDF"},          # 带前导斜杠
        {"announcementTitle": "无原文链接的条目", "adjunctUrl": ""},  # 应丢弃
    ],
}


def fake_get(url, timeout=None):
    return {"stockList": MAP_ROWS}


def fake_post(url, data, timeout=None):
    return QUERY_RESPONSE


class Base(unittest.TestCase):
    def setUp(self):
        fa._org_map.cache_clear()
        fa._query.cache_clear()

    def tearDown(self):
        fa._org_map.cache_clear()
        fa._query.cache_clear()


class TestColumnAndTime(unittest.TestCase):

    def test_column_by_code_prefix(self):
        cases = {"688981": "sse", "600519": "sse", "000001": "szse",
                 "300750": "szse", "920000": "bj", "430047": "bj"}
        for code, expected in cases.items():
            self.assertEqual(_column_for(code), expected, code)

    def test_fmt_time(self):
        self.assertEqual(_fmt_time(1793894400000), "2026-11-06")
        for bad in (None, "", "bad", object()):
            self.assertEqual(_fmt_time(bad), "")


class TestOrgMap(Base):

    def test_parses_and_skips_incomplete_rows(self):
        with patch.object(fa, "_http_get_json", fake_get):
            mapping = fa._org_map(DAY)
        self.assertEqual(mapping["688981"]["orgId"], "gshk0000981")
        self.assertEqual(mapping["300750"]["orgId"], "GD165627")
        self.assertNotIn("", mapping)
        self.assertNotIn("000001", mapping)  # 缺 orgId 的行被跳过

    def test_mapping_cached_per_day(self):
        calls = []

        def counting_get(url, timeout=None):
            calls.append(url)
            return {"stockList": MAP_ROWS}

        with patch.object(fa, "_http_get_json", counting_get):
            fa._org_map(DAY)
            fa._org_map(DAY)
        self.assertEqual(len(calls), 1)

    def test_http_failure_is_visible(self):
        def boom(url, timeout=None):
            raise OSError("连接被重置")

        with patch.object(fa, "_http_get_json", boom), \
                self.assertRaises(AnnouncementFetchError):
            fa._org_map(DAY)

    def test_malformed_payload_is_visible(self):
        with patch.object(fa, "_http_get_json", lambda url, timeout=None: {"foo": []}), \
                self.assertRaises(AnnouncementFetchError):
            fa._org_map(DAY)


class TestQuery(Base):

    def _run(self, response=QUERY_RESPONSE, posted=None):
        posted = posted if posted is not None else []

        def counting_post(url, data, timeout=None):
            posted.append(data)
            return response

        with patch.object(fa, "_http_get_json", fake_get), \
                patch.object(fa, "_http_post_json", counting_post):
            return fa._query(DAY, "688981", 2), posted

    def test_payload_shape(self):
        _, posted = self._run()
        self.assertEqual(len(posted), 1)
        data = posted[0]
        self.assertEqual(data["stock"], "688981,gshk0000981")  # orgId 必须带上
        self.assertEqual(data["column"], "sse")
        self.assertEqual(data["seDate"], "2026-11-08~2026-11-10")  # 2 天回看
        self.assertEqual(data["sortName"], "announcementTime")
        self.assertEqual(data["sortType"], "-1")

    def test_maps_to_news_items_with_https_pdf(self):
        items, _ = self._run()
        self.assertEqual(len(items), 2)  # 无 adjunctUrl 的条目被丢弃
        self.assertEqual(items[0].category, "announcement")
        self.assertEqual(items[0].title, "三季度报告")
        self.assertEqual(items[0].content, "2026-11-06 三季度报告")
        self.assertTrue(items[0].url.startswith("https://static.cninfo.com.cn/"))
        self.assertTrue(items[0].url.endswith("1225660175.PDF"))
        self.assertNotIn("/ /", items[1].url)  # 前导斜杠被正确规整
        self.assertTrue(items[1].url.startswith("https://static.cninfo.com.cn/finalpage/"))

    def test_empty_window_is_not_an_error(self):
        items, _ = self._run(response={"totalAnnouncement": 0, "announcements": None})
        self.assertEqual(items, ())

    def test_unknown_code_is_visible(self):
        with patch.object(fa, "_http_get_json", fake_get), \
                self.assertRaises(AnnouncementFetchError):
            fa._query(DAY, "999999", 2)

    def test_http_failure_is_visible(self):
        def boom(url, data, timeout=None):
            raise OSError("超时")

        with patch.object(fa, "_http_get_json", fake_get), \
                patch.object(fa, "_http_post_json", boom), \
                self.assertRaises(AnnouncementFetchError) as ctx:
            fa._query(DAY, "688981", 2)
        self.assertIn("688981", str(ctx.exception))

    def test_dedup_by_url(self):
        response = {"totalAnnouncement": 2, "announcements": [
            {"announcementTitle": "同一份", "announcementTime": 1793894400000,
             "adjunctUrl": "finalpage/x/1.PDF"},
            {"announcementTitle": "重复链接", "announcementTime": 1793894400000,
             "adjunctUrl": "finalpage/x/1.PDF"},
        ]}
        items, _ = self._run(response=response)
        self.assertEqual(len(items), 1)

    def test_truncates_to_max_items(self):
        response = {"totalAnnouncement": 50, "announcements": [
            {"announcementTitle": f"公告{i}", "announcementTime": 1793894400000,
             "adjunctUrl": f"finalpage/x/{i}.PDF"} for i in range(50)]}
        items, _ = self._run(response=response)
        self.assertEqual(len(items), MAX_ITEMS)

    def test_result_cached_per_day_code_window(self):
        _, posted = self._run()
        with patch.object(fa, "_http_get_json", fake_get), \
                patch.object(fa, "_http_post_json", lambda url, data, timeout=None: QUERY_RESPONSE):
            fa._query(DAY, "688981", 2)
        self.assertEqual(len(posted), 1)  # 第二次命中缓存，不再发请求


class TestPublicEntry(Base):

    def test_day_and_window_passed_through(self):
        seen = []
        with patch.object(fa, "_query", lambda day, code, wb: seen.append((day, code, wb)) or ()):
            fetch_announcements("688981", "中芯国际", day=DAY, days_back=5)
        self.assertEqual(seen, [(DAY, "688981", 5)])

    def test_env_overrides_default_window(self):
        seen = []
        with patch.object(fa, "_query", lambda day, code, wb: seen.append(wb) or ()), \
                patch.dict("os.environ", {"ANNOUNCEMENT_DAYS_BACK": "7"}):
            fetch_announcements("688981", day=DAY)
        self.assertEqual(seen, [7])

    def test_default_window_when_env_absent(self):
        seen = []
        with patch.object(fa, "_query", lambda day, code, wb: seen.append(wb) or ()), \
                patch.dict("os.environ", {}, clear=True):
            fetch_announcements("688981", day=DAY)
        self.assertEqual(seen, [DEFAULT_DAYS_BACK])


class TestRetry(unittest.TestCase):

    def test_retries_then_succeeds(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise OSError("网络抖动")
            return "ok"

        with patch("src.fetch_announcements.time.sleep", lambda _s: None):
            self.assertEqual(fa._retry(flaky), "ok")
        self.assertEqual(len(calls), 3)

    def test_raises_after_exhausting(self):
        def boom():
            raise OSError("一直失败")

        with patch("src.fetch_announcements.time.sleep", lambda _s: None), \
                self.assertRaises(OSError):
            fa._retry(boom, attempts=2)


def _item(url: str, title: str) -> NewsItem:
    return NewsItem(url=url, title=title, content=f"2026-11-10 {title}",
                    category="announcement")


class TestEnrich(Base):

    def test_enriches_top_important_items_in_place(self):
        items = (_item("https://x/1.PDF", "证券变动月报表"),
                 _item("https://x/2.PDF", "2026年三季度报告"),
                 _item("https://x/3.PDF", "董事会决议公告"),
                 _item("https://x/4.PDF", "关于股东减持的公告"))
        calls = []

        def fake_extract(url, **_kw):
            calls.append(url)
            return "正文：营业收入同比增长"

        with patch.object(fa, "extract_text", fake_extract):
            out = fa._enrich(items, top_n=2)

        # 关键词（报告 / 减持）优先于泛泛的月报表、董事会决议
        self.assertEqual(set(calls), {"https://x/2.PDF", "https://x/4.PDF"})
        self.assertIn("正文：营业收入同比增长", out[1].content)
        self.assertIn("正文：营业收入同比增长", out[3].content)
        self.assertNotIn("正文", out[0].content)
        self.assertEqual([i.url for i in out], [i.url for i in items])  # 顺序不变

    def test_failure_keeps_title_only(self):
        items = (_item("https://x/1.PDF", "三季度报告"),)

        def boom(_url, **_kw):
            raise PdfExtractError("解析失败")

        with patch.object(fa, "extract_text", boom):
            out = fa._enrich(items, top_n=1)
        self.assertEqual(out[0].content, items[0].content)  # 绝不伪造正文

    def test_disabled_by_env(self):
        items = (_item("https://x/1.PDF", "三季度报告"),)
        called = []
        with patch.object(fa, "extract_text",
                          lambda u, **_k: called.append(u) or "正文"), \
                patch.dict("os.environ", {"EXTRACT_ANNOUNCEMENT_BODY": "0"}):
            out = fa._enrich(items, top_n=1)
        self.assertEqual(called, [])
        self.assertEqual(out, items)

    def test_empty_items_is_noop(self):
        self.assertEqual(fa._enrich(()), ())

    def test_public_entry_enriches(self):
        items = (_item("https://x/1.PDF", "三季度报告"),)
        with patch.object(fa, "_query", lambda _d, _c, _w: items), \
                patch.object(fa, "extract_text", lambda _u, **_k: "正文内容"):
            out = fetch_announcements("688981", day=DAY)
        self.assertIn("正文内容", out[0].content)


if __name__ == "__main__":
    unittest.main()
