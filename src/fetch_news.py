"""
新闻检索（Tavily）。公告已改为巨潮直连，见 fetch_announcements.py。

实现说明（与 PLAN.md 第六、十七节对应）：
- **公告**：`fetch_announcements.py` 直连巨潮结构化接口，拿到交易所披露原文 PDF，
  解决骨架阶段「Tavily 站点限定检索召回率低」这个最大功能短板。
- **新闻**：仍由运营方的 Tavily Key 检索，主流财经站点限定，Key 只在服务端。
  新闻通道**未配置**时只跳过新闻并给出显式说明（NEWS_SKIPPED_NOTE），
  因为公告走巨潮不需要任何 Key，站点不该因为「没买新闻额度」就整体不可用。
- **通道异常仍然直接抛出，不互相 fallback**：失败必须可见，绝不静默降级成
  「用弱一点的源凑合」，那会把「公告没抓到」伪装成「没有公告」。
  「未配置」是已知状态，「抓取失败」是异常——两者不能混为一谈。
"""
from __future__ import annotations

import os
from functools import lru_cache

from .calendar import today_cn
from .fetch_announcements import fetch_announcements  # 向后兼容：也可从本模块导入
from .models import NewsItem

NEWS_SITES = "site:eastmoney.com OR site:cls.cn OR site:stcn.com"
MAX_RESULTS = 5

NEWS_SKIPPED_NOTE = "新闻通道未配置（运营方未提供检索 Key），本次简报只含交易所公告原文。"


class NewsFetchError(Exception):
    """检索失败。必须上报，不允许静默跳过。"""


def news_configured() -> bool:
    """新闻通道是否已配置。公告走巨潮，不需要任何 Key。"""
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _get_client():
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise NewsFetchError("缺少 TAVILY_API_KEY（托管体验站由运营方在服务端配置）")
    try:
        from tavily import TavilyClient
    except ImportError as exc:
        raise NewsFetchError("缺少依赖 tavily-python，请执行 pip install -r requirements.txt") from exc
    return TavilyClient(key)


@lru_cache(maxsize=256)
def _cached_search(day: str, query: str) -> tuple[NewsItem, ...]:
    """按 (交易日, query) 缓存：同一次检索不重复扣额度，跨天自动刷新。"""
    try:
        response = _get_client().search(
            query=query, max_results=MAX_RESULTS, search_depth="basic",
            topic="news", days=1,
        )
    except Exception as exc:
        raise NewsFetchError(f"检索失败（{query}）：{exc}") from exc
    raw_items = response.get("results", []) if isinstance(response, dict) else []
    found: list[NewsItem] = []
    for entry in raw_items[:MAX_RESULTS]:
        url = str(entry.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue  # 丢弃无 URL 的条目；简报只呈现可追溯的内容
        found.append(NewsItem(
            url=url,
            title=str(entry.get("title") or ""),
            content=str(entry.get("content") or ""),
            category="news",
        ))
    return tuple(found)


def fetch_news(code: str, name: str) -> tuple[NewsItem, ...]:
    query = f"{name} {code} 新闻 动态 ({NEWS_SITES})"
    return _cached_search(today_cn().isoformat(), query)


def fetch_all(code: str, name: str) -> tuple[NewsItem, ...]:
    """
    公告（巨潮）+ 新闻（Tavily）合并去重。

    - 公告失败 → 抛出（抓不到不能当成「没有公告」）。
    - 新闻未配置 → 跳过，只返回公告；调用方负责把 NEWS_SKIPPED_NOTE 显示出来。
    - 新闻已配置但失败 → 抛出（这是异常，不是已知状态）。
    """
    announcements = fetch_announcements(code, name)
    news: tuple[NewsItem, ...] = ()
    if news_configured():
        news = fetch_news(code, name)
    seen: set[str] = set()
    merged: list[NewsItem] = []
    for item in (*announcements, *news):
        if item.url in seen:
            continue
        seen.add(item.url)
        merged.append(item)
    return tuple(merged)
