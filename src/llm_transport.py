"""
大模型 HTTP 传输层（只用标准库，零第三方依赖）。

与 src/analyze.py 的纯校验逻辑分开：本模块只负责「把 messages 发出去、把正文拿回来」，
不判断内容对不对。任何失败都抛 AnalyzeError，绝不返回空字符串冒充成功。

支持任意 OpenAI 兼容的 /chat/completions 端点，包括 WorkBuddy 本机模型、
llama.cpp / Ollama 等本地服务（无鉴权、必须绕过系统代理直连）。
"""
from __future__ import annotations

import ipaddress
import json
from typing import Callable
from urllib import error as _url_error, parse as _url_parse, request as _url_request

_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})
# WorkBuddy 本机模型 / Qwen 类是 thinking 模型：默认把 token 花在 reasoning_content 上，
# 关掉思考链才能在有限预算里产出正文 JSON。llama.cpp / Ollama 会忽略这个扩展字段。
_THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


def is_local_base_url(base_url: str) -> bool:
    """
    回环地址判定：本机模型通常无鉴权，且必须绕过系统代理直连。

    必须严格解析 IP —— 用 startswith("127.") 会把 127.0.0.1.evil.com
    误判成本机地址，等于给公开站点开了一个盲 SSRF 口子。
    """
    host = (_url_parse.urlparse(base_url).hostname or "").lower()
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _scrub_secret(text: str, secret: str) -> str:
    """上游异常文本里可能回显 Authorization，返回给用户/日志前必须打码。"""
    return text.replace(secret, "***") if secret else text


def make_llm_call(*, api_key: str, base_url: str, model: str,
                  timeout: float = 30.0,
                  extra_payload: dict | None = None) -> Callable[[list[dict]], str]:
    """
    返回 llm_call(messages) -> 文本。BYOK 只需换 base_url / model / api_key。
    """
    # 延迟导入：analyze.py 在模块顶部 re-export 本模块，避免循环导入。
    from .analyze import AnalyzeError, response_format

    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AnalyzeError("缺少大模型 base_url")
    local = is_local_base_url(base)
    if not api_key and not local:
        raise AnalyzeError("缺少大模型 API Key")

    # 本机直连：若走系统 HTTP_PROXY，127.0.0.1 会被代理拦截。
    opener = (_url_request.build_opener(_url_request.ProxyHandler({}))
              if local else _url_request.build_opener())
    endpoint = base + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    base_payload: dict = {
        "model": model,
        "temperature": 0,
        "stream": False,
        "response_format": response_format(),
    }
    if local:
        base_payload.update(_THINKING_OFF)
    if extra_payload:
        base_payload.update(extra_payload)

    def _post(payload: dict, *, _retry_format: bool = True) -> tuple[str, str]:
        req = _url_request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
        except _url_error.HTTPError as exc:
            detail = _scrub_secret(exc.read(300).decode("utf-8", errors="replace"), api_key)
            # 部分 OpenAI 兼容端点不支持 json_object：仅对此情形降级一次，其余错误照抛。
            if exc.code == 400 and "response_format" in detail.lower() and _retry_format:
                fallback = {k: v for k, v in payload.items() if k != "response_format"}
                return _post(fallback, _retry_format=False)
            raise AnalyzeError(f"大模型接口返回 HTTP {exc.code}：{detail}") from exc
        except (_url_error.URLError, TimeoutError, OSError) as exc:
            raise AnalyzeError(
                f"大模型接口请求失败：{_scrub_secret(str(exc), api_key)}") from exc
        except json.JSONDecodeError as exc:
            raise AnalyzeError(f"大模型接口返回内容不是合法 JSON：{exc}") from exc
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AnalyzeError("大模型接口响应缺少 choices[0].message") from exc
        return (message.get("content") or ""), (message.get("reasoning_content") or "")

    def _call(messages: list[dict]) -> str:
        payload = {**base_payload, "messages": messages}
        content, reasoning = _post(payload)
        if content.strip():
            return content
        if reasoning:
            # thinking 模型只吐了思考链：关掉思考链重试一次，仍无正文就明确报错。
            content, _ = _post({**payload, **_THINKING_OFF})
            if content.strip():
                return content
            raise AnalyzeError("模型关闭思考链后仍无正文输出，请检查模型或输出长度限制")
        raise AnalyzeError("大模型接口返回了空内容")

    return _call
