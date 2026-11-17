# Pi Agent × 投标易（OpenBidKit_Yibiao）集成深度研究 + 取长补短清单

> 2026-11-17 · 承接《openbidkit-yibiao-benchmark.md》"桌面端工作区 + 独立 Pi Agent 后端"一节。
> 依据：克隆精读 `FB208/OpenBidKit_Yibiao`（client/electron/services/pi/ 19 个文件 6,297 行 + services/agent/ 6 文件）与 Pi 本体 `earendil-works/pi`（monorepo，packages/agent ≈2,550 行、packages/coding-agent/src/core 单 agent-session.ts 即 4,578 行）。所有架构断言以源码为准；口碑数据标注出处与口径。

---

## 一、Pi Agent 是哪个项目（精确定位）

| 项 | 事实 | 证据 |
|---|---|---|
| 项目 | **Pi**，github.com/earendil-works/pi（pi.dev），"minimal, extensible agent harness" | 仓库 README、pi.dev |
| 主体 | Earendil Inc.；npm 包 author 为 Mario Zechner；MIT | npm 包页、anchorterminal 调查 |
| 包族 | `@earendil-works/pi-ai`（统一 LLM API）、`pi-agent-core`（agent loop）、`pi-coding-agent`（CLI+SDK，CLI 命令就叫 `pi`）、`pi-tui`/`pi-mcp`/`pi-codemode`/`pi-durable`/`pi-telemetry`/`chord` 等 12 包 monorepo | `packages/*/package.json`（HEAD 均 1.5.2） |
| OpenBidKit 所用版本 | `client/package.json` 锁 **`@earendil-works/pi-ai` + `@earendil-works/pi-coding-agent` 0.80.10**（依赖锁版本，规避 Pi 在 patch 版本发破坏性变更的毛病） | client/package.json:172-173 |
| 佐证文档 | 仓库根 `pi4java开发文档.md`：FB208 自撰的"把 Pi 1.5.2 无界面内核逐行移植成 Java（pi4java）"规格书，781 行，逐文件列复刻/裁剪/不复刻清单——**这是对 Pi 内核最细的第三方解剖**，本文多处用它交叉验证源码 | pi4java开发文档.md |

Pi 官方定位四模式：交互 TUI / print+JSON / RPC / TypeScript SDK；README 明言 "skips features like sub-agents and plan mode"——**机制在内核、策略在扩展**是它的纲领（第三方解剖文 dpsm "Agent Harness Anatomy" 原话："mechanism in the core, policy in extensions"）。

## 二、OpenBidKit 如何集成 Pi（源码精读）

### 2.1 集成拓扑

```
Electron Main (CommonJS)
 ├─ agentService.cjs (907行) ── 多 Runtime 编排：子代理并发池、排队、用户提问管理、监控事件
 ├─ pi/piRuntimeService.cjs (2,025行) ── 单任务执行器：工作区准备→建会话→阶段循环→提交校验→修复/续跑
 ├─ pi/piSessionFactory.cjs (250行) ── 唯一的 Pi SDK 装配点（动态 import() ESM Pi 包）
 ├─ agent/agentOpenAiProxy.cjs ── 本机 loopback OpenAI 兼容代理（Pi 的唯一模型出口）
 └─ 业务层：contentGenerationAgent / outlineGenerationAgentV2 / globalFactsTaskV2 /
    businessTemplateFillTask / blindCheckAgent …（每个 = 一条 runTask 流水线）
```

### 2.2 关键取舍（逐条有源码出处）

