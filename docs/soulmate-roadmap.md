# SoulMate 替代 Hermes · 总路线图

> 目标（用户规格）：**SoulMate 完全替代 Hermes 作为日常 AI Agent**。
> 开发节奏：**每 15 分钟一个开发周期**，由 cron 自动推进，每周期一个小而完整的垂直切片
>（实现→测试→提交→更新本文件）。本文件是唯一协调中心，周期之间不依赖会话记忆。

## 开发铁律（每个周期必须遵守）
1. **增量开发**：已有文件禁止全量重写，用 old_string/new_string 或 diff；"少一个字母也不行"
2. **每步测试通过再下一步**；跑真实测试，测试失败必须如实记录（禁称完成）
3. **每周期结束必须 git commit**（没做完也提交 WIP + 在周期日志写明下一步）
4. **架构断言必须查代码实证**，不许类推（soulmate 是独立 agent，不走 hermes）
5. 禁止：sudo/reboot/改凭据/改网关/动其他 cron 任务/动 ~/.hermes 配置
6. 修改后要真实验证（跑起来看输出），"写了≠接线了"
7. 周期日志条目格式：`[YYYY-MM-DD HH:MM] 周期#N | 任务 | 结果(✅/⚠️/❌) | 提交sha | 备注`

## 现状盘点（阶段0·周期#1已核实，2026-10-10 查代码实证）
**核实对象**：`acp-proxy/agent/soulmate_agent.py`（4566行）+ skill_manager.py + agent/memory_*.py 等。

### 已有工具（21个内置 + MCP动态，工具定义 L1474-1874 / 执行区 L1119-2688 实证）
- 文件/执行：read_file / read_file_segment / write_file / patch / terminal / execute_code / search_files / batch_execute
- 网络：web_search / web_extract
- 视觉感知：vision_analyze / read_image / sense_ocr / sense_analyze_image / sense_transcribe_audio
- 任务协作：todo / clarify（选项菜单+Future等待）/ search_chat_history / schedule_wakeup（noop自报+streak折叠）
- 进化：request_evolution / check_evolution_status
- MCP 动态工具：经 `/api/mcp/tools/all` 转 function calling（`server__tool` 命名）+ read_mcp_resource / list_mcp_resources

### 已有机制（实证）
- **任务管线**：分步规划+步骤越权防护+自省+重规划+失败重试1次（L3964）；写锁/消息持久化/会话幂等/观察窗解耦
- **记忆（3引擎，均为SQLite）**：MemoryRetrievalEngine（`~/.hermes/soulmate/memory/memory.db`，三因子评分 store/retrieve/consolidate/forget）
  + MemoryConsolidator（`fragments.db`，add_fragment episodic→recall 注入system prompt"相关历史记忆"+相似合并）
  + UserPreferenceLearner（`preferences.db`，learn_from_message+evidence+置信度upsert）；memory_echo/memory_marker 召回归因
- **会话历史检索**：session_recall.SessionRecallEngine（查 opensoul.db agent_sessions/agent_messages）+ search_chat_history 工具
  + _turn_boundary 防搜当前回合 + 跨agent read 二次授权（ASK真人）+ 覆盖率自报 → **1.2 已对标实测勾选（周期#3）**
- **技能机制（三层）**：`.agents/skills` 标准层（只读）+ shared-skills 供给链层（只读）+ 本地 JSON `acp-proxy/skills/`（可写）；
  search_skills + is_injectable_trigger 触发词过滤（>10字）+ record_usage + try_learn_skill 任务完成自动提炼 + 进化引擎 get_created_skills 注入 + KIND_SKILL 归因；测试 tests/test_skill_standard_layer.py
- **周边设施**：ToolRegistry(registry.db) / TokenAnalyzer(usage.db) / PromptManager(templates.db) / AgentCheckpointManager / HealthChecker / IntentClassifier / HotConfigManager / event_bus / agent_tracer / SchemaDoctor

### 缺口（vs 路线图）
- ~~1.1 缺~~ **1.1 已补（周期#2）**：MEMORY.md 型持久记忆（agent/persistent_memory.py + memory_read/write/append 三工具 + 系统提示注入）；~~遗留：code-mode内层工具面未含memory工具~~ 已清（周期#5 code-mode dispatch补齐memory+profile）
- **1.3 半缺**：consolidator/try_learn_skill 已有自动沉淀，但无"可复用经验"去重审计 → **1.3 已补（周期#4）**：ExperienceSink 可复用经验沉淀+两级去重+审计
- **2.1-2.3 缺**：计划-执行-验证（对照原始需求逐条自验）、失败自动换路径（现仅重试1次同路）、多文件改动拆步+逐步提交
- **3.x 缺**：cron 引擎（schedule_wakeup 只是延迟唤醒，非 schedule+执行+日志的任务调度）；定时汇报/监控告警缺
- **4.x 全缺**：微信桥、消息路由、媒体收发（sense_* 只有感知无收发）
- **5.x 全缺**：子代理并行、群组任务编排
- **6.x 半缺**：技能保存/检索/学习已有大半，6.3 用执行结果反哺修正待实测
- **7.x 全缺**：浏览器自动化、docx/pdf/xlsx 生成、TTS、图像生成；另有 send_file/进程管理(text_to_speech等Hermes工具)缺口

