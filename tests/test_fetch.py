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
from src.models import NewsItem
from tests.test_quote import FakeHist, FakePairs

FIXED_DAY = date(2026, 11, 10)


def _fake_announcements(code: str, name: str) -> tuple[NewsItem, ...]:
    """测试专用：公告一律走这个桩，绝不真的请求巨潮。"""
    return (NewsItem(url="https://static.cninfo.com.cn/finalpage/2026-11-10/1.PDF",
                     title="三季度报告", content="2026-11-10 三季度报告",
                     category="announcement"),)


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


class TestQuoteAgainstFakeAkshare(unittest.TestCase):
    """校验我们调的 akshare 函数名与入参没变；接口一旦改名，这里先炸。"""

    def setUp(self):
        fetch_quote._fetch_fields.cache_clear()
        self.calls: list[tuple[str, str]] = []

    def tearDown(self):
        fetch_quote._fetch_fields.cache_clear()

    def _fake_akshare(self):
        calls = self.calls

        def bid_ask(symbol):
            calls.append(("bid_ask", symbol))
            return FakePairs({"最新": 107.75, "涨幅": -1.16, "总手": 255418})

        def hist(symbol, period, start_date, end_date, adjust):
            calls.append(("hist", symbol))
            return FakeHist([{"收盘": 107.75, "涨跌幅": -1.16, "成交量": 255418}])

        def info(symbol):
            calls.append(("info", symbol))
            return FakePairs({"股票简称": "中芯国际"})

        return types.SimpleNamespace(
            stock_bid_ask_em=bid_ask,
            stock_zh_a_hist=hist,
            stock_individual_info_em=info,
        )

    def test_calls_expected_akshare_functions(self):
        with patch.dict(sys.modules, {"akshare": self._fake_akshare()}):
            quote = fetch_quote.get_quote("688981", day="2026-11-10")
        self.assertEqual(quote.name, "中芯国际")
        self.assertAlmostEqual(quote.change_pct, -1.16)
        self.assertIn(("bid_ask", "688981"), self.calls)
        self.assertIn(("info", "688981"), self.calls)

    def test_cache_expires_across_days(self):
        with patch.dict(sys.modules, {"akshare": self._fake_akshare()}):
            fetch_quote.get_quote("688981", day="2026-11-10")
            fetch_quote.get_quote("688981", day="2026-11-11")
        # 站点长期运行不会返回昨天的行情
        self.assertEqual(len([c for c in self.calls if c[0] == "bid_ask"]), 2)

    def test_falls_back_to_hist_when_bid_ask_breaks(self):
        fake = self._fake_akshare()

        def boom(symbol):
            raise RuntimeError("盘口挂了")

        fake.stock_bid_ask_em = boom
        with patch.dict(sys.modules, {"akshare": fake}):
            quote = fetch_quote.get_quote("688981", day="2026-11-10")
        self.assertAlmostEqual(quote.price, 107.75)
        self.assertIn(("hist", "688981"), self.calls)


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
