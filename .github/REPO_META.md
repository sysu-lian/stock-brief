# 仓库发布清单（维护者用）

面向 GitHub 仓库设置的文案与素材，不面向终端用户。改项目定位时，这里和 `README.md` 要一起改。

## Description（154 / 350，直接复制）

```text
每天把 A 股自选股的公告/新闻筛成一份「有没有事」简报：每条结论都附原文链接，三道硬校验拦住 AI 瞎编，不给投资建议。/ Daily A-share briefs with traceable sources, BYOK WebUI & WeCom push. No investment advice.
```

前半句给中文读者讲清价值，后半句英文关键词接管 GitHub 搜索。备选：

- 纯英文（242 字符，面向国际社区）：
  `Daily A-share briefs that answer one question: did anything happen? Every claim links back to its original filing or news, with 3 hard checks against LLM hallucinations. No investment advice, no scoring, no backtests. BYOK WebUI + WeCom push.`
- 极简（35 字符，社媒签名）：
  `A 股自选股「有没有事」简报 · 每条结论附原文链接 · 不给投资建议`

## Topics（About 齿轮，一行复制）

```text
a-stock, china-stock-market, llm, openai-compatible, byok, python, fastapi, akshare, tavily, wecom, information-retrieval
```

不要加 `trading-bot` / `quant` / `stock-recommendation`：会引来错的人，也和「不给建议」的定位冲突。

## 其他字段

| 字段 | 值 |
|---|---|
| Repository name | `stock-brief`（短、好记，与本地目录一致） |
| Visibility | Public |
| Website | `https://stock.chuanxi.fun` |
| Social preview | 上传 `assets/social-preview.png`（1280×640，Settings → Social preview） |

**创建仓库时三个「Add …」开关全部保持关闭**（Add README / Add .gitignore / Add license）：
远端会自动生成同名文件，本地已有这三份，首次 push 会冲突。

## 发帖标题建议

> 做了个不给建议的 A 股简报：每条结论必须附原文链接，AI 编不出来源就作废

备选副标题（诚实筛选受众，比夸大更有辨识度）：
> 如果你想要评分、买卖点和回测，这个项目不适合你

## 差异化口径（可直接复制，基于 2026-11-10 同类项目调研）

- 同类项目大多输出评分、买卖点、买入/观望/卖出和回测；**我们从数据结构里就没有这些字段**。
- 常见做法是「免责声明写不构成投资建议，输出却给买卖信号」；我们真的不产出结论，只给事实和出处。
- 每条结论必须能点回**本次检索抓到的**原文；链接不在白名单、数字对不上、内容一致性过低，
  这条结论直接作废，并把作废原因显示给用户。
- 14 个模块 / 1473 行 / 134 个测试 / MIT：填密钥之前，你可以把整个仓库读完。

**口径红线**：不点名贬低同类项目；不写「唯一 / 首创 / 只有我们防幻觉」——
TradingAgents 在 2026-05 已修复编造数据问题，头部项目普遍有 grounding 机制。
Description 里不要塞竞品名字（350 字符有限，且替别人导流）；完整调研见 `PLAN.md` 第十六节。

## 文案红线

- 用「AI 投资」，不用「AI 炒股」；不出现「智能选股」「推荐」「买入 / 卖出」等措辞。
- **可以写「在线体验可用」**（`stock.chuanxi.fun` 已部署，行情与公告链路真实跑通），
  但**不写「生产可用 / 稳定运行 / 长期免费」**：真实用户密钥的完整成功路径、新闻通道、
  企业微信推送与定时任务仍未验证，README 与站点都要把「还没验证」写出来。
- 示例简报是**真实历史快照**（2026-11-10 收盘后跑通的结果），必须标注「不是实时行情 /
  不构成任何推荐」；不得再出现「演示占位链接」这类与「来源可追溯」定位自相矛盾的素材。
