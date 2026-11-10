"""
AkShare 行情，全局缓存：N 只股票只发 1 次请求。
延迟导入第三方库，保证纯逻辑测试无需安装 akshare。
"""
from __future__ import annotations

from functools import lru_cache

from .calendar import today_cn
from .models import Quote


class FetchError(Exception):
    """数据抓取失败。必须上报到简报/页面，不允许静默跳过。"""


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=4)
def _load_spot(day: str) -> dict[str, dict]:
    """按交易日缓存：同一天 N 只股票只发 1 次请求，跨天自动失效。"""
    try:
        import akshare as ak
        frame = ak.stock_zh_a_spot_em()
    except Exception as exc:
        raise FetchError(f"AkShare 行情接口失败：{exc}") from exc
    if frame is None or frame.empty:
        raise FetchError("AkShare 返回空行情表")
    index: dict[str, dict] = {}
    for row in frame.to_dict("records"):
        code = str(row.get("代码") or "").zfill(6)
        if len(code) == 6 and code.isdigit():
            index[code] = row
    if not index:
        raise FetchError("AkShare 行情表缺少「代码」字段，接口可能已变更")
    return index


def get_quote(code: str, *, day: str | None = None) -> Quote:
    code = str(code).strip().zfill(6)
    row = _load_spot(day or today_cn().isoformat()).get(code)
    if row is None:
        raise FetchError(f"未找到 {code} 的行情（请确认是 6 位 A 股代码，如 688981）")
    return Quote(
        code=code,
        name=str(row.get("名称") or code),
        price=_to_float(row.get("最新价")),
        change_pct=_to_float(row.get("涨跌幅")),
        volume=_to_float(row.get("成交量")),
    )
