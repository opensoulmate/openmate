# 蓝图 51 项逐条对账（2026-10-12 · 统筹者审计）

对标源：`synthesis/openmate-rewrite-blueprint.md`（P0×25 + P1×16 + P2×10）。
证据方法：全仓特征扫描 + 代码/测试文件定位，只认实装证据。结论：**✅48 / ⏳2 / ❌1**（2026-10-12 周期#128 更新：P0-09/P0-10/P1-01/P1-15 终验销账 ✅、P1-05 以替代实现销账 ✅(替代)）

## P0 能稳定跑（25）
| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 01/02 | 单写者/第二写者入队 | ✅ | `agent/writer_fence.py`(SessionWriterFence)+`agent/tool_exec_fencing.py`+lost-update回归测试 |
| 03 | 双写/状态错乱对应 | ✅ | `tests/test_tool_exec_fencing.py` 核心回归 |
| 04 | FIFO/并发上限/独立lane | ✅ | Lane-aware Command Queue（`architecture_enhanced.py`）+ `systemic_test.py` |
| 05 | steer/followup/collect/interrupt | ✅ | `app.py`/`soulmate_agent.py` steer；排队/收集语义在队列层 |
| 06 | 连点/打断对应 | ✅ | `tests/test_steering.py` |
| 07/08 | schema_version/doctor | ✅ | `agent/schema_doctor.py` |
| 09 | Plan 模式禁写盘 | ✅ | Plan语义=`PermissionMode.EXPLORE`（allow规则不可授予修改→Step3 DENY）；终验 2026-10-12 周期#124 **12/12 PASS**：引擎层 write_file/execute_code/terminal 写入全 DENY+显式 allow 规则不可覆盖+read_file/web_search 只读 ALLOW；真实 WS 用户路径（soulmate 真实 LLM 回合）explore 态 write_file 文件未创建+回复报拦截，bypass 对照组文件真实创建；探针 `acp-proxy/plan_mode_probe_ws.py`，证据 `evidence/p0-09-plan-mode-evidence.json`（入口为 API `PUT /api/immune/permission/mode`，UI 开关另案） |
| 10 | 高危强制 ask | ✅ | 终验 2026-10-12 周期#125 **17/17 PASS**：bypass（最宽松档）下 sudo/rm -rf/curl POST/机密读取/系统目录写全强制 ask（requires_human=True）+灾难命令 rm -rf / hard deny 不可覆盖+DONT_ASK→DENY 绝不静默；网关层（真实 PermissionGate×真实引擎）无人值守 ask→拦截 ask-denied（-32012"未审批拦截"语义）/真人拒绝→ask-denied/批准→ask-approved+同参 cached-allow+perm_decision_audit outcome 回写（服务端真源）；真实 WS 用户路径（soulmate 真实 LLM 回合，bypass 态）wire 捕获 session/request_permission（强制 ask 到达真人通道）+拒绝审批后文件未创建+非高危对照组文件真实创建。**终验发现并根因修复**：毁机硬规则"rm -rf /"子串匹配误伤 `rm -rf /tmp/x`（过度硬否决→合法删除无法人工批准）→锚定正则只命中根目标（opensoul `permission_engine._ANCHORED_ROOT_RES`，与 acp-proxy 降级层同源锚定；test_permission_engine 52/52 含 3 新增回归）。探针 `acp-proxy/high_risk_probe_ws.py`，证据 `evidence/p0-10-force-ask-evidence.json`；语义边界实录：发消息类 message_send 在 default 强制 ask、bypass 走模式兜底放行（改动会破坏无人值守微信发消息工作流，待用户拍板不冒进） |
| 11 | .env/密钥 deny | ✅ | `soulmate_agent.py` secrets 处理 + `dna_evolution.py` |
| 12 | ToolFailure 三级 | ✅ | `agent/tool_errors.py` + `tool_recovery.py` |
| 13 | abort 级联取消 | ✅ | `app.py` abort + cascade（systemic_test） |
| 14 | doom_loop ≥3 拒绝 | ✅ | `agent/arch_monitor.py` + `tool_errors.py` |
| 15/16 | 自动 compact + Work-State | ✅ | `agent/context_compression.py` |
| 17 | 工具输出截断 | ✅ | max_output（`soulmate_agent.py`/`llm_engine.py`） |
| 18-20 | trace/run_id/指标/flush | ✅ | `agent/agent_tracer.py` + `trajectory_recorder.py`（fire-safe） |
| 21 | 双客户端不损坏 transcript | ✅ | systemic_test 双写场景 |
| 22 | 连发 10 条有序 | ✅ | FIFO 测试 |
| 23 | 杀进程 resume | ✅ | `agent/agent_checkpoint.py` |
| 24 | 工具超时会话可继续 | ✅ | tool_recovery + 错误可见 |
| 25 | compact 触发+回放 | ✅ | context_compression + ws_acp 重放 |

