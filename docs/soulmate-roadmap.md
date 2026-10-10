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

### 已有工具（31个具名内置 grep实证 + read_mcp_resource/list_mcp_resources + MCP动态）
> 注：含路线图补装的 memory×3 / profile×3 / system_checkup / schedule×3；早期盘点"21个"为阶段0快照。
- 文件/执行：read_file / read_file_segment / write_file / patch / terminal / execute_code / search_files / batch_execute
- 网络：web_search / web_extract
- 视觉感知：vision_analyze / read_image / sense_ocr / sense_analyze_image / sense_transcribe_audio
- 任务协作：todo / clarify（选项菜单+Future等待）/ search_chat_history / schedule_wakeup（noop自报+streak折叠）
- 进化：request_evolution / check_evolution_status
- 运维体检：system_checkup（路线图2.4，只读六分区：服务/端口/磁盘/内存/负载/进程）
- 定时调度：schedule_task / schedule_list / schedule_cancel（路线图3.1 cron引擎：interval/cron周期调度+SQLite持久化+CAS执行+执行日志）
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
- ~~2.1 缺~~ **2.1 已补（周期#7）**：自验闭环（self_verify.py，收尾对照原始需求逐条核对+补做1轮+复检推送）
- **2.2-2.4 全部已补**：失败恢复（周期#8）/复杂任务分解+逐步提交（周期#9）/系统状态一键体检（周期#10）
- ~~3.x 缺~~ **3.1 已补（周期#11）**：cron引擎（agent/soulmate_scheduler.py，interval/cron周期调度+SQLite持久化+CAS到点执行+执行日志，区别于schedule_wakeup一次性唤醒）；**3.2 已补（周期#13）**：定时汇报（agent/cron_reporter.py，执行结果📊汇报行落opensoul.db+wakeup_relay推送面扩到{wakeup,cron,cron_report}即时推送）；**3.3 已补（周期#14）**：监控告警（agent/health_monitor.py，website/service/port三类周期健康检查+防抖告警+恢复通知）；⚠️3.1遗留：调度循环宿主=存活的SoulMateAgent进程（stdio子进程自举），无agent进程时任务不执行（错过不补跑），常驻托管（独立daemon）待后续
- **4.x 半缺**：~~微信桥全缺~~ **4.1a 已补（周期#15）**：微信桥核心（agent/wechat_bridge.py，收发回路+去重+会话映射+失败纪律，loopback传输实证；iLink真实传输=4.1b待做）；消息路由、媒体收发仍缺（sense_* 只有感知无收发）
- **5.x 全缺**：子代理并行、群组任务编排
- **6.x 半缺**：技能保存/检索/学习已有大半，6.3 用执行结果反哺修正待实测
- **7.x 全缺**：浏览器自动化、docx/pdf/xlsx 生成、TTS、图像生成；另有 send_file/进程管理(text_to_speech等Hermes工具)缺口

## 阶段规划（每阶段内拆成 15 分钟可完成的切片）

### ⚡ 紧急插队（优先级最高——先于阶段3执行，做完再回到3.1）
- [x] 0.1 简单对话流式路径增量落库（真实缺陷修复）：simple chat 的 full_response 流式
  积累处（soulmate_agent.py `full_response += safe_text` 附近）按节奏节流调用
  _save_progress(session_id, full_response)（每积累约800字或距上次落库≥2秒，取先到），
  使生成中任意时刻切页/重载都能从库重建已生成内容（任务管线已有per-step落库，此为补齐
  simple路径——用户实测08:22切页丢前段流式内容的根因）——周期#12实装：_progress_saver
  节流器（模块级阈值_PROGRESS_SAVE_CHARS/_INTERVAL_S可测）+_run_llm_with_tools加
  stream_progress开关（仅simple调用点传True，管线步骤/补做保持per-step落库）+流式积累与
  round尾flush双挂钩+_prompt_inner回合开始_reset_progress_row作废上回合残留进度行指针
  （防最终接管UPDATE旧行覆盖丢失上回合部分内容，旧行原样保留、新回合自建新行）

