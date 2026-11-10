"""A 股交易日判断。GitHub Actions 的 cron 只认 UTC 和星期，认不出法定节假日。"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


class CalendarError(Exception):
    """交易日历获取失败。必须报错，不能退化成「周一到周五」假装正确。"""


def today_cn() -> date:
    """业务日期一律用北京时间，避免 UTC 跨日误判。"""
    return datetime.now(CN_TZ).date()


def is_trading_day(day: date, trade_dates: frozenset[str] | set[str]) -> bool:
    """纯函数：先排周末，再查权威交易日历（可注入，便于测试）。"""
    if day.weekday() >= 5:
        return False
    return day.isoformat() in trade_dates


@lru_cache(maxsize=1)
def load_trade_dates() -> frozenset[str]:
    try:
        import akshare as ak
        frame = ak.tool_trade_date_hist_sina()
    except Exception as exc:
        raise CalendarError(
            f"无法获取 A 股交易日历，拒绝继续（避免节假日误跑）：{exc}") from exc
    if frame is None or frame.empty or "trade_date" not in frame:
        raise CalendarError("A 股交易日历返回为空或字段缺失，拒绝继续")
    return frozenset(str(value) for value in frame["trade_date"])


def should_run(day: date | None = None,
               trade_dates: frozenset[str] | set[str] | None = None) -> bool:
    dates = trade_dates if trade_dates is not None else load_trade_dates()
    return is_trading_day(day or today_cn(), dates)
