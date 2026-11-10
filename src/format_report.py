"""生成简报。红涨绿跌（A 股习惯），失败逐条可见，附免责声明。"""
from __future__ import annotations

from datetime import date

from .models import Quote, StockBrief

DISCLAIMER = (
    "本简报仅整理公开信息，不构成任何投资建议；文字由用户自备模型生成，"
    "请点开来源自行核对，以官方披露为准。"
)

DEMO_BRIEF = f"""📅 2026-11-10 收盘 · 股票简报（1 只）

🟢 中芯国际 688981  -1.16%
  摘要：中芯国际发布预告公告，将于近期召开 2026 年第三季度业绩说明会。
  信息提示：无
  来源：https://static.cninfo.com.cn/finalpage/2026-10-31/1225648374.PDF

⚠️ 这是 2026-11-10 收盘后真实跑出来的一次结果，不是实时行情：行情、公告标题、
来源链接都来自当天的真实数据，点开链接即可核对原文。行情会变，请以你查到的为准。
---
{DISCLAIMER}"""


def format_demo_brief() -> str:
    """零 Key 也能看到的首页示例：真实历史快照，不触发任何接口调用。"""
    return DEMO_BRIEF


def _icon(change_pct: float, has_quote: bool) -> str:
    if not has_quote:
        return "⚪"
    if change_pct > 0:
        return "🔴"
    if change_pct < 0:
        return "🟢"
    return "⚪"  # 平盘


def _pct(value: float) -> str:
    return f"{value:+.2f}%"


def format_brief(day: date, quotes: dict[str, Quote],
                 briefs: dict[str, StockBrief],
                 errors: dict[str, str]) -> str:
    total = len(quotes) + len(errors)
    lines = [f"📅 {day.isoformat()} 股票简报（{total} 只）", ""]

    for code in [*quotes, *errors]:
        if code in errors:
            lines += [f"⚪ {code}", f"  ⚠️ 未能生成：{errors[code]}", ""]
            continue
        quote = quotes[code]
        brief = briefs.get(code)
        lines.append(f"{_icon(quote.change_pct, True)} {quote.name} {code}  {_pct(quote.change_pct)}")
        if brief is None:
            lines.append("  ⚠️ 结论缺失")
        elif brief.degraded:
            lines.append(f"  摘要：{brief.summary}")  # 已降级为「信息不足」
            lines.append(f"  原因：{brief.warnings[-1] if brief.warnings else '校验未通过'}")
        elif not brief.has_important_event:
            # 没有值得知道的事：把「搜过了确实没有」和「模型判断不重要」说清楚
            note = brief.warnings[-1] if brief.warnings else ""
            lines.append("  无重要事项" + (f"（{note}）" if note else ""))
        else:
            lines.append(f"  摘要：{brief.summary}")
            lines.append(f"  信息提示：{brief.risk_alert or '无'}")
            for url in brief.sources:
                lines.append(f"  来源：{url}")
        lines.append("")

    if errors:
        lines.append(f"⚠️ 失败 {len(errors)} 只（已逐条说明，未静默跳过）：" +
                     "、".join(errors))
        lines.append("")
    lines += ["---", DISCLAIMER]
    return "\n".join(lines)


def format_failure_notice(day: date, errors: dict[str, str]) -> str:
    lines = [f"⚠️ {day.isoformat()} 简报生成失败", ""]
    for code, reason in errors.items():
        lines.append(f"· {code}：{reason}")
    lines += ["", "本次没有生成任何结论，未静默跳过。请检查数据源或配置后重试。",
              "", DISCLAIMER]
    return "\n".join(lines)