### 阶段1 · 记忆与知识（优先级最高——agent 的"长期大脑"）
- [x] 1.1 MEMORY.md 型持久记忆：读/写/追加工具 + 跨会话加载进系统提示（7cb4e532）
- [x] 1.2 会话历史检索：按关键词查过往会话（SQLite sessions/messages）——周期#3对标实测勾选（真实库398会话search/read/no-match均正确）
- [x] 1.3 记忆自动沉淀：任务完成后把"可复用经验"写入记忆（带去重）——周期#4实装ExperienceSink（agent/experience_sink.py，精确指纹+近似0.85两级去重，重复只累计times_seen；仅created时同步一行MEMORY.md）（ecd46b49）
- [x] 1.4 用户画像：偏好/环境事实独立存储，每次会话注入——周期#5扩展UserPreferenceLearner（record_fact显式记录三类型+upsert不重复+get_profile_summary+get_context_prompt按类型分组注入；环境事实自动学习主题词做key；修裸"我"噪音源）+profile_record/profile_read双dispatch接线（dc2dc3d3）
- [x] 1.5 用户画像审计/清理：profile_audit 工具（action=list 审计可见——噪音嫌疑标记+原因+统计；action=delete 删除须 confirm=true（缺省只预览不落库）+无删除范围拒绝）——周期#6实装，真实库实测68条标64嫌疑/4正常（恰为语义key规范条目不误伤）；⚠️真实库存量清理（64条嫌疑删除）已就绪待用户确认后执行，agent 未动库

### 阶段2 · 自主执行硬化（长任务可靠完成）
- [x] 2.1 计划-执行-验证循环：任务完成后自验（对照原始需求逐条检查）——周期#7实装agent/self_verify.py（确定性需求提取：数字/圆圈序号/顺序连接词/分句，闲聊不自验；三态核对met/uncertain/unmet，"说了≠做了"无工具无完成证据封顶证据不足；未覆盖项→补做1轮上限→复检；多需求或有缺口才推送自检结论）（5fd058ad）
- [x] 2.2 失败恢复：工具报错自动重试/换路径（最多2次），失败原因入日志——周期#8实装agent/tool_recovery.py（FailureKind四态判定复用read_turn.result_ok单一真源+补[CODE_MODE]工具执行异常漏网形态；确定性恢复计划：瞬态+只读→原路重试、read_file↔read_file_segment互备、文件不存在→basename定位路径修正、search_files回退cwd、terminal/write/patch/execute_code副作用禁自动重跑；BLOCKED绝不重试不绕权限引擎；恢复重走_code_mode_tool_call每步仍过permission_gate）+失败原因SQLite持久化(~/.hermes/soulmate/memory/tool_failures.db，recent()/stats()审计)+主循环收口与code-mode批内双接线（83c41f3c）
- [x] 2.3 复杂任务分解器：多文件改动自动拆步骤并逐步提交——周期#9实装agent/task_decomposer.py（TaskDecomposer确定性拆解不走LLM：目标文件抽取保序去重+node_modules等噪音过滤、多文件改动判定=≥2文件+变更动词（纯阅读提问不拆）、一步一文件顺序依赖+末尾整体验证步；commit_step逐步提交=只`git add -- <指定文件>`+commit、realpath前缀越界拒绝、无变更noop、支持新增未跟踪文件、绝不push/reset/checkout）+task_engine.TaskPlanner.plan接线（多文件目标确定性拆步顶掉LLM"simple"误判，拆解失败静默回退LLM计划）+soulmate_agent._commit_step_changes收口（步骤成功即提交该步目标文件并推送📦sha，首试/重试双成功路径接线，失败只记日志不阻塞主链路）（401dc86e）
- [x] 2.4 系统状态感知：服务状态/磁盘/进程 一键体检工具——周期#10实装agent/system_health.py（SystemHealthChecker只读六分区体检：services=systemd --user is-active五态映射(active→ok/activating→warn/inactive/failed→critical/未知→unknown)、ports=本地socket监听+格式校验、disk/memory=shutil.disk_usage+/proc/meminfo解析(mem+swap)、load=loadavg对cpu核数三档阈值(warn≥1×cpu/crit≥2×cpu)、processes=ps解析内存TopN+僵尸计数；阈值disk/mem warn≥85% crit≥95%；HealthReport.overall取最差值+分区格式化输出；全部采集器可注入(测试fake)+SOULMATE_HEALTH_SERVICES/PATHS/PORTS环境变量覆盖受检对象不硬编码拓扑；单项采集异常只降级unknown不炸整轮）+soulmate_agent四点接线（system_checkup工具定义+主dispatch+_system_health_dispatch共享入口lazy-init+code-mode批内dispatch+系统提示工具清单）（e3751d5e）

