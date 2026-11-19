# 任务三级分流门 · 全链真跑实证（2026-11-19）

用户定稿任务架构：小任务→普通 agent 会话独自处理；中等→AI 群组拆解处理（临时工作小组）；
大型复杂→paperclip 式流程化（flow_engine 工单/节点/预算闸）。三块既有件齐了，本次补"分流门"。

## 建了什么

- **统一下达入口**：任一页右下角悬浮球（+ CommandMenu「任务下达」）与 /tasks 顶部内嵌面板，
  同一个 `<TaskTriageGate>` 组件。一句话输入 → AI 判级（LLMEngine，失败回落规则判级并如实标注）。
- **三轨分流**（`/triage/*`，acp-proxy :8092，纯 Python）：
  - 小 → `POST /api/sessions` 开 SoulMate 会话 + `/chat?session=..&forward=..&auto_send=1` 深链自动送达
  - 中 → `POST /api/ai-groups` 自动建组（hermes/soulmate/mimo 三角色）+ `POST /{gid}/tasks`
    任务发布（服务端自动拆解 + 链式执行）
  - 大 → `flow_studio.generate_from_nl` 流程草稿 → 预览（节点/YAML）→ 装载跑
    （`save_user_flow` → `engine.run_now`）
- **判级建议 + 一键换轨**：判级卡三轨任选（改判带"已手动改判"提示）；看板行
  「换轨至会话/群组/流程」把上下文带过去（`context.carried` 记原任务+原产物深链），
  旧记录留痕 `switched_to`。
- **三轨结果统一回看板**：`GET /triage/records` 三轨产物（session/group/flow run）+ 实时状态
  （会话消息数 / 群任务阶段 / 流程实例状态）+ 深链一键打开。
- **个人/企业双语义**：`/triage/mode`（personal|enterprise）——流程化视图徽章
  「一人公司（流程化=一个人的公司）/ 企业内部流程」，模板偏好随之切换
  （personal: file-watch 首推；enterprise: approval-chain 首推），/will 建流工坊同源展示。

## 真验证（三个真实小中大任务，各自走通对应轨道）

| 截图 | 内容 |
|---|---|
| 01-small-judge.png | 小任务判级：Small 95%，建议轨道=会话 |
| 02-small-dispatched.png | 分流完成：已开 SoulMate 会话，「去会话办理」 |
| 03-small-soulmate-reply.png | 会话轨真跑：SoulMate 直接办完（五言绝句《秋兴》+ 简注） |
| 04-medium-dispatched.png | 中任务判级 Medium → 已建组+任务发布 |
| 05-medium-group-brainstorm.png | 群组轨真跑：hermes 集思广益出规划（拆解/步骤/风险） |
| 07-medium-group-page.png | 群组页：3 成员 + 讨论流 |
| 06-flow-draft-preview.png | 大任务判级 Large → 流程草稿（Weekly Publish Reconciliation，5 节点） |
| 12-switch-flow-draft-nodes.png | 换轨产物：群→流程草稿 10 节点（agent/human/decision）+ 装载跑按钮 |
| 08-unified-board.png | 统一回看板：三轨实时状态（chat 2 条消息 / group 子任务×4 / flow completed） |
| 11-final-board.png | 终态看板：chat 已换轨留痕、群任务 scored、流程实例 completed |
| 09-will-flow-studio-personal.png | /will 流程化视图双语义：一人公司徽章 |
| 10-will-templates-enterprise.png | 切企业模式：内部流程徽章 + 模板推荐序切换（approval-chain 首推） |

真跑记录（acp-proxy data/task_triage.db）：
- `tri-c07391cb` chat：SoulMate 会话真答（07:48 五言绝句）→ 换轨至 flow（`tri-482691d1`）
- `tri-80c2bc64` group：4 子任务链式执行到 scored（规划/执行/验证/审查全终态）→ 换轨至 flow（`tri-580f10d3`）
- `tri-f6a7a67e` flow：`run_20261119075234_a5d05427` 装载跑 → 人工审批闸（工单 TO-20261119075237-975e68）
  → approve → 实例 completed

## 自动化验证

- `acp-proxy/tests/test_task_triage.py`：8 绿（判级 LLM/回落、三轨分流、换轨带上下文、
  看板实时状态、模式切换改模板偏好）
- `npx tsc --noEmit` + `npx next build` 过；`systemctl --user restart acp-proxy-a openmate-web` 后真跑