1. **完全隔离的 Pi 运行环境**（`pi/piEnvironment.cjs`）：自建 `runtimeRoot/pi/` 目录树 + 假 HOME（`homeDir/.pi/agent`），`SettingsManager.inMemory`——不读用户机器上的任何 Pi 配置；`noContextFiles/noExtensions/noSkills/noPromptTemplates/noThemes` 全关，工作区约定改走 `agentsFilesOverride` 单一注入点。
2. **本地 OpenAI 代理当唯一模型出口**（`agent/agentOpenAiProxy.cjs`）：`createPiSession` 里注册唯一 provider `'yibiao'`（api: `openai-completions`），baseUrl 指向 `proxyInfo.baseUrl/v1`，token 是随机 base64url。代理负责：转发到用户配置的任意 OpenAI-like 端点、请求排队与并发节流、用量统计（`normalizeTokenUsage`）、错误归一 + 可重试错误标记（`markPiRetryableErrorMessage`——因为 Pi 的自动重试靠**正则匹配错误文案**，代理先把真实错误改写成 Pi 认得的格式）。Pi 对真实供应商零感知。
3. **会话即持久任务**（`piSessionFactory.cjs` + `piPersistentTaskStore.cjs`）：`SessionManager.create/open/inMemory` 三态；持久任务状态机记录 `run_id/phase/prompted_stage/session_file/compaction_pending/stage_index`，resume 时重开原会话文件，`prompted_stage` 一致则只发"继续之前的任务"——依托 Pi 的 JSONL 会话树实现业务级断点续跑。
4. **工具面 = Pi 8 原生 + 5 类自包**：原生 `read/write/edit/bash/grep/find/ls/powershell`；自包 `json-validation`（Ajv 四阶段，支持预置 Schema）、`ask-user`（顺序执行、2–5 选项、唯一 custom 项）、`report-failure`（直接失败/用户确认双模式）、`openxml`（784 行 Word 操作）、并行检索工具 + 各任务业务工具（`payload.create_tools` 回调按任务注入）。全部经 `codingAgent.defineTool + typebox` 注册——**走 Pi 的工具协议，不绕过**。
5. **阶段门禁与缓存优化**：正常模式用 `setActiveToolsByName` 随阶段收缩工具；`fixedToolList` 模式下工具清单与系统提示词恒定（复用请求前缀缓存），阶段权限改在 `beforeToolCall` 链上拦截（抛"当前阶段不能调用 X"）。
6. **免总结收尾**（`piSummaryControl.cjs`）：给每个工具 Schema 追加 `task_complete` 参数 + `afterToolCall` 钩子置 `terminate`——**复用 Pi 原生"整批工具全 terminate 才提前结束"规则**（`agent-loop.ts:691` `shouldTerminateToolBatch` = 全部 finalize 调用 `terminate===true`），省掉末尾一次总结性模型请求。
7. **提交校验-修复双轨**（`piSubmissionPolicy.cjs`）：`session.prompt()` 返回后程序读产物 → `payload.validateOutput` 程序验收 → 三段式修复策略 `normal → change-strategy → minimum`（连续两轮无进展则换方法；再无进展进入"最低完成目标"最后一轮，只修 blocking；仍不行才停）→ 把问题清单拼 `buildSubmissionRepairPrompt` **退回同一会话**继续。Agent 可以胡写，程序兜底验收（与对标报告"双轨"结论一致，此处是其 Pi 侧实现）。
8. **防跑飞三件套**：①无进展看门狗（`startWatchdog`，易标取 8 分钟，`ask-user` 等待期暂停计时）；②轮数上限（`turn_start` 计数，子代理统一 `CHILD_AGENT_MAX_TURNS_PER_PROMPT=100`）；③**空转诊断元代理**（`piStallDiagnosis.cjs`：同阶段同提示词重复即判空转 → `extractSessionRecord` 从会话树摘录执行记录 → 起一个诊断子任务（亦为 Pi 会话）给出继续/收尾/停止结论）。
9. **受保护文件基线**（`piWorkspaceBaseline.cjs`）：程序写入工作区的输入文件登记 `manifest.json + files/<sha256>` 副本（基线放工作区外），每次提交校验前 `restore()` 还原被 Agent 改动的输入——Agent 只许动产物。
10. **子代理治理**（`agentService.cjs`）：Agent 执行中发起的 runTask 视为子代理，同父并发 = 文本并发上限×2，超出排队；轮数 100 上限统一施加；`runChildTask` 让 Runtime 内部子任务（如空转诊断）沿用同一套限制。
11. **可观测**：`session.subscribe` 全事件转发到监控窗（PiAgentMonitorWindow）、diff 记录、模型重试计数；`piSelfCheckService.cjs`（1,082 行）自检链路（端点/代理/命令工具/环境快照）；错误报告 `agentErrorReporter` 上送 Cloudflare Worker 诊断端。

