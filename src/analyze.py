"""
LLM 调用 + 输出校验 —— 整个项目可信度的地基。

设计原则（对应 PLAN.md 第六、九节）：
1. 提示词只是软约束；**下面所有防线都是代码硬约束**，模型说什么都不算数。
2. 三道防线：sources 为空作废 / URL 白名单 / summary 与原文内容一致性。
3. 合规硬约束：结论里出现投资建议类措辞，整条作废。
4. 失败必须可见：任何异常都 raise 或显式降级，绝无静默 pass。

纯函数（validate_llm_output / build_messages）与 I/O（llm_transport.make_llm_call）
分文件存放，因此全部校验逻辑都可以在没有 API Key、没有网络的情况下测试。
"""
from __future__ import annotations

import json
import re
from typing import Callable, Sequence

# 向后兼容：main.py / web/app.py 仍从 analyze 导入调用器；实现已移到独立传输层。
from .llm_transport import is_local_base_url, make_llm_call  # noqa: F401
from .models import NewsItem, StockBrief

MAX_SUMMARY_LEN = 80
DEGRADED_TEXT = "信息不足，未生成摘要"
COVERAGE_THRESHOLD = 0.45

SYSTEM_PROMPT = """你是 A 股公开信息的整理助手。你的唯一任务是：依据「本次检索到的原文」，判断这只股票最近是否有值得投资者知道的事项，并用一句话概括。

硬性规则（违反即视为无效输出）：
1. 只能使用下方提供的原文内容。不得使用你已有的知识补充任何事实、数字、公司名或事件。
2. sources 里的每条 URL 只能从下方给出的 URL 中原样选取，不得改写、拼接或编造。
3. 只做信息整理，不做任何判断或建议。禁止出现「买入 / 卖出 / 建议 / 推荐 / 必涨 / 加仓 / 减仓 / 目标价 / 抄底 / 看好 / 不看好 / 值得买」等措辞。risk_alert 只写中性的「信息提示」，没有就填 null。
4. has_important_event 判定标准：仅当原文涉及以下类别之一才为 true —— 定期报告或业绩预告、重大合同或中标、股东增减持、停复牌、监管函/问询/处罚、并购重组、分红送转、董监高变动、重大诉讼、重大投资或产能变动。单纯的行情波动、泛泛的行业评论、无实质信息的转载都不算。
5. summary 不超过 80 字，必须是一句可以由 sources 原文直接支撑的陈述句。
6. 只输出 JSON，不要输出任何解释文字或 Markdown 代码块标记。"""

USER_PROMPT_TEMPLATE = """股票：{name}（{code}）

本次检索到的原文如下（每条含 URL / 标题 / 正文）：

{context}

请严格按以下 JSON 结构输出：
{{"code": "{code}", "name": "{name}", "has_important_event": true 或 false, "summary": "一句话摘要或空字符串", "risk_alert": "信息提示或 null", "sources": ["来自上方原文的 URL"]}}

若没有值得知道的事项，has_important_event 填 false、summary 填空字符串、sources 填你实际参考过的上方 URL。"""

# 合规红线：出现在结论里即整条作废（只校验模型输出，不校验原文）
FORBIDDEN_TERMS = (
    "买入", "卖出", "建议", "推荐", "必涨", "加仓", "减仓", "目标价",
    "抄底", "看好", "不看好", "值得买", "满仓", "清仓", "止损位", "逢低",
)

_FULLWIDTH = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_TRACKING_PREFIXES = ("utm_", "spm=", "from=")


class AnalyzeError(Exception):
    """LLM 调用或输出校验失败。调用方必须显式处理或上报，不得吞掉。"""


# ---------------------------------------------------------------- 提示词 / 输出约束

def build_messages(code: str, name: str, items: Sequence[NewsItem]) -> list[dict]:
    """把本次检索到的原文拼进 prompt。items 为空时直接报错，绝不让模型凭空作答。"""
    if not items:
        raise AnalyzeError(f"{code} 本次未检索到任何原文，禁止让模型凭空生成结论")
    blocks = [
        f"[{i}] URL: {it.url}\n标题: {it.title}\n正文: {it.content[:800]}"
        for i, it in enumerate(items, 1)
    ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
            code=code, name=name, context="\n\n".join(blocks))},
    ]


def response_format() -> dict:
    # 用兼容面最广的 json_object；json_schema 并非所有 OpenAI 兼容端点都支持，
    # 严格 schema 由 validate_llm_output 在本地强制执行。
    return {"type": "json_object"}


# ---------------------------------------------------------------- 校验工具

def _normalize_url(url: str) -> str:
    """保守规范化：去 fragment、去常见跟踪参数、去尾斜杠。任何一方都同样处理。"""
    u = (url or "").strip().split("#", 1)[0]
    if "?" in u:
        base, query = u.split("?", 1)
        kept = [p for p in query.split("&")
                if not p.lower().startswith(_TRACKING_PREFIXES)]
        u = base + ("?" + "&".join(kept) if kept else "")
    return u.rstrip("/")


def _allowed_map(items: Sequence[NewsItem]) -> dict[str, str]:
    """规范化 URL -> 该 URL 对应的原文（标题 + 正文）。"""
    out: dict[str, str] = {}
    for it in items:
        out[_normalize_url(it.url)] = f"{it.title}\n{it.content}"
    return out


