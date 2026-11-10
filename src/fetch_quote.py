"""
AkShare 行情：单股接口，按交易日缓存。

为什么不用 `stock_zh_a_spot_em`（全市场快照）：
它一次要拉 5900+ 只股票、翻 59 页，2026-11-10 在线上机器实测 **68 秒**，
而 WebUI 单次请求整条链路预算只有 90 秒——一个取价就能把预算吃光。
改用单股接口后同一只股票实测 0.09~0.13 秒，5 只股票仍在 2 秒内。

取数策略（任何一步失败都不静默）：
1. 盘口 `stock_bid_ask_em`：给「最新/涨幅/总手」，盘中即实时；
2. 失败或字段缺失 → 退回日线 `stock_zh_a_hist`：「收盘/涨跌幅/成交量」，非交易日取最近一个交易日；
3. 名称 `stock_individual_info_em`：取「股票简称」；取不到就退回 6 位代码
   （宁可显示代码，也绝不猜一个可能是别家的名字）；
4. 1、2 都失败 → 抛 FetchError，把两边的失败原因都带出去。

延迟导入第三方库，保证纯逻辑测试无需安装 akshare。
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from .calendar import today_cn
from .models import Quote

HIST_LOOKBACK_DAYS = 10  # 日线回看窗口：覆盖长假，够取到最近一个交易日


class FetchError(Exception):
    """数据抓取失败。必须上报到简报/页面，不允许静默跳过。"""


def _to_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# 纯函数：把接口原始返回转成统一字段，不碰网络，可离线测试
# --------------------------------------------------------------------------


def _pairs_to_map(frame) -> dict:
    """AkShare 的 item/value 两列长表 → 普通 dict。"""
    if frame is None or getattr(frame, "empty", True):
        raise FetchError("接口返回空表")
    try:
        items = frame["item"].tolist()
        values = frame["value"].tolist()
    except Exception as exc:  # 接口字段改名
        raise FetchError(f"接口缺少 item/value 列：{exc}") from exc
    return {str(k): v for k, v in zip(items, values)}


def _from_bid_ask(mapping: dict) -> tuple[float, float, float] | None:
    """盘口 → (最新价, 涨跌幅, 成交量)。任一关键字段缺失返回 None，交给日线兜底。"""
    price = _to_float(mapping.get("最新"))
    change = _to_float(mapping.get("涨幅"))
    if price is None or change is None:
        return None
    volume = _to_float(mapping.get("总手"), 0.0) or 0.0
    return price, change, volume


def _from_hist(frame) -> tuple[float, float, float]:
    """日线最后一行 → (收盘, 涨跌幅, 成交量)。"""
    if frame is None or getattr(frame, "empty", True):
        raise FetchError("日线接口返回空表（可能停牌、退市或代码不存在）")
    try:
        row = frame.iloc[-1]
        price = _to_float(row["收盘"])
        change = _to_float(row["涨跌幅"])
        volume = _to_float(row["成交量"], 0.0) or 0.0
    except Exception as exc:
        raise FetchError(f"日线字段解析失败：{exc}") from exc
    if price is None or change is None:
        raise FetchError("日线缺少收盘价或涨跌幅")
    return price, change, volume


def _name_from_info(mapping: dict, code: str) -> str:
    """股票简称；缺失则退回代码——不猜名字，避免张冠李戴。"""
    name = str(mapping.get("股票简称") or "").strip()
    return name or code


# --------------------------------------------------------------------------
# I/O：延迟导入 akshare
# --------------------------------------------------------------------------


def _call_bid_ask(code: str):
    import akshare as ak

    return ak.stock_bid_ask_em(symbol=code)


def _call_hist(code: str, day: str):
    import akshare as ak

    end = date.fromisoformat(day)
    start = end - timedelta(days=HIST_LOOKBACK_DAYS)
    return ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
    )


def _call_info(code: str):
    import akshare as ak

    return ak.stock_individual_info_em(symbol=code)


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


@lru_cache(maxsize=256)
def _fetch_fields(code: str, day: str) -> tuple[float, float, float, str]:
    """按「股票 + 交易日」缓存：同一天重复请求不再打接口，跨天自动失效。"""
    price: float | None = None
    change: float = 0.0
    volume: float = 0.0
    reasons: list[str] = []

    try:
        fields = _from_bid_ask(_pairs_to_map(_call_bid_ask(code)))
        if fields is None:
            reasons.append("盘口缺少最新价或涨幅")
        else:
            price, change, volume = fields
    except Exception as exc:
        reasons.append(f"盘口接口失败：{type(exc).__name__}: {exc}")

    if price is None:
        try:
            price, change, volume = _from_hist(_call_hist(code, day))
        except Exception as exc:
            reasons.append(f"日线接口失败：{type(exc).__name__}: {exc}")

    if price is None:
        raise FetchError(f"行情获取失败（{code}）：" + "；".join(reasons or ["原因未知"]))

    try:
        name = _name_from_info(_pairs_to_map(_call_info(code)), code)
    except Exception:
        name = code  # 名称只影响可读性，退回代码即可，不影响行情与检索

    return price, change, volume, name


def get_quote(code: str, *, day: str | None = None) -> Quote:
    code = str(code).strip().zfill(6)
    if len(code) != 6 or not code.isdigit():
        raise FetchError(f"股票代码必须是 6 位数字，收到：{code}")
    day_value = day or today_cn()
    day_str = day_value.isoformat() if hasattr(day_value, "isoformat") else str(day_value)
    price, change, volume, name = _fetch_fields(code, day_str)
    return Quote(code=code, name=name, price=price, change_pct=change, volume=volume)