## 三、Pi 本体：架构逐项实证（以源码为准）

### 3.1 Agent loop 设计（packages/agent ≈2,550 行，"整个项目的心脏"）

- **双层循环**（`agent-loop.ts` `runLoop`）：外层处理 follow-up 队列（agent 将停时再注入），内层处理工具调用与 steering 消息。全程 `AgentMessage`，**仅在 LLM 调用边界 `convertToLlm` 降级为 Message[]**。
- **钩子面完整**（`types.ts` 逐个验证存在）：`convertToLlm`、`transformContext`、`getApiKey`、`prepareRequest`、`prepareNextTurn`/`prepareNextTurnWithContext`、`finishTurn`（可返回 `end/continue`，continue 触发一次纯上下文续轮）、`getSteeringMessages`/`getFollowUpMessages`（队列各带 `one-at-a-time/all` 模式，`agent.ts:255`）、`beforeToolCall`（可 block/改参/置 terminate）/`afterToolCall`（可覆盖结果/错误/terminate）。
- **整批提前结束规则**：一批工具结果**每一个**都 `terminate:true` 才停循环（`agent-loop.ts:691`）；`stopReason==="length"`（输出被截断）时整批工具调用直接判失败不执行——防带残缺参数乱改文件。
- **事件流**：`agent_start → turn_start → message_start/update/end → tool_execution_start/update/end → turn_end → agent_end`（agent-core 层）；coding-agent 层叠加 session/compaction/`agent_before_settle`/`agent_settled` 等约 40 种扩展事件（`extensions/types.ts` 逐一列出）。
- **工具执行模式**：并行/顺序（单工具 `executionMode` 覆盖），并行结果按上游规定顺序收集；工具内部可再调工具（`nested-tool-calls.ts`）。

### 3.2 工具协议

`ToolDefinition` 字段（pi4java 8.2 逐字段清单 + 源码核对）：`name/label/description/promptSnippet/promptGuidelines/parameters(TypeBox)/prepareArguments/outputSchema/exposure/namespace/reachesTools/annotations/defaultActive/prepareLoadout/executionMode/constrainedSampling/execute`。要点：**promptSnippet/promptGuidelines 是工具对系统提示词的贡献**（`system-prompt.ts` 动态拼装）；参数校验 `utils/validation.ts` 含类型强转，报错文本回传给模型。原生 8 工具：read/bash/powershell/edit/write/grep/find/ls（grep/find 运行时调 `rg`/`fd` 外部程序；OpenBidKit 随 `client/vendor/agent-tools/<平台>/bin` 捆绑 `rg/fd/jq`）。同一文件写操作经 `file-mutation-queue.ts` 串行化。

### 3.3 上下文管理

- **压缩**（`compaction/`）：手动 `compact(instructions)` + 自动（阈值触发 + 溢出触发），`DEFAULT_COMPACTION_SETTINGS = {enabled:true, reserveTokens:16384, keepRecentTokens:20000}`；含分支摘要（branch-summarization）。`estimateTokens` 无 usage 时估算。
- **会话持久化**（`session-manager.ts` 2,036 行）：**append-only JSONL 树**——每条记录 `id/parentId`，`CURRENT_SESSION_VERSION=3`，含 v1→v3 迁移；`create/open/continueRecent/inMemory/forkFrom/findById`；分支导航、分叉、分支摘要。会话文件与 Pi 官方互通是 pi4java 的硬指标（10.4），说明格式是稳定契约。
- **上下文文件**：`DefaultResourceLoader` 按规则发现 `AGENTS.md` 等并拼进系统提示词——OpenBidKit 恰好用 `agentsFilesOverride` 把"工作区约定"当上下文文件注入。
- **溢出判定** `utils/overflow.ts` + token 估算 `estimate.ts` 在 pi-ai 层。

### 3.4 编排与扩展

