# 真业务流程验证 — publish-guard + invoice-flow（真跑一遍，真数据出真结果）

2026-11-19 01:07 CST · 驱动脚本 `run_verification.py`（可重跑）· 流程引擎生产同款装配
（plugin 扫描装载 + MCP 工具桥 + FlowEngine + flow_collab 工单账）

## 真跑 1：公众号发布保障流（publish-guard）

- **真账本**：`~/Documents/Hermes/ai-learning/_state.json` + `~/.hermes/agent-research-state.json`
- **真平台**：微信 `draft/batchget`（草稿箱 total_count=0 可查）；
  `freepublish/batchget` 返回 **48001 api unauthorized** → 对账自动降级为
  「仅按草稿箱对账」并显式报因（不把接口报错当空结果误判「未见发表」）
- **真对账结果**：`publish-guard-reconcile.json` —— **10 项异常**：
  - 封面未挂 ×1：AI编程70 封面图已生成（`~/.hermes/wechat-visuals/.../01-cover.png`）
    但账本 publish 消费块为空——「发布没消费封面」事故的直接实证
  - 待确认积压 ×3：AI编程70/71/72 草稿 11-16～11-18 建，账本仍「待用户后台确认发布」
  - 平台不一致 ×6：AI编程70/71/72 + 架构 60/61/62 账本记有 draft_id，平台草稿箱查无
- **真工单**：`TO-20261119010740-253889` 开进 /tasks 收发台（assignee=用户，due=48h
  超期自动催办），正文 `publish-guard-ticket.md`；交付回流后实例 completed
  （`publish-guard-run.json`）

## 真跑 2：发票报销流（invoice-reimburse）

- **真发票**：`5%发票_大连市西岗区公共事业服务中心_20250418162253.pdf`（微信收文件原件）
- **真识别**：二维码 100% 解析（号码 25112000000078684318 / ¥44,263.50 / 2025-04-18 /
  校验码 5EA0）+ pdftotext 补销售方、税号、商品名称（`invoice-flow-recognize.json`）；
  warnings 如实提示「购买方非东华系（请核对是否为报销发票）」——缺字段/存疑不瞎编
- **真登记/确认/入账**：登记去重入账（`invoice-flow-register.json`）→ human 工单
  `TO-20261119010741-dc40de` 人工确认 → approve 回流入**真报销台账**
  `~/Documents/Hermes/dhcc-reimbursement/ledger/报销台账.json`
  （已确认 1 张 ¥44,263.50，expense/【普】业务费，`invoice-flow-ledger.json`）
- 实例 completed（`invoice-flow-run.json`）

## 结论

两条流程「装了就干活」：cron/manual 真触发、真工具真数据、human 工单真进 /tasks
催办面、审批回流真收口、台账真落盘。样板流程已归位 `examples/`（【样板】标注），
运行时 `plugins/` 只剩真业务插件（bidding 工作台 + publish-guard + invoice-flow 等）。
