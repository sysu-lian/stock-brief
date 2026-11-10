"""
WebUI 薄壳（BYOK）：只调用 src/ 的引擎函数，不内置任何业务逻辑。

安全红线（对应 PLAN.md 第八节）：
- 运营方 TAVILY_API_KEY 只从服务端环境读取，永远不出现在前端或响应里。
- 用户 LLM Key 仅在本次请求的内存中使用：不落盘、不写日志、不回显、不进响应。
- 所有外部异常文本在返回前做一次 scrub，防止上游把 Key 回显在错误信息里。
- 内存级限流保护运营方数据额度，防止公开站点被刷爆。

启动：uvicorn web.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request          # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse     # noqa: E402
from pydantic import BaseModel, Field                        # noqa: E402

from src.analyze import analyze_stock, is_local_base_url, make_llm_call  # noqa: E402
from src.calendar import today_cn                             # noqa: E402
from src.config import ConfigError, load_config, parse_stock_list  # noqa: E402
from src.fetch_news import fetch_all                          # noqa: E402
from src.fetch_quote import get_quote                        # noqa: E402
from src.format_report import DISCLAIMER, format_brief, format_demo_brief  # noqa: E402
from src.models import Quote, StockBrief                     # noqa: E402
from src.sample_data import (DEMO_ITEMS, LOCAL_DEMO_BANNER,  # noqa: E402
                             demo_quote, validate_demo_codes)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_CODES = 5
TOTAL_TIMEOUT = 90.0

app = FastAPI(title="stock-brief", docs_url=None, redoc_url=None, openapi_url=None)


class BriefRequest(BaseModel):
    codes: str = Field(default="688981", max_length=200)
    # 本机模型（WorkBuddy / llama.cpp / Ollama）通常无鉴权，允许留空；
    # 远端 BYOK 仍由路由强制要求非空密钥。
    api_key: str = Field(default="", max_length=256)
    base_url: str = Field(default="https://api.deepseek.com/v1", max_length=256)
    model: str = Field(default="deepseek-chat", max_length=128)

    def __repr__(self) -> str:  # 任何日志/调试输出都不能带出密钥
        return (f"BriefRequest(codes={self.codes!r}, api_key='***', "
                f"base_url={self.base_url!r}, model={self.model!r})")

    __str__ = __repr__


class RateLimiter:
    """进程内内存限流，重启即清空。够用、无状态、不引入依赖。"""

    def __init__(self, per_minute: int = 5, per_day: int = 100):
        self.per_minute = per_minute
        self.per_day = per_day
        self._lock = threading.Lock()
        self._recent: dict[str, list[float]] = {}
        self._daily: dict[str, tuple[str, int]] = {}

    def check(self, ip: str) -> None:
        now = time.time()
        day = date.today().isoformat()
        with self._lock:
            recent = [t for t in self._recent.get(ip, []) if now - t < 60]
            if len(recent) >= self.per_minute:
                raise HTTPException(429, "操作太频繁了，请稍等一分钟再试。")
            recent.append(now)
            self._recent[ip] = recent
            used_day, used = self._daily.get(ip, (day, 0))
            used = used if used_day == day else 0
            if used >= self.per_day:
                raise HTTPException(429, "今天的试用次数已用完，请明天再试（数据通道有额度限制）。")
            self._daily[ip] = (day, used + 1)


limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _require_operator_config(local_demo: bool) -> None:
    if local_demo:
        return  # 本机演示用固定样本原文，不消耗运营方数据额度
    config = load_config()  # 会读取服务端 .env，不触碰用户 Key
    if not config.tavily_api_key:
        raise HTTPException(503, "数据通道尚未配置（由运营方提供），请稍后再试；下方的示例简报仍可查看。")


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    path = STATIC_DIR / "index.html"
    if not path.exists():
        raise HTTPException(500, "前端文件缺失")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/demo")
def demo() -> JSONResponse:
    """零 Key、零接口调用：固定预置快照，任何时间打开都有内容。"""
    return JSONResponse({
        "report": format_demo_brief(),
        "disclaimer": DISCLAIMER,
        "is_demo": True,
        "local_demo": load_config().local_demo,
    })


@app.post("/api/brief")
async def create_brief(payload: BriefRequest, request: Request) -> JSONResponse:
    limiter.check(_client_ip(request))
    try:
        codes = parse_stock_list(payload.codes)
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not codes:
        raise HTTPException(400, "请至少填一个 6 位 A 股代码，例如 688981。")
    if len(codes) > MAX_CODES:
        raise HTTPException(400, f"一次最多查 {MAX_CODES} 只，避免数据通道被刷爆。")

    demo_mode = load_config().local_demo
    if demo_mode:
        try:
            validate_demo_codes(codes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    _require_operator_config(demo_mode)

    base_url, model = payload.base_url.strip(), payload.model.strip()
    local_model = is_local_base_url(base_url)
    # 公开站点不能让访客把请求打到服务器自己的回环端口（盲 SSRF）。
    allow_local = demo_mode or os.getenv("ALLOW_LOCAL_LLM", "").strip() == "1"
    if local_model and not allow_local:
        raise HTTPException(
            400, "本机模型只在「把站点跑在自己电脑上」时可用；在线体验站无法访问你电脑的 127.0.0.1。")
    api_key = payload.api_key.strip()
    if not local_model and len(api_key) < 8:
        raise HTTPException(400, "请先填入你的大模型密钥；不想填的话，上方示例可以直接看。")
    if not base_url or not model:
        raise HTTPException(400, "接口地址和模型名称不能为空。")

    llm_call = make_llm_call(
        api_key=api_key, base_url=base_url, model=model, timeout=30.0,
    )

    def _build() -> str:
        quotes: dict[str, Quote] = {}
        briefs: dict[str, StockBrief] = {}
        errors: dict[str, str] = {}
        for code in codes:
            try:
                if demo_mode:
                    quote, items = demo_quote(code), DEMO_ITEMS
                else:
                    quote = get_quote(code)
                    items = fetch_all(code, quote.name)
                brief = analyze_stock(code, quote.name, items, llm_call)
                quotes[code] = quote
                briefs[code] = brief
            except Exception as exc:  # 逐条写进简报 = 失败可见
                errors[code] = f"{type(exc).__name__}: {exc}"
        report = format_brief(today_cn(), quotes, briefs, errors)
        return LOCAL_DEMO_BANNER + "\n\n" + report if demo_mode else report

    try:
        report = await asyncio.wait_for(asyncio.to_thread(_build), timeout=TOTAL_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "生成超时了，请减少股票数量或稍后重试。") from exc

    return JSONResponse({
        "report": _scrub(report, api_key),
        "disclaimer": DISCLAIMER,
        "is_demo": False,
        "is_local_demo": demo_mode,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
