# 长任务自治能力对比：我们 vs Manus / OMH / Paperclip

> P0-长任务自治交付说明。核心诉求：**长任务自治可靠性——防空转、断点续跑、过程可回放**，
> 且"自报≠完成"。对手天花板：Manus（断点续跑/过程回放）· OMH（四级完成状态）· Paperclip（心跳/防饿死）。
>
> 实现位置：`agent/long_task_autonomy.py`（核心）+ `agent/scripts/long_task_demo.py`（验收演示）
> + `tests/test_long_task_autonomy.py`（32 用例全绿，含 kill→resume→replay 端到端）。
> 运行时数据：`acp-proxy/data/long_task_autonomy.db`（检查点 + 轨迹，进程重启可恢复）。

---

## 一、能力对比表（我们 vs 对手天花板）

| 能力维度 | Manus | OMH | Paperclip | **我们（OpenMate P0）** | 抄入的机制来源 |
|---|---|---|---|---|---|
| **断点续跑** | ✅ 检查点续跑 | — | ✅ crash-safe resume（先认领后关单） | ✅ 检查点落盘 data/，进程被杀/失败后从首个未完成步续跑，**不从头来** | Manus + Paperclip `markBudgetCancelledWorkForResume` |
| **过程可回放** | ✅ 每步输入/输出/决策可回看 | — | — | ✅ 结构化轨迹（每步 input/output/decision/evidence/时长），`get_timeline()`（API/UI）+ `replay()`（人类可读时间线） | Manus 回放体验 |
| **防空转·心跳** | — | — | ✅ 心跳 + 唤醒三态 | ✅ `ProgressMonitor`：心跳 + **实质进展检测**（进展指纹驱动） | Paperclip 心跳 |
| **防空转·无进展降级** | — | — | ✅ ok→warning→hard 三档 | ✅ **四态**：OK→WARNING(自检)→STALLED(降级)→DEGRADED(上报)，N 分钟无实质进展才触发 | Paperclip 三档预算状态机 |
| **防饿死/防误杀** | — | — | ✅ 派发份额+aging 让位 | ✅ **判"卡死"看"多久没进展"而非"跑了多久"**——持续推进的慢任务永不误判空转 | Multica `FailStaleTasks`（wall-clock 不杀健康长活） |
| **环路检测** | — | — | ✅ 重唤醒防风暴 | ✅ 同进展指纹反复出现=原地打转，直接拉满 DEGRADED | Paperclip PAP-13775 |
| **完成状态·多档** | 有限（完成/失败） | ✅ phase×confidence 双轴 | ✅ 五态 run 状态 | ✅ **9 档 confidence**（not_run/running/partly_seen/**reported_done**/seen/failed/**verified**/blocked/cancelled） | OMH labels.py |
| **自报≠完成** | — | ✅ 三道闸 | — | ✅ **自报封顶 reported_done**；要 verified 须**非执行者 + 具名核验命令 + 内容级证据 + 完整性零命中** | OMH §3.2 三道闸 |
| **完整性分类器** | — | ✅ 9 类拒绝规则 | — | ✅ 移植子集：empty/self_referential/unproven_claim_word/unfinished_marker/unnamed_verification_command/stub/prepared_as_observed/self_reporter_as_verifier | OMH `completion_integrity.py` |
| **CAS 崩溃安全** | — | — | ✅ 一切状态翻转 compare-and-set | ✅ claim/settle 全 CAS，部分写永不产生"谎报完成"；恢复扫描回退 running→pending | Paperclip queue.ts CAS + 恢复扫描 |
| **审计可核验** | ✅ | ✅ | ✅ | ✅ `completion_report()` 出多档+完整性 flags；轨迹逐条带 seq/decision | OMH completion_store claim boundary |

**一句话**：我们不只是抄一家——**Manus 的回放 + OMH 的自报≠完成 + Paperclip/Multica 的防饿死判据**三线合流，
且比单一对手在两点上更强：① 防空转判据是"进展签名"而非时长（比 Paperclip 的纯时长更不误杀）；
② 完成状态多档里 `verified` 的四条件硬门槛（比 Manus 的"完成/失败"二态更诚实）。

---

## 二、四项 P0 落点对照

