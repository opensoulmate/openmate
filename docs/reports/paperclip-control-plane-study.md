# Paperclip 类"一人公司"控制平面 精读 + 对比 + 取长补短清单

> 调研日期：2026-11-18 · 调研方式：Paperclip/Multica 克隆精读（codeload tarball，master/main 快照 2026-11-18；Paperclip HEAD≈f295e496）、Multica/VibeKanban/Squad/MAF 官方文档+源码抽读、MetaGPT/CAMEL 源码抽读、ChatDev README 实读
> 目标场景：deleg 协作三件套 —— 流程引擎挂"人的 Agent 节点"（任务发到员工 SoulMate，干完回流）+ 人工节点 + 组织角色路由，即"一人公司/企业员工 Agent 节点网"（SoulMate ACP + OpenSoul 知识库 + flow_engine + A2A + RBAC）
> Wiki 落盘：[[paperclip]] · [[multica]] · [[paperclip-vs-multica]] · [[agent-control-plane-classification]]

---

## 0. 先回答"Paperclip 是哪个项目"

**Paperclip = [paperclipai/paperclip](https://github.com/paperclipai/paperclip)**（MIT，TypeScript 93%，Node.js server + React UI，自托管 + Cloud waitlist）。
README 自述："Paperclip is the app people use to manage AI agents for work. **If OpenClaw is an employee, Paperclip is the company.**" —— 它是 **Agent 控制平面/调度管理层**，不是 Agent 本身：组织架构、工单（issue）、心跳调度、预算管控、审批治理、审计，把多 Worker（Claude Code/Codex/OpenClaw/Hermes/Gemini/Cursor…）当员工管。"If it can receive a heartbeat, it's hired."
规模：114.6k★ / 19.0k fork / 8,230 commits / 272 contributors / 31 releases（v2026.1106.0）。
两层架构（doc/GOAL.md，克隆精读原文）：
1. **Control Plane（本软件）**：agent 注册表+org chart、任务分派与状态、预算与 token 花费、issue 评论/文档/工作产物、目标层级（company→team→agent→task）、心跳监控；并强制"single-assignee issues、atomic checkout and execution locks、blockers、recovery issues、workspace/runtime controls"。
2. **Execution Services（adapters）**：agent 在外部运行、phone home；adapter 定义心跳如何被调用/观察/取消（本地 CLI adapter、HTTP/process adapter、OpenClaw gateway、外部 adapter 插件）。**"The control plane doesn't run agents. It orchestrates them."**

与 ai-agent-platform-deployment skill 的记载一致（Paperclip = 多 agent AI 平台/公司管理）；本机 100.76.2.155:3100 亦有部署实例（含 CEO agent cron）。

---

## 1. Paperclip 精读：公司/角色/任务编排怎么实现

### 1.1 组织结构（Org Chart）

- **agents 表**承载员工：`name`、`title`、job description、`reportsTo`（汇报链）、`status`（verifying/active/paused/retired…）、`pauseReason`、`lifecycleHolds`、模型/运行时 per-agent 选择。
- **汇报链**用递归 CTE 一次查出（`server/src/lib/agent-reporting-chain.ts`）：`WITH RECURSIVE chain AS (… manager.id = chain.reports_to …)`，company 内自底向上。委托"沿 org chart 上下流动"（README: Delegation flows up and down the org chart）。
- **Teams**（doc/TASKS.md）：team 是主组织单位，issue 必属一个 team；team key 生成人类可读工单号（ENG-123）；workflow states per team。
- **人机混合组织**：org chart 同时装人和 agent；**responsible user** 机制给每个 agent 挂"责任人身份"（connection permissions & responsible-user identities）。
- **多组织**：一个部署多个 company，任务/agent/权限/历史全隔离。

### 1.2 任务编排与分发（Issue = 工单）

数据模型（doc/TASKS.md，权威）：
```
Workspace → Initiatives → Projects → Milestones → Issues → Sub-issues
```
- **单 assignee 制**（deliberate: clear ownership prevents diffusion of responsibility）；多人协作拆 sub-issue 各挂各的 assignee；sub-issue 继承 project，不继承 team/labels/assignee；父任务完成自动收尾子任务（auto-close）。
- **状态机**：非扁平 enum —— 每 team 自定义命名状态，挂 6 固定 category（triage/backlog/unstarted/started/completed/cancelled），类目固定有序。
- **依赖**：issue_relations（related/blocks/blocked_by/duplicate）；"agent A can't start until agent B finishes"。
- **目标对齐**：issue.goalId 挂目标；上下文从 task 向上流经 project 到 company goal（README: "Context flows from the task up through the project and company goals"）。

### 1.3 任务分发（派单 = 队列 + 心跳唤醒 + 抢占）

核心代码：`server/src/services/heartbeat/queue.ts`（5,894 行）+ `queued-run-dispatch-order.ts` + `doc/heartbeat-internals.md`。

- **派发序四层**（`queued-run-dispatch-order.ts`）：①Person（人发起/人建任务树上的 run）②Work（agent/routine 发起）③Recovery ④旧 recovery 积压；层内按 readiness→priority→age。
- **防饿死**：每第 4 个空位给最久等待 >60min 的 run（OLDEST_DISPATCH_SHARE）；每第 4 个空位保留给 recovery；队列 per agent、agent 属单 company —— 树优先级不跨 agent/公司插队。
- **任务树归属**一次递归查询（parent 链 ≤32 层）判定"是否人的任务树"。
- **唤醒三态决策**（`modules/wake-queue/domain/policy.ts`，纯函数状态机）：新唤醒遇到执行锁被占时 **proceed / coalesce（并入正在跑的执行）/ defer（排队等锁释放）**；follow-up 消息要么合并进当前 turn，要么等当前 turn 完。
- **定时**：routines（cron/API/webhook 触发）与 issue-monitor 用保存的 future deadline（`services/scheduled-work.ts`），到点即派；心跳只做兜底 recovery scan。
- **agent 侧心跳协议**：worker 收到 heartbeat 后查自己名下 open issues（本机 CEO cron 即此协议："Heartbeat Wake — Check for Work: List ALL open issues assigned to you…"）；"如果它能收心跳，就能被雇佣"。

### 1.4 抢占与任务锁（execution lock）—— 全场最值得抄

- issue 行上直接放锁列：`executionRunId`、`executionLockedAt`、`checkoutRunId`（GOAL.md 称 "atomic checkout and execution locks"）。
- **claim 路径**（queue.ts `claimQueuedRun` → `lockIssueExecutionClaim` → `bindClaimedIssueExecution`）：
  - run 抢占：条件 UPDATE（`status='queued'` 才能翻成 running）+ RETURNING，每条启动路径收敛到这一个函数；agent 行/issue 行 `FOR UPDATE`，锁序固定 **issue → wake → run**。
  - issue 锁校验：assignee 归属（`assigneeAgentId === run.agentId`）、前任 run 仍在执行（`predecessor_still_executing`）、前任清理未完（`predecessor_cleanup_pending`）、case 交接被取代（`case_handoff_superseded`）→ 一律拒领回队列。
  - **数据库唯一约束兜底**：`issues_open_routine_execution_uq`（同一 routine 的 open 执行互斥），撞唯一约束 → conflict → 保持 queued。
- **wake 合并防重复**：`coalesce/defer/proceed` 三态 + 系统 continuation 在任务已关闭时静默取消（`CLOSED_TASK_CONTINUATION`）。

### 1.5 角色 Agent 的上下文组装（上下文共享/隔离）

- `task-context.ts`（`prepareHeartbeatTaskContext`）把**任务包**拼进 run context：continuation summaries、wake comments、task plans、communication guidance、full/compact 两档 assignment Markdown；先做隔离与 secret 脱敏再算 revision。
- **配置新鲜度指纹**（session-fingerprint-inputs.ts）：有效配置变更 → 丢弃/替换会话、工作区、租约（correctness of the next run's config takes priority over continuity）；每个输入分类登记，新增字段不分类测试就红。
- **secret 隔离**：per-run secret registry，解密值仅活在单个请求内；投影只带变量名不带值。
- 目标层级（company→team→agent→task）让 agent 知道"我干的活为什么服务大局"（Goal Alignment）。

### 1.6 成果汇总与人工治理

- **Artifacts & Feedback**：产物文件/文档集中可查、可预览、可对具体段落留言；run history + diff/截图/测试验证。
- **审批**（governance）：review/approval stages 可配置、approve hires、pause/reassign/stop；approval 工作有 `approvals` 表 + `approval-work-notifications`。
- **case handoffs**（caseHandoffs/cases）：带版本号的交办事务（handoffVersion 校验、awaiting_release→pending_dispatch→delivered），被取代即作废——**任务转派的一等公民实现**。
- **审计**：run-log 事件、call event log（MCP 调用审计）、agent-action-audit、activity log。

### 1.7 预算管控（详见 §2.3）

budget_policies 三档 scope（company/agent/project）、warn%/hard stop、reservations、incident、超限暂停 + 恢复续跑。

---

## 2. Paperclip vs Multica 四维对比清单（VibeKanban 择要并入）

**Multica = [multica-ai/multica]**（source-available，Go server + daemon + 14 种 agent CLI 适配，自托管 Docker Compose/Helm）。定位"Agents that show up on the board"：issue 派给 agent 像派给同事——领取、报进度、提 blocker、交回 review；server 管数据，**daemon 跑在你自己机器上**驱动本地 agent CLI。任务队列表 `agent_task_queue`。

### 2.1 心跳调度

| 维度 | Paperclip | Multica |
|---|---|---|
| 心跳方向 | 控制面↔agent 双向：agent 被 heartbeat 唤醒后自查名下 issues；adapter 定义心跳的调用/观察/取消 | daemon→server 单向心跳（15s/次）；server→daemon WS wake 推活 + 轮询兜底 |
| 存活判定 | agent 状态机（verifying/验证失败计数/手动 retry）；孤儿 run 5 分钟宽限后按批修复（≤25 agent/pass） | **3 拍判死**：45s（3 拍）无心跳→missing，任务标 `runtime_offline` 失败；7 天无 agent 绑定才删；心跳即回 online |
| 派发信号 | 心跳间隔只做 recovery scan；已知 deadline 用独立 timer（scheduled-work.ts） | WS wake 即时；正常路径 2m30s–3m 安全轮询（向下抖动）；missed wake 由 30s poll 兜底；`pending_work` 提示触发 15s 超时的带外心跳（≥1s 间隔防放大） |
| 防饿死/公平 | 四层派发序 + 60min aging 份额 + recovery 份额 | 每 agent FIFO 队列 `ORDER BY priority DESC, created_at ASC, id ASC`，无 aging 机制 |
| 失联恢复 | orphan-run recovery、restart adoption、run watchdog | `FailTasksForOfflineRuntimes` 扫描（`FOR UPDATE SKIP LOCKED` 批量收割）、stale-dispatched 回收、自动 requeue（issue/chat 来源可重试） |

### 2.2 工单抢占/任务锁

| 维度 | Paperclip | Multica |
|---|---|---|
| 抢占原语 | run 条件 UPDATE + `FOR UPDATE` 行锁（锁序 issue→wake→run） | **`ClaimAgentTask`**：`UPDATE … WHERE id=(SELECT … FOR UPDATE SKIP LOCKED)` 单语句原子领取 |
| 任务锁 | issue 行 `executionRunId/executionLockedAt/checkoutRunId` + 唯一约束 `issues_open_routine_execution_uq` 兜底 | **per-(issue, agent) 串行化**：NOT EXISTS 同 issue+同 agent 的 dispatched/running/waiting 才可领——**不同 agent 可并行做同一 issue，同一 agent 不会重复做**；唯一索引 `idx_one_pending_task_per_issue_agent_v2` 防重复排队 |
| 领取护栏 | 系统 continuation 任务已关→静默取消；wake 三态合并；前任未清场拒领 | wakeup gate（revision 校验）、runtime 存活 fence、**agent-runtime 重绑定 fence**（`a.runtime_id = atq.runtime_id`，防旧 runtime 幽灵领取）、prepare lease 5min |
| 并发额度 | per-agent 队列 + active-run-slots | 双层限额：daemon 20 / agent 6，取紧者 |
| 防重复输入 | wake coalesce/defer | **评论合并**：`coalesced_comment_ids` 把批量评论合成一个任务；触发评论被编辑/删除→取消任务防按过期输入行动；投递回执 CAS（`SetTaskDeliveredCommentIDs`）防旧 handler 覆盖 |
| 可观测 | run-log 事件 | `task_claim_decision.go` 单一漏斗记录每次领取决策（claimed/candidates_unclaimed/no_visible_candidates…）——排队等待归因 |

### 2.3 预算隔离

| 维度 | Paperclip | Multica |
|---|---|---|
| 策略 | **budget_policies**：scope=company/agent/project × window=lifetime/月 × 金额（分）× warn% × hard stop；状态 ok/warning/hard_stop | **无内置预算**（issue #4076 开着；RFC #6650 明确 "Budget caps / spend-limit enforcement … observability first"） |
| 记账 | cost_events 台账 + company accounting lock（admission 与 settlement 互斥）+ budget_reservations（预留 hold）+ budgetIncidents | `task_usage`（per task×provider×model：token 四类 + `cost_usd_ticks` 1e-10 USD + credits）+ `task_usage_hourly` 聚合；成本靠客户端费率表定价 |
| 执行点 | **派发前拦截**（`getInvocationBlock`）+ 最终 provider 门前 commit dispatch claim（`budgetDispatch.hardedOffAt` 防重复交接）；超限→按 scope 暂停（pauseReason='budget'）并取消在途工作；拨款后走 budget-resume continuation 恢复 | 无拦截；护栏是**看门狗**（idle 2h / tool watchdog / MULTICA_AGENT_TIMEOUT）+ 透传 CLI 参数（per-agent `custom_args` 可加 `--max-turns`/`--max-budget-usd`，env 层 `MULTICA_CLAUDE_ARGS` 做舰队默认，均过 `filterCustomArgs` 黑名单） |
| 隔离粒度 | 公司/agent/项目三层，各自暂停互不牵连 | 工作区隔离（workspace/roles/access scopes），钱不隔离 |

### 2.4 MCP 适配

| 维度 | Paperclip | Multica |
|---|---|---|
| 模型 | **治理型网关**：Application→Connection（remote_http/local_stdio）→Catalog（工具×风险级 read/write/destructive×状态 active/quarantined/disabled）→Profile（allow/deny 包）→Binding（绑 company/agent/project/routine/issue）→Policy（allow/block/**require_approval**/rate_limit/trust_rule，deny 优先）→Gateway 判定 + Call Event 审计 + Action Request 人工审批 | **配置注入型**：`agent.mcp_config` 存 DB，daemon 落临时文件、启动 CLI 带 `--mcp-config --strict-mcp-config`；`--mcp-config` 在 custom_args 黑名单里防绕过。无调用时策略 |
| 双向 | 自身既可当 MCP endpoint 也可当 MCP gateway | 单向（向 agent 注入服务器清单） |
| 启示 | 工具调用治理=独立一层，与 agent 解耦 | 够用主义：先把"agent 用哪些 MCP"钉死在配置里 |

### 2.5 VibeKanban 择要（看板式多 Agent 调度）

**BloopAI/vibe-kanban**（Apache-2.0，Rust monorepo，25.8k★）——**已宣布 sunsetting（shutdown 公告），只抄模式不依赖**。
- **任务尝试模型**：task → task_attempts（每次 agent 运行一条，带 executor_action_type/execution_process）→ workspace（**每任务独立 git worktree + 终端 + dev server**）。
- **防重复/依赖先行**：社区工作流（ericblue/claude-vibekanban）在起 agent 前先分析依赖图（Depends On 列），被阻塞任务不跑；`/sync-plan` 检测"依赖未完就开跑"的违规；**以 VibeKanban 状态为唯一真源**做同步（drift detection）。
- **MCP 自报状态**：`crates/mcp`（task_server/tools/{sessions,task_attempts,workspaces}.rs）让 agent 通过 MCP 建任务/改状态（todo→inprogress→inreview→done）/起 workspace session——**agent 自己驱动调度面**。
- 集中管 MCP 配置（centralise configuration of coding agent MCP configs）。

---

## 3. 分类框架：控制平面 vs 多 Agent 框架 vs 一体化平台

| 类 | 是什么 | 成员 | 抄什么 |
|---|---|---|---|
| **控制平面**（Agent 管控层） | 组织/工单/调度/预算/审批/审计，不管推理 | **Paperclip**（114.6k★ MIT，Node+React）、**Multica**（Go server+daemon，source-available）、**Squad**（bradygaster/squad，MIT，alpha，repo-as-files） | 全部组织机制——这是我们的主场 |
| **多 Agent 框架**（协作 SDK/运行时） | 写 agent 协作代码的框架 | **MAF**（microsoft/agent-framework，A2A/MCP 原生）、MetaGPT、CAMEL、ChatDev、LangGraph、CrewAI | 编排模式、通信协议、上下文组织 |
| **一体化平台**（通用工作流/LLM 应用） | 可视化编排通用业务流 | Dify、n8n | 不抄——flow_engine 保持流程引擎定位，通用自动化留给它们 |

**Squad**（控制平面第三样本，README/CHANGELOG/docs 实读）：人领导的 AI 小队（frontend/backend/tester/lead），团队**以文件活在 repo 里**（`.squad/`）；Coordinator 模式（只路由/扇出/汇总，不做领域活）；**label 驱动路由**（`squad:fenster` 标签派单，无标签按 routing.md 路由）；三层 Ralph（in-session 循环 / 本地 watchdog / **GitHub Actions 云心跳** 30min 自动 triage）；防重复=lockfile 单实例；`decisions.md`+`orchestration-log/` 全程留痕；`squad nap` 上下文卫生（压缩/修剪/归档）；约束预算（rate limiter/file-write guards/shell 限制/PII 清洗）。

**MAF**（通信层参考，官方 README/devblogs 实读）：统一 Semantic Kernel+AutoGen，Python/.NET/Go；编排模式 sequential/concurrent/handoff/group chat/Magentic-One，全部支持 streaming/checkpointing/**human-in-the-loop approvals/pause-resume**；声明式 YAML 定义 agent+工具+记忆+编排拓扑；**A2A 与 MCP 原生**（AgentA2AAdapter/WorkflowA2AAdapter 生成 agent card，AgentMCPTool/WorkflowMCPTool 把 agent 暴露为 MCP 工具；channels 包 `agent-framework-hosting-{a2a,mcp,responses,telegram}`）。

**多 Agent 框架样本**（源码抽读）：
- **MetaGPT**（metagpt/team.py 实读）：Team = roles + SOP + Environment（IM 式共享环境），`hire(roles)`；**investment 预算**（cost_manager.max_budget，`_check_balance` 每轮查、超支 `NoMoneyException`）；run 循环 env.publish_message→各 role 到 idle 为止；哲学 `Code = SOP(Team)`。
- **CAMEL**（camel/societies/workforce/workforce.py 实读）：Workforce = coordinator_agent + task_agent + Worker 池（SingleAgentWorker/RolePlayingWorker）+ **TaskChannel** 队列；任务分解（TASK_DECOMPOSE_PROMPT）→派活（ASSIGN_TASK_PROMPT）→按需建 worker（CREATE_NODE_PROMPT）；失败处理 FailureHandlingConfig/RecoveryStrategy、重试≤3、任务超时 600s、pending≤20、池 10；WorkforceSnapshot 断点快照、PAUSED 人工干预、可选共享记忆。
- **ChatDev**（README 实读）：1.0=虚拟软件公司（CEO/CTO/Programmer+职能研讨会，chat chain 拓扑）；**2.0 (DevAll)** 转零代码多 agent 编排平台（配置定义 agents/workflows/tasks）；MacNet（DAG 拓扑千级 agent）、puppeteer（RL 学习的中央编排器，NeurIPS 2025）。

**我们的定位 = 自研控制面**：Reflex+Pulse（调度内核）+ flow_engine（工单流转，节点 tool/decision/parallel/loop/approval/action）+ RBAC；MAF 作通信层参考（A2A/MCP 通道、HITL 审批、checkpoint 模式）；多 agent 框架只借"任务分解/派活/共享上下文"的编排思想，不引框架。

---

## 4. 《取长补短清单》——按控制平面框架重排（抄什么/怎么抄）

映射对象：**agent_node**（任务发员工 SoulMate，干完回流）/ **human_node**（=flow_engine 现有 approval 节点）/ **角色路由**（组织角色→成员派单）。现状基线：flow_def.py 节点类型 tool|decision|parallel|loop|approval|action，节点级 retry+on_error(fail|skip|goto)，timeout_seconds 为软预算（铁律：只记 over_budget 不杀执行），approval 带 form/timeout_hours/on_timeout。

### A. 工单抢占/任务锁 → agent_node（最高优先）

| # | 抄什么 | 出处 | 怎么抄 |
|---|---|---|---|
| A1 | **执行锁列 + 原子领取** | Paperclip `issues.executionRunId/executionLockedAt` + `bindClaimedIssueExecution`；Multica `ClaimAgentTask` 单语句 `UPDATE…FOR UPDATE SKIP LOCKED` | agent_node 派发前原子领取：`UPDATE flow_node_runs SET status='claimed', execution_run_id=?, locked_at=? WHERE id=? AND status='queued'`（条件 UPDATE 即锁，SQLite/PG 通用）；锁不住就不发 SoulMate。SQLite 场景退化为 IMMEDIATE 事务 + `WHERE status='queued'` 计数校验 |
| A2 | **唯一约束兜底防重复执行** | `issues_open_routine_execution_uq`、`idx_one_pending_task_per_issue_agent_v2` | node_runs 表加 `UNIQUE(flow_run_id, node_id) WHERE status IN ('claimed','running')`（PG 部分唯一索引；SQLite 用触发器/应用层校验）——双保险 |
| A3 | **前任未清场拒领** | `predecessor_still_executing`/`predecessor_cleanup_pending` | 回流落库前校验 execution_run_id 归属；重派（retry）必须等上一次 run 终态+清理完成 |
| A4 | **输入合并防重复** | Multica `coalesced_comment_ids` + 投递回执 CAS；Paperclip wake coalesce/defer/proceed | 同一 agent_node 未完成时到达的重复下发（人工补发/重试按钮）合并进在途任务或 defer，不新开 run；投递回执带 CAS（防旧 handler 覆盖新领取） |
| A5 | **领取决策可观测** | `task_claim_decision.go` 漏斗（claimed/candidates_unclaimed/no_visible_candidates…） | flow_run 事件表记录每次派发决策+拒绝原因（no_capacity/locked/superseded…），排查"为什么没派出去" |

### B. 心跳调度 → Pulse（调度内核）

| # | 抄什么 | 出处 | 怎么抄 |
|---|---|---|---|
| B1 | **唤醒三态决策** | `wake-queue/domain/policy.ts`（proceed/coalesce/defer，纯函数+facts 对象，不碰 DB） | Pulse 的"新任务撞上执行中"分支抽成纯函数策略（可单测、可换存储）；同角色在途→defer 排队，同任务补消息→coalesce |
| B2 | **派发序分层+防饿死** | 四层（Person>Work>Recovery）+ 每 4 个名额给 60min+ 老任务 | 我们工单分"人发起/agent 发起/恢复"三级，加 aging 份额——人工节点产生的任务永远优先，深树不饿死旁人 |
| B3 | **3 拍判死 + 失联回收** | Multica 15s 心跳/45s missing/`runtime_offline` 自动 requeue | SoulMate ACP worker 心跳（15s 级）+3 拍离线判死 + 在途 agent_node 自动回队列（可重试来源）；带外 wake+轮询兜底双通道 |
| B4 | **deadline 独立于心跳** | Paperclip scheduled-work.ts（已知到期时间自己 timer，心跳只兜底） | flow_engine 已有 cron trigger；补"到点直派"而非等下次心跳——例行任务延迟下限=timer 精度 |

### C. 预算隔离 → 成本护栏

| # | 抄什么 | 出处 | 怎么抄 |
|---|---|---|---|
| C1 | **三档 scope 策略表** | Paperclip budget_policies（company/agent×project × window × warn% × hard stop） | budget 表：scope(公司/角色/流程)、窗口(月/终身)、warn 阈值、硬停分值；状态 ok/warning/hard_stop 直出看板 |
| C2 | **派发前拦截** | `getInvocationBlock` 在 run-dispatch 事实对象里先查预算 | agent_node 领取（A1）之前查 budgetBlock；被挡→节点挂起转 human_node（人工审批放行）或 skip（on_error 语义已有） |
| C3 | **超限暂停+恢复续跑** | pauseReason='budget' + budget-resume continuation | 硬停后该角色/流程新任务不再派；充值/调额后从挂起点 resume（对应 flow 已有 goto 续跑语义） |
| C4 | **先记账后放行的防重** | `budgetDispatch.handedOffAt` 在进 provider 前写标记 | agent_node 发出前写 handedOffAt 标记，重启恢复时凭标记区分"发过了没回"与"没发过"——防双发双扣 |
| C5 | **软超时不动手** | 我们铁律与 Paperclip 软预算同向；Multica 反面教材（timeout 把花完钱的 run 直接扔） | 保持 timeout 只记 over_budget；真正的止损靠 C2/C3 预算闸，不靠超时杀 |

### D. 组织架构路由 → 角色路由

| # | 抄什么 | 出处 | 怎么抄 |
|---|---|---|---|
| D1 | **reportsTo 递归链** | `agent-reporting-chain.ts` 递归 CTE | org 表（role/member/reports_to/team）；agent_node 带 `role` 字段，派发解析 role→在线成员；成员不在线沿 reportsTo 向上找 backup/审批人 |
| D2 | **label/角色路由表** | Squad `squad:*` 标签路由 + routing.md | 角色路由做成数据（role→SoulMate 绑定表+兜底规则文件），不写死在流程定义里；流程 YAML 只写 role 不写人 |
| D3 | **单 assignee + sub-issue 拆解** | Paperclip TASKS.md | 一个 agent_node 实例只绑一个执行者；复杂节点用 parallel 分支+子任务承接"多人协作"，责任不分摊 |
| D4 | **RBAC 映射** | Multica owner/admin/member × 每成员可跑哪些 agent；Paperclip connection permissions + responsible user | 我们 RBAC：角色→可触发流程/可看工单/可接管 agent_node 的权限矩阵；每个 agent_node 挂"责任人"（human_node 审批人默认=责任人） |
| D5 | **case handoff 交办** | caseHandoffs（版本号+状态机+被取代即作废） | 转派/升级（员工→主管）做成带版本校验的交接单，避免"转了一半被旧任务覆盖" |

### E. 上下文共享/隔离 + 成果回流

| # | 抄什么 | 出处 | 怎么抄 |
|---|---|---|---|
| E1 | **任务包组装** | task-context.ts（continuation summary/wake comments/plan/两档 assignment markdown） | agent_node 入参标准化"任务包"：目标+验收+上游节点输出（`$.<node>.<field>` 已有寻址）+变更摘要，full/compact 两档防上下文爆 |
| E2 | **快照 diff 告知变化** | Multica `issue_snapshot`（本次派发与上次的 issue 状态对比） | 每次派发记录上游输出快照；重派时告诉 SoulMate"自上次以来变了什么"，不整包重灌 |
| E3 | **配置指纹换代** | session-fingerprint-inputs.ts（有效输入分类+变更即弃会话） | 角色/Skill/模型配置变更→下个节点边界生效并丢弃旧会话缓存（correctness over continuity），指纹输入表驱动 |
| E4 | **review gate 成果回流** | Multica "work lands in review, not in main"；Paperclip artifacts+段落批注 | agent_node 产出→human_node 验收门→才算节点完成；产物+run log 留档，`flow_receipts` 已有雏形直接挂验收结论 |
| E5 | **上下文卫生** | Squad `squad nap`（压缩/修剪/归档）；CAMEL shared_memory 可选共享 | OpenSoul 承接公司级共享知识；run 结束做记忆压缩归档，避免逐任务累积 |

### F. MCP/工具治理（借 Paperclip 网关思想）

| # | 抄什么 | 出处 | 怎么抄 |
|---|---|---|---|
| F1 | **Profile+Binding+Policy 三件套** | MCP-ACCESS-GOVERNANCE.md（allow/deny 包绑 scope，调用时 allow/block/require_approval/rate_limit，deny 优先） | flow_engine tool 节点/MCP 调用前过策略层：工具白名单包（绑角色/流程）+高危工具 require_approval 转 human_node |
| F2 | **风险分级+审计** | Catalog read/write/destructive + Call Event log | 工具注册表带风险级；每次调用落审计事件（谁/哪个节点/参数摘要/结果） |
| F3 | **配置注入钉死 MCP 集** | Multica `--strict-mcp-config`+黑名单防绕过 | SoulMate 下发受控 MCP 清单，worker 参数层禁改 |

### 不抄（避坑）

- VibeKanban 本体（项目 sunsetting）——只抄其 worktree 隔离/依赖先行/状态唯一真源三个模式。
- Multica 的"预算只观测不拦截"（其自身 roadmap 也承认要补）；"timeout 直接丢弃成果"。
- Paperclip 的重型运维面（嵌入式 PG/Cloud 迁移/大量 idle-* 恢复路径）——按需渐进，不预先建。
- LangGraph/CrewAI 等框架引入——控制面自研定位不变，MAF 仅作 A2A/MCP 通道参考。

---

## 5. 结论（一句话版）

Paperclip 的本质经验：**控制面=工单锁+派发序+预算闸+组织链四件套，agent 只是"能收心跳的员工"**；Multica 证明领取原子性（SKIP LOCKED+串行化）和 3 拍判死可以做得极简；两者在预算上一个真闸一个假护栏，我们直接抄真闸。落到 deleg 三件套：agent_node 抄 A1-A5（原子领取+唯一约束+合并防重），human_node 抄 C2/E4（预算闸转人工+review gate），角色路由抄 D1-D3（递归汇报链+路由表+单 assignee），Pulse 抄 B1-B3（三态唤醒+分层派发+3 拍判死），预算抄 C1-C4。
