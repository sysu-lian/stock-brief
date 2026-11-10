"""自托管编排入口：python -m src.main。WebUI 请走 web/app.py。"""
from __future__ import annotations

import sys
import time
from datetime import date

from .analyze import analyze_stock, make_llm_call
from .calendar import should_run, today_cn
from .config import Config, ConfigError, load_config
from .fetch_news import fetch_all
from .fetch_quote import get_quote
from .format_report import format_brief, format_failure_notice
from .models import Quote, StockBrief
from .notify import send_wecom
from .sample_data import DEMO_ITEMS, LOCAL_DEMO_BANNER, demo_quote, validate_demo_codes

SELF_HOST_REQUIRED = ("tavily_api_key", "llm_api_key", "stock_list", "wecom_webhook")


def build_report(config: Config, day: date) -> tuple[str, dict[str, Quote],
                                                     dict[str, StockBrief], dict[str, str]]:
    """逐只处理：单只失败记录后继续，绝不中断整批。"""
    if config.local_demo:
        validate_demo_codes(config.stock_list)
    quotes: dict[str, Quote] = {}
    briefs: dict[str, StockBrief] = {}
    errors: dict[str, str] = {}
    llm_call = make_llm_call(
        api_key=config.llm_api_key, base_url=config.llm_base_url,
        model=config.llm_model, timeout=config.request_timeout,
    )
    for code in config.stock_list:
        try:
            if config.local_demo:
                # 零 Key 本机体验：固定样本原文 + 本机模型，绝不冒充真实数据。
                quote, items = demo_quote(code), DEMO_ITEMS
            else:
                quote = get_quote(code)
                items = fetch_all(code, quote.name)
            brief = analyze_stock(code, quote.name, items, llm_call)
            quotes[code] = quote
            briefs[code] = brief
        except Exception as exc:  # 记录到推送内容 = 失败可见，不是静默 pass
            errors[code] = f"{type(exc).__name__}: {exc}"
    report = format_brief(day, quotes, briefs, errors)
    if config.local_demo:
        report = LOCAL_DEMO_BANNER + "\n\n" + report
    return report, quotes, briefs, errors


def _try_failure_notice(reason: str) -> None:
    try:
        config = load_config()
        if config.wecom_webhook:
            send_wecom(config.wecom_webhook,
                       format_failure_notice(today_cn(), {"全流程": reason}))
    except Exception as exc:
        print(f"[error] 兜底通知也失败：{type(exc).__name__}: {exc}", file=sys.stderr)


def main() -> int:
    started = time.monotonic()
    try:
        config = load_config()
        if config.local_demo:
            # 本机演示：不查交易日历、不连数据通道、不推机器人，只把结果打印出来。
            report, _, _, errors = build_report(config, today_cn())
            print(report)
            print(f"[demo] 本地演示完成：成功 {len(config.stock_list) - len(errors)} "
                  f"/ 失败 {len(errors)} / 耗时 {time.monotonic() - started:.1f}s")
            return 1 if errors else 0

        config = load_config(required=SELF_HOST_REQUIRED)
        day = today_cn()
        if not should_run(day):
            print(f"[skip] {day} 非交易日，跳过（休市不打扰）")
            return 0

        report, _, _, errors = build_report(config, day)
        if len(errors) == len(config.stock_list):
            send_wecom(config.wecom_webhook, format_failure_notice(day, errors))
        else:
            send_wecom(config.wecom_webhook, report)
        success = len(config.stock_list) - len(errors)
        print(f"[done] {day} 成功 {success} / 失败 {len(errors)} "
              f"/ 耗时 {time.monotonic() - started:.1f}s")
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(f"[error] {message}", file=sys.stderr)
        _try_failure_notice(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