### 阶段3 · 定时任务与主动服务
- [x] 3.1 cron 引擎：SoulMate 自己的任务调度（schedule+执行+日志）——周期#11实装agent/soulmate_scheduler.py（SoulMateScheduler：tasks/runs两表SQLite持久化+确定性5字段cron解析（`*`/`*/n`/`a-b`/列表+Vixie dom/dow语义+7=周日，字段跳进next_after非逐分钟盲扫）+interval:<秒|5m|1h>两形态调度串+到点CAS领取（UPDATE rowcount==1多进程/双实例不双跑，错过不补跑不积压重放）+默认执行体default_cron_fire复用wakeup_executor无客户端回合与LLM级失败/零输出判定+失败可见（failed落runs日志+fail_streak累计+周期任务照常排下一次不搞即时重试风暴）+周期表损坏自动停用+snapshot观测）+soulmate_agent四点接线（schedule_task/schedule_list/schedule_cancel工具定义+主dispatch+code-mode批内dispatch+_cron_dispatch共用入口lazy-init+系统提示工具清单）+start.py stdio入口有存活任务才自举调度循环（979bb241）
- [x] 3.2 定时汇报：主动向 OpenMate 推送任务进展/异常——周期#13实装agent/cron_reporter.py
  （CronReporter：每次定时任务执行完成功/失败都产出确定性📊汇报行（任务名/✅❌/
  结果或异常/执行与下次时间/连续失败≥3次提醒，不走LLM）写入该任务会话的
  agent_messages（role=assistant，metadata.turn_origin=cron_report）——失败回合
  零输出形态下异常也有可见产物；无session_id显式跳过不写野行；汇报失败绝不阻塞
  调度主链路）+wakeup_relay推送面扩到RELAYED_TURN_ORIGINS={wakeup,cron,cron_report}
  （cron回合行=任务进展、cron_report行=📊结果/异常都补推给正连前端；普通回合行
  依旧零推送不与流式重复）+soulmate_scheduler.run_due接线_report_run（汇报行
  含fail_streak）；3.1遗留"实时推送归3.2"销账
- [x] 3.3 监控告警：对指定服务/网站做周期健康检查——周期#14实装agent/health_monitor.py
  （HealthMonitor三表SQLite持久化targets/state/events+三类确定性checker
  website=HTTP GET 2xx/3xx、service=systemd --user is-active、port=TCP连通，
  checker可注入+单项异常降级fail不炸整轮；告警防抖=连续失败达fail_threshold才
  🚨告警（单次抖动零打扰）+持续故障每10次续报+仅"告过警"的故障恢复时发✅恢复
  通知（含中断累计失败次数）；告警行turn_origin=health_alert落opensoul.db+
  wakeup_relay推送面扩到{wakeup,cron,cron_report,health_alert}即时推送+
  无session_id显式跳过不写野行+落库失败绝不阻塞检查主链路；run_forever后台
  循环按interval_s到点检查，start.py有启用目标才自举零开销）+soulmate_agent
  四点接线（monitor_add/monitor_list/monitor_remove/monitor_check工具定义+
  主dispatch+_monitor_dispatch共享入口lazy-init+code-mode批内dispatch+系统
  提示工具清单）