- Pi 自身**不做多代理编排**（无 sub-agents/plan mode/todo，README 明示），编排能力全在扩展面：`ExtensionAPI`（`registerTool/registerCommand/registerFlag/sendMessage/appendEntry/registerProvider` 等 + 40 事件）、技能（SKILL.md YAML frontmatter）、提示词模板、包分发（npm/git）。
- **收尾边界**（`agent_before_settle`/`agent_settled`）：一次运行最终收尾前触发，处理函数可追加记录并 `continue:true` 让会话再发一次模型请求——pi4java 文档称其为"使用方实现提交校验、失败修复、出错续跑的基础"。OpenBidKit 没用它而是外置 submit-repair 循环（等效但更粗），SoulMate 可直接抄内核语义。
- 真实世界集成标杆：OpenClaw（README 点名）；OpenBidKit 是"SDK 嵌入 Electron"的又一案例。

### 3.5 可观测

事件流即可观测基座（逐事件可订阅）；`pi-telemetry` 包是**厂商中立遥测契约**（接口+参考实现+一致性测试）；usage-totals/cache-stats 内建。OpenBidKit 在其上再包监控窗、诊断日志、自检。

### 3.6 口碑实证（正面/负面分列，不轻信宣传）

**正面（第三方可查）**
- 规模：GitHub ≈11 万 star（anchorterminal 引 GitHub API 8 Oct 2026 记 113,417；另一快照 122k）、npm 周下载 630 万（npm 页 6,307,903）、946 个下游依赖。
- Earendil 官方口径"全球每周数十万用户"（Pi 1.0 发布文，厂商自述，仅标注不作依据）。
- HarnessRouter 30 天用量榜 **#4/21 CLI agents**（18.8T tokens）；anchorterminal 综评 B（66.4/100），Harnesses 类 #12。
- finds.dev 直言供应链硬化"远超一般 npm 项目"：直接依赖精确锁版本、发布最小年龄 2 天、SLSA provenance、可离线复现构建、计划性 `npm audit`。
- 事件流/RPC 逐条有文档（`--mode json` JSONL 事件、`--mode rpc`），被评"scriptable local harness"首选。
- 设计评价（dpsm 解剖文）："小而稳的内核 + 大扩展面"，任何人想加上限都能在 `finishTurn` 钩子做而不用 fork 循环。

**负面（同样实证）**
- **无权限系统、无沙箱**（README/SECURITY.md 自认）：工具以启动者全权执行；审批/隔离全靠扩展自建。prompt injection 明示 out of scope。
- **破坏性变更发在 patch/minor**：1.0.3（patch）改 Azure provider 命名、1.2.0（minor）四项 breaking——所以 OpenBidKit 锁 0.80.10 是必要防御。
- 默认遥测 ping（安装/更新上报 pi.dev）需 `PI_TELEMETRY=0` 关；2026-06 出过 4 个安全公告（0.79.0 修复）。
- 新贡献者 issue/PR 默认自动关闭，社区进入有摩擦；部分 CI 连续失败 regressed。
- 自动重试靠**正则匹配错误文案**、上下文溢出判定同样靠文案——OpenBidKit 不得不写 `piRetryErrorNormalizer` 改写错误文案来配合（集成税）。

## 四、OpenBidKit 用 Pi 做了什么：原生 vs 自包

