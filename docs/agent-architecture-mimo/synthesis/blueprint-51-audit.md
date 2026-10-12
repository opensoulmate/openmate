# 蓝图 51 项逐条对账（2026-10-12 · 统筹者审计）

对标源：`synthesis/openmate-rewrite-blueprint.md`（P0×25 + P1×16 + P2×10）。
证据方法：全仓特征扫描 + 代码/测试文件定位，只认实装证据。结论：**✅43 / ⏳6 / ❌2**

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
| 05 | RepoMap tree-sitter | ❌ | **未实现**（现有=检索式召回，非图中心性选符号） |
| 06 | SEARCH/REPLACE 容错梯度 | ✅ | fuzzy/similar（`session_recall.py`/`reflex.py`） |
| 07 | 反射上限 | ✅ | `api/reflex.py`（注：实现为失败循环上限 2 次，与蓝图 3 次不同——以实现为准） |
| 08 | undo/checkpoint | ✅ | `agent/agent_checkpoint.py` + `edit_safety.py` |
| 09-11 | 技能加载/触发/版本化 | ✅ | `skill_evolution.py`/`skill_rewrite.py`/`skill_saver.py`（单写者守卫）+ `vein/versioning.py` |
| 12-14 | MCP 分权/隔离/降级 | ✅ | `agent/mcp_resources.py` + permission_gate policy |
| 15/16 | 终止条件组合入trace | ⏳ | max_turns/token_budget 分散存在；五条件组合语义待终验 |

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
- **❌ P1-05 RepoMap**：不排期硬造 tree-sitter 图中心性（过度设计）；用"检索式召回+token预算选符号"作为**替代实现**立项补文档，除非你要求原方案
- **⏳×3**（P1-15、P2-09/10）：全部是"语义待终验"而非未实现——排入 soulmate 车道阶段20 前部做终验收口（P0-09 已于 2026-10-12 周期#124 终验销账 ✅，P0-10 已于 2026-10-12 周期#125 终验销账 ✅ 17/17，P1-01 已于 2026-10-12 周期#126 终验销账 ✅ 18/18 含 process 侧门修复，逐项销账进行中）