### 阶段4 · 多渠道（替代 Hermes 的"触达能力"）
- [ ] 4.1 微信桥：SoulMate 接收/回复微信消息（复用现有 gateway 经验，不改 hermes 侧）
  - [x] 4.1a 桥核心（周期#15）：agent/wechat_bridge.py——WeChatTransport抽象+LoopbackTransport
    回环e2e实证"接收→SoulMate回合→回复"回路；msg_id去重SQLite持久（重启/重复投递不双跑）；
    peer→session映射持久（首见自动new_session，同peer复用同会话）；默认无客户端回合复用
    wakeup_executor失败纪律（refusal/LLM级失败/零输出都不假装成功）+回合失败发失败告知
    不无声消失；回复按行拆块+出站限流min_send_interval_s（iLink撞限流静默丢经验）+
    context_token由transport随原消息回显（协议知识不下沉桥层）；turn_origin=wechat入
    wakeup_relay补推面（正连前端可见微信回合）；start.py SOULMATE_WECHAT_ENABLED显式启用
    默认零开销
  - [ ] 4.1b iLink Bot API 传输：对齐 hermes gateway platforms/weixin.py 协议经验
    （getupdates长轮询/context_token回显/AES媒体CDN）。⚠️**绝不与hermes gateway双消费
    同一iLink会话**（双长轮询会分流hermes入站消息=变相改坏hermes侧）——接入形态须先定
    （独立微信账号 or 替代期独占接管），定前不接
  - [ ] 4.1c 会话级路由：微信peer↔OpenMate会话的映射管理工具（映射查询/改绑/解绑）
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
[2026-10-10 03:22] 周期#7 | 2.1计划-执行-验证循环：新增agent/self_verify.py（SelfVerifier确定性自验不走LLM——需求提取：数字/圆圈序号/顺序连接词整词切分/分号分句/长任务兜底单条，闲聊不自验；三态核对met/uncertain/unmet按bigram+拉丁词证据覆盖，"说了≠做了"护栏=动作类需求无工具执行且无完成证据封顶uncertain不给met）+soulmate_agent收尾接线（MEDIA提取前收口使补做产出也能下发；未覆盖项→补做1轮上限防失控→复检；多需求项或有缺口才推送"🔍需求自检"结论，闲聊零打扰）；tests/test_self_verify.py 13/13+回归125全过(1.x记忆链+plan_execution+task_judge)+py_compile+SoulMateAgent真实导入烟测（3需求1项未覆盖→报告/补做提示词输出实证） | ✅ | 5fd058ad | 2.1勾选；下周期做2.2失败恢复（工具报错自动重试/换路径最多2次+失败原因入日志，对标现有SmartRetryManager）
[2026-10-10 07:12] 周期#8 | 2.2工具失败恢复：新增agent/tool_recovery.py（ToolRecoveryManager：FailureKind四态判定OK/BLOCKED/TRANSIENT/RECOVERABLE/PERMANENT——复用read_turn.result_ok单一真源+补"[CODE_MODE]工具执行异常"漏网形态；确定性恢复计划≤2步——TRANSIENT+只读工具(SAFE_RETRY_TOOLS)原路重试、read_file↔read_file_segment互备、read_file文件不存在→basename定位路径修正(WALK跳过node_modules等+50k条目上限)、search_files路径不存在→回退cwd、terminal/write_file/patch/execute_code副作用禁自动重跑只记日志；BLOCKED绝不重试=不绕权限引擎铁律）+SQLite失败日志(~/.hermes/soulmate/memory/tool_failures.db，TOOL_FAILURE_DB_PATH覆盖，每次失败/恢复动作一行含action/attempt/outcome，recent()/stats()审计，日志写失败不阻塞工具链路)+soulmate_agent接线（_tool_recovery_execute：恢复重执行走_code_mode_tool_call原始执行器每步仍过permission_gate；主循环tool_results.append前收口+code-mode批内_cm_dispatch双接线；lazy-init对齐_persistent_memory模式防__new__构造炸回合） | ✅ | 83c41f3c | 2.2勾选；tests/test_tool_recovery.py 26/26（含BLOCKED零重试/no_plan/exhausted/2次封顶/路径修正真实tmp目录实证）+回归359全过(code_mode/plan_tool_ledger/tool_output_wiring/self_verify/experience_sink/persistent_memory 118+记忆执行链241)；⚠️实现期发现既有回归9例因__new__构造缺_tool_recovery全炸→lazy-init修复后全绿（非本切片引入的旧行为，属测试stub路径）；下周期做2.3复杂任务分解器（多文件改动自动拆步+逐步提交）
[2026-10-10 07:55] 周期#9 | 2.3复杂任务分解器：新增agent/task_decomposer.py（TaskDecomposer确定性拆解不走LLM——目标文件抽取：路径token正则保序去重+node_modules/.next等噪音过滤；多文件改动判定=≥2目标文件+变更动词（纯阅读/提问不拆）；decompose一步一文件顺序依赖+末尾整体验证步依赖全部文件步；commit_step逐步提交=只`git add -- <指定文件>`+commit、realpath前缀校验路径越界拒绝、无变更noop、未跟踪新文件可提交、绝不push/reset/checkout）+task_engine.TaskPlanner.plan接线（多文件目标确定性拆步顶掉LLM"simple"误判/漏拆，拆解异常静默回退LLM计划不炸链路）+soulmate_agent._commit_step_changes（步骤SUCCESS后提交该步目标文件并推送📦sha，首试/重试双成功路径接线，commit失败只warning不阻塞） | ✅ | 401dc86e | 2.3勾选；tests/test_task_decomposer.py 15/15（含真实tmp git仓库实证：逐步提交只含本步文件git show --name-only验证、其余文件保持未提交、noop/越界拒绝/新文件）+回归215全过(2.2/2.1/1.x记忆链142+plan_execution/plan_tool_ledger/task_judge/code_mode 73)+py_compile三文件+SoulMateAgent真实导入烟测；下周期做2.4系统状态感知（服务/磁盘/进程一键体检工具）
[2026-10-10 08:10] 周期#10 | 2.4系统状态感知：新增agent/system_health.py（SystemHealthChecker只读六分区体检services/ports/disk/memory/load/processes——systemd五态映射+socket端口监听+磁盘/内存85%/95%双阈值+load对cpu核数三档+ps内存TopN+僵尸计数；HealthReport.overall最差值；采集器全可注入+SOULMATE_HEALTH_SERVICES/PATHS/PORTS可配；单项异常降级unknown不炸整轮——测试实证该缺口后补的per-item try/except）+soulmate_agent四点接线（工具定义/主dispatch/共享_system_health_dispatch(lazy-init)/code-mode批内+系统提示清单） | ✅ | e3751d5e | 2.4勾选（阶段2全部完成）；tests/test_system_health.py 21/21+回归250全过(1.x/2.x记忆恢复链142+plan_execution/plan_tool_ledger/task_judge/code_mode/tool_output 108)+真实机器烟测13项全ok（4服务active/8090+8092监听/磁盘80.1%/mem 44%/load 4.21@8核/367进程0僵尸）+__new__构造lazy-init烟测PASS；下周期做3.1 cron引擎（SoulMate自己的任务调度：schedule+执行+日志）
[2026-10-10 08:33] 周期#11 | 3.1 cron引擎：新增agent/soulmate_scheduler.py（SoulMateScheduler：tasks/runs两表SQLite持久化+确定性5字段cron解析（*/n/a-b/列表+Vixie dom/dow语义+7=周日，字段跳进next_after）+interval/cron两形态调度串+到点CAS领取（UPDATE rowcount==1多进程不双跑、错过不补跑不积压重放）+默认执行体default_cron_fire复用wakeup_executor无客户端回合+LLM级失败/零输出判定+失败可见（runs日志+fail_streak+周期任务照常排下次）+周期表损坏自动停用+snapshot观测）+soulmate_agent四点接线（schedule_task/schedule_list/schedule_cancel工具定义+主dispatch+code-mode批内+_cron_dispatch共用入口lazy-init+系统提示清单）+start.py stdio入口有存活任务才自举循环 | ✅ | 979bb241 | 3.1勾选；tests/test_soulmate_scheduler.py 24/24（cron解析7例+调度串拒绝+CRUD+interval/cron执行日志重排+失败重排+async fire+CAS输家确定性验证+停用/错过不补跑+损坏停用+接线护栏4例）+回归339全过(1.x/2.x记忆链+plan/task_judge/code_mode/tool_output)+SoulMateAgent真实dispatch烟测（创建/列表/不存在/停用/删除/坏调度串全态输出实证）；⚠️遗留：调度循环宿主=存活agent进程（无进程时不执行错过不补跑），常驻托管待后续；cron回合实时推送归3.2；下周期做3.2定时汇报（主动向OpenMate推送任务进展/异常）
[2026-10-10 09:01] 周期#12 | ⚡0.1紧急插队·simple chat流式路径增量落库：soulmate_agent.py加_progress_saver节流器（≥800字或≥2秒取先到，模块级阈值可测）+stream_progress开关（仅simple调用点4605行传True，任务管线/补做保持per-step落库零变化）+流式积累/round尾flush双挂钩节流落库+_prompt_inner回合开始_reset_progress_row（防接管串行：上回合中断残留_progress_id会让本回合UPDATE旧行覆盖丢失，改为旧行保留+新回合自建新行） | ✅ | f94067b5 | 0.1勾选；tests/test_stream_progress_persist.py 13/13（节流双阈值/时间阈值fake clock/中途被杀部分行/最终接管同行无重复/陈旧指针不劫持旧行/接线护栏4例）+回归271全过(cache_replay_persist/message_tree/plan_execution/plan_tool_ledger/task_judge/code_mode/tool_output/self_verify/1.x记忆链/session_source/stream_continuation/turn_lifecycle)+py_compile+SoulMateAgent导入烟测；下周期做3.2定时汇报（主动向OpenMate推送任务进展/异常）
[2026-10-10 09:27] 周期#13 | 3.2定时汇报：新增agent/cron_reporter.py（CronReporter：每次定时任务执行完成功/失败都产出确定性📊汇报行（任务名/✅❌/结果或异常/执行与下次时间/连续失败≥3次提醒，不走LLM）写入任务会话agent_messages（role=assistant, turn_origin=cron_report）——失败回合零输出形态异常也可见；无session_id显式跳过不写野行；写库失败计数不抛）+wakeup_relay推送面扩到RELAYED_TURN_ORIGINS={wakeup,cron,cron_report}（turn_origin_of/is_relayed_meta，fetch_rows双标记is_wakeup+is_relayed兼容旧键，普通回合行依旧零推送）+soulmate_scheduler.run_due接线_report_run（汇报失败绝不阻塞fire/日志/排下次） | ✅ | 051d2615 | 3.2勾选+3.1遗留"实时推送归3.2"销账；tests/test_cron_reporter.py 16/16（汇报形状5+落库4+relay推送3+调度接线4）+回归119全过(wakeup_relay/soulmate_scheduler/wakeup_executor/schedule_wakeup_wiring 78+observability/relay_traj 41)+py_compile+SoulMateAgent导入烟测（relayed origins=['cron','cron_report','wakeup']）；⚠️诚实边界：推送粒度=消息级（relay 2s轮询），仅正连前端实时、未连接时历史加载可见；汇报行落任务会话属会话内消息；下周期做3.3监控告警（对指定服务/网站做周期健康检查）
[2026-10-10 09:55] 周期#14 | 3.3监控告警：新增agent/health_monitor.py（HealthMonitor：targets/state/events三表SQLite持久化+三类确定性checker website=HTTP GET 2xx/3xx/service=systemd --user is-active/port=TCP连通，checker可注入+单项异常降级fail；告警防抖=连续失败达阈值才🚨告警+每10次续报+仅告过警的故障恢复发✅通知（含中断累计失败次数）；告警行turn_origin=health_alert落opensoul.db，无session_id显式跳过，落库失败不阻塞检查；run_forever后台循环+ensure_started惯例）+wakeup_relay推送面扩到{wakeup,cron,cron_report,health_alert}+start.py有启用目标才自举+soulmate_agent四点接线（monitor_add/list/remove/check工具定义+主dispatch+_monitor_dispatch共享lazy-init+code-mode批内+系统提示） | ✅ | 78ce5784 | 3.3勾选（阶段3全部完成）；tests/test_health_monitor.py 26/26（CRUD校验8例+真实socket/HTTP checker+防抖/续报/恢复边界+告警落库meta+relay推送面+run_due到点判定+接线护栏+SoulMateAgent __new__真实dispatch烟测）+回归161全过(cron_reporter/soulmate_scheduler/wakeup_relay/wakeup_executor/schedule_wakeup_wiring 120+observability/relay_traj 41——其中test_cron_reporter推送面断言随health_alert扩面从恰等改超集)+真实机器烟测（8090/8092端口ok、死端口/坏服务fail→alert事件、monitor_list真实输出）；实现中修2个自发现bug（阈值跨过判定streak==threshold、抖动恢复不发恢复通知）；⚠️3.1遗留同存（监控循环同样宿主存活agent进程，无进程时错过不补跑）；下周期做4.1微信桥（SoulMate接收/回复微信消息）
[2026-10-10 10:22] 周期#15 | 4.1a微信桥核心：新增agent/wechat_bridge.py（WeChatTransport抽象+LoopbackTransport回环e2e实证"接收→无客户端SoulMate回合→回复"回路；msg_id去重SQLite持久=重启/重复投递不双跑；peer→session映射持久首见自动new_session同peer复用；默认回合复用wakeup_executor失败纪律refusal/LLM级失败/零输出都不假装成功+失败发告知不无声消失；回复按行拆块+出站限流min_send_interval_s（iLink撞限流静默丢经验）；context_token由transport随原消息回显；turn_origin=wechat入wakeup_relay补推面；start.py SOULMATE_WECHAT_ENABLED显式启用默认零开销） | ✅ | 6046514e | 4.1a切片（4.1拆三段）；tests/test_wechat_bridge.py 17/17（loopback e2e/去重跨重启/失败隔离/拆块+真实时钟限流0.3s断言/默认回合session映射+三态失败纪律/接线护栏）+回归183全过(cron_reporter/health_monitor/soulmate_scheduler/wakeup_relay/schedule_wakeup_wiring 107+wakeup_executor/wakeup_fire_llm_error/observability/relay_traj 76)+真实导入烟测（relayed origins含wechat）；⚠️iLink真实传输=4.1b未接（诚实边界：当前仅loopback传输，真实微信收发未通）——**接入形态待用户拍板（独立微信账号 or 替代期独占接管），绝不与hermes gateway双消费同一iLink会话**；下周期做4.1c会话级路由（peer↔会话映射管理工具）