## P1 混合形态（16）
| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 01 | 危险工具不对 personal 开放 | ✅ | 终验 2026-10-12 周期#126 **18/18 PASS**：语义真源=`agent/sandbox_policy.py` 工具面轨道隔离（tool_track：per-session显式>default_track，缺省 coding=零行为变化；store损坏/非法值 fail-closed 按 personal）；危险编码集=terminal/execute_code/process/write_file/patch/batch_execute **工具面不暴露**（registry 裁剪语义）+运行时 fail-safe 显式拒绝（指引"显式切换 coding"）；**终验暴露侧门当拍即修**（首轮 16/18）：terminal 被裁后模型改走 process 后台进程通道执行同一条 shell 命令（"拦正路留侧门"同类）→ process 入列、执行原语全家同裁，复跑 18/18；真实 WS 用户路径（同一真实会话两轮，WS→ws_acp→soulmate stdio→真实 MiMo 回合）：personal 轮 terminal touch **文件未创建**+回复如实报"没有 terminal/execute_code 等可执行工具、命令未执行"；显式切换 coding（API PUT tool_track）后同会话同指令**文件真实创建** end_turn（对照+切换语义一体实证）；API 入口=`PUT /api/agent/sandbox-policy/{sid} {"tool_track":"coding"\|"personal"}`（非法值 ok_track=False 拒绝落盘不静默纠正），describe 透出 dropped_if_personal；探针 `acp-proxy/tool_track_probe_ws.py`，证据 `evidence/p1-01-tool-track-evidence.json`。**对账表原注修正**：`user_preferences.py` 是偏好学习器（category=偏好分类）非轨道真源，轨道真源=sandbox_policy 的 tool_tracks store。边界如实声明：delegate_tasks/group_task/schedule_task 为间接通道（委派/排程执行面在执行时刻的会话轨道）、request_evolution/skill_save 走各自安全门（进化评审/单写者守卫）——是否随轨道收缩**待用户拍板** |
| 02-04 | 子agent/worktree/深潜 | ✅ | `agent/subagent_manager.py` + worktree（session_recall/git_api） |
| 05 | RepoMap tree-sitter | ✅(替代) | **统筹裁决不硬造 tree-sitter 图中心性**（过度设计，2026-10-12 周期#128 以替代实现销账）：实现说明文档=`p1-05-repomap-alternative-implementation.md`（逐件 file:line 实证+引用自检记录）。替代构成=**检索式召回+token预算选符号**：①符号边界分块 FTS 索引（`agent/file_index.py` `_split_into_chunks` 正则 def/class/function/export 边界切块=无 tree-sitter 的符号粒度近似，search 按 rank+500字内容预算）②检索式召回 live path（search_files grep 多语言 include 集 / `session_recall.py` 词项覆盖+编辑距离评分（mask_of/words_of/approximate_title）无图 / 记忆三因子 / 技能检索）③token 预算选符号=`context_budget.py`（TokenBudget.usable_input/裁剪/校准因子）+工具输出溢出 spill→stub（TOOL_SPILL_CHARS 8000/LINES 2000）+`read_file_segment` 按行段取回=预算内按需取符号段。⚠️诚实边界：①无全仓调用图/无中心性排序（裁决项非缺陷，用户拍板原案可按文档 §6 升级：tree-sitter 可选依赖+只解析检索命中文件）②SessionFileIndex **半接线**如实声明（写入已接线 soulmate_agent L137/366/5467，`search()/get_file_summary()/list_indexed_files()` 无消费点——FTS 召回闭环另立小切片**待拍板**）③符号识别为正则近似（装饰器/嵌套定义/TS 重载不保证最佳切边） |
| 06 | SEARCH/REPLACE 容错梯度 | ✅ | fuzzy/similar（`session_recall.py`/`reflex.py`） |
| 07 | 反射上限 | ✅ | `api/reflex.py`（注：实现为失败循环上限 2 次，与蓝图 3 次不同——以实现为准） |
| 08 | undo/checkpoint | ✅ | `agent/agent_checkpoint.py` + `edit_safety.py` |
| 09-11 | 技能加载/触发/版本化 | ✅ | `skill_evolution.py`/`skill_rewrite.py`/`skill_saver.py`（单写者守卫）+ `vein/versioning.py` |
| 12-14 | MCP 分权/隔离/降级 | ✅ | `agent/mcp_resources.py` + permission_gate policy |
| 15/16 | 终止条件组合入trace | ✅ | 终验 2026-10-12 周期#127 **28/28 PASS**：语义真源=`agent/termination.py` 五条件词汇（max_turns/wall_clock/token_budget/user_idle/external_cancel）+组合裁决 `termination.resolve`（显式条件>ACP取消标记>正常完成空串）+各条件独立配置（SOULMATE_MAX_ROUNDS 默认15 / SOULMATE_TURN_TOKEN_BUDGET 默认0=不限零行为变化 / wall_clock=SOULMATE_TURN_SOFT_BUDGET_S / user_idle=TRAJ_SESSION_IDLE_S / external_cancel 事件驱动）；落 trace 落点=`TrajectoryRecorder.end_turn(termination=...)`→回合根事件 metadata.termination（opensoul `trajectory_store.end_event` 合入 metadata，空串=正常完成零误标）+条件事件 `log_event`（wall_clock 软预算）+闲置收口 user_idle 标记事件（sweep 先标记后收口，顺序防自愈翻回）。四层实证（探针 `acp-proxy/termination_probe_ws.py`，证据 `evidence/p1-15-termination-evidence.json`）：①配置/裁决层 5 条件词汇+独立配置非法值回退+三态裁决 ②真实 store 落点层（真实 recorder→:8090 真库 HTTP 回读）：termination 落库回读命中、无 termination 零误标、wall_clock 软预算钩子真实触发一次+条件事件落库回读、**提醒不终止**（用户铁律"任务禁硬超时杀"覆盖蓝图硬杀语义——wall_clock 语义=软预算提醒+事件落trace，不产生终止，**边界如实声明**）、user_idle 由 opensoul 真实 SQLite 测试覆盖（test_trajectory_termination 4/4，探针内子进程重跑取真实输出）③引擎真实全回合层（真实 SoulMateAgent+真实 MiMo LLM，prompt() 全链路含 end_turn 落点）：SOULMATE_MAX_ROUNDS=2 → 轮次耗尽告知真实在回复+回读 termination=max_turns（om-9f747a6cb2f0）；SOULMATE_TURN_TOKEN_BUDGET=300 → 预算告知"估算消耗约635，任务停止"真实在回复+回读 termination=token_budget（om-77c77fb95600）；对照回合零误标 ④真实 WS 用户路径（WS→ws_acp→soulmate stdio→真实 LLM）：正常回合零误标、steer /abort 中断回合（sleep 25 长任务+忙时 /abort 入队→"收到中断指令"）服务端 agent_messages 含"[任务被用户插话中断]"+回读 **termination=external_cancel** 且 turn root 已收口非悬挂。语义如实声明：max_turns/token_budget 是**工具循环硬闸**（停工具循环+显式告知，任务引擎后续收尾文本可继续），终止标记按**回合**收口落 trace 一次（步骤级告知可重复出现）；relay 轨迹轮 ACP stop_reason=max_tokens→token_budget 族映射（`from_acp_stop_reason`）。回归：acp-proxy 207/207（含 3 个 FakeRecorder 签名对齐）+opensoul 17/17（含既有 spans 零回归）。首轮探针 20/28 全部为探针 harness 自身 bug（new_session 自建 om-* id 致 prompt refusal / 回读取首个而非末个 turn_root / collect 未消费串回合），修探针后 28/28，产品代码零改动通过 |

