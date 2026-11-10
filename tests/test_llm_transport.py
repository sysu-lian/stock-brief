"""
标准库 LLM 传输层 + 本机演示模式的单测（零第三方依赖、零网络）。

重点验证：密钥不回显、空内容不当成功、thinking 模型可见重试、
回环地址免鉴权且绕过代理、演示数据必须带明确标注。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src import llm_transport
from src.analyze import AnalyzeError, is_local_base_url, make_llm_call
from src.config import load_config
from src.sample_data import (DEMO_ITEMS, LOCAL_DEMO_BANNER, demo_quote,
                             validate_demo_codes)

MESSAGES = [{"role": "user", "content": "hi"}]
SECRET = "sk-SUPER-SECRET-123"


def _chat(content: str, reasoning: str = "") -> bytes:
    message = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return json.dumps({"choices": [{"message": message}]}).encode()


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


class _Opener:
    """记录每次请求，按序返回预设响应或抛出异常。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list = []

    def open(self, req, timeout=None):
        self.calls.append((req, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)

    def payload(self, index: int = 0) -> dict:
        return json.loads(self.calls[index][0].data.decode())


def _http_error(code: int, body: str) -> HTTPError:
    return HTTPError("http://x/v1/chat/completions", code, "err", {}, io.BytesIO(body.encode()))


class TestLocalUrl(unittest.TestCase):
    def test_loopback_is_local(self):
        for url in ("http://127.0.0.1:52742/v1", "http://localhost:11434/v1",
                    "http://127.0.0.2:8080/v1"):
            self.assertTrue(is_local_base_url(url), url)

    def test_remote_is_not_local(self):
        for url in ("https://api.deepseek.com/v1", "https://127.0.0.1.evil.com/v1", ""):
            self.assertFalse(is_local_base_url(url), url)


class TestTransport(unittest.TestCase):
    def _call_with(self, opener, **kwargs):
        options = {"api_key": SECRET, "base_url": "https://api.example.com/v1",
                   "model": "m", "timeout": 5.0}
        options.update(kwargs)
        with patch.object(llm_transport._url_request, "build_opener", return_value=opener):
            return make_llm_call(**options)(MESSAGES)

    def test_remote_requires_api_key(self):
        with self.assertRaises(AnalyzeError) as ctx:
            make_llm_call(api_key="", base_url="https://api.example.com/v1", model="m")
        self.assertIn("API Key", str(ctx.exception))

    def test_empty_base_url_rejected(self):
        with self.assertRaises(AnalyzeError):
            make_llm_call(api_key=SECRET, base_url="", model="m")

    def test_successful_call_sends_expected_payload(self):
        opener = _Opener(_chat('{"ok":true}'))
        self.assertEqual(self._call_with(opener), '{"ok":true}')
        payload = opener.payload()
        self.assertEqual(payload["model"], "m")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["messages"], MESSAGES)
        self.assertNotIn("chat_template_kwargs", payload)  # 远端不乱加扩展字段
        self.assertEqual(opener.calls[0][0].headers["Authorization"], "Bearer " + SECRET)
        self.assertEqual(opener.calls[0][1], 5.0)

    def test_local_endpoint_needs_no_key_and_disables_thinking(self):
        opener = _Opener(_chat('{"ok":true}'))
        text = self._call_with(opener, api_key="", base_url="http://127.0.0.1:52742/v1")
        self.assertEqual(text, '{"ok":true}')
        self.assertEqual(opener.payload()["chat_template_kwargs"],
                         {"enable_thinking": False})
        self.assertNotIn("Authorization", opener.calls[0][0].headers)

    def test_extra_payload_overrides_default(self):
        opener = _Opener(_chat("ok"))
        self._call_with(opener, extra_payload={"max_tokens": 123})
        self.assertEqual(opener.payload()["max_tokens"], 123)

    def test_reasoning_only_retries_with_thinking_off(self):
        opener = _Opener(_chat("", "我在思考…"), _chat('{"ok":true}'))
        self.assertEqual(self._call_with(opener), '{"ok":true}')
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(opener.payload(1)["chat_template_kwargs"],
                         {"enable_thinking": False})

    def test_reasoning_only_twice_raises_visible_error(self):
        opener = _Opener(_chat("", "思考1"), _chat("", "思考2"))
        with self.assertRaises(AnalyzeError) as ctx:
            self._call_with(opener)
        self.assertIn("思考链", str(ctx.exception))

    def test_empty_content_raises(self):
        with self.assertRaises(AnalyzeError) as ctx:
            self._call_with(_Opener(_chat("")))
        self.assertIn("空内容", str(ctx.exception))

    def test_unsupported_response_format_falls_back_once(self):
        opener = _Opener(_http_error(400, '{"error":"response_format not supported"}'),
                         _chat('{"ok":true}'))
        self.assertEqual(self._call_with(opener), '{"ok":true}')
        self.assertNotIn("response_format", opener.payload(1))

    def test_other_http_error_is_not_swallowed_and_key_is_scrubbed(self):
        body = f'{{"error":"bad key {SECRET}"}}'
        opener = _Opener(_http_error(401, body))
        with self.assertRaises(AnalyzeError) as ctx:
            self._call_with(opener)
        message = str(ctx.exception)
        self.assertIn("HTTP 401", message)
        self.assertNotIn(SECRET, message)
        self.assertIn("***", message)

    def test_network_error_is_visible_and_key_is_scrubbed(self):
        opener = _Opener(URLError(f"connection refused for {SECRET}"))
        with self.assertRaises(AnalyzeError) as ctx:
            self._call_with(opener)
        self.assertIn("请求失败", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_non_json_response_raises(self):
        with self.assertRaises(AnalyzeError) as ctx:
            self._call_with(_Opener(b"not json"))
        self.assertIn("合法 JSON", str(ctx.exception))

    def test_missing_choices_raises(self):
        with self.assertRaises(AnalyzeError):
            self._call_with(_Opener(b'{"choices":[]}'))


class TestLocalDemoMode(unittest.TestCase):
    def tearDown(self):
        for key in ("LOCAL_DEMO", "LLM_BASE_URL", "LLM_MODEL", "STOCK_LIST"):
            os.environ.pop(key, None)

    def test_demo_defaults_to_workbuddy_local_model_and_no_keys(self):
        os.environ["LOCAL_DEMO"] = "1"
        config = load_config()
        self.assertTrue(config.local_demo)
        self.assertEqual(config.llm_base_url, "http://127.0.0.1:52742/v1")
        self.assertEqual(config.llm_model, "local_model")
        self.assertEqual(config.stock_list, ("688981",))
        self.assertEqual(config.llm_api_key, "")

    def test_normal_mode_keeps_cloud_defaults(self):
        config = load_config()
        self.assertFalse(config.local_demo)
        self.assertEqual(config.llm_base_url, "https://api.deepseek.com/v1")
        self.assertEqual(config.llm_model, "deepseek-chat")

    def test_demo_data_is_explicitly_labeled(self):
        self.assertIn("不是真实行情", LOCAL_DEMO_BANNER)
        self.assertIn("不构成任何投资建议", LOCAL_DEMO_BANNER)
        self.assertTrue(all(it.url.startswith("https://") for it in DEMO_ITEMS))
        self.assertTrue(all("演示" in it.title for it in DEMO_ITEMS))
        self.assertEqual(demo_quote("688981").name, "中芯国际")

    def test_demo_refuses_codes_without_sample_data(self):
        validate_demo_codes(("688981",))
        with self.assertRaises(ValueError) as ctx:
            validate_demo_codes(("688981", "600519"))
        self.assertIn("600519", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