def _extract_numbers(text: str) -> set[str]:
    """提取数值 token 并归一（全角/千分位/末尾 .0）。用于拦截编造的数字。"""
    normalized = (text or "").translate(_FULLWIDTH)
    out: set[str] = set()
    for token in _NUM_RE.findall(normalized):
        value = token.replace(",", "")
        try:
            number = float(value)
            out.add(str(int(number)) if number == int(number) else str(number))
        except ValueError:
            out.add(value)
    return out


def _only_chinese(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fff]", "", text or "")


def _bigrams(text: str) -> set[str]:
    chars = _only_chinese(text)
    return {chars[i:i + 2] for i in range(len(chars) - 1)}


def _coverage(summary: str, source_text: str) -> float:
    """summary 的中文 2-gram 有多大比例能在原文中找到。"""
    grams = _bigrams(summary)
    if not grams:
        return 1.0
    source = _only_chinese(source_text)
    return sum(1 for g in grams if g in source) / len(grams)


# ---------------------------------------------------------------- 三道防线

def validate_llm_output(raw: dict | str, items: Sequence[NewsItem],
                        code: str, name: str) -> StockBrief:
    """
    把模型输出变成可信结论。任何一道防线不过 → degraded=True，
    summary 固定降级为「信息不足，未生成摘要」，sources 清空。
    """
    warnings: list[str] = []
    allowed = _allowed_map(items)

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AnalyzeError(f"LLM 输出不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise AnalyzeError("LLM 输出必须是 JSON 对象")

    raw_sources = raw.get("sources")
    sources = [s.strip() for s in raw_sources
               if isinstance(s, str) and s.strip()] if isinstance(raw_sources, list) else []

    def degrade(reason: str) -> StockBrief:
        return StockBrief(code=code, name=name, has_important_event=False,
                          summary=DEGRADED_TEXT, risk_alert=None, sources=[],
                          degraded=True, warnings=warnings + [reason])

    # 防线 1：无来源结论
    if not sources:
        return degrade("sources 为空，结论作废")

    # 防线 2：URL 白名单（只认本次检索返回的 URL）
    normalized = [_normalize_url(s) for s in sources]
    if any(u not in allowed for u in normalized):
        return degrade("sources 出现非本次检索返回的 URL，结论作废")

    summary = str(raw.get("summary") or "")
    alert_value = raw.get("risk_alert")
    alert = None if alert_value in (None, "", "null") else str(alert_value)

    # 合规硬约束：结论里不许出现投资建议类措辞
    hit = next((t for t in FORBIDDEN_TERMS if t in summary or t in (alert or "")), None)
    if hit:
        return degrade(f"结论出现投资建议类措辞「{hit}」，整条作废")

    if len(summary) > MAX_SUMMARY_LEN:
        summary = summary[:MAX_SUMMARY_LEN]
        warnings.append(f"summary 超过 {MAX_SUMMARY_LEN} 字，已截断")

    has_event = bool(raw.get("has_important_event"))
    if not has_event or not summary.strip():
        return StockBrief(code, name, False, "", None, normalized, False, warnings)

    # 防线 3：内容一致性（防「URL 是真的、内容是编的」）
    source_text = "\n".join(allowed[u] for u in normalized)
    missing = _extract_numbers(summary) - _extract_numbers(source_text)
    if missing:
        return degrade("summary 含原文未出现的数字：" + ",".join(sorted(missing)))
    coverage = _coverage(summary, source_text)
    if coverage < COVERAGE_THRESHOLD:
        return degrade(f"summary 与原文一致性过低（{coverage:.2f}），结论作废")

    return StockBrief(code, name, True, summary, alert, normalized, False, warnings)


def analyze_stock(code: str, name: str, items: Sequence[NewsItem],
                  llm_call: Callable[[list[dict]], str], *, retries: int = 1) -> StockBrief:
    """编排：组 prompt → 调模型 → 解析 JSON（失败重试 1 次）→ 硬校验。

    本次窗口内一条原文都没有时**不调模型**，直接给「没有公告」这个结论。
    这不是降级：搜过了、确实没有，本身就是一个可交付的答案；
    而让模型在零原文的情况下作答才是我们要防的事。
    """
    if not items:
        return StockBrief(
            code=code, name=name, has_important_event=False, summary="",
            risk_alert=None, sources=[], degraded=False,
            warnings=["本次检索窗口内没有公告或新闻"],
        )
    messages = build_messages(code, name, items)
    last_error: AnalyzeError | None = None
    for attempt in range(retries + 1):
        try:
            text = llm_call(messages)
            try:
                raw = json.loads(text)
            except (json.JSONDecodeError, TypeError) as exc:
                raise AnalyzeError(f"LLM 输出不是合法 JSON：{exc}") from exc
        except AnalyzeError as exc:
            last_error = exc
            if attempt < retries:
                continue
            raise  # 重试后仍失败 → 明确报错，绝不把原始文本推出去
        return validate_llm_output(raw, items, code, name)
    raise AnalyzeError(f"LLM 输出解析失败：{last_error}")
