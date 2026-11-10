"""配置校验 + 密钥不泄露测试（PLAN.md 第八节安全红线）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import (
    Config, ConfigError, _parse_env_file, load_config, parse_stock_list,
)


class TestStockList(unittest.TestCase):

    def test_normal_and_chinese_comma(self):
        self.assertEqual(parse_stock_list("688981,600519"), ("688981", "600519"))
        self.assertEqual(parse_stock_list("688981，600519"), ("688981", "600519"))

    def test_duplicates_are_removed(self):
        self.assertEqual(parse_stock_list("688981,688981"), ("688981",))

    def test_empty_is_allowed(self):
        self.assertEqual(parse_stock_list(""), ())

    def test_invalid_code_raises(self):
        for bad in ("60051", "HK00981", "688981A", "abcdef"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                parse_stock_list(bad)


class TestLoadConfig(unittest.TestCase):

    def _clean(self, **env):
        return patch.dict(os.environ, env, clear=True)

    def test_missing_required_is_reported(self):
        with patch("src.config._load_env_file", lambda *_: None), self._clean():
            with self.assertRaises(ConfigError) as ctx:
                load_config(required=("tavily_api_key", "llm_api_key"))
        self.assertIn("缺少必填配置", str(ctx.exception))
        self.assertIn("tavily_api_key", str(ctx.exception))

    def test_webui_mode_needs_only_operator_key(self):
        with patch("src.config._load_env_file", lambda *_: None), \
                self._clean(TAVILY_API_KEY="tv-123"):
            config = load_config(required=("tavily_api_key",))
        self.assertEqual(config.llm_api_key, "")
        self.assertEqual(config.stock_list, ())

    def test_bad_timeout_raises(self):
        with patch("src.config._load_env_file", lambda *_: None), \
                self._clean(REQUEST_TIMEOUT="abc"):
            with self.assertRaises(ConfigError):
                load_config()

    def test_env_file_parsing(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                        encoding="utf-8") as handle:
            handle.write("# 注释\nTAVILY_API_KEY=tv-123\n"
                         "STOCK_LIST=688981,600519\nLLM_MODEL=\"qwen-plus\"\n")
            path = handle.name
        try:
            parsed = _parse_env_file(Path(path))
        finally:
            os.unlink(path)
        self.assertEqual(parsed["TAVILY_API_KEY"], "tv-123")
        self.assertEqual(parsed["LLM_MODEL"], "qwen-plus")  # 引号被去掉
        self.assertNotIn("# 注释", parsed)

    def test_missing_env_file_raises(self):
        with self.assertRaises(ConfigError):
            load_config(env_file="/definitely/not/here/.env")


class TestSecretHygiene(unittest.TestCase):

    def test_repr_and_str_never_leak_secrets(self):
        config = Config(
            tavily_api_key="TV-SECRET-123",
            llm_api_key="LLM-SECRET-456",
            wecom_webhook="HOOK-SECRET-789",
            stock_list=("688981",),
        )
        for rendered in (repr(config), str(config), f"{config}"):
            for secret in ("TV-SECRET-123", "LLM-SECRET-456", "HOOK-SECRET-789"):
                self.assertNotIn(secret, rendered)
            self.assertIn("***", rendered)
            self.assertIn("688981", rendered)  # 非敏感字段仍可排查

    def test_config_error_does_not_echo_secret(self):
        with patch("src.config._load_env_file", lambda *_: None), \
                patch.dict(os.environ, {"TAVILY_API_KEY": "TV-SECRET-123"}, clear=True):
            with self.assertRaises(ConfigError) as ctx:
                load_config(required=("llm_api_key", "wecom_webhook"))
        self.assertNotIn("TV-SECRET-123", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
