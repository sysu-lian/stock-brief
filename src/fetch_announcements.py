"""
巨潮资讯（cninfo）公告直连 —— P0：把公告覆盖率这个最大短板补上。

背景（对应 PLAN.md 第六节与第十六节行动项）：
骨架阶段公告走的是「Tavily + 官方站点限定检索」，召回率低、还会混进新闻稿。
本模块改为直连巨潮的结构化公告接口，拿到的就是交易所披露原文（PDF），
URL 天然可追溯，正好契合项目「每条结论必须附原文」的产品契约。

接口（2026-11-10 本机实测，只用标准库 urllib，零新增依赖）：
  1. GET  /new/data/szse_stock.json   → 代码→orgId 映射（6264 只全 A 股，热取约 0.3s）
  2. POST /new/hisAnnouncement/query  → 按 orgId + 日期区间查公告
     返回的 adjunctUrl 是相对路径，拼 https://static.cninfo.com.cn/ 即得原文 PDF。

必须知道的硬约束（都已实测，不是推测）：
- `stock` 参数必须带 orgId，只给代码会返回**全市场**结果（300750 空 orgId 时 total=48306）。
- orgId 无法由代码推导（300750 是 GD165627，688981 是 gshk0000981），必须查映射表。
- 公告只有标题、没有正文；content 只能放「日期 + 标题」。因此内容一致性校验
  对公告只能基于标题，比新闻弱 —— 这是诚实的上限，不伪装成有正文。
- 映射表取不到 = 公告通道整体失败，必须显式报错，绝不静默降级。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from functools import lru_cache

from .calendar import CN_TZ, today_cn
from .models import NewsItem
from .pdf_text import PdfExtractError, extract_text

STOCK_MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_PREFIX = "https://static.cninfo.com.cn/"
REFERER = "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122 Safari/537.36 stock-brief/0.1"
)

DEFAULT_DAYS_BACK = 3   # 周一能覆盖到上周五盘后披露；可被 ANNOUNCEMENT_DAYS_BACK 覆盖
MAX_ITEMS = 10          # 单只股票最多取多少条公告，避免 prompt 过长
PAGE_SIZE = 30          # 接口单页上限，取 30 再截前 MAX_ITEMS
TIMEOUT = 15.0
RETRY_ATTEMPTS = 3      # 实测巨潮偶发 IncompleteRead（映射文件 593KB 时连接提前断开）

ENRICH_TOP_N = 3        # 只给最重要的前 N 条公告补正文，控制下载/解析成本
_IMPORTANT_TERMS = (
    "报告", "业绩", "减持", "增持", "质押", "停牌", "复牌", "监管", "问询", "处罚",
    "合同", "中标", "并购", "重组", "分红", "诉讼", "担保", "关联交易", "回购",
)
RETRY_BACKOFF = 0.6


class AnnouncementFetchError(Exception):
    """巨潮公告接口失败。必须上报，不允许静默跳过或退化成空列表。"""


# ---------------------------------------------------------------- HTTP（可注入以便测试）

def _retry(call, *, attempts: int = RETRY_ATTEMPTS, backoff: float = RETRY_BACKOFF):
    """网络抖动重试：只对异常重试，重试耗尽仍失败则原样抛出（失败仍然可见）。"""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 —— 网络层异常类型很多，统一重试
            last = exc
            if i < attempts - 1:
                time.sleep(backoff * (i + 1))
    assert last is not None
    raise last


def _open_json(url: str, data: dict | None = None, timeout: float = TIMEOUT) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers.update({
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": REFERER,
            "X-Requested-With": "XMLHttpRequest",
        })
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _http_get_json(url: str, timeout: float = TIMEOUT) -> dict:
    return _retry(lambda: _open_json(url, None, timeout))


def _http_post_json(url: str, data: dict, timeout: float = TIMEOUT) -> dict:
    return _retry(lambda: _open_json(url, data, timeout))


# ---------------------------------------------------------------- 代码 → 交易所板块

def _column_for(code: str) -> str:
    """按代码前缀判断交易所列。实测 column 不影响过滤结果，但传对更稳妥。"""
    if code.startswith("6"):
        return "sse"      # 沪市（含 688 科创板）
    if code.startswith(("0", "3")):
        return "szse"     # 深市主板 / 创业板
    if code.startswith(("4", "8", "9")):
        return "bj"       # 北交所
    return "szse"


def _days_back() -> int:
    raw = os.getenv("ANNOUNCEMENT_DAYS_BACK", "").strip()
    if not raw:
        return DEFAULT_DAYS_BACK
    try:
        return max(1, min(30, int(raw)))
    except ValueError:
        return DEFAULT_DAYS_BACK


# ---------------------------------------------------------------- 代码映射（按日缓存）

@lru_cache(maxsize=2)
def _org_map(day: str) -> dict[str, dict]:
    """代码 → {orgId, name}。day 只作缓存键：跨天自动刷新，失败不缓存。"""
    try:
        payload = _http_get_json(STOCK_MAP_URL, timeout=20.0)
    except Exception as exc:
        raise AnnouncementFetchError(f"巨潮代码映射获取失败：{exc}") from exc
    rows = payload.get("stockList") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise AnnouncementFetchError("巨潮代码映射返回格式异常（stockList 为空）")
    mapping: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("code") or "").strip()
        org_id = str(row.get("orgId") or "").strip()
        if code and org_id:
            mapping[code] = {"orgId": org_id, "name": str(row.get("zwjc") or "")}
    return mapping


# ---------------------------------------------------------------- 公告查询（按日缓存）

def _fmt_time(value: object) -> str:
    """announcementTime 是毫秒时间戳，转北京时间日期。异常一律返回空串。"""
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=CN_TZ).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


@lru_cache(maxsize=256)
def _query(day: str, code: str, days_back: int) -> tuple[NewsItem, ...]:
    org = _org_map(day).get(code)
    if not org:
        raise AnnouncementFetchError(
            f"巨潮映射中找不到 {code}（可能不是 A 股、已退市或代码有误）")
    end = date.fromisoformat(day)
    start = end - timedelta(days=days_back)
    payload = {
        "pageNum": 1, "pageSize": PAGE_SIZE, "tabName": "fulltext", "plate": "",
        "stock": f"{code},{org['orgId']}", "searchkey": "", "secdate": "",
        "category": "", "trade": "", "seDate": f"{start.isoformat()}~{end.isoformat()}",
        "sortName": "announcementTime", "sortType": "-1", "isHLtitle": "true",
        "column": _column_for(code),
    }
    try:
        data = _http_post_json(QUERY_URL, payload)
    except Exception as exc:
        raise AnnouncementFetchError(f"巨潮公告查询失败（{code}）：{exc}") from exc
    if not isinstance(data, dict):
        raise AnnouncementFetchError(f"巨潮公告返回格式异常（{code}）")
    rows = data.get("announcements")
    if rows is None:
        # 实测：区间内无公告时 announcements 为 null，但会带 totalAnnouncement=0，
        # 这是合法空结果，不是故障；缺这两个计数字段才算格式异常。
        if "totalAnnouncement" in data or "totalRecordNum" in data:
            rows = []
        else:
            raise AnnouncementFetchError(f"巨潮公告返回格式异常（{code}）")
    elif not isinstance(rows, list):
        raise AnnouncementFetchError(f"巨潮公告返回格式异常（{code}）")

    items: list[NewsItem] = []
    seen: set[str] = set()
    for row in rows[:MAX_ITEMS]:
        adjunct = str(row.get("adjunctUrl") or "").strip()
        if not adjunct:
            continue  # 没有原文链接的条目一律丢弃：不可追溯就不能进简报
        url = STATIC_PREFIX + adjunct.lstrip("/")
        if url in seen:
            continue
        seen.add(url)
        title = str(row.get("announcementTitle") or "").strip()
        when = _fmt_time(row.get("announcementTime"))
        items.append(NewsItem(
            url=url, title=title,
            content=f"{when} {title}".strip(),  # 只有标题，无正文，如实标注日期
            category="announcement",
        ))
    return tuple(items)


# ---------------------------------------------------------------- 正文富化

def _body_enabled() -> bool:
    return os.getenv("EXTRACT_ANNOUNCEMENT_BODY", "1").strip() != "0"


def _enrich(items: tuple[NewsItem, ...], *, top_n: int = ENRICH_TOP_N) -> tuple[NewsItem, ...]:
    """给最重要的前 N 条公告补正文。抽取失败保留标题版，绝不伪造。"""
    if not items or top_n <= 0 or not _body_enabled():
        return items
    # 含「报告 / 减持 / 停牌…」等关键词的更可能有实质信息，优先补正文；同权重按时间序
    ranked = sorted(range(len(items)),
                    key=lambda i: (0 if any(t in items[i].title for t in _IMPORTANT_TERMS) else 1, i))
    chosen = set(ranked[:top_n])
    enriched: list[NewsItem] = []
    for i, item in enumerate(items):
        body = ""
        if i in chosen:
            try:
                body = extract_text(item.url)
            except PdfExtractError:
                body = ""  # 失败即保留标题版：不写假正文，也不中断整只股票
        enriched.append(NewsItem(
            url=item.url, title=item.title,
            content=f"{item.content}\n{body}" if body else item.content,
            category=item.category,
        ))
    return tuple(enriched)


# ---------------------------------------------------------------- 对外入口

def fetch_announcements(code: str, name: str = "", *, day: str | None = None,
                        days_back: int | None = None) -> tuple[NewsItem, ...]:
    """取一只股票最近 days_back 天的巨潮公告，并给最重要的前几条补正文。"""
    trade_day = day or today_cn().isoformat()
    window = days_back if days_back is not None else _days_back()
    return _enrich(_query(trade_day, code, window))