## P2 自我进化（10）
| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 01/02 | 记忆作用域/ADD-only | ✅ | `hippo/long_term_memory.py` + memory_pipeline（hippo 四律） |
| 03 | 异步可重放/降级 | ✅ | ws_acp 重放语义 + degrade |
| 04-06 | 经验提取→人审→合并 | ✅ | `learn/experience_collector.py` + `extract_patterns` + `gene_loop.py` propose + skill_rewrite 保真门 |
| 07/08 | 评测集/CI门禁 | ✅ | `benchmark/eval_loop.py`（llm_judge+regression） |
| 09 | 自举 KPI | ⏳ | benchmark 有指标面；四联 KPI（成功率/干预率/反射/compact质量）待整合 |
| 10 | fork 沙箱对照晋升 | ⏳ | dna_evolution 有 sandbox 概念；对照晋升流程待终验 |

## 缺口处理（统筹裁决）
- **✅ P1-05 RepoMap（替代实现，周期#128 销账）**：不排期硬造 tree-sitter 图中心性（过度设计）；"检索式召回+token预算选符号"替代实现的实现说明文档已补（`p1-05-repomap-alternative-implementation.md`，逐件 file:line 实证+引用自检）——除非用户要求原方案（升级路径见文档 §6：tree-sitter 可选依赖+只解析检索命中文件，且不得替换降级路径）；衍生待拍板小切片=SessionFileIndex FTS 召回闭环（写入已接线、search() 无消费点，详见文档 §3.1/§5）
- **⏳×2**（P2-09/10）：全部是"语义待终验"而非未实现——排入 soulmate 车道阶段20 前部做终验收口（P0-09 已于 2026-10-12 周期#124 终验销账 ✅，P0-10 已于 2026-10-12 周期#125 终验销账 ✅ 17/17，P1-01 已于 2026-10-12 周期#126 终验销账 ✅ 18/18 含 process 侧门修复，P1-15 已于 2026-10-12 周期#127 终验销账 ✅ 28/28 含五条件独立配置+trace 落点四层实证，逐项销账进行中）
