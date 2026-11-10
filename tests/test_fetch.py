"""缓存与失败可见测试：同一代码不重复请求、跨天自动刷新、挂了必须报错。"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

from src import fetch_news, fetch_quote
from src.fetch_announcements import AnnouncementFetchError
from src.fetch_news import NewsFetchError
from src.fetch_quote import FetchError
from src.models import NewsItem

FIXED_DAY = date(2026, 11, 10)


def _fake_announcements(code: str, name: str) -> tuple[NewsItem, ...]:
    """测试专用：公告一律走这个桩，绝不真的请求巨潮。"""
    return (NewsItem(url="https://static.cninfo.com.cn/finalpage/2026-11-10/1.PDF",
                     title="三季度报告", content="2026-11-10 三季度报告",
                     category="announcement"),)


class FakeFrame:
    empty = False

    def to_dict(self, orient="records"):
        return [
            {"代码": "688981", "名称": "中芯国际", "最新价": "95.2",
             "涨跌幅": "1.2", "成交量": "123456"},
            {"代码": "600519", "名称": "贵州茅台", "最新价": "1500.0",
             "涨跌幅": "-0.3", "成交量": "999"},
        ]


class FakeClient:
    def __init__(self):
        self.queries: list[str] = []

    def search(self, **kwargs):
        self.queries.append(kwargs["query"])
        is_announcement = "公告" in kwargs["query"]
        return {"results": [
            {"url": ("https://www.cninfo.com.cn/notice/1" if is_announcement
                     else "https://finance.eastmoney.com/a/1.html"),
             "title": "三季度报告", "content": "营业收入同比增长"},
            {"url": "", "title": "无链接条目", "content": "应被丢弃"},
        ]}


class BrokenClient:
    def search(self, **kwargs):
        raise RuntimeError("额度用尽")


class TestQuoteCache(unittest.TestCase):

    def setUp(self):
        fetch_quote._load_spot.cache_clear()

    def tearDown(self):
        fetch_quote._load_spot.cache_clear()

    def _fake_akshare(self, calls):
        def spot():
            calls.append(1)
            return FakeFrame()
        return types.SimpleNamespace(stock_zh_a_spot_em=spot)

    def test_many_codes_only_one_request_per_day(self):
        calls: list[int] = []
        with patch.dict(sys.modules, {"akshare": self._fake_akshare(calls)}):
            smic = fetch_quote.get_quote("688981", day="2026-11-10")
            moutai = fetch_quote.get_quote("600519", day="2026-11-10")
        self.assertEqual(len(calls), 1)  # 全局缓存，只抓一次
        self.assertEqual(smic.name, "中芯国际")
        self.assertEqual(smic.change_pct, 1.2)
        self.assertEqual(moutai.change_pct, -0.3)

    def test_cache_expires_across_days(self):
        calls: list[int] = []
        with patch.dict(sys.modules, {"akshare": self._fake_akshare(calls)}):
            fetch_quote.get_quote("688981", day="2026-11-10")
            fetch_quote.get_quote("688981", day="2026-11-11")
        self.assertEqual(len(calls), 2)  # 站点长期运行不会返回昨天的行情

    def test_unknown_code_raises(self):
        calls: list[int] = []
        with patch.dict(sys.modules, {"akshare": self._fake_akshare(calls)}), \
                self.assertRaises(FetchError):
            fetch_quote.get_quote("000000", day="2026-11-10")


class TestNewsCache(unittest.TestCase):

    def setUp(self):
        fetch_news._cached_search.cache_clear()

    def tearDown(self):
        fetch_news._cached_search.cache_clear()

    def test_same_code_does_not_repeat_search(self):
        client = FakeClient()
        with patch("src.fetch_news.fetch_announcements", _fake_announcements), \
                patch("src.fetch_news._get_client", lambda: client), \
                patch("src.fetch_news.today_cn", lambda: FIXED_DAY):
            first = fetch_news.fetch_all("688981", "中芯国际")
            second = fetch_news.fetch_all("688981", "中芯国际")
        self.assertEqual(len(client.queries), 1)  # 公告走巨潮，Tavily 只查新闻一次
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual({item.category for item in first},
                         {"announcement", "news"})
        self.assertTrue(all(item.url.startswith("https://") for item in first))

    def test_news_failure_is_visible(self):
        with patch("src.fetch_news.fetch_announcements", _fake_announcements), \
                patch("src.fetch_news._get_client", lambda: BrokenClient()), \
                patch("src.fetch_news.today_cn", lambda: FIXED_DAY), \
                self.assertRaises(NewsFetchError):
            fetch_news.fetch_all("688981", "中芯国际")

    def test_announcement_failure_is_visible(self):
        def boom(code: str, name: str):
            raise AnnouncementFetchError("巨潮公告接口不可用")

        with patch("src.fetch_news.fetch_announcements", boom), \
                patch("src.fetch_news._get_client", lambda: FakeClient()), \
                self.assertRaises(AnnouncementFetchError):
            fetch_news.fetch_all("688981", "中芯国际")

    def test_missing_operator_key_is_reported(self):
        with patch.dict(sys.modules, {"tavily": types.SimpleNamespace()}), \
                patch.dict("os.environ", {}, clear=True), \
                self.assertRaises(NewsFetchError) as ctx:
            fetch_news._get_client()
        self.assertIn("运营方", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
