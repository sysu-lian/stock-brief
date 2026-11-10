"""行情模块离线测试：不装 akshare、不连网，全部用假帧。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src import fetch_quote
from src.fetch_quote import (
    FetchError,
    _from_bid_ask,
    _from_hist,
    _name_from_info,
    _pairs_to_map,
    get_quote,
)


class _Col:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class FakePairs:
    """模拟 akshare 的 item/value 两列长表。"""

    def __init__(self, mapping):
        self.empty = not mapping
        self._keys = list(mapping.keys())
        self._values = list(mapping.values())

    def __getitem__(self, key):
        if key == "item":
            return _Col(self._keys)
        if key == "value":
            return _Col(self._values)
        raise KeyError(key)


class _Row:
    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]


class FakeHist:
    """模拟日线 DataFrame，只支持 iloc[-1] 取最后一行的用法。"""

    def __init__(self, rows):
        self.empty = not rows
        self._rows = rows
        self.iloc = self

    def __getitem__(self, index):
        return _Row(self._rows[index])


def _hist_row(close=107.75, pct=-1.16, volume=255418):
    return {"收盘": close, "涨跌幅": pct, "成交量": volume}


class TestParsing(unittest.TestCase):
    def test_pairs_to_map(self):
        got = _pairs_to_map(FakePairs({"最新": 107.75, "涨幅": -1.16}))
        self.assertEqual(got, {"最新": 107.75, "涨幅": -1.16})

    def test_pairs_to_map_empty_raises(self):
        with self.assertRaises(FetchError):
            _pairs_to_map(FakePairs({}))

    def test_pairs_to_map_missing_columns_raises(self):
        class Broken:
            empty = False

            def __getitem__(self, key):
                raise KeyError(key)

        with self.assertRaises(FetchError):
            _pairs_to_map(Broken())

    def test_from_bid_ask_ok(self):
        got = _from_bid_ask({"最新": 107.75, "涨幅": -1.16, "总手": 255418})
        self.assertEqual(got, (107.75, -1.16, 255418.0))

    def test_from_bid_ask_missing_change_returns_none(self):
        self.assertIsNone(_from_bid_ask({"最新": 107.75}))

    def test_from_bid_ask_missing_price_returns_none(self):
        self.assertIsNone(_from_bid_ask({"涨幅": 1.2}))

    def test_from_bid_ask_volume_defaults_to_zero(self):
        self.assertEqual(_from_bid_ask({"最新": 10.0, "涨幅": 1.0}), (10.0, 1.0, 0.0))

    def test_from_hist_takes_last_row(self):
        frame = FakeHist([_hist_row(close=109.02, pct=-1.70), _hist_row()])
        self.assertEqual(_from_hist(frame), (107.75, -1.16, 255418.0))

    def test_from_hist_empty_raises(self):
        with self.assertRaises(FetchError):
            _from_hist(FakeHist([]))

    def test_from_hist_missing_field_raises(self):
        with self.assertRaises(FetchError):
            _from_hist(FakeHist([{"成交量": 1}]))

    def test_name_from_info(self):
        self.assertEqual(_name_from_info({"股票简称": "中芯国际"}, "688981"), "中芯国际")

    def test_name_from_info_blank_falls_back_to_code(self):
        self.assertEqual(_name_from_info({"股票简称": "   "}, "688981"), "688981")

    def test_name_from_info_missing_falls_back_to_code(self):
        self.assertEqual(_name_from_info({}, "688981"), "688981")


class TestGetQuote(unittest.TestCase):
    def setUp(self):
        fetch_quote._fetch_fields.cache_clear()

    def tearDown(self):
        fetch_quote._fetch_fields.cache_clear()

    def _patch(self, bid_ask=None, hist=None, info=None,
               bid_ask_error=None, hist_error=None, info_error=None):
        def _ba(code):
            if bid_ask_error:
                raise bid_ask_error
            return bid_ask

        def _hist_call(code, day):
            if hist_error:
                raise hist_error
            return hist

        def _info(code):
            if info_error:
                raise info_error
            return info

        return [
            patch.object(fetch_quote, "_call_bid_ask", _ba),
            patch.object(fetch_quote, "_call_hist", _hist_call),
            patch.object(fetch_quote, "_call_info", _info),
        ]

    def _run(self, **kwargs):
        patches = self._patch(**kwargs)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return get_quote("688981", day="2026-11-10")

    def test_bid_ask_hit(self):
        quote = self._run(
            bid_ask=FakePairs({"最新": 107.75, "涨幅": -1.16, "总手": 255418}),
            info=FakePairs({"股票简称": "中芯国际"}),
        )
        self.assertEqual(quote.code, "688981")
        self.assertEqual(quote.name, "中芯国际")
        self.assertAlmostEqual(quote.price, 107.75)
        self.assertAlmostEqual(quote.change_pct, -1.16)
        self.assertAlmostEqual(quote.volume, 255418)

    def test_falls_back_to_hist_when_bid_ask_fails(self):
        quote = self._run(
            bid_ask_error=RuntimeError("connection reset"),
            hist=FakeHist([_hist_row()]),
            info=FakePairs({"股票简称": "中芯国际"}),
        )
        self.assertAlmostEqual(quote.price, 107.75)
        self.assertAlmostEqual(quote.change_pct, -1.16)

    def test_falls_back_to_hist_when_bid_ask_lacks_fields(self):
        quote = self._run(
            bid_ask=FakePairs({"最新": 107.75}),
            hist=FakeHist([_hist_row()]),
            info=FakePairs({"股票简称": "中芯国际"}),
        )
        self.assertAlmostEqual(quote.change_pct, -1.16)

    def test_both_sources_fail_raises_with_both_reasons(self):
        with self.assertRaises(FetchError) as ctx:
            self._run(
                bid_ask_error=RuntimeError("盘口炸了"),
                hist_error=RuntimeError("日线也炸了"),
                info=FakePairs({"股票简称": "中芯国际"}),
            )
        message = str(ctx.exception)
        self.assertIn("盘口炸了", message)
        self.assertIn("日线也炸了", message)
        self.assertIn("688981", message)

    def test_name_failure_degrades_to_code(self):
        quote = self._run(
            bid_ask=FakePairs({"最新": 107.75, "涨幅": -1.16, "总手": 1}),
            info_error=RuntimeError("info down"),
        )
        self.assertEqual(quote.name, "688981")

    def test_cache_hits_once_per_day(self):
        calls = []

        def _ba(code):
            calls.append(code)
            return FakePairs({"最新": 107.75, "涨幅": -1.16, "总手": 1})

        with patch.object(fetch_quote, "_call_bid_ask", _ba), \
             patch.object(fetch_quote, "_call_hist", lambda c, d: None), \
             patch.object(fetch_quote, "_call_info", lambda c: FakePairs({"股票简称": "中芯国际"})):
            get_quote("688981", day="2026-11-10")
            get_quote("688981", day="2026-11-10")
        self.assertEqual(len(calls), 1)

    def test_cache_invalidated_on_new_day(self):
        calls = []

        def _ba(code):
            calls.append(code)
            return FakePairs({"最新": 107.75, "涨幅": -1.16, "总手": 1})

        with patch.object(fetch_quote, "_call_bid_ask", _ba), \
             patch.object(fetch_quote, "_call_hist", lambda c, d: None), \
             patch.object(fetch_quote, "_call_info", lambda c: FakePairs({"股票简称": "中芯国际"})):
            get_quote("688981", day="2026-11-10")
            get_quote("688981", day="2026-11-11")
        self.assertEqual(len(calls), 2)

    def test_invalid_code_rejected_before_network(self):
        with self.assertRaises(FetchError):
            get_quote("ABC", day="2026-11-10")

    def test_code_is_zero_padded(self):
        quote = self._run(
            bid_ask=FakePairs({"最新": 10.0, "涨幅": 1.0, "总手": 1}),
            info=FakePairs({"股票简称": "某股"}),
        )
        self.assertEqual(quote.code, "688981")


if __name__ == "__main__":
    unittest.main()