## 阶段规划（每阶段内拆成 15 分钟可完成的切片）

### 阶段1 · 记忆与知识（优先级最高——agent 的"长期大脑"）
- [x] 1.1 MEMORY.md 型持久记忆：读/写/追加工具 + 跨会话加载进系统提示（7cb4e532）
- [x] 1.2 会话历史检索：按关键词查过往会话（SQLite sessions/messages）——周期#3对标实测勾选（真实库398会话search/read/no-match均正确）
- [x] 1.3 记忆自动沉淀：任务完成后把"可复用经验"写入记忆（带去重）——周期#4实装ExperienceSink（agent/experience_sink.py，精确指纹+近似0.85两级去重，重复只累计times_seen；仅created时同步一行MEMORY.md）（ecd46b49）
- [x] 1.4 用户画像：偏好/环境事实独立存储，每次会话注入——周期#5扩展UserPreferenceLearner（record_fact显式记录三类型+upsert不重复+get_profile_summary+get_context_prompt按类型分组注入；环境事实自动学习主题词做key；修裸"我"噪音源）+profile_record/profile_read双dispatch接线（dc2dc3d3）
- [x] 1.5 用户画像审计/清理：profile_audit 工具（action=list 审计可见——噪音嫌疑标记+原因+统计；action=delete 删除须 confirm=true（缺省只预览不落库）+无删除范围拒绝）——周期#6实装，真实库实测68条标64嫌疑/4正常（恰为语义key规范条目不误伤）；⚠️真实库存量清理（64条嫌疑删除）已就绪待用户确认后执行，agent 未动库

### 阶段2 · 自主执行硬化（长任务可靠完成）
- [ ] 2.1 计划-执行-验证循环：任务完成后自验（对照原始需求逐条检查）
- [ ] 2.2 失败恢复：工具报错自动重试/换路径（最多2次），失败原因入日志
- [ ] 2.3 复杂任务分解器：多文件改动自动拆步骤并逐步提交
- [ ] 2.4 系统状态感知：服务状态/磁盘/进程 一键体检工具（运维基础）

### 阶段3 · 定时任务与主动服务
- [ ] 3.1 cron 引擎：SoulMate 自己的任务调度（schedule+执行+日志）
- [ ] 3.2 定时汇报：主动向 OpenMate 推送任务进展/异常
- [ ] 3.3 监控告警：对指定服务/网站做周期健康检查

### 阶段4 · 多渠道（替代 Hermes 的"触达能力"）
- [ ] 4.1 微信桥：SoulMate 接收/回复微信消息（复用现有 gateway 经验，不改 hermes 侧）
- [ ] 4.2 消息路由：OpenMate/微信统一收发
- [ ] 4.3 媒体能力：图片/文件收发、截图理解

### 阶段5 · 并行与协作
- [ ] 5.1 子代理：拆分任务给并行 worker，父代理收尾合并
- [ ] 5.2 多会话并发隔离（写锁已有，扩到工具执行层）
- [ ] 5.3 群组协作：AI 群组里的任务编排（接现有 ai_groups 能力）

### 阶段6 · 技能系统
- [ ] 6.1 技能保存：把成功流程写成 SKILL.md（含触发条件/步骤/坑）
- [ ] 6.2 技能检索与加载：按任务自动匹配并注入
- [ ] 6.3 技能进化：用执行结果反哺修正技能内容

### 阶段7 · 工具生态补齐
- [ ] 7.1 浏览器自动化（Playwright/CDP）
- [ ] 7.2 文档生成（docx/pdf/xlsx）
- [ ] 7.3 TTS/语音
- [ ] 7.4 图像生成/编辑

## 验收总标准（"完全替代"的判定）
拿 Hermes 日常被使用的 10 类任务（开发/调试/运维/调研/发布/办公/定时/多渠道/记忆/协作），
SoulMate 能独立完成且质量相当 = 达标。每阶段末尾跑一次对标实测并记录。

