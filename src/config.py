"""配置读取与校验。安全红线：任何密钥/口令都不得出现在 repr、日志或异常回显里。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Sequence

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
# 本地演示模式的默认值：WorkBuddy 本机模型，OpenAI 兼容协议、无需密钥。
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:52742/v1"
DEFAULT_LOCAL_MODEL = "local_model"
_SECRET_FIELDS = frozenset({"tavily_api_key", "llm_api_key", "wecom_webhook"})


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


class ConfigError(Exception):
    """配置缺失或非法。调用方必须显式处理。"""


@dataclass(frozen=True, repr=False)
class Config:
    tavily_api_key: str = ""    # 运营方提供，仅存服务端
    llm_api_key: str = ""       # 自托管必填；WebUI 由用户表单临时注入
    llm_base_url: str = DEFAULT_BASE_URL
    llm_model: str = DEFAULT_MODEL
    stock_list: tuple[str, ...] = ()
    wecom_webhook: str = ""
    request_timeout: float = 30.0
    local_demo: bool = False   # 仅供本机体验；公开站点必须保持 False

    def __repr__(self) -> str:
        parts = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in _SECRET_FIELDS and value:
                parts.append(f"{f.name}='***'")
            else:
                parts.append(f"{f.name}={value!r}")
        return f"Config({', '.join(parts)})"

    __str__ = __repr__


def parse_stock_list(raw: str) -> tuple[str, ...]:
    """只接受 6 位 A 股代码；非法代码直接报错，不静默丢弃。"""
    codes = [c.strip() for c in (raw or "").replace("，", ",").split(",") if c.strip()]
    for code in codes:
        if not (len(code) == 6 and code.isdigit()):
            raise ConfigError(f"非法股票代码「{code}」：需要 6 位 A 股数字代码（如 688981）")
    return tuple(dict.fromkeys(codes))


def _parse_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析（标准库即可，少一个依赖）。环境变量优先级更高。"""
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _load_env_file(env_file: str | None) -> None:
    path = Path(env_file) if env_file else Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for key, value in _parse_env_file(path).items():
            os.environ.setdefault(key, value)
    elif env_file:
        raise ConfigError(f"指定的配置文件不存在：{path}")


def load_config(*, env_file: str | None = None,
                required: Sequence[str] = ()) -> Config:
    """
    required 由调用场景决定：
    - 自托管 main.py：数据 Key、LLM Key、股票列表、Webhook 都要
    - 托管 WebUI：只需运营方的 TAVILY_API_KEY，用户 Key 绝不落盘
    """
    _load_env_file(env_file)
    try:
        timeout = float(os.getenv("REQUEST_TIMEOUT", "30"))
    except ValueError as exc:
        raise ConfigError(f"REQUEST_TIMEOUT 必须是数字：{exc}") from exc

    local_demo = _env_flag("LOCAL_DEMO")
    config = Config(
        tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_base_url=(os.getenv("LLM_BASE_URL", "").strip()
                      or (DEFAULT_LOCAL_BASE_URL if local_demo else DEFAULT_BASE_URL)),
        llm_model=(os.getenv("LLM_MODEL", "").strip()
                   or (DEFAULT_LOCAL_MODEL if local_demo else DEFAULT_MODEL)),
        stock_list=parse_stock_list(os.getenv("STOCK_LIST", "")
                                    or ("688981" if local_demo else "")),
        wecom_webhook=os.getenv("WECOM_WEBHOOK", "").strip(),
        request_timeout=timeout,
        local_demo=local_demo,
    )
    missing = [name for name in required if not getattr(config, name, None)]
    if missing:
        raise ConfigError("缺少必填配置：" + "、".join(missing) + "（请参考 .env.example）")
    return config
