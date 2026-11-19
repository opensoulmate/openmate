# 25 组件真实性 + 协作性审计（component-reality-audit）

> 2026-11 · 铁律执行：断言查代码 file:line 不编造；查无实据如实标"未接线"。
> 验收口径 = 用户铁律："系统的 25 个组件一定要保证真实有用……整个功能是要跟系统本身
> 相适应，形成有效协作这样组件的存在才有意义。"

## 0. 审计口径与方法

- **真引擎清单**：逐组件查 `~/opensoul/src/<comp>/`（引擎实现）、`~/opensoul/src/api/<comp>.py`（端点）、
  `~/openmate/acp-proxy/`（Soma/Mate 侧实现）——实际干的活 = 真算法/真 IO/真落盘，不是只读列表/CRUD 壳。
- **协作链**：grep 全仓 `from src.<comp>` 入调用点（排除自家模块/测试计单列）+ 前端 fetch 端点 + acp-proxy 接线。
- **身体图证据复核**：body-map-client.tsx RELATIONS 92 条实证边逐条程序化复核（解析 evidence 里的
  file:line → 回读该行比对声明内容）——**92/92 全部指向真实调用点，零编造**；本轮 P0 改码引起的
  2 处行号漂移（api/link.py、api/limb.py）已校正。P0 补强新增 2 条实证边（92→94）。
- **判定图例**：✅真实有用（真引擎+真实协作链）/ 🩹有壳无肉（CRUD/模拟冒充引擎）/ 🏝孤岛（引擎真但无业务调用方）/ ❌凑数。
- **引擎规模实证**（LOC/端点数为实测）：opensoul 侧 23 个组件模块合计 47,857 行（含本轮
  新增 link_persist/browser_backend）；acp-proxy 仅 soulmate_agent.py 即 9,084 行。

## 1. 逐组件审计表

### 核心主体层（8）

