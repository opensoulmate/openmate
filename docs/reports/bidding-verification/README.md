# 招投标插件 · 真实验证报告

> 生成时间：2026-11-17 · 插件：`plugins/bidding/`（plugin.yaml 清单式，设计文档 `docs/plugin-system-design.md` §8 规格）
> 验证方式：调用插件 MCP 工具 handler（`plugins/bidding/main.py`），对一份**真实招标文件**跑通 解析→生成标书草稿→审查报告→报价→入库。

## 真实招标文件

`/home/climbing/Documents/文件及清单/招标文件-海城市地热水资源特许经营项目（水厂自动化工程）.docx`

- 项目名称：海城市地热水资源特许经营项目（水厂自动化工程）
- 项目编号：**LNFH2024036**
- 最高限价：**13,356,305.2 元**
- 招标人：海城融拓水资源开发有限公司 · 招标代理：辽宁富弘项目管理有限公司
- 招标方式：公开招标 · 评标办法：**综合评估法** · 工期 365 日历天

## 四核心工具跑通结果

| 工具 | 结果 | 证据文件 |
|---|---|---|
| `bid_parse` | 抽取 10 项评分办法、11 条废标条款、19 项资格要求、27 项评审因素；项目/编号/限价/工期/质量/保证金/投标有效期结构化 | `01-bid-parse.json` |
| `bid_generate` | 生成 9 章节标书 Word（python-docx），一致性审计**通过**，覆盖 **10/10** 评分得分点；断点续跑复跑 `resumed=True` | `02-bid-generate.json` + `generated/*.docx` |
| `bid_review` | 废标风险审查 **🔴1 🟡9 🟢8**，结论「高废标风险（存在🔴必改项）」（缺 安全考核A/B/C证 → 🔴） | `03-bid-review.json` |
| `price_calc` | 线性插值法 13,000,000 报价 → 价格分 **19.21/30**，未超限价、不低于成本 | `04-price-calc.json` |
| `bid_knowledge_list` | 真实招标文件登记入库（结构化条目） | `05-knowledge.json` |

## 实图 / 实文档证据

- **实文档**：`generated/海城市地热水资源特许经营项目(水厂自动化工程)_投标文件_东华软件股份公司.docx` —— 由 `bid_generate` 真实产出（封面+投标函+资格/商务/技术标+评分响应表+偏离表，含 2 张真实表格：评分响应表、报价汇总表）。
- **实图**：`投标文件-封面.png` —— 上述 Word 经 LibreOffice 渲染的首页截图，可见真实项目名称、投标人、编号 LNFH2024036、报价 13,356,305.2、工期 365 日历天、致 海城融拓水资源开发有限公司。

## 装载集成

`plugin.dsl_api.scan_and_build` 载入 `plugins/` 无错误：`bidding v0.1.0 · 6 页面 · 7 工具 · nav=招投标`，
7 个 MCP 工具全部注册（`bidding__bid_parse/bid_generate/bid_review/price_calc/bid_score_config/bid_generate_status/bid_knowledge_list`），
与 `demo-greeter`、`flow-console` 共存于 `/plugins`；旧 `plugin.json` REST（`/bidding/*`）不受影响。

## 测试

`PYTHONPATH= ~/opensoul/.venv/bin/python -m pytest plugins/bidding/tests/test_bidding_plugin.py -q` → **13 passed**
（清单/6 DSL 校验/工具绑定一致性/四核心工具经 MCP 桥/引擎单测/真实文件端到端）
与既有 `acp-proxy/tests/test_plugin_system.py` 同跑 **47 passed**，无回归。
