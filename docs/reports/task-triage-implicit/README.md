# 三级分流归位：显式入口 → 对话内隐式自动分流（2026-11-19）

用户产品裁定（原话）："我觉得这就是多此一举，即使需要这样做也是在对话中跟 soulmate 沟通完，
它觉得需要协作，它自动转到群组或者流程化"+"为什么还要再弄一个入口出来？"

独立"下达任务"入口（triage FAB / /tasks 内嵌下达面板 / CommandMenu「任务下达」）砍掉，
分流形态归位为**隐式**：用户只在对话里跟 SoulMate 说事，SoulMate 沟通中自己判级——
小=直接干完、中=自动转 AI 群组拆解、大=自动转流程化，升级时透明告知（"这事适合协作，
我转到群组了"），全程用户只说话、无第二入口。判级引擎能力保留（归位不砍杀，迁移优先于删除）。

## 改了什么

- **砍独立入口**：`TaskTriageLauncher`（FAB+模态）、`TaskTriagePanel`（/tasks 下达面板）、
  CommandMenu「任务下达」项、`triage.fab`/`triage.placeholder` 等 29 个入口专属翻译键
  （三语）。`/tasks` 恢复整洁，只留 `TriageRecordsPanel`（分流记录回看，从门内迁出）。
- **分流引擎接进 SoulMate 对话流**（acp-proxy `plugin/task_triage.conversation_triage`
  + `soulmate_agent._implicit_triage`）：
  - 对话中判级（专用对话判级 prompt：寒暄/追问/澄清/还在讨论需求 → small 不升级）；
  - 中 → 自动建 AI 群组 + 发布任务（服务端自动拆解链式执行）；
  - 大 → 流程草稿 → **自动装载跑**（隐式形态无预览确认入口，流程自身人工节点/预算闸
    是安全闸；生成物引用无人可派角色时带可执行性约束重生成一次再装载）；
  - 升级透明告知（SoulMate 口吻，含产物深链），不让用户选轨道；
  - 小任务/LLM 低置信/分流执行失败 → 不惊动用户，SoulMate 直接办（不假造分流）。
  - 只判真·用户对话回合：headless 内部实例（wakeup/子代理/群组真身）、回合出身标记
    （wakeup/cron/wechat/消息路由）、系统派发标记消息、带附件消息一律跳过；
    `SOULMATE_IMPLICIT_TRIAGE=0` 可关。

## 真对话验收（WS → ws_acp → soulmate stdio → 真实 LLM，用户全程只说话）

探针 `acp-proxy/triage_implicit_probe.py`（证据 JSON 同目录
`data/triage-implicit-acceptance-*.json`），三路径全绿（PASS，11/11）：

| 检查 | 结果 | 真源回读 |
|---|---|---|
| P1 小任务 SoulMate 直接干完 | ✅ 真 LLM 直答（向量数据库解释） | 无升级告知、不落分流记录 |
| P2 中任务对话中自动进群组 | ✅ "这事适合协作，我转到群组了"+深链 | 群 `82b7ed92` 真建、任务真发布（5 子任务） |
| P3 大任务自动起流程 | ✅ "我转成流程在跑了"+深链 | `weekly-release-recon-approval` 真跑 `run_20261119231808_eecb8f9d` |
| R1 分流记录留痕（会话归属） | ✅ | `tri-6e23945c`(group) / `tri-9ccd8055`(flow) |
| R4 小任务零记录（会话内直接办） | ✅ | 新记录仅 group+flow 两条 |

## 自动化验证

- `acp-proxy/tests/test_task_triage.py`：15 绿（原 8 + 对话隐式分流 7：small 不升级/
  低置信不升级/中→自动建组+透明告知/大→自动起流程/派单角色重试/装载失败如实降级/
  分流失败回落 SoulMate）
- `node render-test-tasks-inbox.cjs` 27/27（/tasks 页渲染，入口已无、分流记录面板在）
- `npx tsc --noEmit` + `npx next build` 过；`systemctl --user restart acp-proxy-a openmate-web`
  后真对话三路径验收