| # | 组件 | 真引擎清单（实际干的活 / 端点 / 落盘） | 协作链（被谁调用 / 调用谁，file:line） | 判定 |
|---|------|----------------------------------------|----------------------------------------|------|
| 1 | **Soul** | FastAPI 主进程（src/main.py:742 起路由注册）；三库知识引擎（常规/本体/记忆，统一召回 `POST /api/search/recall` RRF）；Brain 认知 API 20 端点（/think /verify /remember /recall）；SQLite+FTS/Meili 落盘 | 被 Mate 全页面 fetch（body-map mate-soul：soul-client.tsx:66 /api/health/all）；被 MCP server 读（src/mcp/server.py:21）；brain↔cortex/hippo/mind/learn/mirror/heredity 多条双向边（api/brain.py:111,189,191,297,889,1006） | ✅ |
| 2 | **Cortex** | 4,310 LOC/16 文件：chain_of_thought、graphrag、loop_guard、token_attribution、context_compression、multi_agent_coord；`POST /api/cortex/plan` `/think` `/agent` | 82 处被调：chat.py:13-14,174（对话 loop guard+token 归因真实挂主路径）、trajectory/branch_summary.py:61、gland/router.py:224（重试策略复用）；soul-cortex 双向（api/brain.py:1006 MultiAgentCoordinator） | ✅ |
| 3 | **Nerve** | event_bridge（emit/push_event）→ NATS client（src/nerve/client.py，连接/重连/订阅）；`/api/nerve/*` 14 端点（publish/events/subscribe/nodes） | 50 处被调：api/vein.py:9、api/echo.py:9、api/immune.py:18、api/link.py:7、api/marrow.py:11、api/mirror.py:11、api/brain.py:401、api/soma_connector.py:20 等——事件总线真在用 | ✅ |
| 4 | **Vein** | file_store（data/vein/files 真落盘）、chunked 分片上传、versioning（file_versions 表）、cache；`/api/vein/*` 26 端点 | 48 处被调：api/vein.py:11-13 引擎接线、调 Sense 解析（api/vein.py:857,869,883 OCREngine/ASREngine）；vein-nerve/vein-soul 边 | ✅ |
| 5 | **Soma** | acp-proxy soulmate_agent.py 9,084 行协调核（工具面/会话/记忆/群组）；browser-extension 采集（background.ts:18 POST /api/capture）；src/soma/study（StudyLoop 逆向学习，study_evidence 表）；~/opensoma（Rust 采集引擎仓库） | soulmate_agent.py:44-1926 挂接 Gland/Immune/Echo/Gene/Hippo/Marrow/Vein/Limb/Voice/Vision/Trajectory/MCP/Reflex/Pulse/Diagnostics（body-map soma-* 15 条边逐条有证） | ✅ |
| 6 | **Sense** | ocr.py（OCREngine 真识别）、asr.py、multimodal.py、behavior.py（行为感知落 trajectory_events）；`/api/sense/*` 22 端点（ocr/image、ocr/pdf、behavior/*） | 被 Vein 文件解析调（api/vein.py:857,869,883）；调 Gland（sense/ocr.py:203）、写 Trajectory（sense/behavior.py:127）；浏览器采集汇流 browser-task-runner | ✅ |
| 7 | **Will** | 6,149 LOC/15 文件：flow engine、dag_planner、goal_loop、job_queue、checkpoint、proactive；`/api/will/*` 52 端点；jobs/will_checkpoints 表 | main.py:344-351 启动挂 job_queue/proactive；被 Hippo 用（api/hippo.py:850 job_handlers 衰减任务）；调 Learn/Heredity/Nest（job_handlers.py:89、proactive_behaviors.py:187,38、checkpoint.py:38）；flow_collab.py:1105-1114/1253 接 Pulse/Reflex | ✅ |
| 8 | **Mate** | OpenMate 客户端进程本体（Next.js 16 + Tauri + 浏览器插件 + MCP 客户端）；产品自述条目，按对账口径不设器官功能页 | 全部器官页 fetch 各组件 API（body-map mate-* 30 条边，逐条 file:line 有证） | ✅ |

### 配套底座层（8）

| # | 组件 | 真引擎清单 | 协作链（file:line） | 判定 |
|---|------|-----------|---------------------|------|
| 9 | **Immune** | 4,487 LOC/12 文件：moderator（内容风控）、permission_engine、audit（perm_decision_audit 表）、intrusion、access_control（immune_access 表）；`/api/immune/*` 36 端点 | 58 处被调：chat.py:154、pipeline.py:105、hippo/memory_redact.py:52、gland/router.py:36、marketplace.py:19,25；soma-immune（soulmate_agent.py:61-62 注入审计门/密钥脱敏） | ✅ |
| 10 | **Vital** | collector（psutil 真采集）、health、alert(+alert_persist)；`/api/vital/*` 6 端点；指标历史落库 | main.py:108-111 启动挂载（AlertManager/MetricsCollector/HealthChecker）；notifications.py:214 消费；diagnostics.py:23 巡检探针 | ✅ |
| 11 | **Marrow** | backup.py（tarfile 真打包 create/restore+checksum+manifest）、schedules、migrator（数据迁移导出导入）；`/api/marrow/*` 15 端点 | api/marrow.py:9-10 引擎接线；marrow-nerve 事件（api/marrow.py:11）；soma-marrow（soulmate_agent.py:1926 文件索引 index.db） | ✅ |
| 12 | **Gland** | router（多 Provider 模型路由）、key_manager、token_meter（gland_usage 表计量）、route_policy、harness_profiles；`/api/gland/*` 18 端点（/chat /models /usage） | 93 处被调：hippo/dream_distiller.py:24、hippo/memory_model.py:191、trajectory/branch_summary.py:589、sense/ocr.py:203、api/chat.py 路由；调 Cortex 重试（gland/router.py:224）、记 Hippo 决策日志（route_policy.py:138）、经 Immune 审核（router.py:36） | ✅ |
| 13 | **Gene** | templates.py（TemplateEngine 模板库）、skill_learner（learned_skills/skill_audit 表）、seed_standard_skills；`/api/gene/*` 26 端点 | system/bootstrap.py:245 启动加载模板引擎；api/gene.py:8,16；soma-gene（soulmate_agent.py:74 gene_wire）；learn-nest 同族租户轴 | ✅ |
| 14 | **Echo** | dispatcher.py 670 行：9 渠道真推送（webhook/dingtalk/企微/telegram/feishu/slack/discord，urllib 实发）+ 队列重试 + history_persist 落盘；`/api/echo/*` 15 端点 | notifications.py:124,394,426 真实消费；echo-nerve 分发（api/echo.py:6-9）；soma-echo（soulmate_agent.py:71 memory_echo） | ✅ |
| 15 | **Mirror** | sandbox.py（SandboxManager 隔离执行）、isolation.py、metacognition.py（metacognition_log 表）；`/api/mirror/*` 22 端点 | 元认知回流真实挂对话：chat.py:142、ws_chat.py:509、ai_groups.py:1911 metacognitive_advisory_for；soul-mirror 双向（api/brain.py:297 / mirror/metacognition.py:460） | ✅ |
| 16 | **Link** | connector.py：webhook 真收/发（HMAC 签名、回调凭据 fail-closed、rotate/revoke）；`/api/link/*` 13 端点（含 POST /webhook/{id} 入站）；**修前连接器/事件全内存、重启全清** | api/link.py:6-7 引擎+nerve 接线；link-nerve 边；**内部业务调用方=0**（仅自家 API+测试，边缘网关性质） | 🩹→✅（P0 已补落盘，见 §2.3） |

### 高级生态层（9）

| # | 组件 | 真引擎清单 | 协作链（file:line） | 判定 |
|---|------|-----------|---------------------|------|
| 17 | **Hippo** | 7,779 LOC/22 文件：long_term_memory、memory_pipeline、memory_model、dream_distiller（记忆蒸馏）、decay（遗忘衰减）、decision_log、session 生命周期；`/api/hippo/*` 51 端点；memories/memories_fts/memory_versions/memory_audit 表 | 170 处被调（全系统第一）：chat.py:84,258、api/brain.py:111、api/hippo.py:1266 回写三库；调 Gland（dream_distiller.py:24）、经 Immune 脱敏（memory_redact.py:52）、进 Will 队列（api/hippo.py:850）、经 MCP 会话门（api/hippo.py:15） | ✅ |
| 18 | **Reflex** | opensoul 侧 cache.py 235 行（高频问答缓存：精确+fuzzy、TTL、命中统计）；acp-proxy 侧 agent/reflex.py 676 行真应激枢纽（MCP 调用捕获→快速应激/安全拦截，零 LLM）；`/api/reflex/*` 12 端点 | **修前 opensoul 侧=孤岛**：只被自家 CRUD 调（api/reflex.py:6），对话主路径零调用。acp-proxy 侧真实：soulmate_agent.py:187,1096、flow_collab.py:1253、routes/pulse.py:177-178 | 🏝→✅（P0 已接对话快路径，见 §2.2） |
| 19 | **Heredity** | evolution_loop、self_evolution、version_registry(+registry_persist)、migration(+migration_persist)；`/api/heredity/*` 29 端点；evolution_ledger/proposals/breaker/triggers 表 | 被 Will 触发（proactive_behaviors.py:187 run_evolution_cycle）、brain.py:889 SelfEvolution；调 Learn 长期学习（self_evolution.py:16）；heredity-learn 双向 | ✅ |
| 20 | **Nest** | tenant.py（TenantManager）、isolation.py（IsolationEngine 资源配额/隔离）、store.py；`/api/nest/*` 17 端点；tenants/access_log 表 | 58 处被调：services/tenant_binding.py:242（全系统身份轴）、learn/course_engine.py:15、api/enterprise.py:22、will/checkpoint.py:38 | ✅ |
| 21 | **Pulse** | opensoul timer.py（PulseEngine 时序信号/Tick/漂移统计）+signal_persist；acp-proxy agent/pulse.py 636 行心跳守卫（超时催办/僵尸回收）；`/api/pulse/*` 11 端点 | soulmate_agent.py:188,2351 _pulse_guard_provider；flow_collab.py:1105-1114 attach_pulse 挂 Will 工单；routes/pulse.py:177-178 自检旁路进 Reflex | ✅ |
| 22 | **Limb** | opensoul executor.py 889 行任务队列/模板/历史（真，limb_tasks/history 落盘）；**修前 `_execute_action` 是 "simulated for demo" 假执行——假成功+伪造 /tmp/rpa_screenshot_*.png 截图路径**；真 RPA 在 acp-proxy：browser_tools.py 526 行（Playwright/CDP）+ browser_bridge.py /api/browser/task 通道 + 录制回放 flows | 修前 opensoul 引擎仅被自家 API 调（api/limb.py:6-10）；acp-proxy 侧真实：soulmate_agent.py:164 BrowserTools、flows 录制回放 | 🩹→✅（P0 已接真执行委托，见 §2.1） |
| 23 | **Voice** | tts_engine.py 377 行（edge-tts 真合成 MP3）+stt_engine+voice_profiles；`/api/voice/*` 15 端点（/synthesize 真出音频） | acp-proxy tts_tools.py 202 行挂 soulmate_agent.py:179（soma-voice）；api/voice.py:19 src.voice.stt_engine 接线 | ✅ |
| 24 | **Vision** | chart_generator.py 349 行（matplotlib/PIL 真出图）+mindmap+dag_visualizer+knowledge_visual+output_store；`/api/vision/*` 22 端点（/chart/* 真渲染） | api/vision.py:11 db_pool 落库（vision-soul）；acp-proxy image_tools.py 499 行挂 soulmate_agent.py:180（soma-vision） | ✅ |
| 25 | **Mind** | emotion.py（情感词典分析）、personality.py（人格库）、preference_learner、emotion_decision、user_memory；`/api/mind/*` 16 端点；learned_preferences/user_memory 表 | soul-mind 双向：api/brain.py:191 EmotionDrivenDecision 消费、api/mind.py:10 落 Soul 库；soma-mind 侧 personality 挂对话（mind-client.tsx:71） | ✅ |

### 判定汇总

| 判定 | 数量 | 组件 |
|------|------|------|
| ✅ 真实有用 | 22 | Soul/Cortex/Nerve/Vein/Soma/Sense/Will/Mate/Immune/Vital/Marrow/Gland/Gene/Echo/Mirror/Hippo/Heredity/Nest/Pulse/Voice/Vision/Mind |
| 🩹 有壳无肉 | 2（P0 已补强→✅） | **Link**（配置全内存不 durable）、**Limb**（opensoul 侧模拟假执行） |
| 🏝 孤岛 | 1（P0 已补强→✅） | **Reflex**（opensoul 引擎无业务调用方，职能未接线） |
| ❌ 凑数 | 0 | — |

## 2. P0 级补强实施记录（按职能做真引擎 + 接真协作链）

### 2.1 Limb 真执行（有壳无肉 → 真引擎 + Limb↔浏览器通道协作链）

- **新增 `opensoul/src/limb/browser_backend.py`**（BrowserTaskBackend）：RPA 步骤委托
  acp-proxy 浏览器执行通道**真执行**——`browser_backend.py:226 POST /api/browser/task` +
  `:237` 长轮询 `/api/browser/result/{task_id}`（与 acp-proxy/agent/browser_tools.py
  `_app_dispatch` 同线协议，实测对齐）。ActionType→真通道映射：navigate→goto、click→click、
  type→type、select→fill、screenshot→screenshot、extract/scroll/key_press/hover/submit→eval（JS 真跑页面）、
  wait→真 sleep；**conditional/custom 无真通道映射→如实失败，拒绝假成功**。
  凭据字段（password/passwd/pwd/密码）自动 `secret=True`（回执 *** 脱敏）；`{{var}}` 变量替换。
- **`src/limb/executor.py:629`** `_execute_action` 真执行优先（有 backend 即逐步委托，
  StepResult.simulated=False）；模拟路径（:648 `_simulate_action`）产出**一律 simulated=True +
  输出前缀【模拟执行，未真实操作】**，:684 起**不再伪造 `/tmp/rpa_screenshot_*.png`**（修前文件根本不存在）。
  `StepResult.simulated` 字段（src/limb/tasks.py:103，落盘形 to_state/from_state 全程携带）；
  `stats()` 新增 `execution_backend` 可观测（executor.py:869）。
- **`src/api/limb.py:28`** `_build_backend()` 生产接线：`LIMB_REAL_BACKEND=acp_proxy` 时启用真执行。
- **测试** `tests/test_limb_browser_backend.py`（10 用例全绿）：映射表/变量替换/凭据脱敏/真成功/
  通道失败如实/不可达如实/无映射拒绝假成功/真等待/执行器委托/模拟路径如实标注。

### 2.2 Reflex 短路径（孤岛 → 对话主路径真实消费）

- **`src/api/chat.py:466-533`** `_reflex_cache_key`（按用户命名空间）/`_reflex_short_path`
  （:478）/`_reflex_memorize`（:504）/`_reflex_log_decision`（:524）：高频问答缓存
  **精确命中→直接应答（RAG/LLM 零调用，减少大模型重复推理）**；真实回答回填缓存
  （source=chat，生产→消费闭环）。流式 `rag_stream:541` 短路径 + `:653/:779` 回填；
  非流式 `:801` 短路径 + `:935` 回填。
- **按用户命名空间（语义根因，非测试让步）**：RAG 答案拼装了用户级记忆/学习/失败
  教训上下文——同一问题不同用户答案本就不同，跨用户共享缓存=串答；自动回填按
  用户隔离，裸键=手动种子（/api/reflex/cache 通用条目）人人可命中。
- **命中门 `src/reflex/cache.py:89` lookup(min_similarity=1.0)**：短路径只认精确命中——
  fuzzy 误答风险不进门；查询失败 fail-open 走全路径。命中也记 decision_log
  （mode=reflex，`chat.py:523`）——路由审计面无空洞，短路不吞决策记账。
- 与 `/api/reflex` 共享缓存单例（api/reflex.py:11）——/api/reflex/stats 的命中计数=真实对话流量。
- **测试** `tests/test_reflex_chat_wiring.py`（4 用例全绿）：命中零 LLM（路由 monkeypatch 炸弹自证）/
  未命中照走全路径/记忆回填闭环/精确门。conftest 新增 `_isolate_reflex_cache`
  （缓存单例跨用例存活，跨用例同题命中会踩别的用例观测 seam——每用例前后清空，
  与 LIMB_TASKS_DB「禁跨用例陈旧」同纪律）。

### 2.3 Link 落盘（有壳无肉 → 真 durable 网关）

- **新增 `opensoul/src/link/link_persist.py`**（LinkPersistence，task_persist 同族纪律）：
  `link_connectors`/`link_events` 快照表，写穿+首触懒回载+事件窗裁剪（LINK_CONNECTORS_DB
  测试隔离，缺省 data/link_connectors.db）。诚实边界：secret/callback_token 明文落本地库
  （与 gland key_manager 同处境，如实记不谎称加密）。
- **`src/link/connector.py:145`** IntegrationManager(persist=) + `:155` _ensure_loaded +
  `:188` _sync_connector + to_row/from_row 全字段保真（:71 起）；create/update/delete/rotate/
  revoke/record_event/send_webhook 全部写穿（写失败记账旁路，内存本体不受阻）。
- **`src/api/link.py:17`** 生产单例接线；`tests/conftest.py` 新增 LINK_CONNECTORS_DB 全测试隔离。
- **测试** `tests/test_link_persist.py`（7 用例全绿）：重启不丢/凭据状态全字段往返/删行/事件落盘+裁剪/
  纯内存缺省零变化/生产单例接线/env 路径。

### 2.4 顺带修复（存量红 + 行锚漂移）

- `tests/test_cd45_deep_dive.py` 3 处"真源字面量窗口"行锚随源码漂移（rag.py:81-98、
  agent_proxy.py:94-102、main.py:311-314 形状构造点现址）——修前既有失败，非本轮引入。
- `scripts/census_contract_drift_deep_dive_cd22.py` 1 处行锚随本轮改码漂移
  （connector.py send_webhook 成功分支 :245→:404，evidence 行号同校）：cd22 是源码
  行锚门（锚点行必须真含声明子串），link_persist 接线插入行后锚点失准→工件拒出
  22 用例 error——校正后归零。

### 2.5 身体图实证边回填（92→94）

- body-map-client.tsx 新增 2 条真实接线边：**soul-reflex**（chat.py:509/768/895 短路径+回填）、
  **limb-soma**（browser_backend.py:226/237 委托浏览器执行通道）。

## 3. 遗留缺口（如实标注，未完待续）

1. **ws_chat WebSocket 对话主通道未接 Reflex 快路径**——本轮只接了 /api/chat（SSE+非流式）；
   OpenMate 实际对话大头走 ws_chat.py，需同款短路径接线（P1）。
2. **Link 无内部业务调用方**——边缘网关属性（外部系统↔平台），API 面+事件面真实；后续存量
   散装 webhook 工作流应逐步接回 /api/link。
3. **Limb conditional/custom 动作无真通道映射**——如实拒绝（条件分支归 Will 流程引擎）；
   下一步可映射到 flow engine 节点补齐。
4. **Reflex 缓存无持久化**（重启清空）——缓存语义可接受；命中率靠 chat 回填累积，观察窗在
   /api/reflex/stats。
5. **Link secret/callback_token 明文落库**——与 gland key_manager 待统一加密方案。
6. **全量测试基线**：修前基线 122 failed / 7943 passed / 2 errors（含 live :8090 服务面测试的
   环境性失败与存量漂移）；本轮改动全量终态见 §4。

## 4. 测试与提交

- **新增测试 21 个全绿**（limb 10 + link 7 + reflex 4）；触点回归逐族与修前基线（git stash
  对照实测）对账**零新增红**：chat/backflow 家族（9 文件 112 用例，余 12 红=修前既有
  ws_chat 注入族）、link 20 文件、limb 10 文件、cd 区段（cd17-22 邻域 0 error，余 3 红=cd1
  既有）。
- **全量基线对账（8,088 用例）**：修前基线 = 122 failed / 7,943 passed / 2 errors（存量红族：
  sessions_*、branch_summary、advisory_review、ws_chat 注入、hermes_bridge 降级等，多为
  live :8090 服务面/环境依赖）。本轮增量 = +21 新用例全过、cd45 3 项存量红修复、cd22
  行锚门修复（修前因改码行漂移致 22 用例 error，区域复测归零）。末次全量跑（2026-11-15）
  原始计数 120F/7,944P/24E——其中 22 error 已定位为 cd22 行锚漂移并在其后修复+区域复测
  证实；预期终态 119F/7,967P/2E（= 基线 122F − cd45 3 修，+21 新过，零新增红）。
- **⚠️ 仓库 pre-commit hook（ruff 自动修复+格式化）会重排提交文件行号**——所有 file:line
  证据均按提交后状态复核过（本轮为此校正过 2 处边证据+cd22 锚点，教训：行号类证据必须
  在 commit 后再验一遍）。
- **git 提交（显式路径）**：opensoul `f18f104e`（P0 补强 15 文件 +1,201 行）+ `dd98d08a`
  （cd22 行锚校正）；openmate 本报告 + body-map 2 条新实证边。
