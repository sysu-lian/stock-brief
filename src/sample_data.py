"""
本地演示用的固定样本数据 —— **不是真实行情，也不是真实新闻**。

只在 LOCAL_DEMO=1 时启用：让没有 Tavily / AkShare / 云模型密钥的人，
也能用自己的本机模型把完整链路跑一遍（prompt → JSON → 硬校验 → 简报）。
公开站点禁止开启该模式；所有输出都必须带 LOCAL_DEMO_BANNER。
"""
from __future__ import annotations

from .models import NewsItem, Quote

LOCAL_DEMO_BANNER = (
    "⚠️ 本地演示模式：行情与原文均为固定示例数据，不是真实行情或新闻；"
    "下方摘要由你本机的模型生成，仅用于验证流程，不构成任何投资建议。"
)

DEMO_CODE = "688981"
DEMO_QUOTE = Quote(code=DEMO_CODE, name="中芯国际", price=0.0,
                   change_pct=1.20, volume=0.0)

DEMO_ITEMS: tuple[NewsItem, ...] = (
    NewsItem(
        url="https://www.cninfo.com.cn/new/disclosure/detail?stockCode=688981&announcementId=1",
        title="中芯国际 2026 年第三季度报告（演示占位）",
        content="中芯国际发布 2026 年第三季度报告，报告期内公司实现营业收入同比增长，"
                "产能利用率较上季度有所提升。公司表示将继续推进既有产能建设计划。",
        category="announcement",
    ),
    NewsItem(
        url="https://finance.eastmoney.com/a/20261110001.html",
        title="中芯国际：三季度产能利用率提升（演示占位）",
        content="中芯国际三季度产能利用率提升，公司披露的第三季度报告显示营业收入同比增长。",
        category="news",
    ),
)


def demo_quote(code: str) -> Quote:
    """只提供 688981 的占位行情；真实部署绝不会走到这里。"""
    return Quote(code=code, name=DEMO_QUOTE.name, price=DEMO_QUOTE.price,
                 change_pct=DEMO_QUOTE.change_pct, volume=DEMO_QUOTE.volume)


def validate_demo_codes(codes) -> None:
    """
    演示模式只有一只股票的样本原文。其他代码必须明确报错，
    否则会把「中芯国际」贴到 600519 上，正是我们最该避免的误导。
    """
    bad = [c for c in codes if c != DEMO_CODE]
    if bad:
        raise ValueError(
            f"本地演示模式只有 {DEMO_CODE}（中芯国际）的示例数据，无法演示：{'、'.join(bad)}")