## 周期日志
[2026-10-09 19:00] 周期#0 | 路线图建立+cron流水线上线 | ✅ | (本文档创建) | 首个自动周期15分钟后启动
[2026-10-10 01:20] 周期#1 | 阶段0盘点：核实21工具/3记忆引擎/三层技能机制+缺口图；清理soulmate_agent.py损坏WIP（截断import`AgentCheckpointMan`+`return clsager`致ImportError，且属用户已否决的防双开守卫类，diff留证docs/devlog/2026-10-10-broken-idempotent-guard-wip.patch后还原HEAD，import实测恢复） | ⚠️ | 1ea328ce | 附带实测tests/test_skill_standard_layer.py 34 passed；下周期做1.1 MEMORY.md型持久记忆
[2026-10-10 01:38] 周期#2 | 1.1 MEMORY.md型持久记忆：新增agent/persistent_memory.py（atomic_write+SOULMATE_MEMORY_PATH覆盖）+memory_read/write/append三工具进builtin_tools与主dispatch+系统提示"持久记忆"段注入（KIND_MEMORY归因，超长显式截断） | ✅ | 7cb4e532 | tests/test_persistent_memory.py 15/15过+既有回归89过（memory_echo/marker/plan_tool_ledger/sandbox_policy）+env覆盖烟测真实输出验证；遗留：code-mode内层工具面未含memory工具（后续切片补）；下周期做1.2会话历史检索对标实测
[2026-10-10 01:53] 周期#3 | 1.2会话历史检索对标实测：真实库opensoul.db（398会话）实测search_chat_history三态——关键词命中（记忆/测试/进化均返回真实会话片段+覆盖率自报）、read模式完整转录（om-fcdac8c51900四条消息带时间戳）、无匹配显式报"无结果"非静默；tests/test_session_recall.py 29/29过（含boundary防搜当前回合/跨agent授权/截断自报/主循环+code-mode双dispatch接线） | ✅ | (本文档提交) | 1.2勾选；下周期做1.3记忆自动沉淀去重审计（对标consolidator/try_learn_skill现有沉淀链路）
[2026-10-10 02:15] 周期#4 | 1.3记忆自动沉淀：新增agent/experience_sink.py（ExperienceSink：SQLite+精确指纹/近似ratio≥0.85两级去重，重复经验只累计times_seen不重复写；audit()/list_experiences()审计复盘；提取规则两级——强标记句经验/教训/坑/铁律优先，确定性不走LLM）+soulmate_agent任务完成钩子（try_learn_skill旁）+仅created时同步一行MEMORY.md；tests/test_experience_sink.py 10/10+回归111全过+SoulMateAgent导入烟测（created/duplicate/duplicate+audit三态实证） | ✅ | ecd46b49 | 1.3勾选；下周期做1.4用户画像（偏好/环境事实独立存储+每次会话注入，对标UserPreferenceLearner现有实现）
[2026-10-10 02:46] 周期#5 | 1.4用户画像：扩展UserPreferenceLearner（不另起炉灶）——record_fact显式记录三类型(preference/environment/identity，fact_type校验+key/value非空+同key upsert不重复)+get_profile_summary画像摘要(fact_type过滤)+get_context_prompt按类型分组注入(偏好/环境事实/身份，每会话进系统提示KIND_PREFERENCE归因)+环境事实自动学习(我的系统是…/我跑在…/我用…，主题词做key同题新值覆盖；终止符只认中文标点——ASCII ./, 会把"Ubuntu 24.04"截成"Ubuntu 24"已修)+噪音源修复(显式偏好模式移除裸"我"，原把"我觉得不对"类句子收成伪偏好)；soulmate_agent接线：profile_record/profile_read两工具进builtin_tools+主dispatch+code-mode内层dispatch（顺带清1.1遗留：code-mode内层补memory三工具）+系统提示工具清单+注入段改"用户画像"；tests/test_user_profile.py 15/15+全量回归1680/1680过(670s)+SoulMateAgent导入烟测+真实库只读烟测 | ✅ | dc2dc3d3 | 1.4勾选；实测真实画像库68条中59条为旧裸"我"模式历史噪音（任务请求被存成偏好），已从源头抑制但存量清理属数据删除须用户拍板→新增1.5切片(profile_audit可见可清)；下周期做2.1计划-执行-验证循环
[2026-10-10 03:05] 周期#6 | 1.5用户画像审计/清理：user_preferences.py加audit_reasons三规则噪音识别（①key=value[:20]截断特征②任务请求/问句③explicit_negative丢"不要"前缀语义反转；规则2/3仅截断特征成立时叠加，防误伤record_fact规范条目如value="测试驱动开发"）+audit_entries审计视图(suspect+原因+统计+only_suspect过滤)+delete_facts(confirm缺省只预览不落库/无删除范围拒绝"禁止无条件清空"/pref_ids+keys+fact_type+only_suspect四选范围)；soulmate_agent接线：profile_audit工具定义+共享_profile_audit_dispatch（主dispatch+code-mode内层双接线）+系统提示工具清单"禁止擅自清库"警示；tests/test_profile_audit.py 19/19+test_user_profile.py接线断言同步三工具tuple 15/15+回归(persistent_memory/experience_sink/session_recall)54/54+py_compile双文件；真实库只读烟测：68条→嫌疑64/正常4（4条正常恰为语义key条目response_style/preferred_language/domain/speed，零误伤） | ✅ | f7281102 | 1.5勾选；⚠️真实库存量清理就绪待用户确认——用户说"清理画像"后SoulMate执行profile_audit(action=delete, only_suspect=true)预览→confirm=true删除64条嫌疑；下周期做2.1计划-执行-验证循环
