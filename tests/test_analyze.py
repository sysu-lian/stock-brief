"""来源校验逻辑测试 —— 项目质量红线第一、四、五条。"""
from __future__ import annotations

import json
import unittest

from src.analyze import (
    AnalyzeError, DEGRADED_TEXT, MAX_SUMMARY_LEN, analyze_stock,
    build_messages, validate_llm_output,
)
from src.models import NewsItem

URL_A = "https://www.cninfo.com.cn/new/disclosure/detail?code=688981"
URL_B = "https://finance.eastmoney.com/a/20261110.html"
SOURCE_TEXT = "中芯国际发布三季度报告，营业收入同比增长 15%，净利润增长 8%。"


def items():
    return [
        NewsItem(URL_A, "中芯国际三季度报告", SOURCE_TEXT, "announcement"),
        NewsItem(URL_B, "相关行业动态", "半导体行业近期产能利用率回升。", "news"),
    ]


def good_raw(**overrides):
    raw = {
        "code": "688981",
        "name": "中芯国际",
        "has_important_event": True,
        "summary": "公司发布三季度报告，营业收入同比增长 15%",
        "risk_alert": None,
        "sources": [URL_A],
    }
    raw.update(overrides)
    return raw


class TestValidation(unittest.TestCase):

    def test_faithful_summary_passes(self):
        brief = validate_llm_output(good_raw(), items(), "688981", "中芯国际")
        self.assertFalse(brief.degraded)
        self.assertEqual(brief.sources, [URL_A])
        self.assertIn("三季度报告", brief.summary)

    # 防线 1
    def test_empty_sources_is_discarded(self):
        brief = validate_llm_output(good_raw(sources=[]), items(), "688981", "中芯国际")
        self.assertTrue(brief.degraded)
        self.assertEqual(brief.summary, DEGRADED_TEXT)
        self.assertEqual(brief.sources, [])

    def test_missing_sources_key_is_discarded(self):
        raw = good_raw()
        del raw["sources"]
        self.assertTrue(validate_llm_output(raw, items(), "688981", "中芯国际").degraded)

    # 防线 2
    def test_hallucinated_url_is_discarded(self):
        brief = validate_llm_output(
            good_raw(sources=["https://fake.example.com/made-up"]),
            items(), "688981", "中芯国际")
        self.assertTrue(brief.degraded)
        self.assertIn("URL", brief.warnings[0])

    def test_tracking_params_are_normalized(self):
        brief = validate_llm_output(
            good_raw(summary="半导体行业近期产能利用率回升",
                     sources=[URL_B + "?utm_source=chat#top"]),
            items(), "688981", "中芯国际")
        self.assertFalse(brief.degraded)
        self.assertEqual(brief.sources, [URL_B])

    def test_extra_query_param_is_rejected(self):
        # 严格白名单：只有跟踪参数可忽略，凭空加的 query 视为另一个 URL
        brief = validate_llm_output(
            good_raw(sources=[URL_B + "?id=999"]), items(), "688981", "中芯国际")
        self.assertTrue(brief.degraded)

    # 防线 3
    def test_fabricated_number_is_discarded(self):
        brief = validate_llm_output(
            good_raw(summary="公司发布三季度报告，营业收入同比增长 99%"),
            items(), "688981", "中芯国际")
        self.assertTrue(brief.degraded)
        self.assertIn("数字", brief.warnings[0])

    def test_off_topic_summary_is_discarded(self):
        brief = validate_llm_output(
            good_raw(summary="该公司近期完成重大资产重组并获监管批复交割"),
            items(), "688981", "中芯国际")
        self.assertTrue(brief.degraded)
        self.assertIn("一致性", brief.warnings[0])

    # 合规红线
    def test_advice_wording_is_discarded(self):
        for word in ("建议买入", "推荐关注", "可以逢低加仓"):
            with self.subTest(word=word):
                brief = validate_llm_output(
                    good_raw(summary="公司发布三季度报告，" + word),
                    items(), "688981", "中芯国际")
                self.assertTrue(brief.degraded)
                self.assertIn("投资建议", brief.warnings[0])

    def test_advice_in_risk_alert_is_discarded(self):
        brief = validate_llm_output(
            good_raw(risk_alert="建议卖出规避风险"), items(), "688981", "中芯国际")
        self.assertTrue(brief.degraded)

    # 其他硬约束
    def test_long_summary_is_truncated_with_warning(self):
        long_text = "公司" * 60
        rich = [NewsItem(URL_A, "三季报", long_text, "announcement")]
        brief = validate_llm_output(
            good_raw(summary=long_text, sources=[URL_A]), rich, "688981", "中芯国际")
        self.assertFalse(brief.degraded)
        self.assertEqual(len(brief.summary), MAX_SUMMARY_LEN)
        self.assertTrue(any("截断" in w for w in brief.warnings))

    def test_no_event_keeps_no_summary(self):
        brief = validate_llm_output(
            good_raw(has_important_event=False, summary="", sources=[URL_A]),
            items(), "688981", "中芯国际")
        self.assertFalse(brief.degraded)
        self.assertFalse(brief.has_important_event)
        self.assertEqual(brief.summary, "")

    def test_invalid_raw_type_raises(self):
        with self.assertRaises(AnalyzeError):
            validate_llm_output([1, 2, 3], items(), "688981", "中芯国际")


class TestPromptAndRetry(unittest.TestCase):

    def test_build_messages_rejects_empty_items(self):
        with self.assertRaises(AnalyzeError):
            build_messages("688981", "中芯国际", [])

    def test_prompt_forbids_outside_knowledge_and_advice(self):
        messages = build_messages("688981", "中芯国际", items())
        system = messages[0]["content"]
        user = messages[1]["content"]
        self.assertIn("不得使用你已有的知识", system)
        self.assertIn("买入", system)
        self.assertIn(URL_A, user)
        self.assertIn(SOURCE_TEXT, user)

    def test_json_parse_failure_retries_once_then_succeeds(self):
        calls = []

        def flaky(_messages):
            calls.append(1)
            return "not-json" if len(calls) == 1 else json.dumps(good_raw())

        brief = analyze_stock("688981", "中芯国际", items(), flaky)
        self.assertEqual(len(calls), 2)
        self.assertFalse(brief.degraded)

    def test_two_parse_failures_raise(self):
        def broken(_messages):
            return "still not json"

        with self.assertRaises(AnalyzeError):
            analyze_stock("688981", "中芯国际", items(), broken)

    def test_llm_exception_is_not_swallowed(self):
        def exploding(_messages):
            raise RuntimeError("upstream 500")

        with self.assertRaises(RuntimeError):
            analyze_stock("688981", "中芯国际", items(), exploding)

    def test_empty_items_never_calls_model(self):
        """零原文是「搜过了没有」，不是「让模型猜」——模型一次都不能被调用。"""
        calls = []

        def should_not_run(_messages):
            calls.append(1)
            return json.dumps(good_raw())

        brief = analyze_stock("688981", "中芯国际", [], should_not_run)
        self.assertEqual(calls, [])
        self.assertFalse(brief.degraded)  # 不是降级，是一个正常结论
        self.assertFalse(brief.has_important_event)
        self.assertEqual(brief.summary, "")
        self.assertEqual(brief.sources, [])
        self.assertIn("没有公告或新闻", brief.warnings[-1])


if __name__ == "__main__":
    unittest.main()
