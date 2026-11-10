"""简报格式测试：红涨绿跌、失败可见、免责声明、演示快照标注。"""
from __future__ import annotations

import unittest
from datetime import date

from src.format_report import (
    DISCLAIMER, format_brief, format_demo_brief, format_failure_notice,
)
from src.models import Quote, StockBrief

DAY = date(2026, 11, 10)


def quote(code, name, pct):
    return Quote(code, name, 100.0, pct, 1000.0)


def brief(code, name, **overrides):
    data = dict(code=code, name=name, has_important_event=True,
                summary="公司发布三季度报告", risk_alert=None,
                sources=["https://www.cninfo.com.cn/a"])
    data.update(overrides)
    return StockBrief(**data)


class TestFormat(unittest.TestCase):

    def test_red_for_up_green_for_down_white_for_flat(self):
        text = format_brief(
            DAY,
            {"600519": quote("600519", "贵州茅台", 1.2),
             "000858": quote("000858", "五粮液", -0.3),
             "300750": quote("300750", "宁德时代", 0.0)},
            {"600519": brief("600519", "贵州茅台"),
             "000858": brief("000858", "五粮液", has_important_event=False),
             "300750": brief("300750", "宁德时代", has_important_event=False)},
            {})
        self.assertIn("🔴 贵州茅台 600519  +1.20%", text)
        self.assertIn("🟢 五粮液 000858  -0.30%", text)
        self.assertIn("⚪ 宁德时代 300750", text)

    def test_no_event_uses_plain_text(self):
        text = format_brief(DAY, {"000858": quote("000858", "五粮液", -0.3)},
                            {"000858": brief("000858", "五粮液",
                                             has_important_event=False)}, {})
        self.assertIn("无重要事项", text)

    def test_sources_are_printed(self):
        text = format_brief(DAY, {"600519": quote("600519", "贵州茅台", 1.2)},
                            {"600519": brief("600519", "贵州茅台")}, {})
        self.assertIn("来源：https://www.cninfo.com.cn/a", text)
        self.assertIn("信息提示：无", text)

    def test_degraded_shows_reason_never_raw_model_text(self):
        degraded = brief("600519", "贵州茅台", degraded=True,
                         summary="信息不足，未生成摘要",
                         warnings=["sources 为空，结论作废"])
        text = format_brief(DAY, {"600519": quote("600519", "贵州茅台", 1.2)},
                            {"600519": degraded}, {})
        self.assertIn("信息不足，未生成摘要", text)
        self.assertIn("sources 为空，结论作废", text)

    def test_errors_are_visible_and_listed(self):
        text = format_brief(DAY, {}, {}, {"600000": "FetchError: 接口超时"})
        self.assertIn("⚠️ 未能生成：FetchError: 接口超时", text)
        self.assertIn("失败 1 只", text)
        self.assertIn("未静默跳过", text)

    def test_disclaimer_always_present(self):
        self.assertIn(DISCLAIMER, format_brief(DAY, {}, {}, {}))

    def test_failure_notice_lists_every_reason(self):
        text = format_failure_notice(DAY, {"688981": "数据失败", "600519": "解析失败"})
        self.assertIn("688981：数据失败", text)
        self.assertIn("600519：解析失败", text)
        self.assertIn("未静默跳过", text)

    def test_demo_snapshot_is_explicitly_labelled(self):
        text = format_demo_brief()
        for token in ("中芯国际", "688981", "真实", "不是实时行情", "不构成任何投资建议"):
            self.assertIn(token, text)

    def test_demo_snapshot_cites_a_real_source_not_a_placeholder(self):
        text = format_demo_brief()
        self.assertIn("https://static.cninfo.com.cn/finalpage/", text)
        self.assertNotIn("占位", text)
        self.assertNotIn("演示占位链接", text)


if __name__ == "__main__":
    unittest.main()