| 标书流水线需要 | Pi 原生提供 | OpenBidKit 自包 |
|---|---|---|
| 多轮工具循环、流式事件 | ✅ agent-loop 全套 | — |
| 会话持久化/断点续跑 | ✅ JSONL 树 + SessionManager | 业务状态机（prompted_stage/compaction_pending/phase）+ 恢复语义 |
| 上下文压缩 | ✅ compact()/自动压缩 | **阶段交接压缩**（业务摘要提示词、正文以工作区文件为准不进摘要、失败补偿重压） |
| 长任务编排 | ❌（无 sub-agent/plan mode） | **阶段循环**（continueTask 交接 + 20+ 命名任务）+ 子代理并发池 + 排队 |
| 产物验收 | ❌ | 提交校验 validateOutput + 三段式修复策略 + 同会话退回修复 |
| 防跑飞 | ❌（Pi 愿赌服输烧 token） | 看门狗 + 轮数上限 + 空转诊断元代理 |
| 文件保护 | ❌ | 受保护文件基线（sha256 副本 + 收尾还原） |
| 用户交互 | ✅ 扩展 UI 子集 | ask-user/report-failure 工具 + Electron 提问窗 + autoAnswer 管理 |
| 模型接入 | ✅ 多 provider | 本地 OpenAI 代理（排队/用量/错误归一/可重试标记） |
| JSON/Word 产物 | ❌ | json-validation、openxml 工具 |
| 省钱小刀 | ✅ 整批 terminate 语义 | task_complete 参数 + afterToolCall 复用该语义免总结请求 |
| 知识注入 | ✅ 上下文文件/技能发现 | agentsFilesOverride 单点注入工作区约定 |
| 可观测 | ✅ 事件订阅 | 监控窗、自检、错误上报 Worker |

一句话：**Pi 提供"一次会话内的可靠工具循环 + 会话/压缩/工具协议"，OpenBidKit 把"流水线、验收、防跑飞、持久任务"全部包在 Pi 外面**；双方的边界恰好是 Pi "机制在内核、策略在扩展"的纲领。

## 五、取长补短清单（对照三层架构：能力→SoulMate ／ 知识→OpenSoul ／ 流程→插件 YAML）

> 每项：抄什么 · 怎么抄 · 归哪层。C 编号沿用《bidding-plugin-improvement-roadmap.md》能力层定版。

| # | 抄什么 | 怎么抄 | 归哪层 |
|---|---|---|---|
| 1 | **钩子面 + 整批 terminate**（beforeToolCall/afterToolCall/finishTurn/prepareRequest/steering/followUp 六类钩子；全批 terminate 才提前结束） | soulmate_agent.py 工具管道加 before/after 钩子链（可 block/改参/覆盖结果）；finishTurn 可返回 end/continue 支撑续轮 | ①能力（SoulMate 内核） |
| 2 | **收尾边界**（agent_before_settle 可追加记录+continue 再发一轮；agent_settled 才算彻底结束） | SoulMate 暴露 settle 边界事件；插件"节点后置校验"挂这里，实现"产出→校验→不过退回同会话修复" | ①能力 + ③流程（校验节点挂点） |
| 3 | **三段式提交修复策略**（normal→change-strategy→minimum；stalled≥2 无 blocking 就接受；minimumGoal 只修阻塞） | 流程引擎校验节点的失败重试语义直接抄 piSubmissionPolicy 54 行逻辑（含修 prompt 模板结构） | ③流程 |
| 4 | **会话 JSONL 树 + 持久任务状态机**（append-only、id/parentId/fork、prompted_stage/compaction_pending） | SoulMate 会话存储按此格式（顺带与 Pi 互通）；流程引擎运行时状态机抄 5 个字段 | ①能力 |
| 5 | **阶段交接压缩**（业务摘要提示词模板："正文/台账以工作区文件为准，不在摘要中摘录"；compaction_pending 断点补偿） | SoulMate compact() 支持自定义压缩指令；插件阶段切换节点声明压缩要求 | ①能力 + ③流程 |
| 6 | **本地 LLM 代理**（排队/用量/错误归一/可重试错误标记） | OpenMate LLM 网关（openmate-delivery-forms 已定）补三件事：错误文案归一（配合自动重试正则）、用量统计入账、请求排队 | ①能力 |
| 7 | **防跑飞三件套**（无进展看门狗 8min + 每提示词轮数上限 + 空转诊断元代理"摘录会话记录→诊断子任务→继续/收尾/停止"） | SoulMate 内建 watchdog + maxTurns（子代理默认 100）；空转诊断做成流程层可选节点（会话摘录工具 extractSessionRecord 是现成模板） | ①能力 + ③流程 |
| 8 | **受保护文件基线**（manifest+sha256 副本、提交前还原、分组登记） | SoulMate 工作区工具纪律：任务输入文件登记基线，settle 前 restore() | ①能力 |
| 9 | **固定工具清单 + 阶段门禁**（清单恒定复用前缀缓存；beforeToolCall 拦截替代改清单） | 流程引擎"阶段工具权限"用 setAllowedTools 语义；SoulMate 实现拦截钩子 | ③流程（权限声明）+ ①能力（实现） |
| 10 | **结构化人机对话工具**（ask-user 2–5 选项+唯一 custom；report-failure 终止需确认、autoAnswerAllowed 递减） | SoulMate 通用工具；直接治 clarify 阻塞与"Agent 私自放弃任务" | ①能力 |
| 11 | **json-validation 预置 Schema 工具**（四阶段、路径越界校验、错误回传模型） | SoulMate 工具；Schema 映射由插件任务配置下发 | ①能力 + ③流程（配置） |
| 12 | **task_complete 免总结**（工具加参数 + afterToolCall 置 terminate 省一次请求） | SoulMate 可选优化开关（长流水线逐节生成时省钱效果显著） | ①能力 |
| 13 | **上下文文件单点注入**（agentsFilesOverride：工作区约定/任务知识作为 AGENTS.md 类文件进系统提示词） | OpenSoul 检索结果按任务组装成"上下文文件"注入，而非全量塞 prompt（对应路线图 2.3 取数接口） | ②知识 |
| 14 | **子代理治理**（并发乘数×2 超出排队 + 轮数上限统一施加 + runChildTask 沿用限制） | 流程引擎并行节点的运行时基线语义 | ③流程 |
| 15 | **依赖锁版本防御**（Pi 在 patch 发 breaking；OpenBidKit 锁 0.80.10） | 引 Pi/pi 类依赖一律精确锁版本 + 供应商错误文案兼容层（piRetryErrorNormalizer 模式） | ①能力（工程纪律） |

