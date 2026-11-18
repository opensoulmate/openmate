# publish-guard — 公众号发布保障（真实业务流，装了就干活）

对账 **Hermes 既有发布账本 vs 微信平台实际发布**，检出真问题、开真工单：

- **账本**（只读整合，禁平行造轮子）：
  - `~/Documents/Hermes/ai-learning/_state.json`（AI课/日更 + AI编程系列）
  - `~/.hermes/agent-research-state.json`（100个AI Agent架构深度分析）
- **实际发布**：微信平台只读接口 `freepublish/batchget` + `draft/batchget`
  （凭据复用发布链 `config.json`，网络不可达自动降级本地口径并显式报因）
- **封面资产**：`~/.hermes/wechat-visuals/**` 封面生成了但草稿没消费（thumb_media_id 空）
  = 发布没消费封面事故的直接实证

## 流程（flows/publish-guard.yaml）

cron 每晚 21:30（AI课 12:00 / 架构 17:03 两个日更时点均已过）+ manual：

```
reconcile（tool 对账）→ route（decision）
  ├─ 有异常 → open-ticket（human_node：工单进 /tasks 收发台，48h 超期自动催办）
  └─ 无异常 → finish（通知）
```

问题口径：**漏发**（日更缺天）/ **封面未挂** / **待确认积压**（草稿超窗未发布）/
**平台不一致**（账本记草稿、平台查无）。

## 工具

- `publish-guard__reconcile {window_days=3, check_platform=true}` →
  `{issue_count, issues[], report, ticket_text, checked[], platform}`

## 配置（全部可选，缺省即真账本）

- `PUBLISH_GUARD_LEDGERS`：JSON 账本配置列表（测试隔离/加账本）
- `WECHAT_PUBLISH_CONFIG`：发布链 config.json 路径（缺省搜现役发布链两处）
