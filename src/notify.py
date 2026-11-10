"""企业微信机器人推送。长文本分片；任一失败都抛错，绝不静默。"""
from __future__ import annotations

MAX_CHARS = 1800  # 企业微信 text 消息内容上限约 2048 字节，留出余量


class NotifyError(Exception):
    """推送失败。"""


def _split(text: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:MAX_CHARS])
            line = line[MAX_CHARS:]
        if len(current) + len(line) > MAX_CHARS:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks or [text[:MAX_CHARS]]


def send_wecom(webhook: str, text: str, *, timeout: float = 10.0) -> int:
    """返回成功发送的片数。"""
    if not webhook:
        raise NotifyError("缺少企业微信 Webhook 配置")
    try:
        import requests
    except ImportError as exc:
        raise NotifyError("缺少依赖 requests，请执行 pip install -r requirements.txt") from exc

    sent = 0
    for chunk in _split(text):
        try:
            response = requests.post(
                webhook,
                json={"msgtype": "text", "text": {"content": chunk}},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise NotifyError(f"企业微信推送失败：{exc}") from exc
        if payload.get("errcode", 0) != 0:
            raise NotifyError(f"企业微信返回错误：{payload.get('errmsg', payload)}")
        sent += 1
    return sent