### ② 防空转：心跳 + 进展检测（不是无限转圈）
- `ProgressMonitor.heartbeat(signature, substantive)`：喂心跳，`substantive=True` 重置空转钟。
- `assess()` 四态：`ok → warning(自检) → stalled(降级) → degraded(上报人类)`，阈值 env 可调。
- **关键判据**：只有"实质进展"才重置钟；同签名反复=环路，直接 degraded。**持续推进的慢任务永不误判**。

### ③ 断点续跑：检查点落盘 data/
- `CheckpointStore`（SQLite `data/long_task_autonomy.db`）：run/step 双表，step 级 status/confidence/evidence。
- 每步执行前 `claim_step`（CAS 认领），执行后 `settle_step`（CAS 关单）——**先认领后关单**，崩溃安全。
- `resume()`：`find_resumable`（未收尾且有未完成步）→ `record_recovery`（卡 running 的回退 pending，至少一次）→ 续跑跳过已完成步。
- **失败 run 也可续跑**（需求"失败后从检查点恢复"）；已 completed/cancelled 不续。

### ④ 过程可回放：结构化执行轨迹
- `TraceRecorder`：每步 `step_start/step_end/heartbeat/verify/recovery/run_end`，逐条带 input/output/decision/evidence/时长/状态。
- `get_timeline(run_id)` → 有序结构化事件（UI/API）；`replay(run_id)` → Manus 式人类可读时间线。
- 严格递增 seq，回放即还原当时决策链，可审计。

### ⑤ 验收钩子（用户可复跑）
```bash
# 1) 跑长任务（可中途 Ctrl-C 或 kill）
python3 agent/scripts/long_task_demo.py --task-id demo --steps 6 --step-delay 0.5 --exec-log /tmp/demo.exec
# 2) 中途 kill 后，从检查点续跑（不从头来）
python3 agent/scripts/long_task_demo.py --task-id demo --resume --exec-log /tmp/demo.exec
# 3) 回放完整轨迹（每步输入/输出/决策 + 多档完成状态）
python3 agent/scripts/long_task_demo.py --task-id demo --replay
```
自动化验收 `tests/test_long_task_autonomy.py::TestEndToEndKillResumeReplay`：
真实子进程跑 6 步 → 中途 `SIGKILL` → 重启 `--resume` → 断言
(1) 每个 step_id 恰好执行一次（已完成步不重做）(2) 从断点续跑非从头 (3) 轨迹完整可回放含被杀前后所有步。

---

## 三、完成状态多档（自报≠完成）示例

演示脚本一跑 6 步：1 步走**非执行者核验**（`verifier=external` + 具名核验命令 + 内容级证据）→ `verified`；
其余 5 步**执行者自报** → 封顶 `reported_done`。`completion_report()`：

```json
{
  "run_status": "completed",
  "run_confidence": "reported_done",   // 未全 verified → 不冒充"已验证"
  "self_reported_count": 5,
  "verified_count": 1,
  "steps": [ {"step_id":"step_1","confidence":"verified","verified":true}, ... ]
}
```

语义对齐 OMH：`reported done`="执行者报了，结果没查"；`verified`="查过且过了"。**done ≠ verified**。

---

## 四、接线（写了≠接线了）

`soulmate_agent.py` 复杂任务循环已接入 P0 钩子（全部 try/except 非致命，零风险不打断真实循环）：
- `_bind_long_task`（步骤循环入口）→ 绑定控制器，进程重启自动续跑；
- `_lta_step_end`（每步收口，成功/重试两处）→ 检查点落盘 + 轨迹记录 + 完成状态裁决（自省=自报→reported_done）；
- `_lta_finalize`（终局）→ 多档完成状态 + 完整性审计。
`tests/test_long_task_autonomy.py::TestAgentWiring` 直接调用真实 agent 方法驱动控制器，
断言检查点/轨迹/多档状态真实落盘（端到端，非 stub）。

---

## 五、对比结论

对标 Manus 的回放体验 ✅ · OMH 的自报≠完成（多档）✅ · Paperclip/Multica 的心跳+防饿死 ✅；
且在"防误杀判据（进展签名 vs 时长）"和"verified 四条件硬门槛"两点上超出单一对手。
32 用例全绿，含真实 kill→resume→replay 端到端，用户可复跑。