**明确不抄**：①Pi 无沙箱/无权限的放权模型——OpenBidKit 能用是因为"本机可信边界"声明，OpenMate 有对话 Agent + 网络服务，必须 MCP 工具契约 + 权限声明 + 安装确认（与前报告"规避 1"一致）；②Pi 的自动重试正则匹配错误文案这种脆弱耦合——抄功能不抄机制，SoulMate 重试用结构化错误码；③多文件手工同步的 UI 注册（前报告规避 3）不变。

### 与既有定版的合并点
- C4（OpenXML 编译）：OpenBidKit 把 openxml 做成 **Pi 工具**（784 行 `piOpenXmlTool.cjs`）让 Agent 直接调——比我们"能力算子"多一层"工具暴露"，建议 C4 交付时同时给 SoulMate 工具定义。
- C5（Mermaid 入图）：同理可注册为工具 + 程序侧确定性渲染（对标报告第五节），Agent 产源码、程序出图。
- 项目事实（路线图 2.1 project_facts.yaml）：OpenBidKit 的 `globalFactsTaskV2`（全局事实表）+ `agentsFilesOverride` 注入就是"事实锁"的 Pi 版实现，印证 C1 方向。

---

## 附：证据索引（主要文件）
- OpenBidKit：`client/electron/services/pi/{piSessionFactory,piRuntimeService,piSubmissionPolicy,piSummaryControl,piWorkspaceBaseline,piStallDiagnosis,piPersistentTaskStore,piEnvironment,piJsonValidationTool,piUserQuestionTool,piTaskFailureTool,piOpenXmlTool}.cjs`、`services/agent/{agentOpenAiProxy,agentToolEnvironment}.cjs`、`services/agentService.cjs`、`services/contentGenerationAgent.cjs`、`pi4java开发文档.md`、`client/package.json`
- Pi：`packages/agent/src/{agent-loop,agent,types}.ts`、`packages/coding-agent/src/core/{agent-session,session-manager}.ts`、`core/compaction/compaction.ts`、`core/extensions/types.ts`、`README.md`
- 口碑：pi.dev、earendil.com/posts/pi-1-0（厂商）、npm 包页、anchorterminal.com/tools/earendil-pi、finds.dev/repo/earendil-works/pi、dpsm.github.io/agent-harness-anatomy/pi/、harnessrouter.ai/harnesses/pi
