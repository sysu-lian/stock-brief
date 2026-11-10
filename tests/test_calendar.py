"""交易日历测试：纯函数注入，不触发网络。"""
from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

from src.calendar import CalendarError, is_trading_day, load_trade_dates, should_run

DATES = frozenset({"2026-11-09", "2026-11-10", "2026-11-11"})


class TestCalendar(unittest.TestCase):

    def test_weekday_in_calendar_runs(self):
        self.assertTrue(is_trading_day(date(2026, 11, 10), DATES))

    def test_weekend_never_runs_even_if_listed(self):
        # 调休上班的周末也一律不跑，由交易日历兜底
        self.assertFalse(is_trading_day(date(2026, 11, 14),
                                       frozenset({"2026-11-14"})))

    def test_holiday_weekday_is_skipped(self):
        # 国庆期间的周四：星期是工作日，但不在交易日历里
        self.assertFalse(is_trading_day(date(2026, 10, 1), DATES))

    def test_should_run_uses_injected_calendar(self):
        self.assertTrue(should_run(date(2026, 11, 10), DATES))
        self.assertFalse(should_run(date(2026, 11, 12), DATES))

    def test_calendar_failure_must_be_visible(self):
        # 日历获取失败时不得静默退化成「周一到周五」
        def boom():
            raise RuntimeError("接口挂了")

        fake = types.SimpleNamespace(tool_trade_date_hist_sina=boom)
        load_trade_dates.cache_clear()
        try:
            with patch.dict(sys.modules, {"akshare": fake}):
                with self.assertRaises(CalendarError):
                    load_trade_dates()
        finally:
            load_trade_dates.cache_clear()


if __name__ == "__main__":
    unittest.main()
