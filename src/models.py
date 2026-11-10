"""共享数据结构。全部为普通 dataclass，不依赖第三方库，便于纯函数测试。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Quote:
    """一只 A 股的当日行情快照。"""

    code: str
    name: str
    price: float
    change_pct: float  # 涨跌幅，百分比数值，如 1.2 表示 +1.2%
    volume: float


@dataclass(frozen=True)
class NewsItem:
    """本次检索返回的一条原文，URL 是后续一切校验的锚点。"""

    url: str
    title: str
    content: str
    category: str  # "announcement" | "news"


@dataclass
class StockBrief:
    """一只股票最终呈现给用户的结论。"""

    code: str
    name: str
    has_important_event: bool
    summary: str
    risk_alert: str | None
    sources: list[str]
    degraded: bool = False  # True = 结论作废，降级为「信息不足」
    warnings: list[str] = field(default_factory=list)
