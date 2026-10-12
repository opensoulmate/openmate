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
- **4.x 半缺**：~~微信桥全缺~~ **4.1a 已补（周期#15）**：微信桥核心（agent/wechat_bridge.py，收发回路+去重+会话映射+失败纪律，loopback传输实证；iLink真实传输=4.1b待做）；**4.2 已补（周期#17）**：统一消息路由（agent/message_router.py，OpenMate/微信统一收发出入口+message_send工具）；**4.3 已补（周期#18）**：媒体收发（agent/media_channel.py，图片/文件收发+截图理解提示块，sense_*感知之外补齐收发形态）
- **5.x 已补全**：~~子代理并行、群组任务编排全缺~~ **5.1 已补（周期#19）**子代理并行、**5.2 已补（周期#20）**工具执行层并发隔离、**5.3 已补（周期#21）**群组任务编排（接ai_groups）
- **6.x 半缺**：6.1/6.2/6.3 已补（技能保存+检索加载注入+执行结果反哺坑沉淀，周期#35）
- **7.x 半缺**：~~浏览器自动化/docx/pdf/xlsx生成/TTS~~ **7.1 已补（周期#36）**：browser 工具面（Playwright/CDP）；**7.2 已补（周期#37）**：document 文档生成（docx/pdf/xlsx）；**7.3 已补（周期#38）**：text_to_speech 语音合成（edge-tts+espeak-ng双后端）；**7.4 已补（周期#39）**：image 图像生成/编辑（Pillow编辑八动作+SOULMATE_IMAGEGEN_API_URL→OpenAI兼容images API真实生成/未配置PIL程序化兜底显式标注）；剩余 Hermes 对标工具缺口=**已清零**（send_file 已补 9.1 周期#50、process 进程管理已补 9.2 周期#51）

## 阶段规划（每阶段内拆成 15 分钟可完成的切片）

### ⚡ 紧急插队（优先级最高——先于阶段3执行，做完再回到3.1）
- [x] 0.2 首条消息提速（用户实测"第一条回复特别慢"：6秒引擎准备+27秒模型大首包）：
  a) new_session 时即预热引擎（记忆检索/画像/工具装配并行化，用户打字时间自然覆盖）
  b) 会话创建时发一次微小预热 LLM 请求（同系统提示前缀，回复丢弃）暖服务端缓存，
  真正首条消息打到热前缀。验收：新建会话→首条消息→首字回复 ≤8 秒（当前约33秒）。
  ——周期#22实装：agent/prompt_prefetch.py（PromptPrefetcher：上下文源并行预取
  asyncio.gather+TTL缓存120s可配SOULMATE_PREFETCH_TTL_S+旁路纪律失败只计数不冒泡+
  take()超时回退现场计算+可观测stats计数）+soulmate_agent四点接线（_prompt_prefetcher
  lazy-init收集器三源pref_context/memory_md/improvement+mcp_tools暖60s工具缓存、
  _prefill_warm微小预热LLM请求同系统提示前缀+同工具面首chunk即断开省输出token不进
  会话历史不落库、new_session创建路径_schedule_prefetch fire-and-forget、
  _run_llm_with_tools三源消费缓存）+基础系统提示抽为_build_base_system_prompt单一真源
  （prefill与主链路逐字同前缀——暖前缀缓存的前提）。真实验收：WS用户路径实测
  新建会话→模拟打字8s→首条消息→首字回复 6.00s（≤8s达标，此前约33s），
  journal实证"[prefetch] warm scheduled→warm done parts=[4源] prefill=1"无失败
- [x] 0.1 简单对话流式路径增量落库（真实缺陷修复）：simple chat 的 full_response 流式
  积累处（soulmate_agent.py `full_response += safe_text` 附近）按节奏节流调用
  _save_progress(session_id, full_response)（每积累约800字或距上次落库≥2秒，取先到），
  使生成中任意时刻切页/重载都能从库重建已生成内容（任务管线已有per-step落库，此为补齐
  simple路径——用户实测08:22切页丢前段流式内容的根因）——周期#12实装：_progress_saver
  节流器（模块级阈值_PROGRESS_SAVE_CHARS/_INTERVAL_S可测）+_run_llm_with_tools加
  stream_progress开关（仅simple调用点传True，管线步骤/补做保持per-step落库）+流式积累与
  round尾flush双挂钩+_prompt_inner回合开始_reset_progress_row作废上回合残留进度行指针
  （防最终接管UPDATE旧行覆盖丢失上回合部分内容，旧行原样保留、新回合自建新行）
- [x] ⚡0.3 会话身份顶替根修（用户判死案例"为什么要有默认"，2026-10-12）：点击谁的
  "新建会话"=建谁的；默认值只许在"什么都不点"的全局新建入口（显式 'soulmate'），
  链路深处零默认。根因=chat-client.tsx 标题自动命名块 `setActiveSession(id, null, …)`
  清空 activeAgentId → 随后 `|| 'soulmate'` 兜底吞身份 → **所有非 soulmate 会话被顶替成
  soulmate**（只有 soulmate"正常"——它就是兜底值本身）。修法三件套：①标题更新传当前
  activeAgentId 不传 null ②connectSession 取值链=会话自带 agentId > store > 入口默认
  ③ws_acp session/new 强制显式 agent_id（缺失→-32602 拒绝；未注册→-32601 响亮报错，
  删一切冒名兜底）。实测 journal 铁证（agent_id=hermes→Started hermes subprocess；
  无 agent_id→拒绝）；完整案例→skill references/agent-identity-threading.md。
  ——修复原落 evo/exp 分支（0d8c2396，evo-commit-theft 落错分支形态），周期#136
  抢救归位 main 64802af9+format-patch 备份（防 evo 分支清理时丢失）

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
  - [x] 4.1c 会话级路由（周期#16）：agent/wechat_router.py（WeChatRouter映射管理面：
    查询全量/按peer过滤/按session_id反查同会话多peer；改绑返回previous_session_id+
    created标记；解绑幂等（下次来消息按首见逻辑自动新建会话，历史保留）；校验拒绝
    空值/空白字符ValueError不静默吞；store可注入+get_wechat_router单例）+BridgeStore
    增list_mappings（最近消息倒序）/unbind（返回被删session_id）；soulmate_agent四点
    接线（wechat_route_list/bind/unbind工具定义+主dispatch+_wechat_route_dispatch
    共用入口lazy-init+code-mode批内dispatch+系统提示工具清单）；只操作peers映射表，
    不碰消息收发不碰hermes侧
- [x] 4.2 消息路由：OpenMate/微信统一收发——周期#17实装agent/message_router.py
  （统一信封ChannelMessage+RouteResult；route_inbound统一入站按channel分发：
  wechat→整体委托WeChatBridge.deliver()（去重/回合/回复单一owner仍在桥内，不双份
  记账）、openmate→RouterStore(channel+msg_id复合主键去重)+无客户端回合（失败纪律
  复用wakeup_executor：refusal/LLM级失败/零输出都不假装成功）；send_outbound统一
  出站一个入口：wechat→WeChatBridge.send_proactive()拆块+限流发peer、openmate→
  落库assistant行到目标会话（turn_origin=msg））+wechat_bridge增量（deliver()公开
  投递入口+send_proactive()主动发送+WeChatTransport.send_text契约（默认NotImplemented
  不破坏既有传输）+LoopbackTransport.proactive记录）+wakeup_relay补推面纳入msg出身
  （入站回合行+出站消息行正连前端实时可见）+soulmate_agent四点接线（message_send
  工具定义+主dispatch+_message_dispatch共用入口lazy-init+code-mode批内await+系统
  提示工具清单）。⚠️诚实边界：微信通道仍loopback传输（iLink=4.1b待用户拍板）；
  openmate出站=落库+relay补推非改写ws客户端连接
- [x] 4.3 媒体能力：图片/文件收发、截图理解——周期#18实装agent/media_channel.py
  （MediaAttachment中立形状kind=image|file+to_meta()对齐agent_messages.attachments
  持久化格式；save_inbound()入站base64（含data URL前缀）落盘本地→带真实路径附件；
  from_paths()出站校验+kind推断；media_prompt_block()媒体提示块=本地路径+读取方式
  确定性提示——图片/截图走vision_analyze/read_image（截图理解不靠LLM猜），文件走
  read_file）+message_router增量（ChannelMessage.media信封字段+纯媒体消息text空合法
  +入站媒体块拼进回合文本+send_outbound带media出站：wechat→桥send_media_proactive
  逐个限流发送、openmate→MEDIA标签行+attachments列持久化（无列ALTER兜底）+
  text与media至少给一个）+wechat_bridge增量（WeChatMessage.media+build_wechat_prompt
  媒体块+WeChatTransport.send_media默认NotImplemented不破坏既有传输+Loopback记录
  +send_media_proactive传输不支持→0+记错不谎报）+soulmate_agent接线（message_send
  工具schema加media数组参数（text不再required）+_message_dispatch解析media经
  from_paths校验透传+系统提示清单） | ✅ | (本次提交) | 4.3勾选（阶段4除4.1b全部完成）；tests/test_media_channel.py 20/20（kind推断/to_meta/from_paths去重与错误/save_inbound四态/提示块+入站媒体进回合3例+微信转发+bridge媒体e2e+出站双通道+不支持传输/校验/接线护栏+__new__真实dispatch烟测带媒体成功/坏类型/坏路径三态）+回归240全过（message_router/wechat_bridge/wechat_router/wakeup_relay/cron_reporter/health_monitor/soulmate_scheduler/wakeup_executor 167+wakeup_fire_llm_error/schedule_wakeup_wiring/observability/relay_traj/proxy_relay_traj 73）+真实机器烟测（SoulMateAgent导入/relayed origins含msg/真实singleton/media_prompt_block输出实证）；⚠️诚实边界：微信媒体真实传输=4.1b iLink AES媒体CDN未接（当前loopback），openmate出站媒体=落库attachments列+MEDIA标签（历史加载API回base64既有链路），入站"截图理解"=媒体提示块引导vision工具（真实理解效果待真图实测）

### 阶段5 · 并行与协作
- [x] 5.1 子代理：拆分任务给并行 worker，父代理收尾合并——周期#19实装agent/subagent_manager.py（SubagentManager：asyncio.Semaphore限并发1-4+每worker独立wait_for超时隔离（失败/超时取消随行可见不影响其余worker，2-8子任务/超时10-600s夹取）+默认worker=隔离工作会话无客户端SoulMate回合（default_subagent_runner复用唤醒回合同款失败纪律refusal/LLM级失败/零输出都不假装成功+turn归因锚点收集产出+超长截断自报，复用wakeup_executor判定单一真源）+parent_goal注入每个worker context（worker看不到父对话）+确定性merge不走LLM（成功/失败分节+按输入顺序+失败收尾警示"不要把失败子任务当成已完成"）+subagent_runs表持久化失败/超时同样落行+snapshot观测（SOULMATE_SUBAGENT_DB_PATH覆盖，写日志失败不阻塞））+soulmate_agent四点接线（delegate_tasks工具定义含tasks/max_concurrency/parent_goal+主dispatch+_subagent_dispatch共用入口lazy-init+code-mode批内await+系统提示清单） | ✅ | (本次提交) | 5.1勾选；tests/test_subagent_manager.py 21/21（prompt构建/校验夹取/并行保序/失败与超时隔离含cancel实证/并发上限/parent_goal注入/merge两态/runs持久化+写失败不阻塞/默认runner四态+截断自报/接线护栏+__new__真实dispatch烟测）+回归516全过（message/media/wechat链107+wakeup/调度/relay链183+plan/tool链104+记忆链122）+py_compile+SoulMateAgent真实导入烟测；⚠️诚实边界：worker是真实LLM回合（各自调模型+工具），多会话并发的工具执行层隔离硬化归5.2（当前worker各用独立会话，共享agent实例观测采集端属弱共享）；下周期做5.2多会话并发隔离（写锁已有，扩到工具执行层）
- [x] 5.2 多会话并发隔离（写锁已有，扩到工具执行层）——周期#20实装agent/tool_exec_fencing.py
  （以realpath为键的跨进程文件栅栏file_fence：flock互斥+锁文件走中央临时目录
  sha256命名不污染项目树+进程死亡自动释放无stale lock+同进程不同fd同样互斥
  （flock按open file description计，进程内进程间一套语义）+有界等待10s超时抛
  FileFenceTimeout——调用方显式报冲突，禁"拿不到锁就无锁裸写"）+接线三处读-改-写
  临界区（patch主循环+code-mode内层双执行点、PersistentMemory.write/append——
  memory_append/memory_write工具与experience_sink的MEMORY.md同步全覆盖）+
  _get_db加busy timeout=30（多会话/子代理worker并发写agent_messages忙等30s，
  不再默认5s抛database is locked）。真实根因实证：无栅栏时两进程并发read-modify-write
  同一文件5/5次lost update（对照实验/tmp/race_control.py），有栅栏全保留
- [x] 5.3 群组协作：AI 群组里的任务编排（接现有 ai_groups 能力）——周期#21实装agent/group_orchestrator.py
  （GroupOrchestrator 驱动 opensoul /api/ai-groups 既有能力：提交主任务=自动分解
  子任务+按角色自动分配→逐个触发execute（失败隔离：单个execute请求失败/无agent
  不阻塞兄弟子任务，409已在执行容忍为在途）→有界轮询list_tasks到终态（5-600s
  预算，耗尽子任务记TIMEOUT显式标注绝不假装成功）→终态读事件流finished/failed
  判定（失败可见，ai_groups execute收尾status一律scored，成败在事件流）→确定性
  merge不走LLM（成功/失败分节+按输入顺序+失败收尾警示"不要把失败子任务当成
  已完成"，与5.1同款纪律）；传输层可注入（测试零网络），缺省httpx调
  SOULMATE_AI_GROUPS_URL；只读查询面list_groups/task_status）+soulmate_agent
  四点接线（group_task工具action=list_groups/orchestrate/task_status+主dispatch
  +_group_dispatch共用入口lazy-init+code-mode批内await+系统提示清单）

### 阶段6 · 技能系统
- [x] 6.1 技能保存：把成功流程写成 SKILL.md（含触发条件/步骤/坑）——周期#23实装
  agent/skill_saver.py（SkillSaver：确定性渲染标准 SKILL.md=YAML frontmatter
  name/description/category/triggers + 固定分区触发条件/步骤/坑/验证，与
  .agents/skills 同构；save_flow 校验显式拒绝空名/空描述/零触发/零步骤（None
  条目不 str() 蒙混成"None"）+ 同名默认不覆盖（overwrite=false 返回 exists
  提示禁静默覆盖既有技能）+ overwrite=true revision 递增原子替换（tmp+
  os.replace 不留半截文件）；slugify 保留 CJK；可写库 ~/.openmate/skill-library
  单一写入源）+ skill_manager saved 层（scan_saved_skills 复用标准层解析但
  read_only=False/saved:<slug>/revision 透传 + 60s TTL 缓存 + list_skills 合并
  =**保存即被 search_skills 命中进系统提示注入面**；update/delete/record_usage
  拒绝 saved 条目=单写入者纪律，更新只走 save_flow(overwrite=true)）+
  soulmate_agent 四点接线（skill_save 工具定义 + 主 dispatch +
  _skill_save_dispatch 共用入口 lazy-init + code-mode 批内 + 系统提示清单；
  保存后强刷 saved 层缓存不被 60s TTL 藏住）。真实机器烟测：生产 SkillManager
  实例 search_skills 命中 saved 条目（排在 std 技能之前）read_only=False
  revision=1（烟测产物已清理）
- [x] 6.2 技能检索与加载：按任务自动匹配并注入——周期#24实装agent/skill_loader.py
  （SkillLoader确定性检索+加载一体：match_for_task集中gate语义——短消息不匹配/
  文件发送请求跳过/只认触发词命中忽略纯描述匹配（pitfall 30防误触发逐条保留），
  命中带match_reason可观测；match_for_step步骤级重新检索+与回合级合并去重
  （步骤命中在前当前步骤相关性优先）；render_local_section本地技能注入段
  （字节契约与历史内联实现逐字节一致=token归因锁定）+预算内截断自报
  （SOULMATE_SKILL_BUDGET_TOKENS默认1500）；render_recommended gene推荐载荷
  内容加载（描述/模板/成功率/相关性，字段容缺不猜不编造，对齐opensoul
  get_context_prompt格式）+stats观测计数）+soulmate_agent四点接线
  （_skill_loader lazy-init共享_skill_manager实例/注入段render_local_section
  +逐项归因/推荐块render_recommended/管线首试+重试消费step_skills）。
  修三处真实缺口：①gene推荐结果此前只注入"可用技能: 名字"描述/模板/成败率
  全丢②任务管线只按用户原话匹配一次技能③注入面无预算
- [x] 6.3 技能进化：用执行结果反哺修正技能内容——周期#35实装agent/skill_evolution.py
  （SkillEvolver确定性反哺闭环不走LLM：回合工具账本→report_evidence.parse_ledger
  拆"真实执行vs被拦截"（只认真实执行，被拦截不算技能缺陷证据）→tool_recovery.
  classify_result单一真源判失败（OK/BLOCKED外=失败，按transient/recoverable/permanent
  细分）→失败签名归一去重（首行前24字符键，已存在的坑不重复写）→确定性坑条目成文
  （同名工具后续OK=「重试/换路径后可成功」/否则「本次未恢复，失败即停」）→经
  SkillSaver.save_flow(overwrite=True)单一写入源revision递增原子替换+skill_manager
  saved层缓存force_refresh强刷（保存即命中检索面）+skill_evolution.db全量审计
  （每次evolved/no_change/skipped都落行含verify自验结果，audit写失败旁路不阻塞）；
  上限：每回合每技能max_new_per_turn=2条新坑、总坑数封顶12超限显式报因；
  单写入者纪律：只进化saved:<slug>可写库技能，标准层只读技能skipped留痕不动；
  正文解析不出（非SkillSaver渲染格式）→skipped"不改写"禁猜禁编造；
  soulmate_agent回合收尾钩子（record_usage之后、try_learn_skill之前，lazy-init
  兼容__new__测试agent+旁路纪律失败只计数不阻塞）。⚠️诚实边界：内容变更仅"坑"
  追加（确定性）；步骤/触发词的语义改写需LLM或用户拍板，未做

### 阶段7 · 工具生态补齐
- [x] 7.1 浏览器自动化（Playwright/CDP）——周期#36实装agent/browser_worker.py
  （独立子进程JSONL协议驱动playwright——服务venv无playwright，默认
  SOULMATE_BROWSER_PYTHON=/usr/bin/python实测带playwright 1.60；常驻浏览器会话
  页面状态跨调用保留；launch本地chromium或SOULMATE_BROWSER_CDP_URL附加既有
  浏览器两模式；单动作失败ok:false不杀会话）+agent/browser_tools.py（BrowserTools
  有界调用+诚实状态三态：后端不可用显式报因+启动文档化/超时显式+页面状态丢失
  如实告知/崩溃重建提示一次；动作级timeout_ms自动留5s余量短于请求级硬超时——
  真实smoke暴露等长时watchdog先开火把"选择器没找到"误报成会话超时重建；截断
  自报；screenshot自动落盘~/.hermes/soulmate/browser_shots返回路径可MEDIA下发）
  +sandbox NETWORK_TOOL_NAMES归类browser（受限会话同款收缩）+soulmate_agent
  四点接线（browser工具定义9动作/主dispatch/_browser_dispatch共享lazy-init/
  code-mode批内+系统提示清单）
- [x] 7.2 文档生成（docx/pdf/xlsx）——周期#37实装agent/docgen_tools.py
  （DocGenTools 确定性生成结构化 spec→真实文件不猜排版：docx=python-docx
  heading/paragraph/bullet/table/pagebreak+title大标题、xlsx=openpyxl 多表
  首行加粗表头+按内容估列宽+rows单表简写、pdf=同款docx经LibreOffice headless
  转换（服务venv无reportlab，独立UserInstallation防抢profile锁，中文可提取）
  +输出落~/.hermes/soulmate/documents/返回MEDIA:路径可直接下发+filename
  白名单校验禁路径穿越+依赖缺失/转换失败/超时四态显式报因禁静默（PDF失败时
  同内容docx保留可交付））+soulmate_agent四点接线（document工具定义/主dispatch/
  _document_dispatch共享lazy-init/code-mode批内+系统提示清单）
- [x] 7.3 TTS/语音——周期#38实装agent/tts_worker.py（edge-tts独立子进程worker
  JSON单发协议——主agent进程可能已有事件循环，进程内asyncio.run不可用故子进程隔离+
  有界超时；合成失败退出码+JSON报因回传禁静默）+agent/tts_tools.py（TTSTools文本→
  真实音频文件落~/.hermes/soulmate/audio/返回MEDIA:路径可直接播放下发；双后端分层：
  主后端edge-tts在线神经语音（服务venv实测带edge_tts）+离线兜底espeak-ng（系统实测
  可用，在线不可用时降级本地合成）；诚实多态：在线失败离线成功显式"⚠️在线语音不可用
  <原因>+已用espeak-ng离线合成（音质较机械）"禁假称edge-tts成功、双后端全失败逐后端
  报因+安装提示、超时显式、0字节文件不算成功、空text/过长>5000字/非法voice/rate/文件名
  越界显式拒绝；voice→espeak语言确定性映射zh→cmn）+sandbox NETWORK_TOOL_NAMES归类
  text_to_speech（edge-tts出网）+soulmate_agent五点接线（text_to_speech工具定义/
  主dispatch/_tts_dispatch共享lazy-init/code-mode批内+系统提示清单）
- [x] 7.4 图像生成/编辑——周期#39实装agent/image_tools.py（ImageTools 输出落
  ~/.hermes/soulmate/images/返回MEDIA:路径；编辑=确定性Pillow八动作 resize/crop/
  rotate/flip/convert/annotate/compose/thumbnail（含RGBA→JPG白底合成、CJK字体
  Noto Sans CJK确定性查找+SOULMATE_IMAGE_FONT覆盖、文字折行）；生成=双后端分层
  对标7.3诚实多态：SOULMATE_IMAGEGEN_API_URL 指向 OpenAI 兼容 images API 时真实
  AI生成（b64_json/url两形态+有界90s超时），未配置→PIL程序化绘制兜底（渐变底+
  文字卡）且输出显式标注"⚠️程序化绘制（非AI生成…不理解画面语义）"禁假称AI生成
  +配置文档化；AI后端失败显式报因禁降级冒充；0字节/不可解码产物不算成功、
  filename白名单禁路径穿越）+sandbox NETWORK_TOOL_NAMES归类image+soulmate_agent
  五点接线（image工具定义9动作/主dispatch/_image_dispatch共享lazy-init/code-mode
  批内+系统提示清单）

## 验收总标准（"完全替代"的判定）
拿 Hermes 日常被使用的 10 类任务（开发/调试/运维/调研/发布/办公/定时/多渠道/记忆/协作），
SoulMate 能独立完成且质量相当 = 达标。每阶段末尾跑一次对标实测并记录。

### 2026-10-10 对标实测（周期#30，阶段1-7完成时）——可复跑 `acp-proxy/acceptance_benchmark.py`

**两轮实测实录（含 harness 事故，如实入档）**：
- **v1 走错入口（10 回合全打偏）**：用 HTTP `/acp/send` 驱动 → proxy.py L97/L141 自证
  "本文件硬编码 hermes acp 路由"（spawn `hermes acp`，空响应兜底 `hermes -z`）——测的全是
  Hermes 不是 SoulMate。教训固化：**验收/SoulMate 测试禁走 /acp/send，必须走 `/ws/acp` +
  `session/new{agent_id:"soulmate"}`**（前端 src 零处调用 /acp/send，用户路径的"禁顶替"红线
  未破；hermes -z 兜底只存在于该测试端点链）。
- **v2 正确路径（下表）**：WS 真实链路（AGENT_ROUTES["soulmate"]→`python -m agent.start
  --stdio`→SoulMateAgent 真实 LLM+真实工具）。中途被 sibling/evo `systemctl restart
  acp-proxy-a/b`（17:44:48 SIGKILL 全部子进程）打断一轮，重跑取全。
- harness 自身两坑（已修进脚本）：同一 WS 连接上前一超时回合的在途帧会串进下一任务的回复
  采集（必须按 sessionId 过滤）；240s 回合上限偏紧（改 300s）。

| # | 类别 | 结果 | 耗时 | 证据（服务端副作用优先） |
|---|------|------|------|--------------------------|
| 1 | 开发 | ❌ | 86s/1.4s 两轮 | 文件未创建。run1 journal 实证 infection-guard `verdict=clarify`"用户请求缺失…疑似验收标记"→`verdict=terminate`→"TERMINATE，工具调用未执行"；run2 1.38s 合成"任务执行报告"零工具执行（短路报告，见 R2） |
| 2 | 调试 | ✅ | 146s | buggy.py 内容回读：`return a - b`→`return a + b` + FIXED 标记（修得对） |
| 3 | 运维 | ✅ | 13s | system_checkup 真实体检（磁盘 80.6%/8090 监听） |
| 4 | 调研 | ❌ | 15s | web_search 硬依赖 SearXNG:8888 未启动→归零；agent 诚实自报"搜索后端未启动"（补了既有知识，不冒充搜索结果） |
| 5 | 发布 | ✅ | 18s | message_send→opensoul.db agent_messages 命中探针行=3 |
| 6 | 办公 | ✅ | 71s | report.xlsx 真实产物 4.9KB 有效 xlsx + execute_code 回读自验 |
| 7 | 定时 | ❌ | >240s | schedule 3 工具链超时且最终无落库（cron_tasks.db tasks 空，无 accept_probe）——慢/卡待查（R3） |
| 8 | 多渠道 | ⚠️ | 106s | 回合跑了且回"任务执行报告"，但回复采集被任务7在途帧污染（harness 坑，已修）；wechat 回环出站无独立可查痕迹→待复测 |
| 9 | 记忆 | ❌ | >240s | MEMORY.md 无标记。journal 实证 infection-guard `verdict=clarify`"用户请求为空却单方面向持久化记忆写入…请说明触发依据"→拦死（与 #1 同根因 R1） |
| 10 | 协作 | ✅ | 31s | journal 级实证 delegate_tasks 真执行（permission=allow + provenance + 两 worker 真实产出 ALPHA_DONE/BETA_DONE 各 13.3s）+ subagent_runs.db 2 行 |

**判定：真实达标 5/10（调试/运维/发布/办公/协作），1 待复测（多渠道），4 未达标
（开发/调研/定时/记忆）——距"完全替代"仍有实差距，缺口高度聚类在 R1。**

**根因聚类（journal/代码实证，非猜测）**：
- **R1 InfectionGate 误杀（P0，#1/#9）**：注入审计门的"用户请求"上下文传参断链（detail 自述
  "用户请求缺失/为空"），把用户明确点名的 write_file/memory_append 判成疑似注入；clarify
  在无人值守路径又升级 terminate→工具不执行。与用户防护哲学"根据用户语意来防护"直接冲突。
- **R2 说了≠做了（#1 run2）**：工具被拦/短路后合成"任务执行报告"声称完成，违反 2.1 护栏
  语义（报告必须以工具执行证据为前提）。（周期#32 ⚡8.2 已治理：短路=语义缓存回放，
  执行类请求禁入缓存；报告/自检以工具执行证据为前提，被拦报"被拦截+原因"禁报"完成"。）
- **R3 长回合可靠性（#7）**：多工具链回合 240s+ 不落终态也不落库。
- **R4 调研单点依赖（#4）**：web_search 无后端可用性兜底（SearXNG 未启动=归零）。

### 2026-10-11 抽样对标实测（周期#64，阶段11 完成后）——可复跑
`ACCEPT_CATEGORIES="开发,办公,定时,协作,记忆" ACCEPT_RUN_TAG=<tag> python acceptance_benchmark.py`

| # | 类别 | 结果 | 耗时 | 证据（服务端副作用优先） |
|---|------|------|------|--------------------------|
| 1 | 开发 | ✅ | 301.74s | dev_hello.py 存在+marker 命中；**WS 300s 超时→10.4 后到回读恢复**（0.0s/1次），verify_mode=server_side_late |
| 2 | 办公 | ✅ | 65.26s | report.xlsx 4963B 有效 xlsx（11.5 治理回归目标：全窗口零注入审计终止） |
| 3 | 定时 | ✅ | 74.53s | cron_tasks.db accept_probe 行=1 |
| 4 | 记忆 | ✅ | 106.81s | MEMORY.md 含 ACCEPT_MEM_MARK_9 |
| 5 | 协作 | ✅ | 27.4s | 回复含双 worker 产出+subagent_runs 行 |

**判定：抽样 5/5 通过**（4 类 server_side + 1 类 server_side_late，本抽样无 reply_assisted 弱项）。
新证据/新发现：
- **10.4 后到回读路径首次在非构造场景真实触发**（ws_timeouts=1/recovered=1）——11.1 遗留④
  "late_readback 未触发"边界部分销账（仍单样本）。
- **开发类 301.74s 显著变慢**（11.1 同任务 48.2s）：dev_hello.py 在开跑 1 分钟内即落盘，
  超时发生在回复回收侧（后到回读 0.0s=产出早已就绪），疑上游延迟波动/冷启动首回合，
  单样本待归因 → 12.3。
- 采集侧系统帧过滤照常生效（self_check×3/heartbeat×2）；办公类 65s 干净通过=11.5
  溯源治理无回归。
- harness 收尾清理边界：cleanup() 只清 MEMORY 标记/accept_probe 行/发布探针行，
  /tmp 工作目录仅在**开跑前** rmtree（docstring 承诺"测试产物显式清理"未完全兑现），
  本轮残留 3 件已手动清理 → 12.5。

### 2026-10-11 抽样对标实测（周期#76，阶段12 完成后）——可复跑
`ACCEPT_CATEGORIES="开发,办公,定时,协作,记忆" ACCEPT_RUN_TAG=<tag> python acceptance_benchmark.py`

| # | 类别 | 结果 | 耗时 | 证据（服务端副作用优先） |
|---|------|------|------|--------------------------|
| 1 | 开发 | ✅ | 23.63s | dev_hello.py 存在+marker 命中+**运行账本命中**（terminal 执行行=1 含程序输出非源码回显）+回复报告输出=是 |
| 2 | 办公 | ✅ | 44.93s | report.xlsx 4964B 有效 xlsx（全窗口零注入审计终止） |
| 3 | 定时 | ✅ | 30.80s | cron_tasks.db accept_probe 行=1 |
| 4 | 记忆 | ✅ | 24.98s | MEMORY.md 含 ACCEPT_MEM_MARK_9 |
| 5 | 协作 | ✅ | 18.75s | 双 worker 产出+subagent_runs 近 8 行 |

**判定：抽样 5/5 通过，5 类全部 server_side（本抽样无 reply_assisted/late 弱项）。**
新证据/新发现：
- **12.6② verify_dev 三判据首次真实 run 走中**：开发类证据从"文件 marker"升级为
  "文件+运行账本+回复报告"三判据（运行账本=命中，真源=trajectory_events），S1 假过
  形态在真实验收路径封死。
- **开发类 23.63s 回归常态**（#64 曾 301.74s、#75 24.44s）——#64 的 301s 确认为
  上游波动/冷启动个案（12.3 归因结论复核成立），stall_guard 上线后无复发。
- 本轮零注入审计误杀、零 WS 超时（late_readback 未触发，路径实证仍在 #58/#71/#64）；
  采集侧系统帧过滤照常（self_check×3）。
- 测试产物清理归零实证：MEMORY 标记 0、accept_probe 行 0、/tmp/soulmate_accept 已删。

## 阶段8 · 验收缺口修复（下一批切片，按优先级）
- [x] ⚡8.1 InfectionGate 误杀修复（P0 用户可见缺陷，先于一切）：①审计门必须拿到本回合
  用户原始 prompt（修上下文传参断链，验收断言=journal 不再出现"用户请求缺失/为空"）
  ②用户明确点名的动作（prompt 明文要求的工具/路径/内容）给语义豁免——防护看用户意图
  而非模式匹配（禁一刀切白名单守卫，用户反感守卫类防御）③无人值守路径 clarify 不得
  升级 terminate（放行+留痕或按模式配置）。验收=重跑 acceptance_benchmark.py 开发/记忆两类。
  ——周期#31实装：根因实证=任务管线首试/重试两个 _run_llm_with_tools 调用点漏传
  user_text→审计门拿到"## 用户请求（未提供）"→用户明文点名的动作被误判注入。
  ①两调用点补传 user_text + check() 空 user_text 留痕告警；②溯源豁免（签名命中文本
  逐字来自用户请求=用户自己写的内容豁免，夹带内容照常拦）+JUDGE_PROMPT 补
  "用户意图优先/用户请求缺失不构成拦截理由"两条判据；③无人值守 clarify 超限
  放行+留痕（source=clarify_exhausted_pass+轨迹账本 infection:pass_with_trace），
  有人值守保持 terminate；INFECTION_CLARIFY_EXHAUSTED=terminate|pass_with_trace
  按模式配置。真实验收=开发/记忆两类 2/2 ✅（详见周期#31日志）
- [x] ⚡8.2 短路合成报告治理：任务执行报告/自检结论必须以工具执行证据为前提（工具被拦时
  报"被拦截+原因"，禁报"完成"）；锁定 run2 1.38s 秒合成"任务执行报告"的短路路径。
  ——周期#32实装：新增agent/report_evidence.py（执行证据前提确定性治理——parse_ledger
  账本拆"真实执行vs被拦截"（permission单一真源：allow/ask-approved/degraded-allow/
  cached-allow/infection:pass_with_trace=执行证据，deny/ask-denied/sandbox-restricted/
  loop_guard:*/其余infection:*=被拦截，[xxx]伪条目两头不计）+assess四态(no-claim/
  grounded/blocked/unsupported)+amend_response报告标题降级"（⚠️ 未经执行验证）"+
  governance_banner被拦截报"被拦截+原因"+is_cacheable_request语义缓存准入）；
  短路根因实证=语义缓存exact命中回放旧"任务执行报告"（跳过LLM/工具，1.38s形态）→
  执行类请求（创建/修改/删除…）get侧不读缓存、put侧不入缓存（纯信息问答仍缓存）；
  self_verify收紧"说了≠做了"：动作类met必须以工具执行证据为前提（原has_marker
  自证漏洞"回复自称已完成即给met"封死），被拦工具evidence报⛔被拦截+原因不计证据。
- [x] 8.3 长回合治理：schedule 3 工具链 >240s 无终态根因（LLM 慢 or dispatch 卡死）+
  回合级进度/超时可见（禁静默）。
  ——周期#33实装+根因实证（journal 17:52-17:57 验收#7 复盘）：**非 dispatch 卡死**，
  回合在真实推进——每工具调用一次注入审计 LLM 往返 + R1 审计误杀触发失败/重试轮
  把 3 工具链拉到 5-8 分钟级，harness 240s 到点放弃即"无终态无落库"（R1 修复后
  同链路实测 78s 完整终态，见周期#33探针）。可见性治理：新增 agent/turn_watchdog.py
  （TurnWatchdog：后台心跳默认 60s SOULMATE_TURN_HEARTBEAT_S 推快照[已用时/轮次/
  工具数/最近工具]给客户端+journal，不依赖主循环轮次边界、LLM 单轮慢也照发；
  软预算 240s SOULMATE_TURN_SOFT_BUDGET_S 超限发一次 ⚠️"继续执行不中断"——用户
  铁律任务禁硬超时杀只提醒；旁路纪律 notify 抛错只计数不冒泡；max_lifetime 960s
  自过期防孤儿心跳）+soulmate_agent 五点接线（import+_watchdog_for/
  _stop_turn_watchdog lazy-init 兼容 __new__ 测试 agent+工具循环前 start+轮次/工具
  节点 beat+turn.close 订阅收口——所有 close 路径必发事件，孤儿心跳不残留）。
  ⚠️诚实边界：耗时本身治理（单工具链审计 LLM 往返开销优化）未做，另案切片
- [x] 8.4 调研兜底：web_search 后端可用性探测 + SearXNG 未启动时显式降级路径（替代后端
  或启动文档化），禁静默归零。
  ——周期#34实装：新增agent/search_backend.py（SearXNG JSON主后端+Bing HTML兜底
  降级链[unified-search技能已验证的中间层兜底，SOULMATE_SEARCH_BING_FALLBACK=0 可关]
  +probe()连通性探测+四态语义严格区分——ok/empty/inconclusive/unavailable：可信空
  只认searx JSON results:[]（报"非故障"），bing HTML 0块=反爬拦截与真无结果不可区分
  →inconclusive"不足以断言无资料"禁冒充可信空，全挂=显式逐后端报因+启动文档化
  [start.sh/searx.webapp，SOULMATE_SEARXNG_URL 可配]）。修静默归零双根因：①后端挂了/
  非JSON/缺results键被报成"无搜索结果"②query裸拼URL未编码（空格/中文截断→假零）→
  统一quote()；soulmate_agent两执行点（主循环+code-mode批内）收口run_search单一真源，
  内联curl-SearXNG清零（接线护栏断言全仓无残留调用点）。
  真实机器实证（SearXNG恰好未启动）：searxng❌不可达+bing✅0块（疑似反爬）→输出
  "⚠️未获得搜索结果…不足以断言无资料…启动方法start.sh"——旧实现同场景会报
  "搜索不可用: curl stderr"或假"无搜索结果"，现四态诚实成文
- [x] 8.5 验收复测：多渠道/定时两类（harness 已修 sessionId 过滤）；每完成一个 ⚡项即
  重跑对应类别对标。⚠️复测铁律（周期#31 实证）：验收 prompt 必须带唯一防缓存标记——
  semantic-cache EXACT 命中会回放旧响应（假跑 0.98s 级），且文件存在性验证会被
  上次被打断轮的陈旧产物骗过（假阳性）——验证前先清理陈旧产物。
  ——周期#40复测落地：acceptance_benchmark.py 增 ACCEPT_CATEGORIES 类别过滤
  （"每完成一个⚡项即重跑对应类别"能力）+每次运行注入唯一 RUN_TAG 防缓存标记
  （复测铁律①）+setup() 先 cleanup() 清陈旧产物（复测铁律②）。真实复测定时/多渠道
  两类 2/2✅（tag=RETEST85_1791643501）：定时 76.57s server_side（cron_tasks.db
  accept_probe 行实证+journal schedule_task 真实创建#3）、多渠道 40.56s reply_assisted
  （journal wechat_route_list+message_send 真实工具账本 permission=allow、
  "已发文本1块给微信peer"）；延迟非 0.98s 短路形态+journal 零 semantic-cache 回放
  =非缓存假跑；测试产物复核清理归零（accept_probe 行 0）。
  ⚠️诚实边界：每类单次样本（同机低负载）；多渠道 verify 为 reply_assisted（微信通道
  loopback 传输，真实 iLink 归 4.1b 待拍板）；8.6 双链合并落地后建议全量 10 类复测。
- [x] 8.5b 全量 10 类验收对标实测（周期#41）：分批 5+5 真实跑完 10 类（tag=FULL10_A/B），
  9/10 当场过；调研类失败连环挖出 3 个真根因并逐一修复后类别复测转✅（tag=FULL10_I）：
  ①环境根因=SearXNG 未启动（启动后仍挂）→②search_backend 15s 超时 < SearXNG 冷查询
  实测 20s（curl exit 28 被误报"不可达"→模型误称"SearXNG 未启动"）→③semantic-cache
  88% SEMANTIC 命中回放旧误报（复测标记只防 EXACT；根因=动作类判据不认"用 <工具名>"
  点名与搜索类动词）→④verify_research 裸子串"不可用"判据把正确回答假阴性杀掉
  （补做说明引用提示语/心跳粘连）。修复三连：report_evidence 动作类判据补搜索动词+
  工具点名（5a8a16a3）、search_backend 超时 30s+exit28 措辞（0aab4f8c）、
  verify_research 主答区语义判据+须含被搜事实+剥心跳（ce1ec4c4/23fa4f94）。
  最终形态：调研类真实 75.66s 回合、web_search 真实执行、答案正确（2024-10-07）。
  全量 10 类单轮确认跑（tag=FULL10_FINAL）台面 10/10，但发布类经行归属取证为
  **陈旧行假阳性**（om-aa107108ab94 只有 user 行无投递行，模型误报 message_send
  "未注入"拒发）——harness cleanup 漏清 agent_messages 探针行（复测铁律②漏网形态）；
  修复（89013b1a：cleanup 预清探针行+verify 只认 accept-pub-target 投递行）后
  发布类真复测✅（tag=FULL10_J，投递行=1 真实落库，message_send 本次正常调用）。
  ⚠️诚实边界：10 类均有真实通过证据（9 类在 FINAL 轮、调研/发布为修复后类别复测），
  但非同一轮全量全绿；同机低负载单样本/类；发布类"工具未注入"误报为间歇性
  模型行为（8.5c-②），非确定性缺失。
- [x] 8.5c 验收发现遗留（周期#41 实证，优先级高；①②④均已治理，③⑤转后续另案）：
  ①✅回复风格（周期#43 治理，d3dc9841）：任务类回答尾部固定带"补做结果/逐项说明"
  元叙事（违反验收 prompt "简短回答一句话"）——三根因实证逐一修：
  C（"固定带"主根因）=自否定元指令被当需求：验收防缓存标记行"此行仅为…忽略其
  语义"被分句拆成需求项且必然 unmet → 每轮必触发补做；修=is_self_negating 显式
  自否定措辞不算需求（"此行仅[为是]/只是"须伴"标记/占位"防误杀"此行只是第一部分"
  类真实需求，"忽略大小写"类任务内容不受影响）；
  A=条件子句"如果X就Y"被拆成独立需求项、短答覆盖不到 → unmet → 补做；修=条件项
  unmet 封顶 uncertain 只提醒不补做（missing_requirements 恒不含条件项）；
  B=build_remediation_prompt 旧措辞"完成后逐项简要说明结果"直接命令模型产出逐项
  复盘；修=改"只输出需要补充的内容本身，禁'补做结果/逐项说明'复盘标题"；
  连带修 harness 判据假阳性（STYLE85C_1 实证）：需求自检报告复述的需求原文"如果
  搜索工具不可用…"被并帧采集混入主答区 → 正确回答误判"自报不可用"——主答区边界
  补"🔍需求自检/可用性更正声明"切割点（元叙事区需求原文复述 ≠ 模型自报）。
  tests/test_remediation_style.py 14例+同域回归120全过；真实验收调研类复测 ✅1/1
  （tag=STYLE85C_2，31.5s，主答区自报不可用=否）——"补做结果/逐项说明"尾部在
  STYLE85C_1/2 两轮真实 run 中均消失实证。
  ⚠️诚实边界：①需求自检报告（observability 推送）仍并帧进合并采集视图（"简短
  回答"场景合并视图非纯一句话）——采集侧帧过滤属 8.5c-⑤ 遗留；用户明示简短时
  抑制自检推送涉及可观测性取舍待拍板；②补做内容若模型自发复盘无确定性剥离
  （刻意不做输出后处理过滤，防过度防御）；③单样本复测（同机低负载）。
  ②✅可用性误报（**本日两次实证，最高优先**）——周期#42实装治理：模型谎称工具
  "不在工具列表/运行时未注入"
  ——run-A 称"没有 web_search"（账本实证同回合已成功调用 web_search）、FINAL 轮
  发布类称"message_send 运行时未注入"并拒发（代码实证 builtin_tools 无条件装配+
  系统提示有清单，且下一回合同工具正常调用=间歇性模型行为非确定性缺失）。
  治理=可用性声称对照工具账本/self._active_tool_names 确定性核验（8.2 延伸：
  "说了≠做了"的镜像"做了/有，说没有"），amend_response 同款降级修正。
  ——周期#42实装：report_evidence 增可用性声称抽取（registration/availability/
  generic 三类正则+结果类宾语排除"没有 X 的结果"防误纠+词尾边界防排除词被回溯
  绕过"terminal 输出"缩成"termina"）+assess_availability 两级判据（registration
  =工具面真源 _active_tool_names 在册即证伪；availability=模糊声称仅"本回合真实
  执行成功"证伪——被拦截的调用反向支持模型说法不更正；generic=有成功执行才证伪；
  无真源无账本一律不更正）+amend_availability/availability_banner 前置更正声明
  （原文保留可审计）+soulmate_agent 接线（证据治理后、自验前，真源=_active_tool_
  names）；tests/test_tool_availability_claims.py 18例+回归167全过；⚠️诚实边界：
  ①治理的是"说"侧（更正声明），模型拒做行为本身靠更正声明引导，未做强制重试
  ②availability 类声称未试过就喊不可用的（在册但零执行）不武断更正（防误纠）
  ③✅InfectionGate 自愈类低危命令豁免（周期#46 治理）：模型自主
  `bash ~/searxng-data/start.sh` 自愈环境曾被判 infection:clarify（有人值守回合中断
  自愈链，clarify 超限还升级 terminate 杀整轮）——实装确定性豁免语义
  （infection_guard.classify_self_heal 纯自愈批次=全部 terminal 单一命令匹配
  systemctl --user 生命周期/journalctl 只读/start.sh 启动脚本三类窄签名、拒 shell
  元字符链式与混入非自愈命令；LLM 审计 clarify 一级降级=放行+留痕
  self_heal_pass_with_trace（原疑问留痕可审计），terminate 照常终止、启发式劫持签名
  在前不受豁免影响、工具仍过 permission_gate/sandbox 不绕权限）+JUDGE_PROMPT 补
  自愈指导语双保险；
  ④✅SearXNG 常驻托管（周期#44 治理）：searxng.service（systemd --user）已建成
  enabled 开机自启（Restart=always，手动进程已移交 systemd 接管），search_backend
  STARTUP_HINT 升级首选 `systemctl --user start searxng`——"重启即丢全靠手动起"销账；
  ⑤✅harness 采集侧按帧来源过滤（周期#45 治理）：turn_watchdog 心跳帧/self_verify
  需求自检推送/report_evidence 治理横幅曾被 harness 并进回复文本采集（⏳ 快照与
  答案粘连、"简短一句话"合并视图非纯一句话）——实装 agent/frame_origin.py 帧分类
  （origin_of 帧首前缀判别 heartbeat/self_check/audit_notice/answer；标记常量从
  生产者模块 import 单一真源 SNAPSHOT_PREFIX/REPORT_HEADER/GOV_BANNER_PREFIXES/
  AVAIL_BANNER_PREFIX；audit_notice 两形态：纯横幅帧整帧丢弃、横幅+正文同帧按
  banner+空行+body 契约只剥横幅块保正文）+acceptance_benchmark._rpc 采集收口
  split_frames（entry.system_frames+summary system_frames_total 过滤计数可观测）。
- [ ] ⚡8.6 双链分叉事故处置（见周期#30日志）：main(1b63f2ac) 与 evo/exp-1791613018
  (d431bcc0) 在 6.2 处分叉成两套实现（skill_loader vs skill_matcher），#25-#29 只在
  evo/exp 链——链合并/取舍决策涉及取舍**待用户拍板**。周期#31 已做非破坏处置准备：
  两实现对比（main skill_loader 248行13函数：步骤级 match_for_step/gene 推荐载荷
  render_recommended/预算截断自报/stats 观测 vs evo 链 skill_matcher 192行：match/
  render_bodies，已被 6.3-7.4 依赖）+合并建议=以 evo 链为主干（唯一含 #25-#29）、
  移植 skill_loader 独有增量、main 快进到合并结果（恢复生产树全特性）；8.1 修复已
  双链双落地（98d6025f main / 9d17ebdc evo 链）零丢失。
- [ ] 后续（非紧急，另案）：微信 iLink 真实传输 4.1b（接入形态待用户拍板，绝不双消费
  hermes 会话）、ASR 语音输入（多模态路由另案）、文生图
  大模型 API、AI 群组真实成员端到端。
  （原"调度/监控常驻托管（3.1 遗留）"已 ✅ 周期#48 销账：systemd --user
  soulmate-daemon.service 常驻宿主，无 agent 进程也到点执行）

### 阶段9 · Hermes 对标工具面补齐（周期#48 规划；⚡8.6/4.1b/后续均待用户拍板，**流水线下一刀=9.1**）
> 现状盘点（周期#48 工具面 grep 实证）：对标 Hermes 核心工具清单（read_file/write_file/
> patch/search_files/send_file、terminal/execute_code、web_search/web_extract、
> vision_analyze/read_image、todo/memory/process/text_to_speech），SoulMate 缺口仅剩
> send_file 与 process 进程管理两项（sense_transcribe_audio 已覆盖 ASR、text_to_speech 已销账）。
- [x] 9.1 send_file 文件下发工具：对标 Hermes send_file 语义（path+message→📎文件卡片），
  与 MEDIA 标签下发通道（fileviewer-media-tag-pattern）对齐不双轨；文件不存在/空 path/
  越界显式报因；产物经既有 MEDIA: 渲染面到前端（预览/下载/复制三键卡片）
  ——周期#50实装：agent/send_file_tools.py（SendFileTools 确定性校验→MEDIA 标签：
  空 path/不存在/目录/非普通文件/越界显式报因禁静默，realpath 收口 `..` 与软链穿越，
  根目录默认=用户主目录+/tmp、SOULMATE_SEND_FILE_ROOTS 可覆盖并列明；成功=附言+📎信息行
  （文件名+字节数，0 字节如实报 0）+MEDIA: **末行**（周期#39 输出契约））+soulmate_agent
  七点接线（顶部 import/主循环 dispatch/code-mode 批内 dispatch/_send_file_dispatch 共享
  lazy-init 入口（成功产出登记待交付清单）/工具定义进 LLM 工具面/系统提示工具清单/
  网关后处理 _merge_pending_send_media 确定性交付兜底——工具产出的 MEDIA 标签模型未回显
  也补到回复末行走**同一通道**（回显不重复、pop 语义一回合只交付一次、回合开始作废残留），
  对标 fileviewer-media-tag-pattern"不依赖 LLM"教训）
- [x] 9.2 process 后台进程管理工具：对标 Hermes process 语义（list/poll/log/kill/write/
  submit 有界子集），后台任务可见可管（用户极度重视可观测性）；禁硬杀长任务（到点只报
  状态不 SIGKILL 既有铁律对齐）；子进程生命周期归系统（观察窗哲学）
  ——周期#51实装：agent/process_tools.py（ProcessTools：start/list/poll/log/kill/
  write/submit/close 八动作——start=start_new_session 脱离会话进程组（生命周期归系统，
  会话断开/代理重启不杀）+stdout/stderr 落日志文件；注册表 SQLite 持久
  ~/.hermes/soulmate/memory/processes.db（SOULMATE_PROCESS_DB_PATH/SOULMATE_PROCESS_LOG_DIR
  可覆盖），重启后 list/poll/log 照常；状态判定真实进程状态+PID 复用守卫（/proc starttime
  比对，复用的陌生 PID 不冒认 running）；kill=显式意图默认 SIGTERM 杀进程组（force=true
  才 SIGKILL）、有界等待 _KILL_WAIT_S 不硬杀、幂等；**工具内无任何到点 SIGKILL 路径**
  （禁硬杀长任务铁律）；write/submit=stdin 写入（submit=写入+回车）仅对本进程启动的
  进程可用，重启失联显式报"stdin 不可用"禁假装成功；log=行/字节双限截断自报；全部
  校验显式报因（空 command/未知 action/未知 proc_id/坏 cwd/日志缺失））+soulmate_agent
  六点接线（顶部 import/主循环 dispatch/code-mode 批内 dispatch/_process_dispatch 共享
  lazy-init 入口/工具定义进 LLM 工具面/系统提示工具清单）
- [x] 9.3 文生图真实后端 e2e：真实 images 后端端到端实证+配置文档化（周期#39 只实证了
  PIL 兜底与失败态），诚实多态保持
  ——周期#52实装：后端三级链（①SOULMATE_IMAGEGEN_API_URL OpenAI 兼容自有/本地后端优先
  ②未配置→Pollinations 免费真实 AI 生成（GET /prompt/{q} 返回图像字节，默认启用，
  SOULMATE_IMAGEGEN_POLLINATIONS=off 关闭、_URL/_MODEL/_REFERRER 可覆盖；隐私边界：
  prompt 文本发给 pollinations.ai 云服务，自有/本地后端走①）③都关闭→PIL 兜底）+
  配置文档化（_GEN_HINT 三级优先级+各 env 含义）+实测暴露并修 2 真缺口：
  ①AI 后端产物实际格式与扩展名不符（Pollinations 回 JPEG 落 .png → FileViewer
  mimeType 错配，pitfall 3c）→_align_ext 按 PIL 真实 format 改后缀 ②公共后端偶发
  5xx/402→有界重试 2 次（对齐 MiMo 400 重试纪律）仍失败显式报因
- [x] 9.4 AI 群组真实成员端到端（后续区提级）：群组任务链路已通（周期#21/ai-groups-e2e），
  补真实多 agent 成员参与的端到端验收
  ——周期#53 验收实跑（acp-proxy/e2e_ai_groups_multimember.py 可复跑探针，3 真实成员
  hermes(advisor)/soulmate(executor)/mimo(verifier) 建组→下任务→自动链式执行全程无手动
  execute→群组 WS 同挂采集）：**首轮实测暴露并根因修复 2 真缺口**：①冒名兜底+PATH 盲区
  双根因——journal 实证 `Agent resolve: mimo -> hermes`/`soulmate -> hermes`（mimo 成员的
  子任务实际以 hermes 大脑执行，独立性铁律"每个agent都应该是独立的"被违反）：根因=
  systemd 服务 PATH 不含 ~/.local/bin（mimo 只装在那，which 全盲"Agent list refreshed:
  0 available"）+`_resolve_agent_binary` 尾部"默认用hermes"兜底链静默顶替→修复
  ws_chat.which_agent（常规 PATH+用户安装目录+返回绝对路径，子进程 PATH 同补）+
  _resolve_agent_binary 重写（真实身份只认自己、实例后缀剥后缀仍认本体、找不到原样返回
  下游显式"Agent未安装"、**删除一切冒名兜底**；仅角色槽位 advisor-1/executor 保留
  model/关键词映射）②观测面冒名（soulmate 子任务 started 事件显示 `binary=hermes`）→
  _channel_desc 如实标注（soulmate=`channel=soulmate-acp`，CLI 成员=`binary=<自己>`）；
  另修 opencode CLI_ARGS 过时（`-q` 已不是有效 flag 实测打印帮助文本假成功→`run`，经
  run_agent_proxy 真跑回"收到"）

### 阶段10 · 韧性与真源收口（周期#54 上游降级实证后的下一批）
- [x] 10.1 LLM 传输韧性治理（周期#54 实证：MiMo token-plan 上游 429/503/
  incomplete-chunked 三态并发，现有 3 次重试（2s/4s 退避）在持续降级下快速耗尽
  →真实回合长时间空转无输出）——周期#55 实装：
  a) utils/llm_retry 策略面：MAX_ATTEMPTS 3→5 + RETRY_MAX_DELAY_NO_HEADERS_MS
  30s→60s（退避上限拉长；无头总退避 2+4+8+16=30s 有界）；新增
  RETRY_MAX_WAIT_MS=60s 单次等待硬上限（SOULMATE_LLM_RETRY_MAX_WAIT_S 可配）
  ——kilocode 原对有头等待只 cap i32≈24.8 天，上游一个 Retry-After: 86400 就
  让回合静默挂一天，恰是"禁静默空转"禁止形态，已收口；429 尊重 Retry-After
  语义保留（≤上限的头值原样生效）
  b) classify_failure 确定性分类（rate_limit/timeout_status/server_error/
  client_error/protocol_error（含 incomplete chunked read 实证形态）/
  read_timeout/connect_error/network_error/transient_message）
  c) llm_engine 三路（chat/chat_stream/chat_stream_with_tools）12 个重试/放弃
  决策点接线 record_llm_failure→tool_failures.db 可观测（旁路纪律不阻塞+
  测试期不污染真实库）；MAX_TURN_SENDS 8→12 随尝试数联动（仍<理论最坏
  4段×5发=20，封顶真实生效）
  验收=注入故障真实 HTTP 服务器（非 mock，tests/test_llm_transport_resilience.py）：
  429+503 抖动→回合完成、incomplete chunked read→回合完成、持续 429→显式
  "[LLM错误"有界报因（5 发封顶）、Retry-After: 3600→硬上限截断照常完成
  （修复前=asyncio.sleep(3600) 静默空转）；失败文案契约逐字未动
- [x] 10.2 检测=注册=列表真源收口（周期#53 遗留③销账）：opensoul
  src/api/agents.py detect_agents 仍用 `shutil.which(info["binary"])`——systemd
  PATH 盲区（~/.local/bin 不在 PATH）与 9.4 同根因，AGENT_REGISTRY available 全
  None→"检测成功的agent就应该是注册的"铁律的检测面仍断；修法=复用
  ws_chat.which_agent 单一真源（常规 PATH+用户安装目录+绝对路径 spawn 校验），
  验收=detect 后 available 与真实 ACP 能力一致、未注册不进列表
  ——实现落 opensoul（姊妹流水线 B3 线）：a01b79cf detect/uninstall/list/send
  统一 which_agent 单一真源 + 77346163 B3 探测即注册（services/agent_registry.py
  probe_agent：L1 真ACP握手 acp-proxy /agents/probe + L2 真开会话 ws session/new
  两级全过才 registered=1，list_registered 只回 registered=1=未注册不进列表）；
  周期#56 真机验收闭环（盲区 A/B 实证 + 真实探测链 e2e 5/5 + live registry 2/61 实证）
- [x] 10.3 run_agent_proxy 挂死可见性（周期#53 诚实边界②销账）：群组任务执行
  无执行超时（hermes 长回合实测 12 分钟+仍完成，禁硬杀/防守卫铁律）→治理=
  进度心跳+执行时长可查询+可显式取消，**不加到点 SIGKILL**；验收=人为慢回合
  可见进度且可取消，正常长回合不被误杀
  ——周期#57 实装（opensoul ws_chat.py/ai_groups.py）：a) 进度心跳（_heartbeat
  后台任务默认 30s SOULMATE_PROXY_HEARTBEAT_S，on_event("heartbeat") 推群组执行
  事件流可见，旁路纪律失败不冒泡）b) 执行时长可查询（_PROXY_RUNS 登记表+
  proxy_run_snapshot 实时 elapsed_s/last_event/lines + find_proxy_run(task_ref)
  查找 + GET /api/ai-groups/{g}/tasks/{t}/exec-status）c) 可显式取消
  （cancel_proxy_run：SIGTERM 独立进程组→有界等待→force=True 才 SIGKILL +
  POST /api/ai-groups/{g}/tasks/{t}/exec-cancel；显式取消不自动重试不评分，
  任务以取消收口）；执行主路径无任何到点自动杀（源码契约测试钉死）
- [x] 10.4 验收 harness 降级适配（周期#54 半程已做：ACCEPT_PROMPT_TIMEOUT_S
  可配）：余下=WS 超时任务的"服务端后到产出"二次回读（实证定时类 WS 300s 超时
  但 accept_probe 行后到落库，纯 harness 口径漏记真实完成）；验收=模拟慢回合，
  harness 结果与服务端真源一致
  ——周期#58 实装（acceptance_benchmark.py）：a) late_readback 预算内轮询服务端
  真源（_read_back_reply=agent_messages 该 session assistant 落库行真源回读 +
  各类别验证器复用，服务端副作用优先纪律不变），后到产出出现即判过附"后到回读
  Ns/M次"可观测，预算耗尽（ACCEPT_LATE_READBACK_S 默认300s/轮询
  ACCEPT_LATE_READBACK_POLL_S 默认5s）如实 False 禁把超时当成功；b) _drain_ws
  超时期间持续排水后到帧——防 WS 读缓冲顶满反压 agent stdout 把回合拖死，
  按 sessionId 过滤不混他会话帧，连接断开只停排水不冒泡；c) main() 超时路径与
  普通 WS 失败路径分离（prompt_timed_out 标记），verify_mode=
  server_side_late/reply_assisted_late/ws_timeout_unrecovered 三态诚实区分，
  summary.late_readback 记超时数/恢复数；验收=模拟慢回合真实跑通（tag=LR58，
  ACCEPT_PROMPT_TIMEOUT_S=5 人为压短 WS 口径）：WS 5s 超时→服务端回合照跑→
  后到回读 20.0s/5次 恢复，1/1✅ verify_mode=server_side_late，harness ok 与
  服务端真源一致（dev_hello.py 真实落盘含 SOULMATE_DEV_OK 独立复核）

### 阶段11 · 下一批切片（周期#58 规划——阶段1-10切片清零后的续刀，按优先级）
- [x] 11.1 全量10类验收对标实测（周期#59）：harness 带 10.4 超时降级适配后全量单轮
  重跑（tag=FULL11_1791677778），**10/10 当场全过**（上游 MiMo token-plan 已恢复
  /api/llm/test pong 后开跑）：开发48.2s/调试38.0s/运维14.4s/调研32.1s（答案
  Python 3.13 发布日 2024-10-07）/发布21.6s（投递行=1 真实落库）/办公96.1s
  （report.xlsx 4962B 有效）/定时87.1s（accept_probe 行=1）/多渠道46.0s/记忆78.0s
  （MEMORY.md 标记）/协作37.6s（subagent_runs 双worker）；server_side 7类+
  reply_assisted 3类（运维/调研/多渠道）；本轮 WS 超时 0 个（late_readback 未触发，
  降级期口径留待下次降级复核）；采集侧系统帧过滤实证生效（heartbeat×3+self_check×7
  被过滤）；测试产物清理归零（accept_probe 行 0、MEMORY.md 标记 0、/tmp 探针产物删除）
- [x] 11.2 _run_soulmate_turn 挂死可见性（周期#60，周期#57 诚实边界①销账）：10.3 三件套
  （进度心跳/执行时长可查询/可显式取消）扩到群组任务的 soulmate ACP 回合通道
  （此前只覆盖 CLI 通道 run_agent_proxy）；同样不加到点 SIGKILL。
  ——周期#60 实装（opensoul d1b85151）：ws_chat.py 抽 register_proxy_run/finish_proxy_run
  共用登记表（channel='cli'/'acp_ws'，快照带 channel）+cancel_proxy_run 通道感知
  （CLI=killpg 信号链；acp_ws=cancel_cb 摘观察窗停止本次执行等待，会话生命周期归系统
  不杀远端生成，绝无到点自动杀）+ai_groups._run_soulmate_turn 三件套接线（登记进同一
  登记表→exec-status/exec-cancel 两个任务级端点对两种通道同一套语义；心跳默认
  SOULMATE_PROXY_HEARTBEAT_S=30s 走 on_event→_emit_exec_event 群组事件流；返回形状
  改 (text, source, success) 与 run_agent_proxy 对齐，source='cancelled' 直通既有
  取消收口不重试不评分）+诚实超时（timeout=等待预算，耗尽如实报"回合未完成"+已收
  部分保留，禁把超时当成功）。真机验收：live :8092 真实 SoulMate 回合 24.0s 回"OK"
  ——心跳×2（"已用10s｜已收0块"→"已收1块｜最近: OK"）+运行中快照逐秒可查
  （elapsed/lines/last_event/channel=acp_ws）+done 收口；tests/test_soulmate_turn_
  run_control.py 6/6（真实 websockets 服务端三形态：流式/挂住/沉默——心跳+快照、
  显式取消 source='cancelled' 禁冒充完成、超时≠成功、正常回合不误杀、ai_groups
  exec-status/exec-cancel 端点真接线、源码契约零 killpg/SIGKILL）+10.3 回归 8/8+
  ai_groups 回归 46 全过
- [x] 11.3 LLM 故障分类记账补链（周期#55 诚实边界①销账）：utils/llm_transport→
  dna 调用路径接 record_llm_failure（tool_failures.db 九态分类），使进化引擎的
  LLM 调用失败也可审计
  ——周期#61实装：llm_transport 增 _record_failure（lazy import 复用
  agent.llm_engine.record_llm_failure 单一真源，utils 层不持 agent 模块级依赖），
  记账点=重试排程/放弃双接×状态与传输两分支 + 底部链路放弃点 + 防护层两出口
  （自身异常/HTTP 200 空输出——dna"静默返空"历史形态）；传输错误在
  _post_single_link 放弃点已记账、防护层对 TransportError 不重复记（防双行）；
  path 归因线程化（post_chat_completion/complete_with_protection 增 path 参数，
  dna._call_llm 传 path="dna"→tool_name=llm:dna、llm_gateway 传 path="gateway"
  →llm:gateway），strand_id 进 error 行归因到具体 strand；降级链换 link 成功也
  留上游失败审计行（可审计≠只记最终失败）
- [x] 11.4 soulmate-daemon 常驻托管覆盖面真实验收（周期#48 遗留）：实证"后建
  任务/目标也被轮询拾取"（daemon 启动后新建 schedule_task/监控目标在周期内真被
  执行/检查，而非只有启动时存量在跑）
  ——周期#62验收落地（可复跑 acp-proxy/acceptance_daemon_coverage.py，8/8✅）：
  daemon（01:51:53 启动，PID 515550）存活 7.5h 后新建探针 task#11（interval:30s）
  与 target#2（port 127.0.0.1:8090）→ 21s 后监控目标被拾取真实检查（monitor_events
  行 ok"端口 8090 监听中"）、52s 后 cron 任务被拾取并真实 LLM 回合执行成功
  （runs 行 status=ok detail=end_turn + journal daemon 进程 "✅ 执行 #11
  accept114_cron → end_turn" + 真源 "Response [accept114-session-*]: OK"）；
  执行者归因=journal 仅 daemon 进程行且 ps 实证无其他轮询宿主（agent stdio 进程
  不在跑=daemon 独占拾取）；探针产物清理归零（runs/monitor_events/session/
  messages 回读全 0）。代码级佐证：claim_due/_due_targets 每次 poll 重新查库
  （无启动时快照）。⚠️诚实边界：①同机低负载单样本×2轮 ②首轮空 session_id 探针
  触发"拒绝给消失的会话造回合"既有失败纪律（runs 行 failed 可见=失败不假装成功
  语义照常；真实用户路径 schedule_task 恒带当前 session_id 不受影响）③3.1 遗留
  语义"错过不补跑"未变（本次只验覆盖面不改语义）
- [x] 11.5（周期#59 新发现）注入审计误杀良性文档任务：办公类验收回合中
  InfectionGate "[注入审计终止]"打断一轮工具执行（文档生成工具输出目录受限 vs
  任务要求 /tmp/soulmate_accept/report.xlsx 绝对路径被判疑似劫持），产物最终生成
  成功（4962B 有效 xlsx）但回合多走弯路；治理方向=审计判据区分"路径越界"与
  "注入劫持"（越界应回显限制说明，不升级终止）
  ——周期#63 实装（journal 08:20 实录还原真实根因比上述更具体：doc_generate 落
  ~/.hermes/soulmate/documents/report.xlsx（工具结果"✅ 文档已生成"即来源证明），
  助手 cp 到用户指定路径，审计员只看输出文本+待执行调用**看不到工具结果**，把本回合
  刚生成的文件当"来源不明的既有文件"→clarify 反复→超限升级 terminate）：
  ①审计输入附"本回合已执行的工具结果（来源证明）"快照（check() 新参
  prior_tool_results，soulmate_agent 从 messages 工具结果行取≤12条×500B）
  ②JUDGE_PROMPT 补判据第7条（路径越界≠注入劫持+同回合工具产出有来源证明不得称
  "来源不明的既有文件"）③确定性溯源降级 resolve_path_claims——判词来源/路径类质疑
  的每个被质路径以真源自证（用户明文指定 / 本回合工具结果生成语义行）→降级放行+
  留痕（provenance_pass_with_trace，原疑问留痕，**不升级终止**）；任一路径不可自证
  或判词另有外发/篡改类行为指控→保持原判（宁不纠不误放）④docgen 越界回显限制说明
  （请求路径越界时显式说明输出目录受限+解法：basename 生成后按用户指定路径复制，
  不再只有"filename 非法"一句）
### 阶段12 · 下一批切片（周期#64 规划——阶段11 清零+抽样实测 5/5 后的续刀，按优先级）
- [x] 12.1 多渠道出站独立可查痕迹 + 多渠道 verify 升级 server_side（周期#30 遗留
  "wechat 回环出站无独立可查痕迹→待复测" + 11.1 reply_assisted 弱项）：wechat 往返
  出站留确定性审计行（发送方/通道/去重键/时间），验收 harness 多渠道类判据从回复辅证
  改服务端痕迹回读；iLink 真实传输仍待用户拍板不动，只做 loopback/现有通道留痕。
  ——周期#65实装：agent/outbound_audit.py（OutboundAudit：outbound_log 表单一写入源
  =ts/channel/target/kind/status/blocks/sender/dedup_key/text_preview；record 旁路纪律
  写失败只计数不冒泡+recent 过滤回读+clear 拒绝全表清空防误删审计）+wechat_bridge
  三条出站路径留痕（_send_blocks 回复路径 dedup_key=入站msg_id、send_proactive 文本、
  send_media_proactive 媒体，失败态照留 status=failed/blocks=0）+message_router
  openmate 出站留痕（成功 dedup_key=om-rowid/失败照留；wechat 经桥留痕一次单一真源
  不双份记账）+acceptance_benchmark 多渠道 verify 改服务端痕迹回读（channel=wechat+
  target=accept_peer+status=sent+text_preview 含探针文案，空回复也能判）、多渠道进
  SERVER_SIDE、cleanup 预清痕迹探针行（复测铁律②）
- [x] 12.2 运维/调研 verify 升级 server_side（11.1 两类 reply_assisted 弱项）：为两类
  找确定性服务端判据（运维=体检结果落账/审计行，调研=检索调用账本+引用来源行），
  提升"完全替代"判定可信度；不为凑判据造新落库负担，优先复用工具账本/journal 真源。
  ——周期#66实装：真源=opensoul.db trajectory_events（trajectory_recorder 真实回合
  已写入的 tool_call 轨迹账本，复用既有落账零新增落库）——运维=system_checkup 账本行
  result_preview 含 8090+磁盘结论（体检结果落账）、调研=web_search 账本行
  result_preview 含来源 URL（检索调用账本+来源行）；acceptance_benchmark 增
  _tool_ledger 会话级账本回读（report_evidence.parse_ledger 单一真源判执行/拦截、
  session/new 注入会话上下文=新建会话天然免疫陈旧行假阳性、落账异步缺行有界轮询4s、
  三态 ok/missing/unavailable）+verify_ops/verify_research 双判据（账本+回复辅证）
  +账本不可用诚实降级 reply_assisted（禁假称 server_side）；至此 10/10 类全部
  server_side。连带修2真缺口：①周期#59 _read_result_snapshot 改造漏传 db_path+
  硬依赖 created_at 列致 _read_back_reply 既有测试红（temp 库查空），快照读取器
  补 db_path 参数+去 created_at 依赖+回复行间"\n\n"契约还原②8.5c-② 可用性治理
  抽取盲区（事故句"当前会话的工具列表中没有 web_search"容器在前形态漏抽注册类，
  只抽到须执行证明的 availability 类→模型拒不调用即无法证伪→更正声明未触发）：
  report_evidence 增 _REG_LIST_CONTAINER+放宽名字在后形态"会话的"间隔词
- [x] 12.3 开发类回合 301.74s 超时复核归因（周期#64 新发现，用户重视速度）：四段计时
  （LLM 首包/注入审计往返/工具执行/回复回收）+区分上游延迟波动 vs 本地可控耗时+
  多样本（≥3 轮）复测。——周期#67实装：新增 acp-proxy/turn_timing_report.py（单一真源
  =trajectory_events+journal 错误行扫描+未归属空洞检测，可复跑）+补 step 自省 LLM 往返
  落账（`_self_reflector.reflect` 首试/重试两处 segment span——此前静默零行，323.7s
  空洞归因全靠 journal 才定位到）。归因实锤（5 样本禁单样本定论）：①正常开发回合
  34-47s 构成=规划/预步 4.4-8.8s（TaskState+计划 LLM）+工具循环 LLM 19.4-31.8s
  （3-4 轮，首包 5-8s/轮）+step 自省 0.03-9s+工具执行毫秒级+回复回收 0.3-0.4s，
  注入审计仅 1/5 轮触发一次判词（约16s）②事故轮 380.73s 的 323.69s 空洞=step1 自省
  调用上游传输挂死：静默约313s 后 RemoteProtocolError（peer closed connection）→
  llm_transport 重试(1/5) 8s 成功。结论：**触发=上游波动（5 样本 0 复发）、放大=本地
  传输层无 stall 超时（本地可控，挂死约293s 才见错）**→治理并入 12.4；修复落点
  utils/llm_transport.py 为他会话 WIP，单写入者纪律解禁后实施。
- [x] 12.4 单工具链注入审计 LLM 往返开销治理（8.3 诚实边界另案）：确定性预筛先行——
  来源干净（用户明文点名+本回合工具产出可溯源）的工具调用不走 LLM 判词往返，可疑才
  升级 LLM 审计；判据是溯源不是模式白名单（禁"守卫化"变味），防护语义零放松，
  同场景回归测试证明拦截面不缩水。**12.3 并入项**：①LLM 传输 stall 超时（无流停顿
  检测→挂死约293s 才见错，重试本可 8s 恢复；落点 utils/llm_transport.py=WIP 解禁后）
  ②注入审计误杀证据：良性 dev 回合 1/5 次 terminal 被 LLM 判"疑似提示词注入劫持"
  terminate（工具未执行），疑触发=harness 防缓存行"（此行仅为防缓存标记，忽略其语义）"
  措辞像注入指令——确定性预筛正应先放行该类（用户明文点名命令+可溯源），治理时同题回归。
  ——周期#68实装第一刀：agent/audit_prescreen.py（①prescreen_batch 免审判据=用户明文
  **连续原文片段**全覆盖+残渣惰性（仅空白/引号/中文标点；ASCII 路径/shell 语义字符与
  字母数字即升级 LLM 审计——挡"用户写 rm -rf、模型补 /"拼装逃逸；禁逐词拼凑覆盖；
  覆盖算法迭代上限 64 轮保守回落）；**只认 user_text 溯源**，本回合工具结果不作免审
  依据（注入恰藏在工具结果里，零缩水关键负控制）②fold_marker_lines 窄签名
  （"此行/本行 仅/只是+标记/占位/防缓存"）自述占位行从审计输入折叠——12.4② 疑触发
  措辞不再进判词输入，RUN_TAG 保留可审计）+InfectionGate.check 两点接线（启发式签名
  仍在预筛之前=劫持签名零缩水；免审留痕 source=prescreen_user_verbatim+
  prescreen_skipped 计数）。tests/test_audit_prescreen.py 16/16（免审/零缩水负控制/
  标记折叠/接线护栏）+影响面 9 文件 190/190。
  ——周期#69实装①：utils/stall_guard.py 无流停顿检测——asyncio.wait_for 独立
  看门狗按**字节间隙**计时（等响应头+响应体每 chunk 前），不依赖 httpx 超时机制
  （事故实证：响应头已到后 285s 零字节而 read=120 未开火=websockets serve 循环
  超时盲区，对端 FIN 才见 RemoteProtocolError）；间隙>stall（默认 60s）或总时长
  >deadline（默认 240s）→ 本次尝试中止抛 httpx.ReadTimeout（错误契约零变化，
  is_transient/read_timeout 分类与重试环原样接住）→重试恢复（事故"重试 8s 可恢复"）；
  涓流间隙<stall 照常收完整 body=零误杀负控制钉死；SOULMATE_LLM_STALL_TIMEOUT_S/
  _ATTEMPT_DEADLINE_S/_STALL_GUARD 可配。**落点修正（代码+账本双实证）**：事故
  调用是 llm_engine.chat()（tool_failures 行 llm:chat|protocol_error），非初稿
  所写 utils/llm_transport——守卫共享单源**两处都接**（chat() + llm_transport
  ._post_single_link），流式路径另案。连带修复红灯账本：evo 混入 WIP（345440e4）
  record_llm_failure 参数错位（source 当 path、action/attempt 压没→llm:dna 错标
  llm:empty_output、九态全 unknown；纯 HEAD worktree 复测 6 例红=先证非本切片引入）
  →恢复 11.3 逐字段保真转发+撤除无请求身份的传输层幂等去重（不同请求同形失败
  误并=审计丢行，模块级缓存跨测试误抑制"no such table"实证）。tests/
  test_llm_stall_guard.py 16/16 + test_llm_transport_accounting 9/9（6红转绿）
  + 受影响面 359/359。
  **12.4 ③已实装（周期#72）**：免审命中率 stats 实测观察——见下。
  ——周期#70实装②：audit_prescreen 增 _EXEC_VERB_RE 窄名单（python/python2/python3/
  python3.N/node/bash/sh）+residue_is_exec_verb（残渣**恰好单 token**=解释器名才豁免）
  +value_exempt_from_audit（溯源类型 user_verbatim/exec_verb:<tok>）+prescreen_batch_ex
  （source 三态：批次内只要有一个执行动词豁免，整批如实标 prescreen_exec_verb 不冒充
  全明文；prescreen_batch 保持 2 元组薄包装兼容）+InfectionGate.check 接线 source 按
  溯源类型留痕。**零缩水论证+负控制**（测试钉死）：新增豁免面仅"模型自选解释器名"
  一个 token（运行用户点名的脚本文件，无注入语义）；破坏性动词（rm/sudo/curl/awk/
  sed/xargs/env）、flag 逃逸（-c/-m/-e）、链式（;|&&|｜）、重定向 >、多 token、大小写/
  版本形态变体、工具结果溯源、批次内一个脏调用→全部照常升级 LLM 审计；启发式劫持
  签名仍在预筛之前零缩水。tests/test_audit_prescreen.py 24/24（新增 10 例：7 解释器
  正例/破坏动词 7 例/flag链式 8 例/多 token 4 例/工具结果/批次级/启发式/包装兼容）
  + 同域回归 192/192。
  ——周期#72实装③（免审命中率 stats 实测观察，12.4 全部完成）：InfectionGate 增
  prescreen_escalated/llm_audits 计数+stats() 快照（checked/tool_batches/命中率，
  无数据报 None 禁假 100%），两 journal 留痕行（"预筛免审（source）"/"预筛升级审计"）
  带累计命中率、soulmate_agent 免审批次账本留痕（[infection-guard] 伪条目
  permission=infection:prescreen_exempt，parse_ledger 两头不计零缩水测试钉死）+
  可复跑探针 acp-proxy/prescreen_hitrate_report.py（journal+opensoul.db 轨迹账本
  双通道交叉核对，不一致如实报差异）。**真实实测**（3 类真实 WS 验收 3/3✅：
  开发 20.01s/调研 33.43s/办公 55.27s）：接线后干净窗口 6 工具批次命中率 50%
  （免审 3=user_verbatim 2+exec_verb 1，升级审计 3=办公类模型自造 xlsx 内容），
  journal 与账本双通道计数一致（3=3）。实测暴露并修复 1 真缺陷：子进程 stderr 被
  ws_acp 中继双份记账 journal→探针样本翻倍，按内层日志行去重（测试钉死）。
  tests/test_prescreen_stats.py 12/12（计数器/命中率/空批次不进分母/零缩水
  terminate 保留/账本伪条目两头不计/探针正则契约/双份去重/repr 参数解析）
  +同域回归 147/147。
- [x] 12.5 harness 收尾清理 + late_readback 扩测：run 末补 rmtree 工作目录对齐 docstring
  "测试产物显式清理"；ACCEPT_PROMPT_TIMEOUT_S 人为压短构造多类别后到产出场景
  （开发/办公/记忆 3 类），销 10.4 单样本边界 + 11.1 遗留④二次回读路径扩证。
  ——周期#71实装：①cleanup() 末尾 rmtree(WORKDIR)（run 末产物显式清理，setup()
  预清路径幂等不受影响；cleanup 的 agent_messages 探针行清理改走 AGENT_MESSAGES_DB
  模块常量单一真源=测试可隔离不摸真实库）②真实扩测 tag=LR125_1791716400
  ACCEPT_PROMPT_TIMEOUT_S=20 压短 WS 口径，开发/办公/记忆 3 类**全部** WS 超时→
  后到回读 3/3 恢复（ws_timeouts=3 recovered=3，verify_mode=server_side_late×3：
  开发 0.0s/1次产出已就绪、办公 20.0s/5次 report.xlsx 4979B 有效、记忆 5.0s/2次
  MEMORY.md 标记）；run 末 /tmp/soulmate_accept 实证已删除（12.5a 销账）、测试
  产物清理归零（MEMORY 标记 0/accept_probe 行 0）。
- [x] 12.6 轨迹/审计闭环抽查（可观测性，用户重视）：tool_failures.db 抽查探针
  （失败分类行完整性/无孤儿行/llm:dna 归因不串线）+ 11.5 溯源降级真实判词触发观察
  （自然发生时复核留痕行）+ llm:dna 真实降级审计行闭环（周期#61 遗留③）+
  12.3 新增两项：①infection-guard 判词依据不落账（verdict=terminate detail=空
  ——判了什么全靠猜）②verify_dev 判据弱项（只查文件 marker 不查"用 terminal 运行它"，
  S1 被审计误杀的 terminal 步骤仍判 pass）+ 周期#69 新增两项：③tool_failures.db
  ts 存秒非毫秒（违全局时间规范，抽查探针顺带核）④dna_evolution
  ._record_llm_failure_safe 死包装（零调用点，签名与恢复后 _record_failure_safe
  不兼容=接线即 TypeError 地雷，清理或对齐）。
  ——周期#73实装（探针+③④+分类缺口，余①②+11.5观察）：新增 acp-proxy/
  audit_trail_probe.py 可复跑抽查探针（只读六面：分类行完整性=kind/action/
  outcome/attempt 值域核+kind=unknown 缺口如实列、无孤儿行=收尾行须有先行恢复
  尝试行（连接口径实证：尝试行以 step 工具名落行、收尾行以原工具名落行，note
  "换路径: read_file → read_file_segment" 是连接键）+llm action↔outcome 契约配对、
  llm 归因串线=tool_name 必须 llm:<已知path>+llm:dna 行必带 [strand_a|strand_b]、
  llm:dna 闭环（周期#61遗留③）、ts 毫秒/秒混存普查、11.5 溯源留痕自然触发观察）；
  真实库抽查实证 4 类发现：2 归因串线行（WIP 错位历史产物 llm:[Errno -2]…/
  llm:empty_output，历史不改写）、llm:dna DecodingError 4 行落 unknown=分类缺口、
  孤儿 0（口径修正后真无孤儿）、llm:dna 真实降级行 4→6 条=闭环成立。修 3 根因：
  ③record() ts 改毫秒 UTC（存量 133 行秒单位不改写历史，探针普查如实报）④dna 死
  包装对齐（_record_llm_failure_safe 委托 kwargs 与 _record_failure_safe(path,
  source,action,attempt,outcome,detail) 签名不匹配 TypeError 被吞=记账静默丢失
  地雷→对齐委托 path="dna"+strand 归因进 detail+request_id 幂等保留）+classify_
  failure 补九态码串透传（预分类 code 不再落 unknown）与 httpx.RequestError 兜底
  →network_error（DecodingError 非 TransportError 兄弟类，4 行 unknown 缺口根因）；
  顺带修存量测试基建红灯（test_prescreen_stats _run 用 get_event_loop 批内排序
  污染 6 红，纯 HEAD worktree 先证非本切片引入→改 asyncio.run 对齐周期#55 修法）。
  ——周期#74实装①判词依据落账：根因=<terminate/> 是自闭合标签 tag 内无处写原因，
  parse_verdict detail 恒空、evidence 只存 resp 截断（纯标签判词时=裸标签噪声），
  落账侧 message 回退通用兜底文案→「判了什么全靠猜」（真实库实证 33 条拦截行
  全无判词依据）。修三件：①infection_guard.extract_rationale（剥三态 verdict
  标签取自由文本判定依据，空白折叠+400 限长，纯标签如实空）+_llm_audit
  detail=detail or rationale（clarify 标签内文优先不覆盖）+evidence 只留依据
  文本不把裸标签冒充依据②soulmate_agent 非 ok 分支判词依据伪条目落轨迹账本
  （name=[infection-guard]，arguments.verdict_basis≤400+result_preview≤300+
  permission=infection:verdict_basis 独立值不与 infection:{action} 混计数；
  parse_ledger 两头不计=纯可观测；纯标签无依据如实落"判词未附依据（纯标签）"
  不编造）+journal detail=改落依据③JUDGE_PROMPT 要求判 terminate/clarify 必须
  在标签外附一句判定依据（落账供审计追溯）。连带修探针盲区：第6面扫描列误用
  不存在的 arguments_json（真实列 metadata_json）对 [infection-guard] 伪条目
  全盲→_traj_text_columns PRAGMA 实存列容缺；新增第7面 check_verdict_basis
  （落账行/真实依据/诚实标记/空依据三形态正则判空/拦截行历史缺口如实计数）。
  ——周期#75实装②verify_dev 判据弱项（12.6 余项清零）：根因=开发类只查文件
  marker 不查"用 terminal 运行它"，S1 被审计误杀的 terminal 步骤（未执行）仍判
  pass=假过（真实库 15 条 dev_hello 轨迹行实证运行证据本来就在账本里：name=terminal
  +args 引用 dev_hello+result_preview=程序输出）。修=verify_dev 三判据（文件 marker
  +运行账本+回复报告输出）：运行证据=_tool_ledger(("terminal","execute_code"))
  执行行（parse_ledger 单一真源，被拦行不计）args/name/result 含 dev_hello 且
  result_preview 含程序输出 SOULMATE_DEV_OK 且非源码回显（print( 正则——cat/echo
  源码不算运行）；_tool_ledger 扩多名字一次回读+名字容忍"terminal: <cmd>"冒号前缀
  形态（CLI 轨迹行实证）；账本不可用诚实降级文件+回复（mode_note=ledger_unavailable
  不假称 server_side）。
  11.5 观察=探针持续复跑（周期#75 复跑：provenance 0/self_heal 0 未自然触发如实报；
  verdict_basis 落账行 0=修复后尚无新拦截，历史 33 条不改写如实报）。
- [ ] 待用户拍板（不占阶段顺序）：⚡8.6 双链合并取舍、4.1b iLink 接入形态、
  ASR 语音输入多模态路由、P1-01 轨道间接通道（delegate_tasks/group_task/
  schedule_task 委派排程是否随 personal 轨道收缩）+personal 轨道渠道适配默认值
  （wechat/cron 会话是否默认 personal——现默认 coding 不自动切换防破坏无人值守）

### 阶段13 · 下一批切片（周期#76 规划——阶段12 清零+抽样实测 5/5 后的续刀，按优先级）
- [x] 13.1 流式路径 stall 停顿治理（周期#69 诚实边界②"另案"，事故家族最后已知缺口）：
  chat_stream*/chat_stream_with_tools 消费端挂起与非流式同族盲区（12.3 事故轮即
  llm:chat 非流式已由 stall_guard 封死，流式侧未接）。方向=消费侧按 chunk 间隙计时的
  看门狗（复用 send_with_stall_guard 单源语义），契约约束：partial 输出/has_output
  重试规则耦合不动、既有错误文案逐字不动、涓流零误杀负控制必测。
  ——周期#77实装：utils/stall_guard 同源扩面（aiter_with_stall_guard 消费侧 chunk
  间隙看门狗+open_stream_with_stall_guard 流式"等响应头"看门狗+_await_with_stall
  单源计时+stream_deadline_s 流式总兜底默认900s SOULMATE_LLM_STREAM_DEADLINE_S
  ——连续出字属合法长生成，比非流式240s宽松）+llm_engine 四点接线（chat_stream/
  chat_stream_with_tools 各"等响应头+chunk间隙"两点），停顿抛 httpx.ReadTimeout 落进
  既有 except httpx.TransportError——partial 输出/has_output 重试规则与"[LLM响应超时]"
  文案零改动；接线期真实生产实证并修复同族既有缺陷：send_with_stall_guard 收集
  aiter_bytes 已解码字节却保留 content-encoding: gzip 头重建 Response=二次解码
  DecodingError（2026-10-11 16:09 journal：token-plan gzip 响应下 chat() 全部报
  "[LLM错误: Error -3 while decompressing...]"、会话标题被错误文案污染）→改收
  aiter_raw 原始字节与头一致（纯文本假服务器测试未覆盖 gzip 形态，回归钉已补）。
- [x] 13.2 注入审计拦截自然触发端到端实证（周期#74 诚实边界③销账）：构造注入攻击
  prompt 真实 LLM 回合（隔离 session 防污染）→ 实证 terminate+判词依据 verdict_basis
  落账（探针第7面新拦截行有依据）+banner 用户可见报因+工具被拦报告治理负路径（⚡8.2
  诚实边界）真实场景触发。验收=audit_trail_probe 第7面历史缺口不增、新行有依据。
  ——周期#78实证：两段式间接注入（注入检测样本同步SOP，4条样本逐字携带劫持签名，
  签名只在载体文件/工具参数不在用户明文→8.1②豁免不适用）隔离session真实WS用户路径，
  自然升级链 clarify→clarify→terminate(clarify_exhausted)（journal三连实证），
  verdict_basis落账21行全真实依据（第7面历史缺口33→0不增、空依据0），
  🛡️banner带判词依据落库+⚡8.2负路径真实触发（⛔工具被拦截逐条报原因+报告降级），
  样本文件全程未落盘。探针=injection_probe_ws.py（主8断言）+
  injection_probe_turn3/turn4.py（8.2负路径配料），可复跑。
- [x] 13.3 注入审计 LLM 往返开销治理（8.3 期间另案）：12.4 只做了免审面（确定性
  溯源预筛），剩余升级审计仍是**每工具一次 LLM 往返**（3 工具链曾被拉到 5-8 分钟）。
  方向=同回合同签名批次批量审计（一批工具一次判词）或会话内判词复用；先测当前每工具
  审计耗时分布拿基线再动刀，判词语义（JUDGE_PROMPT/verdict_basis 落账）零缩水。
  ——周期#79第一刀（基线埋点+实测，治理刀未动）：infection_guard._llm_audit 每次判词
  往返计时（含 timeout/error/unavailable 失败路径——失败往返最贵）落 stats
  （llm_audit_last/mean/max/total_ms）+journal 行"LLM审计完成 verdict=X 耗时=Nms
  tools=a,b（累计/均值/最大）"+可复跑探针 audit_cost_report.py（分布 count/mean/
  p50/p95/max/min/total+按 verdict+按工具归因，生产者-消费者正则互相咬合）。
  **基线实测**（tag=BASE133_1791727000 开发+办公 2 真实回合）：5 次判词往返/总 18.99s/
  均值 3.8s/p50 2.2s/p95 7.2s/最大 7.2s/全 verdict=ok 零失败零超时；按工具归因
  execute_code 3 次均值 3.5s、document 2 次均值 4.2s；窗口 7 工具批次中 2 免审 5 升级
  （命中率 28.6%）。**基线结论**：单次往返均值 3.8s（远低于 12.3 事故期 16s），开销
  主因=**次数**而非单次时长（开发链 3 次审计≈10.6s/35s、办公 2 次≈8.4s/58.6s≈15-30%
  回合时长）；批内多工具已是一次判词（"一批工具一次判词"已是现状，链级多次=模型一轮
  一工具的按轮近似）。治理刀优先级（下拍按此动刀）：①免审面继续扩展（每免一批省一次
  往返≈3.8s，确定性零语义风险）②会话内判词复用须先测同签名复现率（clarify 重试链是
  候选命中场景；注意 prior_tool_results 逐轮增长会让精确输入复用命中率≈0，需按
  （user_text+output+tool_calls）签名复用并接受 provenance 上下文不进 key 的取舍）
  ③审计输入瘦身需补 chars 维度相关性数据再动（判词语义零缩水约束）。
  ——周期#80 治理刀（零语义风险面全做完，含 1 处实证缺陷根因修复）：
  **① 免审面扩展（数字算子）**：audit_prescreen 增纯 ASCII 数字 token 残渣免审
  （_NUMERIC_RESIDUE_RE 窄形态：仅 [0-9] token 空白分隔；0x/-/./,/e/全角/链式
  混排一律照常升级——组装逃逸负控制钉死，"0x"+"4141" 拼装被 _MIN_SPAN(4) 挡），
  source=prescreen_numeric 不冒充全明文。**真实路径实效**（WS 回合 GOV133NUM2）：
  `sleep 3` 批免审零 LLM 往返（修复前同形态升级+2549ms 审计）。
  **实证缺陷根因修复（同轮探针暴露）**：fold_marker_lines 旧 `^.*短语.*$` 整行
  折叠——标记短语内联在同一行真实请求后时把整个用户请求吞掉（溯源依据丢失→
  'sleep 3' 整值假升级+判词员看不到用户请求）→ 改标记**子句级**折叠（含短语的
  括号组整体/无括号折到句末标点），同行真实请求原样送审；残余边界=自述短语后
  无句末边界的同句内容仍随子句折叠（与旧行为一致不放宽，如实记录）。
  **② 同签名复现率实测（判词复用动刀前数据）**：sig=(user_text,output,tool_calls)
  claims 三元组 / full=+prior_tool_results 双口径埋点落 journal，真实 4 回合
  （开发/办公/数字探针×2）4 样本 **两口径复现率均 0%**（含 output_text 的严口径
  需输出逐字相同才命中——自然回合几乎不发生；13.2 clarify 重试链（/tmp/.txt ×4）
  是已知候选命中场景但先于签名埋点）。**结论**：②零取舍口径（full，含 provenance）
  实测无收益；有收益口径（sig，provenance 不进 key）是安全取舍（理论上漏"同 claims
  但新工具结果揭示来源为注入"的场景）→ **不擅动，待用户拍板**。
  **③ chars 相关性实测（瘦身动刀前数据）**：4 样本 chars 154-1031、单次 2.5-6.7s
  （总 20.08s/均值 5.0s），pearson=0.452（n=4；3 样本子集为 -0.483——小样本正负号
  翻转=无稳定相关）——本窗口审计耗时**非输入长度驱动**（chars=365 的 document
  审计 6.7s 最慢、chars=154 的 terminal 2.5s），瘦身预期收益低，动刀暂缓。
  ⏳待用户拍板（安全取舍，不擅动）：②判词复用是否接受"provenance 不进 key"
  取舍（收益面=clarify 重试链等同 claims 复发场景，风险面=同 claims 但新工具
  结果揭示来源为注入的理论漏检；零取舍口径已实测无收益）。13.3 主体（零语义
  风险治理面+两项动刀前测量）已完成勾选。
- [x] 13.4 AI 群组真实成员端到端（"后续另案"清单遗留，用户重视 AI 群组）：多 agent
  真实成员（非 loopback 模拟）群组任务链式执行+每步回群聊的端到端验收探针，
  对齐 references/ai-groups-e2e.md 验收行。
  ——周期#81 实施：验收行对齐暴露并根因修复 1 真缺口——**GROUP_MSG_PUSH 死常量**
  （acp-proxy 无 /internal/group-message 端点），任务链"每步结果回群聊"只落
  discussion_messages + task_progress 时间线帧，而聊天流前端只认 agent_message 帧
  （ai-groups-store case 'agent_message'）→ 群里实时看不到 Agent 回复（用户历史投诉
  "群里永远没有 Agent 回复"残留形态）。修复=ws_group 新增 POST /internal/group-message
  （只广播 agent_message 帧**不落库**=落库以 _notify_group_chat 直写为单一真源防双写、
  仅 loopback、persist=true 可选落库）+ _notify_group_chat 真实消费 GROUP_MSG_PUSH
  （旁路纪律失败只记日志）+ ai-groups-e2e.md 契约行同步。可复跑探针
  acp-proxy/ai_groups_acceptance_probe.py（A1-A7）+ ai_groups_ui_probe.py（A8 CDP）。
  真实验收 8/8 行全过（tag=ACC134-1791715968）：3 真实成员 hermes/soulmate/mimo 建组→
  双 WS 客户端→下任务 4 子任务链式执行 144s 全 scored（依次 hermes→soulmate→mimo→
  hermes 真身，无手动 execute）→ A4 行帧同源 8/8（4 agent_reply 行 × 2 WS 客户端，
  帧内容=行内容前缀=修复生效实证）→ A5 source=soulmate-agent 零冒名+auto_score 5.75+
  capability 4 行落库 → A6 双 WS 互见 user_message → A7 讨论 agent_message 流 4 帧 →
  A8 UI DOM 含建组入口/真实群组名/3 Agent/四子步骤/Send to @All。
- [x] 13.5 技能进化语义改写（6.3 诚实边界）：步骤/触发词 LLM 辅助改写+内容保真校验
  （改写后步骤/触发词/描述零丢失断言已有基建），防 LLM 改坏技能——若涉及语义取舍
  需用户拍板则只做确定性部分。
  ——周期#82 实装 agent/skill_rewrite.py（SkillRewriter：LLM 只允许步骤措辞改写
  +触发词增补两件事；**确定性零丢失保真门** fidelity_check=①步骤数量一致（合并/
  拆分=取舍→拒绝"需用户拍板"）②每步语义载体 token（引号/反引号/路径/flag/数字/
  常量/扩展名 hard_tokens 确定性提取）原样保留③触发词只增不删（删除=取舍→拒绝）
  且新增项过 is_injectable_trigger+40字上限④写回后**回读复验**（load_skill→
  parse_sections 重走保真门+坑/验证逐字比对），不过则还原原文报拒绝——产出用
  不同路径回读验证不信生成自报；LLM 不可用/超时/输出不可解析/空提案全态显式报因
  禁编造）+SkillEvolver.evolve_semantics 阶段（频控：同技能 rewrite_min_interval_s
  默认24h+每回合≤max_rewrite_per_turn=1 次真实 LLM 尝试、门禁跳过不占名额；
  审计落 skill_evolution.db 只记 rewritten/rejected 真实行；name/description/坑/
  验证逐字透传=取舍类不在此通道）+soulmate_agent 回合收尾钩子接线（旁路纪律失败
  只记日志不阻塞，llm=llm_engine.chat 单一传输真源）。
- [ ] 例行观察（不占阶段顺序）：长窗口免审命中率（prescreen_hitrate_report.py）+
  审计闭环（audit_trail_probe.py）探针每若干周期复跑留痕；11.5 溯源降级留痕
  （provenance/self_heal/verdict_basis）自然触发后复核。

### 阶段14 · 下一批切片（周期#83 规划——例行观察+抽样 5/5 后的续刀，按优先级）
- [x] 14.1 code_mode 挂死脚本进程级隔离（subprocess）：周期#83 只做了残留线程回收
  （宽限期到点注入退出异常），**吞异常死循环脚本（while+try/except）仍持 GIL 楔死
  同进程**——真隔离=脚本执行搬子进程（browser_worker 同款 JSONL/子进程模式），
  GIL 面彻底消失；顺带评估单次调用/批级超时语义在子进程下的等价迁移。
  ——周期#84实装：新增 agent/code_mode_worker.py（子进程执行体，JSONL 协议：
  tool_call 跨进程请求内层工具（父进程 dispatch=permission gate 不绕过）、
  call_log/`result` 赋值/print 即时流式快照（被杀时部分产物不丢）、受限 builtins
  与父进程单一真源 import）+code_mode.py execute() 路由（默认 process=_execute_in_
  process：批级超时=kill 进程组 start_new_session+killpg 连带清子进程、单次调用
  超时父进程判定随结果回传 call_timeout 标记、宽限 0.5s 在途收尾、子进程崩溃如实
  报退出码+stderr 尾；SOULMATE_CODE_MODE_ISOLATION=thread 回退旧线程模式代码路径
  保留含 #83 回收机制）。语义等价迁移评估结论：单次调用超时/调用上限/参数校验/
  异常可见/result 优先 stdout 回落/block 标记逐字等价；两点差异如实记录——
  ①dispatch 超时后协程从"泄漏后台"变为被取消（更干净）②批超时宽限 0.5s 内子进程
  未收尾即 kill（线程模式是留自旋等回收，子进程模式更强）。真实验收：吞异常死循环
  （while+try/except）批超时期间父进程 CPU 任务 0.0x 秒未被拖慢（对照 #83 线程模式
  ~100 倍变慢）、无 code_mode_worker 残留进程
- [x] 14.2 慢回合归因扩样（12.3 turn_timing_report 同款四段计时）：周期#83 抽样
  记忆类 300s WS 超时（后到回读恢复）+开发类 127.13s（vs 常态 23-45s）单样本
  波动待归因；19:33 真实 llm:chat 触发 stall guard 240s 总预算兜底（retry 恢复，
  错误契约按设计工作）——是上游慢还是本地段慢，≥3 样本禁单样本定论。
  ——周期#85归因扩样 9 样本（开发 4：380.73/125.86/22.13/39.75s；记忆 5：330.39/
  233.39/106.25/24.74/54.34s；后两个=RETEST_1791722853 新鲜 WS 用户路径 2/2✅），
  **结论：上游 LLM 为主因，本地段无卡死**——①慢样本 77-96% 时长在 LLM 往返/传输
  等待（om-25b400c46a99 单空洞 323.69s=RemoteProtocolError peer closed 传输挂起
  journal 10:19:49 才重试恢复；om-1b090d627ba3 step_reflect 单轮 280.75s 撞 240s
  stall guard 预算；om-d687c9794104 chat_turn 211.22s/15 轮 llm 208.87s+解压错误
  重试风暴）②本地段小且稳定：prompt 前置实测 0.89s（journal 19:28:29.431 [prompt]
  CALLED→30.322 _run_llm_with_tools starting）、工具执行 ms 级、回复回收 0.36-0.85s
  ③"⑤未归属空洞"首段 6-32s（9/9 样本都有）实为**未落账 LLM 往返**（planner 任务
  拆解/意图分类/标题生成）——journal 实证请求在回合开始 ~1s 内已发出，等待=上游
  延迟非本地准备（时长随上游波动 6.04s↔32.03s 同形态）。附带观察：httpx.DecodingError
  "incorrect header check" 解压错误间歇高发（7 历史样本 4 命中成簇）且错误文本污染
  会话标题（"Session title generated: … -> [LLM错误: …]"）；step_commit 对 /tmp
  验收目标恒报"路径越界拒绝"（2.3 越界拒绝设计使然，/tmp 产物不进 git 步骤=噪音
  非缺陷）。证据：/tmp/turn_timing_142.json + /tmp/turn_timing_142_fresh.json
- [x] 14.4 落账补洞（14.2 仪表洞根治）：planner 任务拆解/意图分类/标题生成/注入
  审计/自检等 LLM 往返进 trajectory_events——当前四段归因靠 journal 事后解释空洞，
  补洞后"空洞≈0"才是归因仪表完备的验收行。
  ——周期#86实装：trajectory_recorder.record_aux_llm_call（"[aux] {label}"前缀行，
  metadata.kind=aux_llm，与主轮同口径=llm_call 无 end_at 计时覆盖；parent 未知挂
  根层不伪造父子；never-raise）+soulmate_agent._aux_llm_chat 统一落账口（计时+
  status=llm_outcome_status 判定、语义面与 llm_engine.chat 逐字等价、落账失败绝不
  阻塞、无ctx只跳过不伪造session）+_AUX_TRAJ_CTX 可变dict上下文（fire-and-forget
  标题任务先于回合根span创建也拿到后补 root_id——纯ContextVar值做不到）；五个
  调用点全部收口（_llm_plan_call=plan/replan/reflect 任务拆解与自省按消息面特征词
  确定性归类、_judge_task_continuation_llm、_infection_llm_call 注入审计判词、
  _generate_session_title、skill_sem_rewrite 技能语义改写）+turn_timing_report
  ②b 辅助LLM往返与主轮分列（llm_total_s 不被污染）且计入时间覆盖。实测面核对：
  意图分类（intent_classifier.classify）/自检（self_verify）/上下文压缩
  （context_compressor.compress）经查代码为确定性无LLM往返（路线图口径按实况
  收敛，不虚报缺口）。真实验收：RETEST_1791724754 两个新鲜 WS 用户路径
  （om-066eef733bd5 开发/om-1e8b1ffbfe62 记忆）**⑤未归属空洞=0s（0 段）双会话
  达成**（14.2 期首段空洞 6-32s 9/9 样本形态清零）；aux 行首次给出精确拆分——
  开发 79.36s/3 次（plan 35.13s+title 40.37s+reflect 3.86s）、记忆 67.32s/4 次
  （plan 12.73s+title 16.18s+reflect 21.69s+16.72s），实证 14.2 推断（首段空洞=
  未落账 LLM 往返且等待在上游）并量化：标题生成/规划单次可达 35-40s。
- [x] 14.5 上游传输错误韧性（14.2 附带观察）：httpx.DecodingError（incorrect header
  check）解压错误——核实 llm_engine 重试链是否覆盖该异常类/或请求禁透明压缩；
  错误文本不再进会话标题（"[LLM错误: …]"污染）。
  ——周期#87实装（核实结论：重试链**此前不覆盖**——DecodingError 是 httpx.RequestError
  族非 TransportError 兄弟类，四个 `except httpx.TransportError` 重试 catch 面全部
  漏掉它→一次坏压缩体即落外层 except 零重试直接"[LLM错误: …]"）。修三件：
  ①请求禁透明压缩（根因消除）——llm_engine 五处 client 构造（_make_client/
  chat_stream/chat_stream_with_tools×2/chat）+model_fallback.link.headers()（_apply_link
  整体替换 headers 漏了即降级链压缩复活）+llm_transport._post_single_link 发送面
  统一 `Accept-Encoding: identity`（LLM 响应体小，gzip 透明解码=DecodingError 温床，
  不请求压缩则该错误类无从发生；调用方显式值优先）②重试链覆盖 DecodingError——
  is_transient_error 认 DecodingError（重发新响应可自愈=瞬时）+llm_engine 三路
  与 llm_transport 的 catch 面扩为 `(httpx.TransportError, httpx.DecodingError)`
  （既有 plan_error_retry/has_output/降级链/记账语义零变化）③错误文本禁进会话
  标题——utils.llm_retry.is_llm_error_text（失败契约前缀判定单一真源）+
  _generate_session_title 检出"[LLM错误…]/[LLM响应超时]"即保留 user_text[:30]
  临时标题不写库不同步（14.2 实证污染形态销账）。真实验收：WS 用户路径开发类
  1/1✅ 31.42s verify_dev 三判据命中，journal 实证"Session title generated: … ->
  创建并运行测试脚本文件"干净标题+全窗口零 DecodingError（真实上游对 identity
  编码正常响应=禁压缩无副作用实证）。
- [ ] 14.3 例行观察持续（周期#88 复跑更新）：24h 窗口免审命中率 **62.9%**（44 免审/
  26 升级/70 批，双通道 44 vs 38 边界差异如实报；来源 user_verbatim 31+exec_verb 12+
  numeric 1）、审计往返 14 次均值 3.84s p50 2.65s p95 6.72s、同签名复现率 **0%**
  （9 样本 sig/full 两口径）、chars↔耗时 pearson 0.391（n=9 仍无稳定相关）——
  长窗口继续探针留痕，长尾 p99/复现率样本量攒够再评 13.3② 判词复用取舍
  （仍待用户拍板）。
- [x] 存量缺陷（周期#86 发现）：test_trajectory_rounds::
  test_infection_terminate_early_return_still_records_round 断言未跟上 verdict_basis
  账本条目（round tools 1→2）——**周期#88 修钉**（verdict_basis 条目=周期#74 设计行为，
  断言跟上新账本形状+纯标签诚实标记钉死+配对判词形态伴生用例）。
- [ ] ⏳ 待用户拍板后落地：⚡8.6 双链合并、4.1b iLink 形态、ASR 语音输入、
  13.3② provenance 取舍、13.2 签名误伤豁免语义。

### 阶段15 · 下一批切片（周期#89 规划——路线图切片清零后按遗留实证排序）
- [x] 15.1 调度常驻托管（3.1 遗留销账）：常驻宿主部分已由周期#48 交付
  （soulmate_daemon.py + systemd --user soulmate-daemon.service 无条件托管
  run_forever，e2e"无agent进程也到点执行"已实证）；周期#90 销剩余缺口=
  **错过留痕**：补跑1次之外溜掉的执行点显式落 runs 表 status='missed'
  （_missed_slots 确定性枚举 + 封顶100截断自报 + missed 不计 fail_streak +
  schedule_list ⏭️ 不冒充❌），"错过不补跑"（不积压重放）语义保持不变。
  验收已过：杀掉全部 agent 进程期间到点任务 → daemon 独立 claim+fire
  （runs ok 行+真实LLM回合）+ 📊汇报行落库（turn_origin=cron_report）
  + missed 行"错过10个执行点（22:53~23:02）"真实落库+journal⏭️行，
  轻微迟到/到点执行零 missed 噪音（真实库双形态实证）。
- [x] 15.2 工具执行时长落账（14.4 诚实边界③销账）：trajectory_events 全库 542 行
  tool_call 事件 duration_ms **全为 0.0**（周期#89 真实库实证）——工具执行段在
  四段归因（12.3/14.2）里是盲段。修=工具执行点计时进 all_tool_calls 账本条目
  → record_llm_call 透传落 tool_call 事件 duration_ms → turn_timing_report 增
  "工具执行段"独立列，⑤未归属空洞进一步收窄。
  ——周期#91实装：soulmate_agent 三落账点执行计时（主执行路径/缓存命中路径
  `(time.time()-tool_start)*1000` + batch 批内子调用 `_e['duration_ms']` 自身时长）
  + trajectory_recorder._tool_child_events duration_ms 透传（record_llm_call/
  record_round 两批共用单一真源；旧条目缺省 0 不改写历史）+ turn_timing_report
  ③工具执行段独立列（tool_exec_s/tool_exec_calls/tool_exec_metered；batch: 批内
  子调用含于 batch_execute 行、汇总不重复计明细独立可见；工具计时跨度计入空洞
  覆盖→⑤收窄；未计时行按行数诚实标注）。真实验收：WS 用户路径 调研+发布 2/2✅
  （web_search duration_ms=20092 真实库行、message_send=52ms、[infection-guard]
  伪条目如实 0），turn_timing_report 双会话四段拆分 ③独立列 20.09s/0.05s 可见、
  ⑤空洞 0s。
- [x] 15.3 aux LLM 延迟治理（14.4 量化：标题生成/规划单次 35-40s）：先归因哪些
  aux 在首字回复关键路径上（标题=fire-and-forget 不阻塞？规划=串行前置？），关键
  路径 aux 做 prompt 瘦身+短超时回退（失败走既有 14.5 临时标题/确定性规划回退），
  0.2 首条消息提速成果不回退。验收：首字回复时间对比+aux 计时账本不变差。
  ——周期#92实装（归因+治理两刀；真实首字对比待下拍验收轮转补测后勾选）：
  **归因实证**（trajectory_events aux_llm 真实库30行）：title 9行均值17.1s/峰值
  62.6s 但 fire-and-forget（create_task）**不在**首字关键路径；plan 11行均值11.2s/
  峰值35.1s **串行挡在首字前**（每条消息一次，旧顺序 TaskPlanner.plan 先 LLM 规划
  后被 decomposer 覆盖丢弃=纯浪费）；task_judge 条件串行（规则不明时）；reflect
  10.2s/infection_audit 5.0s 在执行段非首字。prompt 瘦身经归因**不需要**（标题小
  prompt 同形态均值17s→TTFT 主导，体积非变量）。治理：①pre-gate 前移
  （task_engine.plan 多文件确定性拆步先于 LLM 规划——decomposer 结果本就覆盖
  LLM 计划，前移后多文件目标完全跳过规划往返，语义面不变）②关键路径短预算
  （_aux_llm_chat label∈{plan,task_judge} 走 SOULMATE_AUX_CRITICAL_BUDGET_S
  默认12s/<=0关闭，到点 TimeoutError→调用点既有确定性回退（plan"简单任务"、
  task_judge"new"），账本如实 status="timeout"；title/reflect/replan/
  infection_audit 非关键路径不设限零语义变化）；0.2 提速成果不回退（prefetch 零
  改动）。存量修钉：test_no_silent_truncation [:3000] 守护 vs 周期#86
  _aux_llm_label 取样两车道相撞（HEAD 已挂非本刀引入）→ 取样上限命名常量
  _AUX_LABEL_SAMPLE_CHARS 显式化（非数据截断，守护不弱化）。
  ——周期#93 验收补测（真实 WS 用户路径 probe_first_token_153.py，4/4✅勾选）：
  MF 多文件目标（a.py+b.py）aux 账本 **[aux] plan 行=0**（pre-gate 完全跳过规划
  往返），首个用户可见输出 0.59s=📋任务拆解确定性推送（旧形态此处被 plan 往返
  11.2s均值/35.1s峰值挡首字）；SF 单文件任务 [aux] plan=2.48s status=ok（≤12s
  预算帽，本例 LLM 快未触发超时回退）+首字 16.9s/全程 18.4s end_turn；服务端
  副作用实证 a.py/b.py/c.py 三个文件 FIXED 标记真实落盘；aux 账本不变差
  （新行 plan 2.48s vs 基线均值 11.2s、title 12.5s/reflect 11.0s/infection_audit
  4.4s 均≈基线不劣化、全部 status 诚实∈{ok,timeout,error} 零超预算记 ok）；
  0.2 提速不回退（MF 首个可见输出 0.59s 远快于基线 6.00s 口径）。诚实边界：
  MF 首帧=任务拆解推送（用户可见但非最终回复首字，主回复首字未单独计时），
  SF 首字 16.9s 含工具执行轮非纯 LLM TTFT；超时回退真实触发仍未实测（单测
  覆盖，需造慢 LLM），title 峰值 62.6s 后台任务按设计不设限。
- [x] 15.4 工作区遗留清理（开发期数据删前备份铁律）：untracked 杂物归位——根目录
  游离 chat-client.tsx（964 行≠真实 src/app/(app)/chat/chat-client.tsx 2900 行，
  疑旧拷贝）、agent/tool_infra.py（零引用死模块，周期#82 起未动未提交）、
  fault_degradation_*（10-06 旧产物）。备份到 devlog 附件后删除或补齐接线。
  ——周期#94 实装：4 文件字节级备份至
  docs/devlog/attachments/2026-10-12-workspace-cleanup-154/（sha256 双侧一致后
  删原文件；root-chat-client.tsx=964行旧拷贝经 diff 实证与真实客户端无共同结构，
  tool_infra.py=零引用死模块（docstring 所称 image_generator 等模块实名
  image_tools/docgen_tools/tts_tools 且各自有 MEDIA 实现，接线=纯重构零用户价值
  →按"删除"分支处置），fault_degradation_test.py+results.json=10-06 旧探针产物
  零引用）+周期#90 顺延项一并处置：cron_tasks.db disabled 遗留探针任务 #2
  wd_probe_1791631（周期#33 产物）备份 JSON 后删除（tasks 表清零，8 行 runs 历史
  审计行不改写）；清理后 grep 零引用+docgen/image/tts 64 测试全过（被删模块无
  被依赖实证）；未动非本清单 untracked（docs/agent-architecture/*-wechat-preview.html
  ×2=他线产物、src/app/api/trace/=已注册路由 trace 信标）。
- [ ] 15.5 flaky 断言观察（周期#87②遗留）：test_llm_stall_guard::
  test_head_stall_aborts_fast 3.3s vs 3.0s 时序边际——持续复现才放宽上限，
  不预防性改。——周期#94 观察：该测试已更名为
  TestStallAbortAndRecover::test_head_stall_aborts_fast_and_retry_recovers（周期
  #87 断言随规格更新），单跑 5/5 过（call 1.25-1.27s）+32 例批跑过（1.26s），
  3.3s 形态未复现→按约不放宽上限，持续观察。——周期#95 观察：单跑 3/3 过
  （call 1.31-1.50s）+32 例批跑过（stall_guard 17+stream_stall 15），仍未复现，
  上限维持 3.0s 不动，继续长窗口观察。——周期#131 观察（第二十六轮）：单跑 1/1
  过（call 1.48s）+32 例批跑过（18.69s），仍未复现，上限维持 3.0s 不动。
- [x] 验收对标轮转例行（15 区内）：每周期抽 2 类轮转覆盖 10 类全集（周期#89
  调试+运维 2/2✅、周期#91 调研+发布 2/2✅、周期#93 开发+办公 2/2✅（开发
  22.46s dev_hello.py+运行账本命中、办公 38.03s report.xlsx 4978字节有效）、
  周期#94 定时+多渠道 2/2✅（定时 83.65s accept_probe 行=1、多渠道 162.54s
  出站审计行 wechat→accept_peer 含探针文案）、周期#95 记忆+协作 2/2✅（记忆
  53.03s MEMORY.md 含 ACCEPT_MEM_MARK_9、协作 61.54s 双worker产出+subagent_runs
  近8行=8，双双 end_turn verify_mode=server_side）——**本轮转全集 10/10 收官**
  （周期#89→#95 五周期累计），14.3 三探针手法沿用。后续复跑按需：新功能落地
  后跑对应类别/用户要求时跑全量，不搞无目标例行空转。

### 阶段16 · 下一批切片（周期#95 规划——阶段15 清零+轮转 10/10 收官后的续批）
现实盘点：路线图**可自主执行的开发切片已全部清零**，剩余未勾项只有两类——
"待用户拍板"（5+1 项）与"长窗口例行观察"（15.5/14.3/11.5）。本批按"给用户
决策材料优先、质量观察收尾、无反馈不空转"排序：
- [x] 16.1 待拍板项决策包（一次给齐，供用户拍板；每项=现状/选项对比/建议/
  风险，落 docs/devlog/ 决策包文档+路线图摘要，不再零散埋在周期日志里）：
  ⚡8.6 双链合并取舍、4.1b iLink 接入形态、ASR 语音输入多模态路由、
  13.3② 判词 provenance 取舍（零取舍口径已实测无收益）、13.2 签名误伤豁免语义
  （含 base64 绕过面，安全取舍）、1.5 画像存量清理确认（64 条嫌疑待用户说清）
  ——周期#96实装：决策包落 docs/devlog/2026-10-12-decision-package-161.md
  （6 项各含现状实证/选项对比表/建议/风险+文末拍板清单）。本周期新实证：
  ①8.6 形势已变——merge-base 3d81e051 起 evo/exp-1791613018 冻结 13 提交
  （#23-#31 时代）而 main 前进 99 提交，周期#31"以 evo 链为主干"建议过时
  （main #35-39 已独立重做 6.x/7.x），建议改 A 归档封存（或 C 移植 evo 独有
  探针后归档）；②1.5 存量数字更新——真实库实测 14 条/9 嫌疑（"68/64"为
  周期#6 旧时点已作废），嫌疑聚类=key截断生成×9+否定前缀丢失语义反转×8；
  ③ASR 依赖实测 faster-whisper 仅在 hermes venv（opensoul venv
  ModuleNotFoundError）。六项建议=A(C)/A/C/A/A+C/A，等用户逐项拍板后落地
- [x] 16.2 例行观察趋势汇总（14.3 三探针长窗口数据成趋势行：免审命中率/
  审计往返耗时分布/同签名复现率，跨周期对比给用户看质量趋势；探针照常复跑
  留数据，汇总只读不改产品代码）——周期#97实装：三探针复跑+跨周期趋势汇总
  落 docs/devlog/2026-10-12-observation-trend-162.md（4 趋势表+一屏结论）。
  趋势读数：①免审命中率稳态 55~63%（#72 50%→#83 55.1%→#88 62.9%→#97
  60.2%，样本 6→98 批）②审计往返均值稳定 3.8~4.2s/p95 6.7~8.2s 累计 56 次
  零失败，chars↔耗时 pearson 0.252 仍无稳定相关（瘦身维持不做）③同签名复现率
  4 窗口 0%（当前 22 样本 sig/full 双口径 0/22）→ **强化 16.1④ 判词复用建议 A
  （不启用）**④闭环质量全绿：孤儿 0/verdict_basis 21 行零缺口/分类缺口零新增
  （9 unknown 与 #88 边界清单逐一致）/llm:dna 闭环 12 行；11.5/8.5c-③/13.2
  自然触发仍 0 行如实报。15.5 flaky 第三轮未复现（单跑 3/3+批跑 32 过）
- [ ] 16.3 候选·用户实测反馈优先（用户实际使用暴露的缺陷优先补）：无反馈时
  维持例行观察+决策包跟进，不造无依据新需求（"用户问A别研究B"纪律）
  ——周期#98首轮反馈挖掘：opensoul.db agent_messages 近窗口全为验收探针会话
  （session_source=test/ai-group-task-chain/cron）、wechat_bridge.db 0 行、
  tool_failures.db 近行全为 s1 测试会话合成参数、ai_groups discussion 全为
  agent 讨论——**零真实用户反馈**，按兜底转决策包跟进（16.1③"待确认"查实，
  见周期#98日志）。后续每周期先挖一遍这些触点，有真实反馈即优先补

### 阶段17 · 语音输入与多模态（周期#99 立项——16.1③可行性查实后按用户原要求落地）
- [x] 17.1 音频输入多模态直路由（后端链路）：用户原要求"语音直接路由多模态大模型，
  不走 whisper→文本两步"（16.1③A/C 共同前半段，兜底层归拍板项）——
  llm_engine.attach_audio_parts（音频附件→OpenAI input_audio 内容块直发；锚点定位=
  用户文本"[附件已保存到: path]"注记，找不到绝不乱挂，fallback_last 供辅助提示词形态；
  mime→format 映射 MP3/WAV/FLAC/M4A/OGG，未映射/空数据显式跳过报因）+soulmate_agent
  提取/落盘/_TURN_AUDIO 回合上下文（可变list同 _AUX_TRAJ_CTX 语义，子代理 worker 走
  完整 prompt 回合自动覆盖=会话间音频绝不窜台）+主链路 _run_llm_with_tools 每轮请求
  挂回（请求无状态，工具多轮间音频随轮携带）+_aux_llm_chat 随行（plan/task_judge/
  reflect/title 自动带上，infection_audit 显式排除=审计员不掺音频）+纯语音临时标题
  🎤 语音消息+引导语（实证补丁：无引导时模型去 bash 读文件不用"听"到的内容）；
  **reasoning→content 回退**（provider 实测坑：MiMo 音频回合偶发答案只写
  reasoning_content、content 留空，旧消费面直接丢弃=用户看空回复——chat_stream/
  chat_stream_with_tools/chat 三路收口：content 空且无 tool_calls 才回退，content
  正常不掺、工具轮不掺）
- [x] 17.2 前端语音输入（录音按钮+MediaRecorder→audio ACP 块，走 ws_acp 透传；
  附件历史渲染 audio 卡片）——chat-client.tsx 四接线点：sendAcpPrompt audio ACP 块
  （{type:'audio',data,mimeType} 与 17.1 后端 wire 契约逐字一致）+录音状态机
  （MediaRecorder 开始/停止→音频附件，麦克风权限拒绝/无设备/空录音/转码失败显式报因）
  +消息视图 audio 卡片（与文件视图音频卡同布局：日期在上/信息/播放器/按钮在下，3e 铁律）
  +附件 chip 图标/fileAttachments 收集/loadHistory mime→type 映射的 audio 分支；
  smart-prompt.tsx 麦克风按钮接线（原为无 onClick 占位按钮）+录音红态+秒计时；
  新增 src/lib/audio-record.ts（录音 MIME 协商+统一转 16-bit PCM 单声道 WAV——
  浏览器录音格式 webm/ogg/mp4 各异而 MiMo input_audio 只认 MP3/WAV/FLAC/M4A/OGG，
  转 WAV 后格式永远可直发；转换失败诚实回退原格式附着+显式提示，禁假称转换成功）；
  顺带根治存量 build 红灯（周期#94 提交的 docs/devlog 备份 root-chat-client.tsx 为截断
  .tsx 被 tsconfig `**/*.tsx` 卷进类型检查→npm build 必红，exclude 加 "docs" 根治，
  备份文件本体不动）
- [ ] 17.3 候选·16.1③兜底层（whisper/mimo-v2.5-asr 兜底）待用户拍板后落地

### 阶段18 · 部署与语音实测（周期#101 规划——17.1/17.2/8.5c-②b 部署缺口+语音真机验证）
- [x] 18.1 归位部署+真实 WS audio e2e（周期#102）：evo 安全窗口（documented API
  POST /api/evolution/config evolution_interval=86400 双实例暂停）→共享树 checkout main
  归位（88932dc1，evo 两分支实验提交留 evo/exp-* 不丢）→npm build 全绿→重启
  openmate-web+acp-proxy 双实例（部署缺口 3 提交 17.1/17.2/8.5c-②b 全量上线）→
  **真实 WS audio e2e PASS**（edge-tts 语音样本"今天下午三点我们去公园放风筝，记得
  带上橘子和香蕉"→模型"听感作答"逐字复述✅无 bash 读文件，20.3s；wire 实证
  input_audio×4+journal[MSG]音频块+attachments 落库）；16.3 反馈挖掘零反馈维持；
  拒做补做自然触发采样（部署后调研类 1/1 ✅63.18s 无自然拒做=复发率观察待多轮采样）
- [x] 18.1b 自验锚点噪音修复（18.1 e2e 实证缺陷当拍即修）：self_verify.extract_requirements
  剥系统附件锚点注记 _ANCHOR_NOTE_RE（"[附件已保存到:…]"/"[文件/图片已保存到:…]"/
  "[语音附件已随消息直发：…]"族）——管道元信息被拆成需求项必然 unmet→每条带附件
  消息必触发补做+"覆盖 0/N"噪音横幅（e2e 实测三项全"未覆盖"其中两项是锚点）；
  纯语音消息剥后无正文=闲聊不自验零噪音；修复后复测 items 3→1 实证
- [ ] 18.2 录音→发送→听感作答真机全链（需用户浏览器麦克风+真实会话，待用户实测配合）
- [x] 18.3 文本+音频消息覆盖率假阴性（18.1 复测残留，周期#103 当拍即修）：探针式
  元指令"复述你听到的内容"被答对仍判未覆盖→补做1轮+残留横幅（确定性词元匹配
  天然验不了"听到"，正确答案以媒体语义内容作答与指令文本零词元重合是常态）——
  self_verify 增 is_perception_meta（纯感知复述元指令判定；带动作动词的媒体任务
  如"把音频内容写入文件"=真实可验需求照常核对）+unmet 封顶 uncertain 不进补做
  清单+ok/覆盖率分母不含感知项（单条元指令=不触发横幅）+format_report 感知项
  标签"（感知类元指令，无法核验）"+覆盖行"另 N 项…不计覆盖"（不冒进修覆盖率
  阈值，_MET_MIN/_UNCERTAIN_MIN 未动）；真实 WS audio e2e 复测 PASS 见周期#103

### 阶段19 · 部署后观察与估算收口（周期#104 规划——18.x 部署上线后的诚实边界销账+长窗观察）
- [x] 19.1 音频 token 估算纳入（17.1 诚实边界③销账）：estimate_tokens 只看 str
  content，attach_audio_parts 挂进 wire 的 input_audio 块零计入 → ①token 归因报表
  conversation_history/tool_results 少算音频（长录音可观：60s≈375tok、10min≈3750tok，
  秒×6.25=MiMo 官方 audio token 计量，16.3 查实）②上下文预算裁剪判据偏差（音频回合
  裁剪时机偏晚）——且 str(list) 形态把整段 base64 载荷当文本计（0.4/字符虚高数万
  token，双消费点同病）——周期#104 实装：token_attribution.estimate_content_tokens
  块语义估算（text→文本公式、input_audio→秒×6.25、image→低细节近似常量、未知块
  递归剥 base64/blob 载荷绝不当文本计；音频时长 WAV 头 byte_rate 解析真实秒数，
  其他容器/解析失败按 PCM 密度字节保守下界显式不假称精确）+双消费点接线
  （context_budget._calibrated 裁剪估算+soulmate_agent 归因循环同款 helper）
- [x] 19.2 拒做复发率多轮采样（18.1 诚实边界②销账）：部署后调研/开发类自然触发
  多轮采样（每类≥3轮），统计自然拒做率+8.5c-②b 补做自愈率，数据落 devlog 趋势行
  ——周期#105 采样收官（部署后累计 8 轮：开发 4 轮全净 26.07/26.91/62.16/33.58s、
  调研 4 轮 63.18✅/35.57✅/142.0❌拒做复现→补做自愈/62.12✅）：**自然拒做复发率
  调研 1/4、开发 0/4、合计 1/8；补做自愈 1/1**（REJ192_调研_1 实录：拒做原文
  "web_search 工具不可用（也没有 web_extract）"→8.5c-②b hook 自愈，落库最终回复=
  补做答案"Python 3.13 于 2024 年 10 月 7 日正式发布"+可用性更正声明（首轮），答案
  逐字正确）——拒做属模型间歇行为不根除，补做把失败自愈为成功（用户可见面干净）。
  采样同时实证两缺陷当拍即修：
  ①19.2b **补做轮工具账本落账缺口**（产品）：拒做补做/自验补做两条补做轮通道均不传
  traj_seg_id → _traj_record_round 全跳过，补做真执行的工具行永不落 trajectory_events
  （实测 web_search 真执行成功但账本执行行=0，验收判据/归因/审计对补做轮全盲）；
  修=两补做调用点与主轮同款建 llm 段 span（remediation_turn:/selfverify_remediation:
  标签）+traj_seg_id 透传+finally 收口 end 盖总账；tests/test_refusal_remediation +4
  （捕获式 traj 替身实证 record_round 带 web_search 工具行挂补做段下/段收口/空 span
  id 旧行为不崩/双调用点接线钉/验收判据接线钉）+test_trajectory_recorder 接线钉
  断言跟上（start_llm_span 5→7/end_llm_span 10→12/llm_outcome_status 6→8）
  ②19.2c **验收判据拒做补做形态假阳性**（测试面）：补做契约"首轮原文留档 journal/
  流式帧"→WS 并帧采集把留档拒做原文并进 reply，verify_research 主答区判据被打
  "自报搜索不可用"假阳性（落库最终回复干净仍判负）；修=acceptance_benchmark 验证
  按"落库回复=最终回复"单一真源回读（verify_source=persisted_final_reply），帧文本
  只作 excerpt 透明留证不删改
- [ ] 19.3 候选·感知类元指令英文形态扩展（18.3 诚实边界③：当前仅覆盖
  repeat/what you hear 常见形）——低优先，观察到真实漏判形态再补，不造无依据需求
- 常设观察（不占阶段顺序）：15.5 flaky 断言（3.3s 边际四轮未复现，上限 3.0s 不动）、
  16.3 反馈挖掘（每周期先查四触点，有真实反馈即优先补）

### 阶段20 · 下一批切片（周期#131 规划——对标缺口销账 51/51 清零后的续批）
规划依据=周期#130 收官日志候选清单+本周期代码考古实证（不造无依据需求）：
- [x] 20.1 SessionFileIndex FTS 召回闭环（"写了≠接线了"考古实证双缺口，当拍即补）：
  写入点 `index_file(session_id, fp)` 传参错位（file_path 收 session_id、content 收
  路径字符串=索引存"路径当内容"垃圾）+search() 全库零消费者（只写不读）——
  实装/测试/证据见周期#131日志
- [x] 20.2 反射产生源接入点（考古实证：bootstrap_kpi._edit_reflection 明文自报
  "反射库 0 行（ReflectionEngine.record_reflection 无产生源接入）"——record_reflection
  写入面存在但产品链路零产生源，KPI 恒 0）：把真实反射产生源（确定性时刻：自验收尾
  出结论/工具失败恢复成功/补做自愈完成等）接入 record_reflection，反射库有真实行
  + bootstrap_kpi 反射 KPI 可观测；产生源选择查代码实证不硬造
  ——周期#132实装：agent/reflection_sources.py 三确定性时刻合成器（from_self_verify
  自验收尾出结论=coverage真值/missing/uncertain 入弱点、from_remediation 拒做补做
  收口成功0.8/未自愈0.3两态、from_tool_recovery 恢复成功时刻）+record_safely fail-safe
  旁路+soulmate_agent 四点接线（①自验结论块②补做成/败两态③_tool_recovery_execute
  recovered 分支）；顺手修真 bug：reflection_id 同毫秒主键碰撞静默丢第二条→uuid 后缀
  唯一化。真跑：19.2 验收真实 WS 回合自然命中→生产 reflections.db 2 行+live KPI
  available:true（详见周期#132日志）
- [ ] 20.3 候选·Gene 模板面对照门（P2-10 诚实边界③：gene_loop promote 提案→Gene
  模板的人审晋升通道未过对照门）——**待用户拍板**后落地，不先斩后奏
- [ ] 20.4 候选·16.1 六项拍板项深预研（决策包已给全，仅用户要求时做深预研）
- 常设观察（不占阶段顺序）：15.5 flaky 断言、16.3 反馈挖掘、19.2 长窗采样轮转

### 阶段21 · 下一批切片（周期#133 规划——代码考古实证候选，不造无依据需求）
规划依据=周期#133 代码考古（bootstrap_kpi 自报面+产生源接线核查+待查项收口）：
- [x] 21.1 compact KPI 语义消歧+阈值核账（KPI 自报面考古实证，20.2 同族"零产生源/
  语义歧义"模式）：bootstrap_kpi compact_quality available:false note="窗口内无 compact
  事件（压缩未触发或产生源未接入）"语义歧义——考古核查产生源**已接线**（soulmate_agent.py
  `self._ctx_compressor.should_compress(messages)`→`log_event(kind="compact",
  metadata={before_count,after_count})`+wiring 测试钉源码断言"压缩点未落 compact 事件
  =KPI 数据源断线"），0 事件真相=571 回合未触发压缩（ContextCompressor 默认
  max_tokens=8000×compression_threshold 0.8=6400 估算 token 门槛，中文 1.5 字/token 粗估）
  →①KPI note 消歧（区分"未触发"与"未接入"，产生源接线状态可探测）②阈值与真实长会话
  消息量分布核账（6400 估算门槛 vs 模型真实上下文窗口是否匹配、估算器粗估误差方向）
  ——周期#134实装+核账：①消歧=bootstrap_kpi._compact_producer_status 确定性源码检视
  三态（wired/not_wired/unknown——缺 should_compress 判定/缺 kind=compact 落账分别报因，
  源码不可读如实 unknown 不冒充）+_compact_threshold_context 门槛语境（8000×0.8=6400，
  与 ContextCompressor 构造缺省同源）；0 事件 note 三态分述（已接线未触发→含门槛可对照/
  未接入→数据源断线报因/不可探测→如实报），有事件形状一致透出同字段；tests 22/22
  （三态注入+真实源接线钉+note 三态+门槛读数+populated 形状）+回归 181 过，真实库直算
  回读 note="已接线：窗口内未触发压缩——估算门槛约 6400 token"②阈值核账（只读审计
  evidence/211-compact-threshold-audit.json）：真实消息量 258 会话 547 条按同公式估算
  分布 max 5051/p50 257/p90 787→**0% 会话过 6400 门槛**（0 compact 事件的实证解释，
  与"571 回合未触发"互证）；**估算器公式与自述口径矛盾实证**（docstring"中文约1.5字/
  token"=÷1.5，代码 `chinese_chars * 1.5`=×1.5→中文系统性高估 ~2.25×→压缩触发偏早，
  门槛处真实中文 token≈2844）；token_attribution 估算器真实校准 948 对 factor=1.0059
  （轻微低估 0.6%，与前者不同源分开陈述）；门槛 vs 真实窗口：6400=65536 的 9.77%
  （高估修正后 4.34%）——门槛偏保守但当前零触发无实害；估算器公式修正=行为变更列
  **候选 21.1b 待用户拍板**不擅动
- [ ] 21.2 候选·cron 常驻托管独立 daemon（3.1 遗留实证："调度循环宿主=存活的
  SoulMateAgent 进程（stdio 子进程自举），无 agent 进程时任务不执行（错过不补跑）"——
  schedule_task 创建的任务依赖 agent 进程存活才 fire）——形态（常驻进程 vs systemd
  timer 触发）**待用户拍板**，拍板前不落地
- [ ] 21.3 候选·反射 quality 语义化评分升级（20.2 诚实边界①：quality=coverage 真值或
  固定档位 0.8/0.3 非模型判断；与 P2-10 诚实边界①"行为级对照 EvalCase+LLM judge"为
  同族升级路径，eval_pipeline.py 已有数据集原语）——引入 LLM judge/行为证据加权有
  成本/延迟取舍，**待用户拍板**
- 已收口待查项（不占切片）：周期#132 诚实边界④"/acp/send 返回 hermes 横幅"查清=**设计
  如此非缺陷**——proxy.py L97 注释明文"HTTP /acp/send（本文件硬编码hermes acp路由）"
  +L142 spawn `hermes acp`，该入口是 hermes relay 回合记账面（RelayTrajectory），
  hermes 启动横幅为正确语义；SoulMate 验收真源继续只取 WS 用户路径
- 常设观察（不占阶段顺序）：15.5 flaky 断言、16.3 反馈挖掘、19.2 长窗采样轮转

## 周期日志

[2026-10-12 14:45] 周期#136 | git 归位 TODO 收口·**身份修复抢救归位 main**（0d8c2396 只落 evo/exp 分支、main 缺失=随时丢失风险）+19.2 第十轮（调研+发布）+15.5 第三十一轮+16.3 四触点：①开工核实发现头等新 issue：用户判死案例"为什么要有默认"的身份顶替根修（0d8c2396：ws_acp session/new 强制显式 agent_id+chat-client 身份取值链三件套）在 evo/exp-1791779611 上独有、main（权威链）缺失——evo 分支随时被 evo 清理（周期#85"提交落 evo 分支、分支删除即丢失"同形态），修复面临丢失+兜底复活风险→抢救归位：worktree /tmp/om-main-136 `git cherry-pick 0d8c2396`→**main 64802af9**（2 文件 17+/11-，与原提交逐字节一致 git diff=0 行）+py_compile ws_acp.py 过+修复标记 grep 命中（-32602 拒缺 agent_id/-32601 未注册响亮报错/标题不清 agentId）+key 扫描 0 命中+format-patch 备份 docs/devlog/attachments/2026-10-12-identity-fix-rescue/（skill"当场 commit+patch 备份"铁律补齐）+回归 test_ws_session_recovery 17/17 过②部署面核实（写了≠部署了）：.next/BUILD_ID 14:05:30+openmate-web next-server 14:06:41 启动（晚于 build=非 stale build 41e 形态），前后端两半均已上线；e2e 实测 journal 铁证在原提交时刻（agent_id=hermes→Started hermes subprocess/缺 agent_id→拒绝），本周期为抢救归位不重复全量 e2e③#135 归位 TODO 半收口：身份修复已归位 main 不再依赖 evo 分支存活（evo/exp 侧 0d8c2396 仍在，其分支 evo 自管不动，后续 merge 重复副本内容一致无冲突）；e7a7c2ce（21.1 冗余副本）丢弃仍待 evo 安全窗口（共享树=部署面不动，bootstrap_kpi 只读非关键路径口径不变）④19.2 第十轮（调研+发布，tag=REJ136_1791786700，WS 真实用户路径 verify_mode 全 server_side，结果 JSON 回读 run_tag/latency/stop_reason/verify_mode 与台面一致）**2/2✅**：调研 202.73s（web_search 执行行=1 真源=trajectory_events+回复含 3.13/2024+主答区自报搜索不可用=否）+发布 11.92s（opensoul.db 投递行 accept-pub-target=1，消息行 #1881）双双 end_turn 无拒做——部署后累计 **61 轮**：开发 9/9、调试 5/5、发布 6/6、运维 6/6、办公 6/6、定时 5/5、多渠道 5/5、记忆 5/5、协作 5/5、调研 9/10，拒做复发率 1/61、补做自愈 1/1（下轮轮换 定时/多渠道）⑤15.5 flaky 第三十一轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.29/1.34/1.46s）+32 例批跑过（stall_guard 17+stream_stall 15，18.81s），3.3s 边际三十一轮未复现→上限 3.0s 不动⑥16.3 四触点（agent_messages 近 24h user 行=0、user_feedback 0 行、wechat_bridge_log.jsonl 431B 末行仍 1791584264 历史 send_skipped、tool_failures 125 行近窗全 llm:dna 合成 empty_output 无新真实失败反馈、ai_groups discussion_messages 70 行持平）——零真实反馈维持⑦产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db tasks=0、/tmp/soulmate_accept_results.json 留证后删除） | ✅ | main 64802af9 + (本次提交) | 身份修复抢救+常设观察轮；Gate1=cherry-pick 64802af9 真实落盘（git diff 0d8c2396↔64802af9 两文件 0 行=逐字节一致）+patch 备份 5396B+roadmap 两区块真实落盘；Gate2=修复三件套为已部署生产代码（ws_acp 强制 agent_id 在 ws_acp_endpoint session/new 真实路径、chat-client 取值链在 sendAcpPrompt 真实路径、BUILD_ID/next-server 时间线核实部署面已上线）+ws_session_recovery 17/17 真实回归；Gate3=真实产出=py_compile+pytest 17/17+15.5 单跑 3+批跑 32 真实输出+19.2 验收 2/2 真实 WS 回合（/tmp/soulmate_accept_results.json 回读与台面一致）+16.3 四触点真实 sqlite/文件查询；⚠️诚实边界①身份修复 e2e journal 铁证为原提交时刻（14:06）实测记录，本周期验证=字节一致+回归+部署时间线，未重复全量 e2e②e7a7c2ce 丢弃 TODO 仍待 evo 安全窗口（本周期只收口身份修复面，main↔evo/exp 分叉仍在）③调研 202.73s 为该类历史高端样本（区间 32-202s），与 15.5 单跑/批跑同机并发可能抬高延迟如实报④本轮无自然拒做≠拒做消失（1/61 累计间歇率），19.2b 落账实效待自然复发核账⑤15.5 三十一轮未复现≠证明不 flaky⑥agent_messages user 行 24h=0 口径与近周期一致；下周期=21.1b/21.2/21.3 待用户拍板维持（拍板前不落地）+git 归位 TODO 剩余项（e7a7c2ce 丢弃，evo 安全窗口执行）+19.2 长窗继续（下轮轮换 定时/多渠道）+15.5 例行

[2026-10-12 14:16] 周期#135 | git 分叉三角清点（evo-commit-theft 复发·周期#97/#98 同族）+阶段21 阻塞确认（21.1b/21.2/21.3 待用户拍板）+常设观察轮（16.3/15.5/19.2）：①**git 分叉三角清点（本周期头等新 issue）**——开工核实共享树被 evo 切到 evo/exp-1791779611 且与 main **真分叉**（merge-base=8419628f 周期#133，各有一条对方没有的提交）：main 领先 29715828+0ccac9da+778759f6（=21.1 完整实装+_compact_producer_status 三态消歧+阈值核账 evidence+周期#134 日志=权威链），evo/exp 领先 e7a7c2ce"feat(kpi) 21.1"（提交体自署"周期#134"）= **同一件 21.1 工作被重复做两遍**，e7a7c2ce 即周期#97/#98"提交落 evo/exp 分支"bug 复发产物②**三角对比定权威**：git worktree /tmp/om-main-135（main 隔离树）实测 main 侧 tests/test_bootstrap_kpi.py **22/22 过**=权威链健康完整；两版实现不同名（e7a7c2ce=_producer_wired+_chain_estimates+独立 test_bootstrap_kpi_compact_disambig.py 162 行；main=_compact_producer_status(wired/not_wired/unknown)+_compact_threshold_context+evidence/211-compact-threshold-audit.json）但同目标同证据结论（0% 过门槛/中文约 2.25×高估）→ e7a7c2ce 唯一增量 `_chain_estimates`（KPI 端点内实时分布）与 main 一次性 evidence 文件功能等价，移植=冗余膨胀（其测试 import 的 _producer_wired/_chain_estimates 与 main 函数名不兼容），**判定 e7a7c2ce=冗余副本不移植**③**归位决策（诚实边界）**：evo 心跳活跃（strand_a/b cycle 797/733 实时），纪律"evo 在跑禁 checkout/reset/切分支"→ **本周期不擅动共享树**，不归位不 reset（禁 reset --hard 不看 diff 铁律）；隐患如实记录=部署面（共享树=e7a7c2ce bootstrap_kpi.py）≠权威仓（main 版），但 bootstrap_kpi 为只读 KPI 端点非关键路径、两版均自洽可服务；**归位 TODO（evo 安全窗口执行）**=核实 e7a7c2ce 冗余后共享树 checkout main+丢弃 e7a7c2ce（保留 main 版 bootstrap_kpi.py），或 evo 自身 merge 时冲突解为保留 main 版④21.1b（估算器公式修正=行为变更）/21.2（cron 常驻托管 daemon）/21.3（反射 quality 语义化）三候选**维持待用户拍板**（"拍板前不落地"不先斩后奏），阶段21 无未阻塞可落地项→本周期走常设观察轮⑤16.3 四触点（agent_messages 近 24h user 行=0、user_feedback 0 行、wechat_bridge_log 末行仍 1791584264 历史 send_skipped、ai_groups discussion_messages 70 行持平）——零真实反馈维持⑥15.5 flaky 第三十轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.45/1.25/1.30s）+32 例批跑（stall_guard 17+stream_stall 15）过（18.41s），3.3s 边际三十轮未复现→上限 3.0s 不动⑦19.2 长窗采样（运维+办公，tag=REJ135_1791785622，WS 真实用户路径 verify_mode 全 server_side）**2/2✅**：运维 23.49s（system_checkup 账本执行行=1 真源=trajectory_events+8090/磁盘 82.9%）+办公 50.99s（report.xlsx 4963 字节有效 xlsx）双双 end_turn 无拒做——部署后累计 **59 轮**：开发 9/9、调试 5/5、发布 5/5、运维 6/6、办公 6/6、定时 5/5、多渠道 5/5、记忆 5/5、协作 5/5、调研 8/9，拒做复发率 1/59、补做自愈 1/1⑧产物清理：/tmp/soulmate_accept_results.json、/tmp/e7_test.py、/tmp/main_kpi.py、worktree /tmp/om-main-135 归零 | ✅ | (本次提交) | git 分叉清点+常设观察轮；本轮无产品代码改动（清点+docs+观察）；Gate1=roadmap git diff 本条目真实落盘（worktree /tmp/om-main-135 内路径限定 docs/soulmate-roadmap.md，git diff --cached 核实不含他会话 WIP/untracked）；Gate2=N/A（无新符号接线）——git 分叉判定证据=git log/diff/merge-base 真实输出+main tests 22/22 真实 pytest 输出非自报；Gate3 真实产出=15.5 单跑 3+批跑 32 真实 pytest 输出+19.2 验收 2/2 真实 WS 回合（/tmp/soulmate_accept_results.json 回读 run_tag/latency/stop_reason/verify_mode 与台面一致）+16.3 四触点真实 sqlite/文件查询；⚠️诚实边界①归位 TODO 未执行（evo 在跑纪律禁 checkout），部署面≠权威仓的 bootstrap_kpi 差异如实记录非隐藏，evo 安全窗口需人工/下周期收口②e7a7c2ce 判"冗余"基于两版同目标同结论+main 22/22 健康，但未逐行比对全部 roadmap 差异（判定聚焦 bootstrap_kpi 实现面）③21.1b/21.2/21.3 阻塞是"待用户拍板"非技术阻塞，用户拍板后可立即落地④本轮无自然拒做≠拒做消失（1/59 累计间歇率）⑤15.5 三十轮未复现≠证明不 flaky；下周期=若用户拍板 21.1b/21.2/21.3 则落地之，否则 git 归位 TODO 收口（evo 安全窗口）+19.2 长窗继续（下轮轮换 调研/发布）+15.5 例行

[2026-10-12 13:33] 周期#134 | 21.1 compact KPI 消歧+阈值核账（阶段21 首刀；21.2/21.3/21.1b 待用户拍板不先斩后奏）+16.3 四触点+19.2 第八轮收尾（记忆+协作，第八轮全集收官）+15.5 第二十九轮：①16.3 四触点（agent_messages 近窗 user 行 15 条全为 REJ134 验收探针/子代理派发文案非真实反馈、user_feedback 0 行、tool_failures.db 表缺零表空库、ai_groups discussion_messages 70 行持平、wechat_bridge_log.jsonl 431B 仍 10-10 历史 send_skipped）——零真实反馈维持②21.1①消歧实装（29715828）：bootstrap_kpi 增 _compact_producer_status（确定性源码检视三态 wired/not_wired/unknown——缺 should_compress 判定=压缩触发未接线、缺 kind=compact 落账=KPI 数据源断线分别报因，源码不可读如实 unknown）+_compact_threshold_context（8000×0.8=6400 与 ContextCompressor 构造缺省同源）；_compact_quality 0 事件 note 三态分述（已接线未触发→含门槛可对照/未接入→断线报因/不可探测→如实报）+populated 形状一致透出同字段；tests/test_bootstrap_kpi.py 22/22（新增 7 例：三态注入/真实源接线钉/门槛读数/note 三态/populated 形状）+受影响面回归 181 过（bootstrap_kpi+reflection_sources+trajectory_recorder/rounds+turn_lifecycle+refusal_remediation+report_evidence+self_verify+relay_trajectory）；真实库直算回读 note="窗口内无 compact 事件（产生源已接线：窗口内未触发压缩——估算门槛约 6400 token）"（含糊"或产生源未接入"形态清零）③21.1②阈值核账（0ccac9da，只读审计 evidence/211-compact-threshold-audit.json）：真实消息量 258 会话 547 条按 _estimate_messages_tokens 同公式估算 max 5051/p50 257/p90 787/p99 2614→**0% 会话过 6400 门槛**（0 compact 事件实证解释+与 571 回合未触发互证）；**估算器公式与自述口径矛盾实证**（docstring"中文约1.5字/token"=÷1.5，代码 `chinese_chars * 1.5`=×1.5→中文系统性高估 ~2.25×→压缩触发偏早，门槛处真实中文 token≈2844）；token_attribution 估算器真实校准 948 对（backfill actual_prompt_tokens vs estimate_gap）factor=1.0059=轻微低估 0.6%（与前者不同源分开陈述不混算）；门槛 vs 模型窗口：6400=65536（MODEL_CONTEXT_LIMITS mimo）的 9.77%、高估修正后 4.34%——门槛偏保守但当前零触发无实害，**估算器公式修正=行为变更列候选 21.1b 待用户拍板**不擅动④19.2 第八轮收尾（记忆+协作，tag=REJ134_1791783038，WS 真实用户路径，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：记忆 35.96s（memory_append 追加 ACCEPT_MEM_MARK_9→MEMORY.md 服务端真源含标记=是）+协作 22.73s（subagent 双 worker 并行，回复含双 worker 产出+subagent_runs 近8行=8）双双 end_turn 无拒做——部署后累计 57 轮：开发 9/9、调试 5/5、发布 5/5、运维 5/5、办公 5/5、定时 5/5、多渠道 5/5、记忆 5/5、协作 5/5、调研 8/9，拒做复发率 1/57、补做自愈 1/1（**第八轮轮转全集收官**：#130 开发+调试→#131 运维+办公→#132 调研+发布→#133 定时+多渠道→#134 记忆+协作）；采集侧系统帧过滤实证生效（self_check×2 被过滤）⑤15.5 第二十九轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.47/1.55/1.53s）+32 例批跑过（stall_guard 17+stream_stall 15，18.63s）未复现→上限 3.0s 不动⑥测试产物清理归零（/tmp/soulmate_accept 探针自清=删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db tasks 表 0 行）⑦git 纪律：共享树开工即在 evo/exp-1791779611（与 main 同 commit 8419628f，evo 双心跳 observe 阶段活跃），全程 `git worktree add /tmp/wt134 main` 隔离树开发+测试+提交零接触共享树（29715828+0ccac9da+本条 docs 三拍小步 commit） | ✅ | (本次提交) | 21.1 勾选；Gate1 git diff 真实落盘（bootstrap_kpi +68/tests +92/evidence JSON/路线图 2 处，三拍小步 commit 防 evo 截走）；Gate2 grep _compact_producer_status/_compact_threshold_context 在 _compact_quality 真实路径消费（compute_kpis→_compact_quality=端点 GET /api/agent/bootstrap-kpi 同一计算面）+真实源接线钉 test_producer_status_wired_on_real_source+既有 test_soulmate_compress_site_logs_compact_event 双钉；Gate3 真实产出=tests 22/22+回归 181 真实 pytest 输出+真实库直算回读新 note 形态+阈值核账 evidence JSON（258 会话真实查询/948 对真实校准）+19.2 验收 2/2 真实 WS 回合（结果 JSON 回读）+15.5 真实 pytest（单跑 3+批跑 32）；⚠️诚实边界①部署缺口如实记：21.1 新 KPI 面在 main（worktree 提交），运行中 acp-proxy 端点仍为旧面——共享树在 evo 分支+evo 活跃禁重启加载，直算真源已验证等价，待归位/重启窗口自然生效（只读分析面无紧迫性）②估算器公式矛盾（2.25×高估）本周期只核账不修（行为变更待拍板 21.1b），修与不修的取舍=触发偏早 vs 中文长会话被过度压缩，当前 0 触发无实害③真实消息量分布按 agent_messages 存量（258 会话）估算，为消息面下界（系统提示/工具结果不在表内），门槛结论"0% 过线"方向稳健④token_attribution 校准 factor 1.0059 为该估算器方向，与 context_compression 估算器不同源不同公式，禁混算⑤本轮无自然拒做≠拒做消失（1/57 累计间歇率），19.2b 落账实效待自然复发核账；下周期=第九轮验收轮转开启（开发+调试）+15.5 例行+16.3 持续，或用户拍板项落地（21.1b 估算器公式修正/21.2 cron 常驻托管/21.3 反射 quality 语义化/16.1 六项/18.2 真机录音/17.3 兜底层）

[2026-10-12 13:35] 周期#133 | 常设观察轮（20.3/20.4 待用户拍板不先斩后奏）+代码考古规划阶段21+16.3 四触点+19.2 第八轮（定时+多渠道）+15.5 第二十八轮：①16.3 四触点（agent_messages 近 24h user 行=0、user_feedback 0 行、wechat_bridge_log.jsonl 431B mtime 仍 10-10 06:17 历史 send_skipped、tool_failures 125 行近窗全 llm:dna 合成 empty_output/read_timeout+1 历史 terminal（om-97a4f3c38d62）无新真实失败反馈、ai_groups discussion_messages 70 行持平）——零真实反馈维持②19.2 第八轮轮换（定时+多渠道，tag=REJ133_1791782200，WS 真实用户路径，verify_mode 全 server_side，结果 JSON 回读 run_tag/latency/stop_reason 与台面一致）**2/2✅**：定时 74.07s（cron_tasks.db tasks 表 accept_probe 行=1，探针自清回读 ~/.hermes/soulmate/memory/cron_tasks.db tasks=0）+多渠道 26.26s（wechat→accept_peer 出站痕迹行=1）双双 end_turn 无拒做——部署后累计 55 轮：开发 9/9、调试 5/5、发布 5/5、运维 5/5、办公 5/5、定时 5/5、多渠道 5/5、记忆 4/4、协作 4/4、调研 8/9，拒做复发率 1/55、补做自愈 1/1（下轮余类：记忆+协作）③15.5 第二十八轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（--durations call 1.30/1.35/1.36s）+32 例批跑过（stall_guard 17+stream_stall 15，18.13s）未复现→上限 3.0s 不动④代码考古规划阶段 21（三候选切片写进路线图，不造无依据需求）：KPI 自报面考古发现 compact_quality available:false note"压缩未触发或产生源未接入"语义歧义→产生源接线核查（soulmate_agent.py should_compress→log_event kind="compact"+before/after 已接线、wiring 测试钉源码断言）→0 事件真相=571 回合未触发压缩（ContextCompressor 默认 8000×0.8=6400 估算 token 门槛）→**21.1 消歧+阈值核账**候选；21.2 cron 常驻托管（3.1 遗留"无 agent 进程任务不执行"）/21.3 反射 quality 语义化评分升级（20.2①+P2-10①同族升级路径）两候选**待用户拍板**⑤周期#132 诚实边界④销账：/acp/send HTTP 返回 hermes 横幅查清=**设计如此非缺陷**（proxy.py L97 注释"本文件硬编码hermes acp路由"+L142 spawn `hermes acp`，该入口=hermes relay 回合记账面 RelayTrajectory，hermes 横幅为正确语义）⑥反射库自然增行核账：live GET /api/agent/bootstrap-kpi edit_reflection count=3 avg_quality=0.667（#132 后又自然新增 1 行，产生源持续自然命中，非探针自造）⑦产物清理：/tmp/kpi133.json、/tmp/soulmate_accept_results.json 已删 | ✅ | (本次提交) | 阶段21 立项+21.1 候选；本轮无产品代码改动（观察+考古+docs），Gate1=roadmap git diff 真实落盘（阶段21 规划节+周期日志条目）；Gate2 不适用（无新符号）；Gate3 真实产出=19.2 验收 2/2 真实 WS 回合（结果 JSON 回读）+15.5 真实 pytest 输出（单跑 3+批跑 32）+KPI HTTP 回读+sqlite 四触点查询输出；⚠️诚实边界①21.1 阈值核账未做（本轮只立项）：6400 估算门槛是否匹配真实长会话待下周期实测消息量分布②21.2/21.3 待用户拍板不先斩后奏③本轮无自然拒做≠拒做消失（1/55 累计间歇率），19.2b 落账实效待自然复发核账④15.5 二十八轮未复现≠证明不 flaky⑤compact 0 触发仅覆盖现有 571 回合窗口（多为短探针会话），真实用户长会话分布外推未证⑥下周期=21.1 compact KPI 语义消歧+阈值核账（可直接落地无需拍板）+19.2 第九轮（记忆+协作）+15.5 例行+16.3 四触点；20.3/20.4/21.2/21.3 四项待用户拍板清单：Gene 模板面对照门、16.1 六项深预研、cron 常驻托管形态、反射评分 LLM judge 升级
[2026-10-12 13:20] 周期#132 | 20.2 反射产生源接入点（"写了≠接线了"考古实证零产生源当拍即补）+16.3 反馈挖掘+19.2 第七轮（调研+发布）+15.5 第二十七轮：①考古确认 record_reflection 写入面存在但产品链路零调用方（读侧 get_improvement_context 已接 prompt 注入，写侧全库 grep 零消费）②实装 agent/reflection_sources.py：三确定性时刻合成器（from_self_verify 自验收尾出结论=coverage 真值非硬造+missing/uncertain 入弱点；from_remediation 拒做补做收口成功/未自愈两态；from_tool_recovery=_tool_recovery_execute recovered 时刻）+record_safely fail-safe 旁路（引擎爆炸只记日志返回 False 绝不冒泡）+None 引擎 lazy-init③soulmate_agent 四点接线（import+自验结论块（与结论横幅同门槛：多需求项或有缺口）+拒做补做成/败两态+恢复成功分支）④首跑测试 3 挂暴露真 bug：reflection_id=refl_<毫秒> 主键同毫秒碰撞→第二条 INSERT IntegrityError 被 fail-safe 吞掉=静默丢行→refl_<毫秒>_<uuid8> 唯一化（tests/test_reflection_sources.py 9/9 过）；bootstrap_kpi 0 行注释更新对齐 20.2 语义⑤回归 174 全过（bootstrap_kpi+reflection_sources+trajectory_recorder/rounds+turn_lifecycle+refusal_remediation+report_evidence+self_verify+relay_trajectory）⑥Gate3 真跑：重启 acp-proxy 加载新代码后 19.2 验收真实 WS 回合**自然命中**产生源（自验 items=2 出结论）→生产 reflections.db 真实 2 行（refl_1791781222525_e339ba80/refl_1791781238601_36b46ae6，uuid 后缀新格式，quality=0.5=coverage 真值）→live GET /api/agent/bootstrap-kpi 反射 KPI available:true count=2 avg_quality=0.5（此前恒 available=False）——产生源→存储→KPI 消费全链真实跑通⑦16.3 四触点：agent_messages 近 24h user 行=0、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped、tool_failures.db 0 字节空库、ai_groups discussion_messages 70 行持平——零真实反馈维持⑧19.2 第七轮（调研+发布，tag=REJ132_1791781186，verify_mode 全 server_side）**2/2✅**：调研 36.17s（web_search 执行行=1 真源=trajectory_events+回复含 3.13/2024）+发布 15.79s（opensoul.db 投递行=1）双双 end_turn 无拒做——部署后累计 53 轮：开发 9/9、调试 5/5、发布 5/5、运维 5/5、办公 5/5、定时 4/4、多渠道 4/4、记忆 4/4、协作 4/4、调研 8/9，拒做复发率 1/53、补做自愈 1/1⑨15.5 第二十七轮：test_head_stall_aborts_fast_and_retry_recovers 单跑过（--durations call 1.39s）+32 例批跑过（18.72s）未复现→上限 3.0s 不动⑩产物清理：/tmp/soulmate_accept 已删、/tmp/refl_probe_132.txt 已删、KPI 临时 JSON 已删 | ✅ | (本次提交) | 20.2 勾选；Gate1 git diff 真实落盘（reflection_sources.py 6990B/soulmate_agent +20/reflection_engine +3/tests 175 行，三拍小步 commit 防 evo 截走）；Gate2 接线钉源码断言（三产生源各自落在真实函数路径：from_tool_recovery 在 _tool_recovery_execute 函数体内含 recovered 分支、from_remediation×2 在拒做补做两态、from_self_verify 在结论块）+最强证据=生产回合自然命中写入真源行（非探针自造）；Gate3 真实产出=9/9 新测+174 回归真实 pytest 输出+生产 reflections.db 2 行 sqlite 回读+live 端点 HTTP 回读 available:true count=2 与直算一致；⚠️诚实边界①产生源为确定性时刻合成（无 LLM 评分）：quality=coverage 真值或固定档位 0.8/0.3，语义化质量分非模型判断②自验产生源触发=结论横幅同门槛（多需求项或有缺口），单条已覆盖闲聊不产行防噪音③工具失败恢复产生源本窗口未自然命中（恢复事件低频）——接线钉+单测覆盖，留待自然命中核账④/acp/send HTTP 测试路径实测返回 hermes 启动横幅非 SoulMate 回合（无副作用、未采信为证据，真实证据全取 WS 用户路径）——该路径默认路由语义待查⑤本轮无自然拒做≠拒做消失，1/53 累计间歇率；19.2b 落账实效待自然复发核账⑥15.5 二十七轮未复现≠证明不 flaky；下周期=20.3 Gene 模板面对照门/20.4 六项深预研均待用户拍板（不先斩后奏）→期间跑常设观察（15.5/16.3/19.2 第八轮轮换：余类 定时/多渠道/记忆/协作）+代码考古规划阶段 21 候选切片（不造无依据需求）
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
[2026-10-10 10:40] 周期#16 | 4.1c会话级路由：新增agent/wechat_router.py（WeChatRouter映射管理面：查询全量/peer过滤/session_id反查同会话多peer、改绑返回previous_session_id+created、解绑幂等、校验拒绝空值/空白字符、store可注入+get_wechat_router单例）+wechat_bridge.BridgeStore增list_mappings/unbind+soulmate_agent四点接线（wechat_route_list/bind/unbind工具定义+主dispatch+_wechat_route_dispatch共用入口lazy-init+code-mode批内+系统提示清单） | ✅ | (本次提交) | 4.1c勾选；tests/test_wechat_router.py 24/24（查询/反查/改绑/解绑幂等/校验7例/跨实例持久/单例/接线护栏+__new__真实dispatch烟测bind→list→rebind→unbind→错误路径全态）+回归183全过(cron_reporter/health_monitor/soulmate_scheduler/wakeup_relay/schedule_wakeup_wiring/wakeup_executor/wakeup_fire_llm_error/observability/relay_traj)+真实机器只读烟测（真实映射库0条=4.1a loopback未写真实库符合预期，校验路径实证）；⚠️4.1b iLink传输仍待用户拍板接入形态（独立微信账号 or 替代期独占接管，绝不双消费同一iLink会话）；下周期做4.2消息路由（OpenMate/微信统一收发）
[2026-10-10 11:04] 周期#17 | 4.2消息路由（统一收发）：新增agent/message_router.py（UnifiedMessageRouter：统一信封ChannelMessage+RouteResult；route_inbound统一入站按channel分发——wechat整体委托WeChatBridge.deliver()单一owner不双份记账、openmate=RouterStore(channel+msg_id复合主键持久去重)+无客户端回合失败纪律复用wakeup_executor（refusal/LLM级失败/零输出都不假装成功）；send_outbound统一出站——wechat→send_proactive拆块+限流发peer、openmate→落库assistant行turn_origin=msg）+wechat_bridge增量（deliver()公开投递入口+send_proactive()+WeChatTransport.send_text默认NotImplemented不破坏既有传输+LoopbackTransport.proactive）+wakeup_relay补推面纳入msg出身（入站回合行+出站消息行正连前端实时可见）+soulmate_agent四点接线（message_send工具+主dispatch+_message_dispatch共用lazy-init+code-mode批内await+系统提示） | ✅ | (本次提交) | 4.2勾选；tests/test_message_router.py 16/16（信封/去重跨实例/入站三态/校验4例/微信委托真桥e2e+去重+失败告知/出站双通道/默认回合体失败纪律/接线护栏+__new__真实dispatch烟测）+回归240全过(cron_reporter/health_monitor/soulmate_scheduler/wakeup_relay/schedule_wakeup_wiring/wakeup_executor/wakeup_fire_llm_error/wakeup_observability/relay_trajectory/proxy_relay_traj/wechat_bridge/wechat_router)+真实机器烟测（singleton/relayed origins含msg/校验三路真实输出/SoulMateAgent导入）；⚠️诚实边界：微信通道仍loopback传输（4.1b iLink待用户拍板接入形态，绝不双消费hermes会话）；openmate出站=落库+relay补推；⚠️发现_split_blocks块上限下限200（既有契约，测试改用450字断言）；下周期做4.3媒体能力（图片/文件收发、截图理解）
[2026-10-10 11:35] 周期#18 | 4.3媒体能力（图片/文件收发、截图理解）：新增agent/media_channel.py（MediaAttachment中立形状+to_meta对齐attachments持久化格式+save_inbound入站base64落盘+from_paths出站校验+media_prompt_block媒体提示块——图片/截图走vision_analyze/read_image截图理解、文件走read_file）+message_router增量（ChannelMessage.media+纯媒体消息text空合法+入站媒体块拼回合文本+send_outbound带media：wechat→桥send_media_proactive逐个限流、openmate→MEDIA标签+attachments列持久化ALTER兜底）+wechat_bridge增量（WeChatMessage.media+build_wechat_prompt媒体块+send_media默认NotImplemented+Loopback记录+send_media_proactive传输不支持→0不谎报）+soulmate_agent接线（message_send工具media参数+dispatch校验透传+系统提示） | ✅ | (本次提交) | 4.3勾选（阶段4除4.1b全完成）；tests/test_media_channel.py 20/20+回归240全过（消息/唤醒/调度链167+观察/轨迹链73）+真实机器烟测（导入/relayed origins/singleton/提示块输出）；⚠️诚实边界：微信媒体真实传输=4.1b iLink AES媒体CDN未接（loopback），入站截图理解=提示块引导vision工具（真实理解效果待真图实测）；下周期做5.1子代理（拆分任务给并行worker，父代理收尾合并）
[2026-10-10 11:58] 周期#19 | 5.1子代理（拆分任务给并行worker，父代理收尾合并）：新增agent/subagent_manager.py（SubagentManager：asyncio.Semaphore限并发1-4+每worker独立wait_for超时隔离——失败/超时取消随行可见不影响其余worker，2-8子任务/超时10-600s夹取；默认worker=default_subagent_runner=隔离工作会话无客户端SoulMate回合，复用唤醒回合同款失败纪律refusal/LLM级失败/零输出都不假装成功+turn归因锚点收集产出+超长截断自报，判定复用wakeup_executor单一真源；parent_goal注入每个worker context；确定性merge不走LLM=成功/失败分节+按输入顺序+失败收尾警示；subagent_runs表持久化失败/超时同样落行+snapshot观测，写日志失败不阻塞）+soulmate_agent四点接线（delegate_tasks工具定义tasks/max_concurrency/parent_goal+主dispatch+_subagent_dispatch共用入口lazy-init+code-mode批内await+系统提示清单） | ✅ | (本次提交) | 5.1勾选；tests/test_subagent_manager.py 21/21（prompt构建/校验夹取/并行保序/失败与超时隔离含cancel实证/并发上限/parent_goal注入/merge两态/runs持久化+写失败不阻塞/默认runner四态+截断自报/接线护栏+__new__真实dispatch烟测）+回归516全过（message/media/wechat链107+wakeup/调度/relay链183+plan/tool链104+记忆链122）+py_compile+SoulMateAgent真实导入烟测；⚠️诚实边界：worker是真实LLM回合（各自调模型+工具），多会话并发的工具执行层隔离硬化归5.2（当前worker各用独立会话，共享agent实例观测采集端属弱共享）；下周期做5.2多会话并发隔离（写锁已有，扩到工具执行层）
[2026-10-10 12:35] 周期#20 | 5.2多会话并发隔离（写锁已有，扩到工具执行层）：新增agent/tool_exec_fencing.py（以realpath为键的跨进程文件栅栏file_fence——flock互斥+锁文件走中央临时目录sha256命名不污染项目树+进程死亡自动释放无stale lock+同进程不同fd同样互斥+有界等待10s超时抛FileFenceTimeout显式报冲突禁无锁裸写）+接线三处读-改-写临界区（patch主循环/code-mode内层双执行点+PersistentMemory.write/append覆盖memory工具与experience_sink的MEMORY.md同步）+_get_db加busy timeout=30 | ✅ | 50132e40 | 5.2勾选；tests/test_tool_exec_fencing.py 11/11（真多进程lost-update回归/同进程双fd互斥/有界等待/超时消息可见/进程死亡自动释放/锁文件不污染项目目录/持久记忆并发追加不丢行/超时显式报错不改文件/code-mode patch行为实测冲突+正路径/接线护栏/_get_db timeout）+回归553全过（plan/tool/subagent/session链207+记忆/消息/调度链287+persistent_memory/experience/profile链59）+py_compile+真实导入烟测；根因实证：无栅栏两进程并发read-modify-write 5/5次lost update（/tmp/race_control.py对照），有栅栏全保留；⚠️诚实边界：terminal/execute_code任意副作用无法枚举上锁（归任务分解/意图层）、git index.lock串行化（task_decomposer.commit_step并发提交）与共享agent实例观测采集端弱共享待后续；下周期做5.3群组协作（AI群组里的任务编排，接现有ai_groups能力）
[2026-10-10 12:52] 周期#21 | 5.3群组协作（AI群组任务编排，接现有ai_groups能力）：新增agent/group_orchestrator.py（GroupOrchestrator驱动opensoul /api/ai-groups既有任务面——提交主任务=自动分解子任务+按角色自动分配→逐个触发execute失败隔离（单个execute请求失败/无agent不阻塞兄弟，409已在执行容忍为在途）→有界轮询list_tasks到终态（wait_s 5-600s预算，耗尽子任务记TIMEOUT显式标注不假装成功）→终态读事件流finished/failed判定成败（ai_groups execute收尾status一律scored，成败真相在事件流——实证发现）→确定性merge不走LLM（成功/失败分节+按输入顺序+失败收尾警示同5.1纪律+超长截断自报）；传输层可注入零网络测试，缺省httpx调SOULMATE_AI_GROUPS_URL；只读查询面list_groups/task_status）+soulmate_agent四点接线（group_task工具action=list_groups/orchestrate/task_status+主dispatch+_group_dispatch共用入口lazy-init+code-mode批内await+系统提示清单） | ✅ | (本次提交) | 5.3勾选（阶段5全部完成）；tests/test_group_orchestrator.py 14/14（happy path顺序/execute失败隔离含兄弟照常执行实证/409容忍/无agent显式失败/事件failed覆盖scored/timeout显式标注/提交失败抛错/校验4例/merge确定性+截断自报/空子任务/list_groups与task_status格式/接线护栏+__new__真实dispatch烟测四态）+回归133全过（subagent/message/media/wechat/tool_fencing链85+code_mode/tool_output链48）+py_compile+SoulMateAgent真实导入烟测+真实机器烟测（:8090 ai-groups health ok，list_groups真实返回2个现存群组4703ec60 B群组/1cfdb9e5 A群组）；⚠️诚实边界：编排对象是ai_groups既有执行面（run_agent_proxy跑真实agent binary），本模块不重复实现执行器；轮询为快照轮询非WS实时尾随（ws_group实时面在前端侧）；真实群组端到端跑真实agent执行待有真实成员群组实测；下周期做6.1技能保存（把成功流程写成SKILL.md含触发条件/步骤/坑）
[2026-10-10 13:25] 周期#22 | ⚡0.2紧急插队·首条消息提速：新增agent/prompt_prefetch.py（PromptPrefetcher：上下文源并行预取asyncio.gather+TTL缓存120s可配SOULMATE_PREFETCH_TTL_S+旁路纪律收集器/prefill失败只计数绝不冒泡绝不阻塞+take()超时回退现场计算（行为与预热前逐字一致）+可观测stats计数）+soulmate_agent四点接线（_prompt_prefetcher lazy-init收集器=pref_context/memory_md/improvement三源+mcp_tools暖60s工具缓存、_prefill_warm微小预热LLM请求同系统提示前缀+同工具面首chunk即断开省输出token不进会话历史不落库、new_session创建路径_schedule_prefetch fire-and-forget、_run_llm_with_tools三源消费缓存）+基础系统提示抽_build_base_system_prompt单一真源（prefill与主链路逐字同前缀=暖前缀缓存前提） | ✅ | 13193cfa | 0.2勾选（紧急插队清空）+0.1补勾（周期#12实装后复选框漏勾）；tests/test_prompt_prefetch.py 15/15（并行barrier实证同步/异步混跑、TTL fake clock命中与过期、失败隔离、prefill同前缀+不进缓存、schedule fire-and-forget、接线护栏new_session调度/主链路消费/同前缀真源/预热不落库）+回归207全过（plan/tool_judge/code_mode/tool_output/self_verify/cache_replay/message_tree/session_lifecycle/persistent_memory/stream_progress/stale_session_recovery/ws_session_recovery）+py_compile+导入烟测；真实验收=WS用户路径（initialize→session/new→模拟打字8s→首条消息）首字回复 6.00s≤8s达标（此前约33s），prompt全程8.21s，journal实证[prefetch] warm scheduled→warm done parts=[4源] prefill=1无失败；⚠️诚实边界：单次实测样本（同机低负载），27s模型大首包改善幅度依赖服务端前缀缓存生效程度，建议用户实际使用体感复核；下周期做6.1技能保存（把成功流程写成SKILL.md含触发条件/步骤/坑）
[2026-10-10 13:52] 周期#23 | 6.1技能保存（成功流程写成SKILL.md含触发条件/步骤/坑）：新增agent/skill_saver.py（SkillSaver确定性渲染标准SKILL.md=frontmatter name/description/category/triggers+固定分区触发条件/步骤/坑/验证与.agents/skills同构；save_flow校验显式拒绝空名/空描述/零触发/零步骤+None条目不str()蒙混成"None"（本轮测试实测暴露的真缺口，已修_as_list）+同名默认不覆盖（exists提示禁静默覆盖既有技能）+overwrite=true revision递增原子替换（tmp+os.replace不留半截文件）；slugify保留CJK；可写库~/.openmate/skill-library单一写入源）+skill_manager saved层（scan_saved_skills复用标准层解析但read_only=False/saved:<slug>/revision透传+60s TTL缓存+list_skills合并=保存即被search_skills命中进注入面；update/delete/record_usage拒绝saved条目=单写入者纪律）+soulmate_agent四点接线（skill_save工具定义+主dispatch+_skill_save_dispatch共用入口lazy-init+code-mode批内+系统提示清单；保存后强刷saved层缓存不被60s TTL藏住） | ✅ | 979a1b1a | 6.1勾选；tests/test_skill_saver.py 22/22（渲染roundtrip/校验7例/去重守卫+revision递增/CJK slug/多行frontmatter/pitfalls可选/saved层检索命中+TTL强刷/单写入者守卫/接线护栏+__new__真实dispatch烟测四态）+回归311全过（skill_standard_layer+skill_saver+gene_extract+plan/steering/token链159 + persistent_memory/experience/code_mode/tool_output/message/subagent/group/fencing链152）+py_compile+SoulMateAgent导入烟测+真实机器烟测（生产SkillManager实例search_skills命中saved条目排std技能之前、read_only=False revision=1，烟测产物已清理）；⚠️诚实边界：保存→检索→注入的大半链路经saved层合并已通，但"按任务自动匹配注入"的真实命中率与6.3技能进化（执行结果反哺修正）待后续周期实测；下周期做6.2技能检索与加载（按任务自动匹配并注入）
[2026-10-10 14:45] 周期#24 | 6.2技能检索与加载（按任务自动匹配并注入）：新增agent/skill_loader.py（SkillLoader确定性检索+加载一体——match_for_task集中gate语义（短消息≤10字不匹配/文件发送请求跳过/只认触发词命中忽略纯描述匹配，pitfall 30语义逐条保留）+match_reason可观测、match_for_step步骤级重新检索+与回合级合并去重（步骤命中在前）、render_local_section注入段字节契约与历史内联实现逐字节一致（token归因锁定）+预算截断自报SOULMATE_SKILL_BUDGET_TOKENS默认1500、render_recommended gene推荐载荷内容加载（描述/模板/成功率/相关性字段容缺）+stats计数）+soulmate_agent四点接线（_skill_loader lazy-init/注入段/推荐块/管线首试+重试step_skills）。修三处真实缺口：①gene推荐只注入名字描述模板成败率全丢②管线只按用户原话匹配一次③注入无预算 | ✅ | 597e2f05 | 6.2勾选；tests/test_skill_loader.py 26/26（gate语义6例/步骤级合并去重5例/字节契约+预算7例/推荐加载5例/观测+接线护栏+__new__烟测3例）+回归211全过（token_attribution_wiring字节契约19+skill_standard_layer 34+skill_saver 22+plan/tool/code_mode/self_verify/记忆链110）+py_compile；⚠️evo并发git事故实录：evo周期内pre-branch stash+checkout（stash@{0}=evo-pre-branch-1791613018）把soulmate_agent.py未commit接线补丁收走（工作树回HEAD），按铁律禁pop evo stash/禁主树checkout，改git worktree add /tmp/wt62 main隔离树git stash show -p提取补丁重放+新文件拷入+测试+commit后worktree remove——补丁6接线点全部找回零丢失；⚠️诚实边界：gene /skill/recommend响应映射（opensoul gene.py）暂不含code_template字段——render_recommended已字段容缺支持模板加载，待opensoul端点扩字段后自动生效（本次未动opensoul未重启8090）；技能命中率真实效果待用户实际使用验证；下周期做6.3技能进化（用执行结果反哺修正技能内容）
[2026-10-10 18:45] 周期#31（main镜像；#25-#30 日志在真实工作链 evo/exp-1791613018） | ⚡8.1 InfectionGate误杀修复（验收R1）+⚡8.6双链处置准备：根因实证=任务管线首试/重试两个_run_llm_with_tools调用点漏传user_text→审计门"## 用户请求（未提供）"→用户明文点名的write_file/memory_append被误判注入（验收#1/#9死因）；修复①管线两调用点补传user_text+check()空user_text留痕告警②用户明文内容溯源豁免（签名命中文本逐字来自用户请求=用户自己写的内容豁免，夹带照常拦）+JUDGE_PROMPT补"用户意图优先/请求缺失不构成拦截理由"③无人值守clarify超限放行+留痕（clarify_exhausted_pass+infection:pass_with_trace），有人值守保持terminate，INFECTION_CLARIFY_EXHAUSTED按模式配置 | ✅ | 98d6025f(main)/9d17ebdc(evo链cherry-pick) | tests/test_infection_guard.py 47/47（新增8.1三组17例+既有terminate用例改有人值守前提）+回归252全过+evo链worktree复测47/47+149全过；真实验收=acceptance_benchmark开发/记忆 2/2✅（83s/91s真实LLM+工具，dev_hello.py+MEMORY.md服务端副作用实证，journal零infection-guard拦截零空user_text告警）；⚠️复测发现semantic-cache污染验收（v1两回合Cache EXACT命中回放旧响应假跑+记忆假阳性=周期#30被杀轮残留stale marker）→8.5复测铁律：prompt带唯一防缓存标记+验证前清陈旧产物；⚠️诚实边界：R2短路合成报告（缓存回放里仍在）归⚡8.2待做（→周期#32已治理销账）；⚡8.6最终取舍待用户拍板（对比+建议已写入8.6条目）
[2026-10-10 19:04] 周期#32 | ⚡8.2 短路合成报告治理（验收R2）：新增agent/report_evidence.py（执行证据前提确定性治理——parse_ledger账本拆"真实执行vs被拦截"（permission单一真源：allow/ask-approved/degraded-allow/cached-allow/infection:pass_with_trace=执行证据；deny/ask-denied/sandbox-restricted/loop_guard:*/其余infection:*=被拦截；[xxx]伪条目两头不计）+assess四态(no-claim/grounded/blocked/unsupported)+amend_response报告标题降级"## 任务执行报告（⚠️ 未经执行验证）"+governance_banner被拦截报"被拦截+原因"禁报完成+is_cacheable_request语义缓存准入）；短路根因实证=语义缓存exact命中回放旧"任务执行报告"（跳过LLM/工具=run2 1.38s形态）→soulmate_agent三处接线（缓存get侧执行类请求不读缓存、put侧执行类回复不入缓存、回复治理收口先于自检且落库=治理后文本+banner推前端）；self_verify收紧"说了≠做了"（动作类met必须以工具执行证据为前提——原has_marker自证漏洞"回复自称已完成即给met"封死，被拦工具evidence报⛔被拦截+原因不计证据） | ✅ | (本次提交) | 8.2勾选+R2销账；tests/test_report_evidence.py 22/22+回归426全过（self_verify 13 + cache_replay/turn_outcome/output_guard/plan/plan_tool_ledger/task_judge_wiring/stream_progress/session_recall/experience_sink/persistent_memory 169 + code_mode/tool_output_wiring/turn_lifecycle/message_router/wechat_bridge/prompt_prefetch/infection_guard/tool_recovery/token_attribution 222）；真实验收=毒丸探针3/3✅（语义缓存埋exact命中的合成"任务执行报告"毒丸→WS真实用户路径/soulmate发同请求：毒丸FAKE标记未回放+journal实证"[semantic-cache] 执行类请求不走缓存"+dev_hello.py真实创建且内容/标记命中（执行证据），latency 51.95s≠1.38s短路形态，自检2/2基于真实工具）；⚠️诚实边界：治理负路径（工具真被拦时banner）以单测实证，真实拦截场景未在探针触发；下周期做8.3长回合治理（schedule 3工具链>240s无终态根因+回合级进度/超时可见）
[2026-10-10 19:45] 周期#33 | ⚡8.3 长回合治理（R3）：根因实证=**非dispatch卡死**（journal 17:52-17:57 验收#7复盘，回合在真实推进），每工具一次注入审计LLM往返+R1误杀失败/重试轮把3工具链拉到5-8分钟级，harness 240s到点放弃即"无终态无落库"；实装agent/turn_watchdog.py（TurnWatchdog：后台心跳默认60s SOULMATE_TURN_HEARTBEAT_S推快照[已用时/轮次/工具数/最近工具]给客户端+journal不依赖轮次边界LLM单轮慢也照发+软预算240s SOULMATE_TURN_SOFT_BUDGET_S单次⚠️"继续执行不中断"禁硬杀+notify旁路纪律只计数不冒泡+960s自过期防孤儿心跳）+soulmate_agent五点接线（import+_watchdog_for/_stop_turn_watchdog lazy-init兼容__new__测试agent+工具循环前start+轮次/工具节点beat+turn.close订阅收口） | ✅ | (本次提交) | 8.3勾选；tests/test_turn_watchdog.py 8/8+回归169全过（turn_lifecycle/turn_outcome/loop_guard/prompt_prefetch/infection_guard/report_evidence/stream_progress）+py_compile+导入烟测+双实例重启health OK；真实验收=WS用户路径探针（schedule 3工具链+唯一防缓存标记，复用acceptance_benchmark采集器含sessionId过滤）：78.1s end_turn、需求自检3/3 met、journal实证心跳"[turn-watchdog] session=om-13507ea2b926 ⏳ 回合进行中：已用 60s｜第 2/15 轮｜已执行工具 1 个（最近：schedule_cancel）"、cron_tasks.db实证任务#2(wd_probe_1791631)创建后按要求停用(enabled=0)——R1修复后同链路从">240s无终态"变78s完整终态（8.5定时类预热通过，探针脚本wd_probe_ws.py留库可复跑）；⚠️诚实边界：耗时本身治理（单工具链审计LLM往返开销优化）未做另案；下周期做8.4调研兜底（web_search后端可用性探测+SearXNG未启动显式降级，禁静默归零）
[2026-10-10 19:57] 周期#34 | 8.4 调研兜底（R4）：web_search 后端可用性探测+显式降级禁静默归零——新增agent/search_backend.py（SearXNG主后端+Bing HTML兜底降级链+probe()+四态语义：可信空只认searx JSON results:[]报"非故障"、bing 0块=inconclusive疑似反爬禁断言"无资料"、全挂=逐后端报因+启动文档化start.sh/searx.webapp）+修静默归零双根因（①后端挂了/非JSON/缺results键误报"无搜索结果"②query裸拼URL未编码空格/中文截断假零→quote()）+soulmate_agent两执行点收口run_search单一真源内联curl-SearXNG清零 | ✅ | e4e25c13 | 8.4勾选；tests/test_search_backend.py 24/24+回归109全过(code_mode/sandbox_policy/tool_recovery/tool_output)+真实机器实证（SearXNG未启动场景旧报"搜索不可用:curl stderr"/假零→现"⚠️未获得搜索结果…不足以断言无资料…启动方法"）+SoulMateAgent导入烟测；⚠️诚实边界：Bing兜底实测被反爬拦截0块（inconclusive已如实标注，SearXNG启动后主链路即恢复多引擎聚合）；8.5验收复测/⚡8.6双链合并（待用户拍板）仍未做；下周期做8.5验收复测（多渠道/定时两类，prompt带唯一防缓存标记+先清陈旧产物）
[2026-10-10 20:45] 周期#35 | 6.3 技能进化（阶段顺序下一刀；⚡区0.1/0.2已完、8.6待用户拍板）：新增agent/skill_evolution.py（SkillEvolver确定性反哺闭环——账本parse_ledger拆真实执行/被拦截+classify_result单一真源判失败+签名去重+确定性坑条目成文[恢复成功/未恢复两态措辞]+SkillSaver.save_flow(overwrite=True)revision递增原子写回+skill_evolution.db全量审计+每回合每技能≤2新坑/总坑≤12封顶显式报因+只动saved可写技能标准层单写入者纪律skipped留痕）+soulmate_agent回合收尾钩子（record_usage后lazy-init+旁路纪律不阻塞） | ✅ | (本次提交) | 6.3勾选；tests/test_skill_evolution.py 20/20（坑沉淀/去重/双上限/恢复两态/拦截不算失败/标准层跳过/正文不可解析不改写/内容保真步骤触发词描述零丢失/审计落行含verify结果/审计写失败旁路/接线护栏钩子顺序断言）+回归143全过(skill_saver/loader/standard_layer/usage_receipt/self_verify/experience_sink/report_evidence)+真实机器烟测（SoulMateAgent导入+钩子接线inspect实证+真实单例db路径）；⚠️诚实边界：仅"坑"追加，步骤/触发词语义改写需LLM或用户拍板未做；下周期做7.1浏览器自动化（Playwright/CDP工具面）或8.5验收复测
[2026-10-10 21:05] 周期#36 | 7.1浏览器自动化（阶段顺序下一刀；⚡区0.1/0.2已完、8.6待用户拍板）：新增agent/browser_worker.py（独立子进程JSONL协议驱动playwright——服务venv无playwright故走子进程隔离，默认SOULMATE_BROWSER_PYTHON=/usr/bin/python实测带playwright 1.60；常驻浏览器会话页面状态跨调用保留；launch本地chromium或SOULMATE_BROWSER_CDP_URL附加既有浏览器；单动作失败ok:false不杀会话）+agent/browser_tools.py（BrowserTools有界调用+诚实三态：后端不可用显式报因+启动文档化/超时显式+状态丢失如实告知/崩溃重建提示一次；截断自报；screenshot自动落盘返回路径可MEDIA下发）+sandbox NETWORK_TOOL_NAMES归类browser+soulmate_agent四点接线（browser工具定义9动作goto/snapshot/click/type/fill/eval/screenshot/back/close+主dispatch+_browser_dispatch共享lazy-init+code-mode批内+系统提示清单） | ✅ | (本次提交) | 7.1勾选；tests/test_browser_tools.py 15/15（JSONL真子进程往返/崩溃重建/超时/不可用双形态interpreter缺失+playwright缺失stderr尾部如实报因/截断自报/screenshot自动落盘/接线护栏+__new__真实dispatch烟测+lazy-init构造）+回归191全过（browser+sandbox_policy+skill_saver/loader/standard_layer+self_verify/report_evidence/search_backend）+code_mode/tool_output/tool_recovery/infection_guard 121全过+运行时_build_base_system_prompt实证browser行进系统提示；真实机器smoke全绿（真实chromium：goto/snapshot标题正文链接/type后eval回读'ni hao'证明状态跨调用/坏选择器干净报因/截图11.4KB真实PNG/close）；实现中真实smoke暴露并修复1个真缺口：动作级timeout_ms与请求级硬超时等长时watchdog先开火把"选择器没找到"误报成"会话超时重建"→动作级自动留5s余量；⚠️诚实边界：单测走fake worker（真实playwright成本放smoke），CDP附加模式代码就绪未实测（无既有9222会话），JS eval任意副作用属工具固有能力（过permission_gate）；下周期做7.2文档生成（docx/pdf/xlsx）或8.5验收复测

[2026-10-10 21:29] 周期#37 | 7.2文档生成（阶段顺序下一刀；⚡区0.1/0.2已完、8.6待用户拍板）：新增agent/docgen_tools.py（DocGenTools确定性生成结构化spec→真实文件：docx=python-docx（heading/paragraph/bullet/table/pagebreak+title）、xlsx=openpyxl（多表+首行加粗表头+按内容估列宽+rows简写）、pdf=同款docx经LibreOffice headless转换（服务venv无reportlab，独立UserInstallation防抢profile锁）；输出落~/.hermes/soulmate/documents/返回MEDIA:路径；filename白名单禁路径穿越；依赖缺失/转换失败/超时四态显式报因禁静默，PDF失败时同内容docx保留）+soulmate_agent四点接线（document工具定义/主dispatch/_document_dispatch共享lazy-init/code-mode批内+系统提示清单） | ✅ | (本次提交) | 7.2勾选；tests/test_docgen_tools.py 12/12（docx全块型回读/xlsx多表+表头加粗+列宽+rows简写/pdf四态含后端不可用·失败·超时均保留docx/文件名越界拒绝/自动命名落盘/MEDIA输出/接线护栏+__new__真实dispatch烟测+lazy-init构造）+回归185全过（docgen+browser+skill_saver/loader+code_mode+tool_output_wiring+infection_guard+prompt_prefetch）+py_compile+导入烟测+_build_base_system_prompt运行时实证document行进系统提示（f-string花括号转义坑：插值区写{name,rows}会NameError，已转义{{}}）；真实机器烟测三格式全绿（服务同款interpreter：docx 36940B/xlsx 5474B双表/PDF 84713B真实LibreOffice转换pdftotext中文可提取'PDF烟测/第一章概述'，独立回读docx段落+表格单元+xlsx B3/加粗属性一致）+双实例重启health OK；⚠️诚实边界：PDF走docx中转（复杂排版保真度受LibreOffice转换限制）、xlsx不支持公式/样式定制；下周期做7.3 TTS/语音或8.5验收复测
[2026-10-10 21:47] 周期#38 | 7.3 TTS/语音（阶段顺序下一刀；⚡区0.1/0.2已完、8.6待用户拍板）：新增agent/tts_worker.py（edge-tts独立子进程worker JSON单发协议——主agent进程可能已有事件循环，进程内asyncio.run不可用故子进程隔离+有界超时，失败退出码+JSON报因回传禁静默）+agent/tts_tools.py（TTSTools文本→真实音频落~/.hermes/soulmate/audio/返回MEDIA:路径；双后端分层：edge-tts在线神经语音（服务venv实测带edge_tts）主后端+espeak-ng离线兜底（系统实测可用）；诚实多态：在线失败离线成功显式⚠️报因禁假称edge-tts成功/双后端全失败逐后端报因+安装提示/超时显式/0字节不算成功/空text·过长>5000字·非法voice·rate·文件名越界显式拒绝；voice→espeak语言确定性映射zh→cmn）+sandbox NETWORK_TOOL_NAMES归类text_to_speech（edge-tts出网）+soulmate_agent五点接线（工具定义/主dispatch/_tts_dispatch共享lazy-init/code-mode批内/系统提示清单） | ✅ | (本次提交) | 7.3勾选；tests/test_tts_tools.py 22/22（成功/MEDIA行/自定义文件名/voice·rate透传与默认值/参数拒绝6例含路径穿越与拒绝不落盘/在线失败·超时→离线兜底/双后端失败双因齐报/0字节不算成功/后缀mp3→wav切换/语言映射4例/JSON尾行解析）+py_compile+SoulMateAgent导入烟测（__new__裸实例_tts_dispatch真实dispatch四态）+回归全量tests/ 2176全过（403s全库零失败）+_build_base_system_prompt运行时实证text_to_speech行进系统提示；真实机器验收双后端全绿：edge-tts真实合成acceptance.mp3 21168字节ffprobe实证mp3可播3.528s（服务同款interpreter）+espeak-ng离线兜底offline.wav 114688字节ffprobe实证wav 2.600s（⚠️文案+MEDIA正常）；⚠️诚实边界：语音输入/ASR（听懂语音）不在本切片（只做TTS输出方向）、edge-tts需联网（离线自动降级espeak-ng音质机械）；下周期做7.4图像生成/编辑或8.5验收复测

[2026-10-10 22:40] 周期#39 | 7.4图像生成/编辑（阶段顺序下一刀，阶段7全部完成；⚡区0.1/0.2已完、8.6待用户拍板）：新增agent/image_tools.py（ImageTools：编辑=确定性Pillow八动作resize/crop/rotate/flip/convert/annotate/compose/thumbnail——RGBA→JPG白底合成/CJK字体确定性查找(Noto Sans CJK+SOULMATE_IMAGE_FONT覆盖)/overlay越界拒绝；生成=双后端诚实多态对标7.3：SOULMATE_IMAGEGEN_API_URL→OpenAI兼容images API真实AI生成（b64_json/url+90s有界超时）或未配置→PIL程序化绘制兜底（渐变底+文字卡）显式标注"⚠️程序化绘制（非AI生成…）"禁假称AI+配置文档化、AI失败显式报因禁降级冒充、0字节/不可解码不算成功、filename白名单禁穿越）+sandbox NETWORK_TOOL_NAMES归类image+soulmate_agent五点接线（image工具定义/主dispatch/_image_dispatch共享lazy-init/code-mode批内+系统提示） | ✅ | fde10dea | 7.4勾选（阶段7工具生态补齐全部完成）；tests/test_image_tools.py 23/23（编辑八动作尺寸与像素实证/JPG白底/文字上画/拼接三态/生成兜底诚实标注/AI后端成功·失败·空内容·垃圾字节四态/参数拒绝与路径穿越/0字节/接线护栏+__new__真实dispatch烟测+lazy-init构造）+全量回归2199 passed零失败（18m32s全库）+py_compile+_build_base_system_prompt运行时实证image行进系统提示（fuzzy patch误伤tts描述行已按脚本法修复，py_compile过）；真实机器烟测全绿（服务同款interpreter PIL 12.3.0：程序化绘制400x240→resize→annotate金色水印→convert jpg→compose纵向拼接→thumbnail 64x38，PIL独立回读全部产物尺寸/mode一致，负路径显式报因）+双实例重启health OK；实现中测试暴露并修复1个真缺口（_size默认值分支在generate未传尺寸时误判"尺寸非法"）+1个输出契约修正（兜底提示曾拼在MEDIA:行后，改前置保持MEDIA末行供网关按行提取）；⚠️诚实边界：AI图像生成API后端未实测（无真实API配置，走注入fake单测实证，代码就绪待用户配置SOULMATE_IMAGEGEN_API_URL）；程序化绘制是兜底非语义生成；下周期做8.5验收复测（多渠道/定时两类，prompt带唯一防缓存标记+先清陈旧产物）或8.6双链合并（待用户拍板）

[2026-10-10 22:55] 周期#40 | 8.5验收复测（多渠道/定时两类；⚡区0.1/0.2已完、8.6待用户拍板）：acceptance_benchmark.py增量改造三件套——①ACCEPT_CATEGORIES类别过滤（"每完成一个⚡项即重跑对应类别对标"能力落地，ACCEPT_CATEGORIES=定时,多渠道实测只跑2任务）②每运行注入唯一RUN_TAG防缓存标记进prompt（复测铁律①：semantic-cache EXACT命中回放旧响应假跑0.98s级）③setup()先cleanup()清陈旧产物（复测铁律②：上次被打断轮的accept_probe行/记忆标记会假阳性）+summary记run_tag/categories | ✅ | (本次提交) | 8.5勾选；真实复测2/2✅（tag=RETEST85_1791643501，真实WS用户路径session/new→session/prompt，每类独立session）：定时76.57s end_turn server_side（cron_tasks.db accept_probe行=1+journal实证schedule_task真实创建#3「accept_probe」interval:1h含持久化回执）、多渠道40.56s end_turn reply_assisted（journal实证wechat_route_list+message_send真实工具账本permission=allow"已发文本1块给微信peer「accept_peer」"，response_len=859）；非缓存假跑三证（延迟76s/40s≠0.98s短路形态、journal零semantic-cache回放、工具账本带真实result_preview）；Gate1 git diff三处改动落盘、Gate2 类别过滤/RUN_TAG/setup预清理均被本次真实运行走中（只跑2类+tag打印+预清理生效）、Gate3 2/2真实产出（DB行+journal工具账本）；测试产物复核清理归零（accept_probe行0）；py_compile过 | ⚠️诚实边界：每类单次样本（同机低负载）；多渠道verify为reply_assisted（微信通道loopback传输，真实iLink归4.1b待用户拍板）；8.6双链合并落地后建议全量10类复测；下周期做全量10类验收对标实测（harness已支持类别过滤，超时则分批5+5跑，结果写进路线图）并规划下一批切片（当前阶段1-7+⚡8.1-8.5全完，余项全为待用户拍板/另案）

[2026-10-10 23:55] 周期#41 | 8.5b 全量10类验收对标实测（分批5+5+修复链+复测）：分批真实跑完10类（tag=FULL10_A/B）9/10当场过、调研类连环挖出4真根因逐修后转✅+全量单轮FINAL台面10/10但发布类经行归属取证为陈旧行假阳性（模型误报message_send"未注入"拒发，旧cleanup漏清agent_messages探针行）修复后真复测✅——修复五连：①report_evidence动作类判据补搜索/检索/抓取/爬取动词+_TOOL_NAMED_RE显式工具点名（5a8a16a3，根因：调研回合被判非执行类→semantic-cache 88% SEMANTIC命中回放旧误报，复测标记只防EXACT）②search_backend超时15s→30s SOULMATE_SEARCH_TIMEOUT_S可配+exit28措辞"响应超时…勿断言未启动"（0aab4f8c，根因：SearXNG冷查询实测20.0s>15s→curl exit 28误报"不可达"→模型误称"SearXNG未启动"）③verify_research语义收敛=剥心跳快照+主答区判自报（引号内引用不计）+须含被搜事实3.13+2024（ce1ec4c4/23fa4f94，根因：裸子串"不可用"判据被补做说明引用提示语/心跳粘连假阴性杀正确回答，全文regex打地鼠E/G/H三轮不收敛）④cleanup补清agent_messages探针行+verify_publish只认accept-pub-target投递行（89013b1a，复测铁律②漏网形态）⑤SearXNG手动启动（start.sh，20s冷聚合实证） | ✅ | 5a8a16a3/0aab4f8c/c6abf31e/23fa4f94/89013b1a | 8.5b勾选；tests：report_evidence+self_verify 35、search_backend+report_evidence 48、回归126（cache_replay/turn_outcome/output_guard/infection_guard/skill_evolution）全绿+verify判据7历史形态离线判别全对；真实验收=10类全有真实通过证据（开发59.95s/调试64.39s/运维23.14s/调研75.66s web_search真实执行答案2024-10-07/发布33.76s投递行真实落库/办公151.9s xlsx 4964B/定时116.32s cron_tasks.db行/多渠道53.87s工具账本/记忆63.16s MEMORY.md标记/协作61.66s subagent_runs双worker），journal工具账本permission=allow实证；⚠️诚实边界：调研/发布为修复后类别复测形态（非同一轮全量全绿），每类单样本同机低负载；新发现入8.5c待办5项（①回答尾部"补做结果/逐项说明"元叙事违反"简短一句话"②**可用性误报本日两次实证最高优先**：run-A谎称没有web_search（账本证同回合已调用）、FINAL轮谎称message_send未注入拒发（代码证无条件装配+下回合正常）→治理=可用性声称对照工具账本/_active_tool_names确定性核验③InfectionGate拦模型自启SearXNG自愈④SearXNG无常驻托管重启即丢⑤心跳帧混入harness回复采集）；下周期做8.5c-②可用性误报治理（工具面确定性核验+amend降级修正，用户可见缺陷最高优先）
[2026-10-10 23:59] 周期#42 | 8.5c②可用性误报治理（"说了≠做了"的镜像"做了/有，说没有"）：report_evidence增可用性声称抽取find_availability_claims（registration/availability/generic三类正则，覆盖"没有X工具/X运行时未注入/X不在工具列表/X工具不可用/无法使用X/工具未注入"等实证形态；结果类宾语排除"没有X的结果"防误纠+词尾边界防排除词被正则回溯绕过）+assess_availability两级判据（registration=工具面真源_active_tool_names在册即证伪proof=registry；availability模糊声称仅本回合真实执行成功才证伪proof=executed——被拦截调用反向支持模型说法不更正；generic=有成功执行才证伪；无真源无账本一律不更正=宁漏纠不误纠）+amend_availability/availability_banner前置更正声明（原文保留可审计，列证伪依据）+soulmate_agent接线（8.2证据治理后、2.1自验前，真源=getattr(self,"_active_tool_names")） | ✅ | a02984e2 | Gate1 git diff 407行真实落盘+Gate2 grep接线点（assess_availability/banner/amend三调用+_active_tool_names真源传参）+Gate3 tests/test_tool_availability_claims.py 18/18（抽取7形态+结果宾语排除+注入词非工具语境排除+具名压制generic噪音+两级判据registry/executed/未知工具不纠/在册未试不纠/被拦不纠/generic/无真源不纠+banner措辞两态+接线护栏先证据后自验）+回归167全过（report_evidence/self_verify/tool_recovery/cache_replay/code_mode/plan_tool_ledger/degraded_spill/turn_lifecycle）+py_compile+SoulMateAgent导入烟测（__new__+_active_tool_names真实更正输出实证）；⚠️诚实边界：治理"说"侧更正声明，拒做行为本身未强制重试；同机单样本未跑真LLM回合（间歇性误报需用户实际使用观察）；下周期做8.5c-①回复风格元叙事治理
[2026-10-11 00:45] 周期#43 | 8.5c-①回复风格元叙事治理（回答尾部"补做结果/逐项说明"）：三根因实证逐一修（d3dc9841）——C"固定带"主根因=自否定元指令被当需求（验收防缓存标记行"此行仅为…忽略其语义"被分句拆成需求项且必然unmet→每轮必触发补做），修is_self_negating显式自否定措辞不算需求（"此行仅[为是]/只是"须伴"标记/占位"防误杀真实需求）；A条件子句"如果X就Y"拆成独立需求项短答覆盖不到→unmet→补做，修条件项unmet封顶uncertain只提醒不补做；B补做提示词旧措辞"完成后逐项简要说明结果"直接命令模型逐项复盘，修"只输出需要补充的内容本身禁'补做结果/逐项说明'复盘标题"；连带修harness判据假阳性（STYLE85C_1实证：需求自检报告复述需求原文"如果搜索工具不可用…"被并帧采集混入主答区→正确回答误判"自报不可用"，主答区边界补🔍需求自检/可用性更正声明切割点） | ✅ | d3dc9841 | 8.5c-①勾选（8.5c①②全完，③④⑤转后续另案）；Gate1 git diff 289行真实落盘、Gate2 grep接线点（is_self_negating进extract两处/is_conditional进verify/soulmate_agent真实补做路径5926-5931完好）、Gate3 tests/test_remediation_style.py 14例+同域回归120全过（self_verify/report_evidence/tool_availability/infection_guard/plan_tool_ledger）+双实例重启health OK；真实验收调研类两轮实测：tag=STYLE85C_1挖出harness判据假阳性（"补做结果/逐项说明"尾部已消失实证）→judge修复+离线判别2例→tag=STYLE85C_2 ✅1/1（31.5s，主答区自报不可用=否、含3.13+2024）；⚠️诚实边界：需求自检推送仍并帧进合并采集视图（8.5c-⑤采集侧过滤遗留，"简短回答"合并视图非纯一句话）、补做内容若模型自发复盘不做输出后处理剥离（防过度防御）、单样本同机低负载；下周期：⚡区仅剩8.6待用户拍板→规划下一批切片（候选8.5c-⑤采集侧帧过滤/8.5c-④SearXNG systemd托管/8.5c-③自愈豁免评估）
[2026-10-11 00:55] 周期#44 | 8.5c-④ SearXNG 常驻托管（调研类FULL10失败链环境根因①销账："重启即丢全靠手动起"）：新增 systemd --user searxng.service（ExecStart=~/SearXNG-master/venv/bin/python3 -m searx.webapp+WorkingDirectory+SEARXNG_SETTINGS_PATH=~/searxng-data/settings.yml+Restart=always+enabled，Linger=yes实证开机自启）+手动进程PID 413102精确kill移交systemd接管（新PID 471760）+search_backend STARTUP_HINT升级首选`systemctl --user start searxng`（start.sh/手动命令保留兜底，测试同步断言） | ✅ | (本次提交) | 8.5c-④勾选（8.5c①②④全完，③⑤另案）；Gate1 git diff真实落盘（search_backend.py 4插2删+test 2行）；Gate2 grep STARTUP_HINT真实输出路径两处（hint=214/223）+重启实例后运行时已加载新常量；Gate3 tests/test_search_backend.py 26/26（新增systemd命令断言）+真实机器三证：systemd托管SearXNG真实查询"命中25条"（python.org结果回读）/`systemctl --user restart searxng`后active且复搜正常（"重启即丢"销账实证）/双实例重启health OK；⚠️诚实边界：开机自启以Linger=yes+enabled静态配置为证（禁reboot未做真实重启实测）；下周期做8.5c-⑤ harness采集侧按turn_origin/帧类型过滤（心跳快照/需求自检推送混入合并回复视图，8.5c-①遗留"简短回答合并视图非纯一句话"同源）
[2026-10-11 01:10] 周期#45 | ⚡8.5c-⑤ harness 采集侧按帧来源过滤（心跳快照/需求自检推送/治理横幅混入合并回复视图）：根因=回答帧与系统帧走同一 agent_message_chunk 通道（ACP ContentChunk 无 metadata 字段，wire 不带 turn_origin）→ 采集侧只能按帧类型判别；实装 agent/frame_origin.py（origin_of 帧首前缀分类 heartbeat/self_check/audit_notice/answer + split_frames 合并过滤——纯横幅帧整帧丢弃、横幅+正文同帧按 banner+空行+body 契约只剥横幅块保正文防把非流式整发的回复误杀；只认帧首防正文中间同字样误伤；📋管线进度/🛑停止提示按设计归回答帧）+标记常量单一真源抽取（turn_watchdog SNAPSHOT_PREFIX/SOFT_WARN_PREFIX、self_verify REPORT_HEADER、report_evidence GOV_BANNER_PREFIXES/AVAIL_BANNER_PREFIX，生产者改措辞测试即红）+acceptance_benchmark._rpc 采集收口（entry.system_frames+summary system_frames_total 过滤计数可观测） | ✅ | 43d51f6f/d7541a83 | 8.5c-⑤勾选（8.5c①②④⑤全完，仅③另案）；Gate1 git diff 两提交286行真实落盘；Gate2 grep split_frames 在 _rpc 采集真实路径+本次真实运行命中过滤（计数即证）；Gate3 tests/test_frame_origin.py 12/12（真实生产者输出钉死三类帧+软预算帧+补做后缀+横幅两形态拆分+回答帧不误伤+混合计数）+同域回归122全过（self_verify/report_evidence/turn_watchdog/tool_availability/remediation_style/infection_guard）+双实例重启health OK；真实验收=WS 用户路径复测 tag=FRM85C_1 2/2✅（调研38.2s：合并回复=纯一句话"Python 3.13 于 **2024 年 10 月 7 日**正式发布。"38字零粘连（8.5c-①"合并视图非纯一句话"销账实证）、self_check×1 帧被过滤；定时100.11s：60s 心跳真实触发 heartbeat×1+self_check×1 过滤，cron_tasks.db 行=1）；⚠️诚实边界：帧分类是前缀判别非 wire 元数据（SDK 无 metadata 字段，真 turn_origin 需协议/前端扩展另案）；📋 任务拆解/🔧 步骤管线进度按设计留在回答帧（只过滤 roadmap 点名三类）；relay_trajectory 轨迹采集同源粘连已治理（周期#47）；下周期：⚡区仅剩8.6待用户拍板→规划下一批切片（候选：8.5c-③自愈豁免评估/轨迹采集同源过滤/新一批能力切片）
[2026-10-11 01:20] 周期#46 | 8.5c-③ InfectionGate 自愈类低危命令豁免语义（FULL10 调研类失败链遗留③：模型自启 SearXNG 自愈被 clarify 打断）：实装 agent/infection_guard.classify_self_heal 确定性分类（纯自愈批次=全部 terminal 单一命令匹配三类窄签名：systemctl --user 生命周期/journalctl 只读/start.sh 启动脚本（含周期#41 事故命令原文 bash ~/searxng-data/start.sh），拒 shell 元字符链式、拒混入非自愈命令、只认 command/cmd 键与权限引擎 _shell_command_of 同语义）+check() 豁免语义=LLM 审计 clarify 一级降级放行+留痕（source=self_heal_pass_with_trace，原疑问留痕可审计、不消耗 clarify 预算不再升级 terminate 杀整轮），terminate 照常终止、启发式劫持签名在前不受豁免影响、工具仍过 permission_gate/sandbox 不绕权限+JUDGE_PROMPT 补自愈指导语双保险+soulmate_agent 轨迹账本留痕分支扩 source（permission=infection:pass_with_trace 与 report_evidence 执行证据口径一致） | ✅ | 3006bee4 | 8.5c-③勾选（8.5c ①②③④⑤全完）；Gate1 git diff 256行真实落盘（3文件）；Gate2 grep 接线点（check() classify_self_heal 调用+降级返回+留痕分支+self_heal_note 日志）+真机分类实证（事故命令/systemctl --user start→True，sudo/链式rm→False，SoulMateAgent 导入 OK）；Gate3 tests/test_infection_self_heal.py 20/20（分类正负11例含事故命令原文/豁免语义8例含有人值守反复 clarify 不再 terminate 回归+terminate 保留+启发式不受豁免+非自愈行为不变/指导语/接线护栏）+同域回归87全过（report_evidence/self_verify/tool_availability/remediation_style/frame_origin/turn_watchdog）+既有 infection_guard 47/47 零回归+双实例重启 health OK（8092/8095）；⚠️诚实边界：豁免按命令形态分类非意图理解（新形态自愈命令如 pip install 补依赖未纳入窄签名，漏网时回到正常审计不误放）；LLM 审计误判的真机复现未跑真 LLM 回合（判词层用注入 fake 判词实证降级路径，指导语对真判词的降噪效果待用户实际使用观察）；下周期规划下一批切片（候选：轨迹采集同源过滤/调度监控常驻托管 3.1 遗留/新一批能力切片；⚡8.6 待用户拍板）
[2026-10-11 01:50] 周期#47 | 轨迹采集同源过滤（周期#45遗留"relay_trajectory 轨迹采集同源粘连"销账）：真实库实证=hermes acp 启动横幅"╭─── Hermes Agent v0.15.2 … Available Tools …"与回答同走 agent_message_chunk 通道，relay_trajectory 把横幅并进 llm_call content（真实行 content[:500] 全被框线占满、真实回答被完全遮蔽）——8.5c-⑤ 同源粘连在轨迹采集侧的孪生缺陷；实装 frame_origin.strip_engine_chrome（合并文本开头剥引擎chrome框线块 ╭顶边→╰底边，窄签名=块内命中 Available Tools/Agent v/upstream 防误杀含框线正文/ASCII图，只剥开头正文中间框线不动，底边行粘连回答"╰───╯回答…"按边框后尾巴归回答不吞——chunk流无分隔符拼接实证形态）+relay_trajectory.filtered_text（帧级 split_frames 三类系统帧+合并级 strip_engine_chrome，_finalize 与 observe_cli_turn 双路径收口，过滤计数进日志可观测） | ✅ | 7615c61b | Gate1 git diff 305行真实落盘（3文件）；Gate2 grep filtered_text/strip_engine_chrome 在 _finalize(299)/CLI路径(265) 真实记账路径；Gate3 tests/test_relay_chrome_filter.py 12/12（真实库横幅形状剥除/截断横幅/窄签名不误伤框线图/中间横幅不动/系统帧过滤/full_text回退/CLI兜底/纯回答零变化/计数可观测）+同域回归45全过（test_frame_origin 12+test_relay_trajectory 17+test_proxy_relay_traj 16）+py_compile+真实库横幅行回放实证（n=1 剥除命中生产数据签名）+双实例重启 health OK（8092/8095）；⚠️诚实边界：引擎chrome窄签名按已知标记判别（其他 CLI 引擎自造横幅若无这三标记不剥，漏网时回到原样不误删）；粘连只处理底边行形态（侧边行粘回答不切，防把边框内合法内容误剪）；本切片未跑真 hermes 回合落库复测（真实库旧行即实证样本+集成测试覆盖记账路径），建议下次 hermes 会话后抽查 trajectory content；下周期：调度监控常驻托管（3.1/3.3遗留：调度/监控循环宿主存活agent进程，无进程时不执行错过不补跑）或新一批能力切片；⚡8.6 待用户拍板
[2026-10-11 01:58] 周期#48 | 调度/监控常驻托管（3.1/3.3遗留销账）：新增agent/soulmate_daemon.py（独立常驻宿主：无条件托管soulmate_scheduler+health_monitor两条后台循环——与start.py嵌入式"有任务才启动"不同，常驻宿主的意义是后建任务/目标也被轮询拾取；SIGTERM/SIGINT优雅退出双stop()收尾；多宿主共存安全=SQLite CAS到点领取不双跑）+systemd --user soulmate-daemon.service（Restart=always+enabled开机自启，journal常驻日志） | ✅ | (本次提交) | 3.1/3.3遗留销账（"无进程时不执行"闭环）；Gate1 git diff三文件真实落盘；Gate2 grep run_daemon→run_forever双循环托管+systemd ExecStart真实拉起journal"[daemon] 常驻托管已启动：cron调度+健康监控（db=真实双库）"；Gate3 tests/test_soulmate_daemon.py 6/6（无条件托管has_enabled=False也托管/优雅退出双stop/stop_event注入/真实调度器守护宿主内claim→fire→runs行ok/真实监控器落state行/接线护栏）+同域回归72全过（soulmate_scheduler/health_monitor/cron_reporter）；真机e2e探针（pgrep实证零agent.start进程=遗留前提成立）：探针任务#7到点9s内被守护进程claim+fire，run#1诚实failed（全新会话DB零行拒绝造回合=拒绝纪律正确行为，首条📊汇报落库即建DB行）→run#2 ✅ ok end_turn（真实LLM回合"守护探针"落agent_messages+📊定时汇报3.2链同走）——无agent进程也到点执行实证；探针任务已删（runs留审计3行）；⚠️诚实边界：全新创建未落库会话被首fire拒一次是拒绝纪律设计行为（防给消失会话造回合），首行落库后自愈（_reload_session_from_db）；systemd开机自启以enabled+Linger静态配置为证（禁reboot未实测）；下周期规划下一批切片（候选：8.6待用户拍板后的双链合并/新能力切片）
[2026-10-11 02:15] 周期#49 | 验收对标实测（⚡区仅剩8.6待拍板、4.1b/后续待拍板→按流水线预案跑验收+规划下一批切片）：真实WS用户路径4类抽测 tag=ACCEPT48_1791655467 **4/4✅**（开发110.22s dev_hello.py真实落盘+marker命中/调研47.72s web_search真实执行答案Python 3.13发布日2024-10-07正确+主答区零自报不可用/记忆78.45s MEMORY.md探针标记真实写入/协作30.79s subagent_runs双worker产出+近8行=8），采集侧系统帧过滤实证生效（heartbeat×1+self_check×2被过滤）；规划阶段9（Hermes对标工具面补齐）：工具面grep实证对标Hermes核心清单缺口仅剩send_file+process进程管理（sense_transcribe_audio已覆盖ASR），排9.1 send_file/9.2 process/9.3文生图真实后端e2e/9.4 AI群组真实成员端到端四切片+拍板项集中列表；缺口区同步修正7.4已完成的陈旧"仍缺图像生成"表述 | ✅ | (本次提交) | 本周期为验收/规划周期（无代码改动）；Gate1=git diff路线图三处改动真实落盘；Gate2=N/A（纯文档+验收，无新代码符号）；Gate3=acceptance_benchmark真实运行4/4通过（server_side判定+MEMORY.md/文件/subagent_runs数据工件回读）；⚠️诚实边界：4类抽测非全量10类（全量约25分钟防与下周期撞车，10类全量证据在周期#41 FULL10链）；下周期做9.1 send_file文件下发工具
[2026-10-11 02:55] 周期#50 | 9.1 send_file 文件下发工具（Hermes 对标工具面）：新增agent/send_file_tools.py（SendFileTools 确定性校验→MEDIA 标签：空 path/不存在/目录/非普通文件/越界显式报因禁静默，realpath 收口 .. 与软链穿越，根目录默认=用户主目录+/tmp+SOULMATE_SEND_FILE_ROOTS 覆盖并列明；成功=附言+📎信息行（文件名+字节数，0 字节如实报 0）+MEDIA: 末行（周期#39 输出契约））+soulmate_agent 七点接线（import/主 dispatch/code-mode 批内/_send_file_dispatch 共享 lazy-init（成功登记待交付清单）/工具定义/系统提示清单/网关 _merge_pending_send_media 确定性交付兜底——MEDIA 标签模型未回显也补末行走同一通道不双轨、回显不重复、pop 一回合一次、回合开始作废残留，对标 fileviewer-media-tag-pattern"不依赖 LLM"教训） | ✅ | (本次提交) | 9.1勾选；Gate1 git diff 四文件真实落盘（send_file_tools.py 新增+soulmate_agent.py 七点+tests 新增+路线图）；Gate2 grep 接线点全数命中（dispatch 调用×2/工具面 extend/系统提示行/合并方法调用+定义/残留作废），真机 smoke 经真实 dispatch 产出"📎 文件已准备好: soulmate-roadmap.md (127069 字节)"+MEDIA 末行+pending 登记+merge 兜底实证；Gate3 tests/test_send_file_tools.py 18/18（MEDIA 末行契约/消息透传/空 path/不存在/目录/越界含软链穿越/0 字节诚实/相对路径/环境变量根目录/接线护栏+__new__ 真实 dispatch 烟测+merge 三态（未回显补末行/回显去重/一回合一次）+lazy-init 构造）+同域回归151全过（image/docgen/tts/prompt_prefetch/stream_progress_persist/tool_availability/tool_output_wiring 121+code_mode 30）+py_compile+双实例重启 health OK（8092/8095）；⚠️诚实边界：MEDIA 提取正则 `[\w\-\/\.]+` 不匹配含空格文件名（既有通道固有限制，本工具不改网关契约）；前端三键卡片渲染复用既有 MEDIA 渲染面（fileviewer-media-tag-pattern 已实证），本切片未跑真 LLM 回合下发 e2e（merge 兜底保证不依赖模型回显）；周期内另发现工作树存在他方未跟踪 WIP（acp-proxy/agent/tool_infra.py 等）按单写入者纪律未动未提交；下周期做9.2 process 后台进程管理工具（list/poll/log/kill/write/submit 有界子集，禁硬杀长任务）
[2026-10-11 02:44] 周期#51 | 9.2 process 后台进程管理工具（Hermes 对标工具面最后一项，缺口清零）：新增agent/process_tools.py（ProcessTools：start/list/poll/log/kill/write/submit/close 八动作——start=start_new_session 脱离会话进程组（生命周期归系统，会话断开/代理重启不杀）+stdout/stderr 落日志文件；注册表 SQLite 持久 ~/.hermes/soulmate/memory/processes.db（SOULMATE_PROCESS_DB_PATH/SOULMATE_PROCESS_LOG_DIR 可覆盖），重启后 list/poll/log 照常；状态判定真实进程状态+PID 复用守卫（/proc starttime 比对，复用的陌生 PID 不冒认 running）；kill=显式意图默认 SIGTERM 杀进程组（force=true 才 SIGKILL）+有界等待不硬杀+幂等，**工具内无任何到点 SIGKILL 路径**（禁硬杀长任务铁律对齐）；write/submit=stdin 写入（submit=写入+回车）仅本进程启动的进程可用，重启失联显式报"stdin 不可用"禁假装成功；log=行/字节双限截断自报；全部校验显式报因）+soulmate_agent 六点接线（顶部 import/主循环 dispatch/code-mode 批内 dispatch/_process_dispatch 共享 lazy-init/工具定义进 LLM 工具面/系统提示工具清单） | ✅ | (本次提交) | 9.2勾选（Hermes 对标核心工具清单缺口清零）；Gate1 git diff 四文件真实落盘（process_tools.py 新增+soulmate_agent.py 六点+tests 新增+路线图）；Gate2 grep 接线点全数命中（dispatch 调用×2/工具面 extend/系统提示行/def _process_dispatch）+真机 smoke 经真实 dispatch 全链路实证（start→running→exited code=0→log 回读 SMOKE92_MARKER→sleep 30 跑过 1s 仍 running 证不自动杀→显式 SIGTERM 停止 code=-15→list 双行可见）；Gate3 tests/test_process_tools.py 28/28（真实子进程 e2e×2/禁硬杀/SIGTERM vs SIGKILL/进程组收编/kill 幂等/write-submit-close stdin e2e/重启 stdin 失联显式报因/PID 复用守卫含对照组/跨实例持久/日志截断自报与行限/校验拒绝 7 例/接线护栏+__new__ 真实 dispatch 烟测+lazy-init）+同域回归 123 全过（send_file/image/docgen/tts/code_mode/tool_output_wiring）+py_compile+SoulMateAgent 服务同款 interpreter 导入烟测+_build_base_system_prompt 运行时实证 process 行进系统提示+双实例重启 health OK（8092/8095）；⚠️诚实边界：write/submit 只对本代理进程启动且 stdin 未关的进程可用（重启后旧进程无句柄如实报失联，Hermes 的 PTY 全跨重启交互另案）；本切片未跑真 LLM 回合调用 process 工具 e2e（dispatch 烟测+工具面接线已证，真回合归下次验收对标）；periodic smoke 登记行/日志已清理；工作树他方 WIP（tool_infra.py 等）仍未动未提交；下周期做9.3 文生图真实后端 e2e（SOULMATE_IMAGEGEN_API_URL 配置文档化+真实 images API 端到端实证，若无可测配置则文档化配置方法+保持诚实多态并转 9.4）
[2026-10-11 03:25] 周期#52 | 9.3 文生图真实后端 e2e：image_tools.py 生成后端三级链——①SOULMATE_IMAGEGEN_API_URL OpenAI 兼容自有/本地后端优先（原有）②未配置→Pollinations 免费真实 AI 生成（_pollinations_generate：GET /prompt/{quote(prompt)}?width&height&nologo=true 返回图像字节，默认启用，SOULMATE_IMAGEGEN_POLLINATIONS=off 关闭、_URL/_MODEL/_REFERRER 覆盖；隐私边界显式文档化：prompt 文本发 pollinations.ai 云服务）③都关闭→PIL 程序化绘制兜底（诚实标注不变）+配置文档化（_GEN_HINT 三级优先级+各 env）；e2e 实测暴露并修 2 真缺口：①产物格式与扩展名不符（Pollinations 回 JPEG 落 .png，FileViewer mimeType 错配 pitfall 3c）→_align_ext 按 PIL 真实 format 改后缀（副本丢 format 时回探文件头）②公共后端偶发 5xx/402→有界重试 2 次（对齐 MiMo 400 重试纪律）仍失败显式报因禁降级冒充 | ✅ | (本次提交) | 9.3勾选；tests/test_image_tools.py 30/30（新增 Pollinations 7例：真实 HTTP 路径 URL 构建/URL编码禁裸拼/90s超时/model+referrer 透传/失败不假称成功/空内容/opt-out 落兜底/OpenAI 配置优先/JPEG→.jpg 对齐）+工具六套件回归 125/125（image+docgen+tts+browser+send_file+process）+py_compile+真实 e2e 3 次成功（真 Pollinations AI：8658B/11080B/4321B 真 JPEG，PIL 独立回读尺寸格式一致，_image_dispatch 贯通 agent 分发层绿）；⚠️诚实边界：①免费匿名额度间歇 402 限流（实测连跑 3 次 1 成功，属服务端配额非本机故障，REFERRER 可提额；要稳定建议配 SOULMATE_IMAGEGEN_API_URL 自有后端）②真实 AI 语义质量未评（只验了生成链路真通+产物可解码）③OpenAI 兼容 API 形态（b64_json/url）仍走注入 fake 单测（无真实 key 可测）；工作树他方 WIP（tool_infra.py 等）未动未提交；下周期做9.4 AI 群组真实成员端到端（补真实多 agent 成员参与的端到端验收）
[2026-10-11 04:26] 周期#53 | 9.4 AI群组真实多agent成员端到端验收（阶段9全部完成，路线图切片清零剩待用户拍板项）：新增 acp-proxy/e2e_ai_groups_multimember.py 可复跑探针（3真实成员 hermes(advisor)/soulmate(executor)/mimo(verifier) 建组→下任务→自动链式执行全程无手动execute→群组WS同挂采集帧）；首轮实测暴露并根因修复3真缺口：①冒名兜底+PATH盲区双根因（journal实证"Agent resolve: mimo -> hermes"/"soulmate -> hermes"，mimo成员子任务实际以hermes大脑执行，独立性铁律被违反；根因=systemd PATH不含~/.local/bin（which全盲"Agent list refreshed: 0 available"）+_resolve_agent_binary尾部"默认用hermes"兜底静默顶替）→ws_chat.which_agent（PATH+用户安装目录+绝对路径spawn+子进程PATH补全）+_resolve_agent_binary重写（真实身份只认自己、实例后缀剥后缀认本体、找不到原样返回显式"Agent未安装"、删除一切冒名兜底；仅角色槽位保留映射）②观测面冒名（soulmate子任务started事件显示binary=hermes）→_channel_desc如实标注（channel=soulmate-acp vs binary=<自己>）③opencode CLI_ARGS过时（-q实测打印帮助文本exit 0假成功→run，经run_agent_proxy真跑回"收到"） | ✅ | (本次提交) | 9.4勾选（阶段9收口）；Gate1 git diff双仓真实落盘（openmate：探针脚本+路线图；opensoul：ws_chat.py/ai_groups.py+tests×2新增）；Gate2 grep接线点全数命中（which_agent×5调用点/_channel_desc emit点/run_agent_proxy binary_path）+活路径journal实证修复后"Agent resolve: mimo -> mimo"/"soulmate -> soulmate"/"Agent proxy running: /home/climbing/.local/bin/mimo run"；Gate3 tests/test_ai_groups_resolve.py 11/11+test_ai_groups_channel_desc.py 3/3+既有ai_groups回归22/22+py_compile×2+opencode修复经run_agent_proxy真跑实证；复跑e2e VERDICT: PASS 6/6（V1四子任务全scored无手动execute/V2三真实成员各真身回群聊 reply_agents=[hermes,mimo,soulmate]/V3 agent_reply行4=子任务数/V4 WS 70帧task_progress实时广播/V5父任务收口scored avg=5.5/V6 result行source=hermes,soulmate-agent,mimo,hermes无一冒名）；⚠️诚实边界：①首轮探针窗口900s短于链全程（hermes CLI回合5-12分钟级）致首轮V1/V3/V5窗口假红，已改2400s复跑全绿——链路两轮均真实推进，非链路缺陷②run_agent_proxy无执行超时（hermes长回合实测最长12分钟+仍完成；挂死风险另案，未加超时守卫遵"禁硬杀/防守卫"铁律）③AGENT_REGISTRY available全None（检测层which盲区同根因，属"检测=注册=列表"另一面，本切片未修）④两轮测试组数据（group 3d2b4f76/44a1ed8f）留在ai_groups.db可清理；下周期：路线图切片全部完成→跑验收对标实测并把结果写进路线图+规划下一批切片（待用户拍板项8.6/4.1b/ASR不占顺序）
[2026-10-11 04:58] 周期#54 | 验收对标实测（5类抽样+工具面真回合探针）+规划阶段10：**上游 MiMo token-plan 降级实证周期**——acceptance_benchmark 5类（tag=ACCEPT54_1791664601）1/5✅（协作239.93s subagent_runs双worker真实产出ALPHA/BETA_DONE；开发/办公/定时3类WS 300s超时、记忆类141.6s回复=[LLM错误 429]）；直接探针3连实证上游三态并发（429 Too Many Requests/RemoteProtocolError incomplete chunked read/500 503 ServiceUnavailableError）+journal同款错误进真实回合→失败归因上游非本地回归（周期#43 同链路31.5s✅对照）；工具面真回合探针（toolface_probe_ws.py 新增，send_file/process 真LLM回合销9.1/9.2诚实边界）同因未成——但降级期治理栈全按设计工作实证：①8.2证据治理拒绝造假（dev类回合收口"⚠️执行证据不足…未经执行验证"不假称完成）②失败纪律显式报LLM错误非静默③turn_watchdog软预算240s心跳可见④观察窗纪律：定时类WS 300s超时后回合服务端跑完accept_probe行后到落库（harness口径漏记真实完成→10.4）⑤测试产物清理归零（accept_probe行0、/tmp探针产物删除）；ACCEPT_PROMPT_TIMEOUT_S 可配（harness半程修）+规划阶段10四切片（10.1 LLM传输韧性治理最高优先/10.2检测=注册=列表真源收口=周期#53遗留③/10.3 run_agent_proxy挂死可见性禁硬杀/10.4 harness后到产出二次回读） | ⚠️ | (本次提交) | 诚实边界：5/5均非代码缺陷失败（上游降级），有效通过样本仅协作1类；9.1/9.2真LLM回合缺口未销（探针脚本toolface_probe_ws.py已留库可复跑，待上游恢复后跑）；⚠️并发写警示：acceptance_benchmark.py 曾报sibling修改（git diff核实工作树仅本周期改动，HEAD未动）；下周期做10.1 LLM传输韧性治理（上游降级实证=用户可见缺陷最高优先）
[2026-10-11 07:20] 周期#55 | 10.1 LLM传输韧性治理（阶段10第一刀，上游降级=用户可见缺陷最高优先）：utils/llm_retry——MAX_ATTEMPTS 3→5+RETRY_MAX_DELAY_NO_HEADERS_MS 30s→60s（退避上限拉长，无头总退避2+4+8+16=30s有界）+新增RETRY_MAX_WAIT_MS=60s单次等待硬上限（SOULMATE_LLM_RETRY_MAX_WAIT_S可配；根因实证=kilocode对有头等待只cap i32≈24.8天，上游一个Retry-After:86400就让回合静默挂一天=10.1禁的"空转"形态，收口；有头听头语义保留≤上限原样生效）+classify_failure九态确定性分类（rate_limit/timeout_status/server_error/client_error/protocol_error含incomplete chunked read实证形态/read_timeout/connect_error/network_error/transient_message）+llm_engine三路（chat/chat_stream/chat_stream_with_tools）13个重试/放弃决策点接线record_llm_failure→tool_failures.db（tool_name=llm:<path>旁路纪律不阻塞+测试期不污染真实库）+MAX_TURN_SENDS 8→12随尝试数联动（仍<理论最坏4段×5发=20封顶生效） | ✅ | (本次提交) | Gate1 git diff 16文件231行+新测试文件真实落盘；Gate2 grep record_llm_failure 13接线点三路全覆盖+注入故障真实运行命中rate_limit/server_error/protocol_error分类记账行（工具输出实证）；Gate3 tests/test_llm_transport_resilience.py 14/14（真实HTTP服务器注入故障非mock：429+503抖动→回合完成/incomplete chunked read→回合完成/持续429→显式"[LLM错误"5发有界报因/Retry-After:3600→0.2s硬上限截断照常完成（修复前=asyncio.sleep(3600)静默挂））+受影响9套件245全过+全量回归**2350 passed零失败**（23:59；首轮9挂逐一定责：1本切片策略钉=test_llm_stream_usage_emission"3发"断言随MAX_ATTEMPTS重推导、8存量=test_infection_self_heal×7[get_event_loop测试基建排序污染，未改动文件test_health_monitor+infection_self_heal pair复现实证非本切片引入，修run()=asyncio.run]+test_probe_provenance挂周期#53探针e2e_ai_groups_multimember未声明test出身[补真实载荷_meta.source=test声明]，修复后二轮全绿）；⚠️诚实边界：①故障分类记账只接llm_engine三路（utils/llm_transport→dna调用路径未接，另案）②持续降级5发30s退避后仍显式失败非无限等待（"要么完成要么显式报因"达标，完成率取决于上游恢复速度）③错误文案契约逐字未动（既有exact断言零改动）④policy偏差已显式标注在llm_retry常量区（kilocode逐值对照测试改名test_constants_match_policy）；下周期做10.2 检测=注册=列表真源收口
[2026-10-11 06:58] 周期#56 | 10.2 检测=注册=列表真源收口（周期#53遗留③销账）：实现已落 opensoul（姊妹流水线 B3 线先行提交：a01b79cf「detect/uninstall/list/send 统一 which_agent」+77346163「B3探测即注册」，services/agent_registry.py probe_agent=L1真ACP握手（acp-proxy /agents/probe）+L2真开会话（ws session/new 拿sessionId）两级全过才 registered=1、list_registered 只回 registered=1），本周期真机验收闭环销账——①盲区A/B实证：服务真实 PATH（/home/climbing/opensoul/.venv/bin:/usr/bin:/usr/local/bin:/bin，无 ~/.local/bin 与 ~/.npm-global/bin）下裸 shutil.which 对 mimo/opencode/claude/openclaw **全 None**（"available 全None"根因逐字复现）、which_agent 全部命中真实绝对路径；live /api/agents/detect available=True 带真实 path（mimo=~/.local/bin/mimo 等 9 项）=修复已生效（agents.py 05:05落盘 < 05:07:58服务启动，运行进程即新代码，未重启）②真实探测链 e2e **5/5 PASS**（/tmp/probe_chain_e2e.py，AGENT_REGISTRY_DB 临时库隔离不污染真实注册表，网络全真）：mimo L1"ACP handshake OK"+L2 真开会话 sessionId=ses_ed7fd4…→registered=1 进 list_registered；fake-agent-zzz 被拒"不在 acp-proxy 注册表…拒绝探测（禁冒名兜底）"→registered=0 不进列表③live /api/agents/registry total=2（hermes/soulmate 均 registered=1+session_ready=1）vs detect 61 个安装项——未注册不进列表 live 实证④opensoul tests：test_agent_detect_which+test_agents_probe_registry 19/19、test_agents 9/9 全过 | ✅ | (opensoul a01b79cf/77346163；本文档提交) | 10.2勾选；Gate1 git diff 本条目+勾选真实落盘、代码面改动归属 opensoul 两提交（非本周期产出，如实注明）；Gate2 grep which_agent 在 detect/uninstall/list/send 真实路径（a01b79cf）+probe_agent→list_registered 真实链路被 e2e 走中；Gate3 真实探测链 5/5+live API 双证据+opensoul 既有测试 28 例全过；⚠️诚实边界：detect 的 available=安装语义（装没装），真实 ACP 能力语义在 registered/session_ready（探测即注册），验收"available 与真实 ACP 能力一致"按两段语义合验；正例只探 mimo 一例（其余 agent 注册留给管理页用户点"检测"，cron 不批量改用户可见列表）；临时 e2e 库已清理、真实注册表零改动；下周期做10.3 run_agent_proxy 挂死可见性（进度心跳+执行时长可查询+可显式取消，不加到点 SIGKILL）
[2026-10-11 07:45] 周期#57 | 10.3 run_agent_proxy 挂死可见性（周期#53 诚实边界②销账）：ws_chat.py run_agent_proxy 治理三件套——①进度心跳（_heartbeat 后台任务默认30s SOULMATE_PROXY_HEARTBEAT_S，on_event("heartbeat","⏳执行进行中：已用Ns｜已收N行｜最近:xxx") 推群组 _emit_exec_event 事件流可见，旁路纪律失败不冒泡不阻塞）②执行时长可查询（_PROXY_RUNS 登记表+proxy_run_snapshot JSON安全快照实时 elapsed_s/last_event/lines/pid + find_proxy_run 按 task_ref 查找+list_proxy_runs 列举+登记表64条有界）③可显式取消（cancel_proxy_run：SIGTERM 独立进程组[start_new_session=True，killpg 收编整棵进程树]→有界等待3s→force=True 才 SIGKILL，**执行主路径无任何到点自动杀**）+ai_groups 接线（_execute_task_impl 传 task_ref=group/task、显式取消不自动重试不评分任务以取消收口、GET /api/ai-groups/{g}/tasks/{t}/exec-status 查询端点+POST .../exec-cancel 显式取消端点，无可取消对象显式404不造假） | ✅ | (opensoul ws_chat.py/ai_groups.py/tests×2；本文档提交) | 10.3勾选；Gate1 git diff 四文件真实落盘（ws_chat.py +189行+ai_groups.py 接线/端点+tests/test_agent_proxy_run_control.py 新增8例+brain_wiring桩签名兼容**kwargs）；Gate2 grep task_ref 透传调用点+端点调用 find/cancel_proxy_run+心跳 on_event 进 _emit_exec_event 群组事件流，live curl 实证两路由已注册（exec-status 回 run:null 不造假/无可取消对象 404 显式报因）；Gate3 tests/test_agent_proxy_run_control.py 8/8（真实子进程非mock：心跳≥2条+运行中快照查询/显式取消 SIGTERM 如实报 cancelled 禁冒充完成/2.5s 长回合不被误杀/SIGTERM 忽略者 force=True 才 SIGKILL 两级取消/task_ref 查找三态/ai_groups 端点真取消真实子进程+cancel-requested 事件落库/_execute_task_impl 取消不重试不评分+task_ref 透传/源码契约无 wait_for 到点杀）+同域回归50全过+批量 -k 回归63全过+真机烟测 SMOKE_103_PASS（服务同款 interpreter：3.2s 慞回合运行中快照 elapsed=3.2s lines=7 last='tick 6'、task_ref 命中、SIGTERM 取消 returncode=-15、3条心跳事件、结束后 status=cancelled）+opensoul 服务重启 health OK | ⚠️诚实边界：①心跳/取消覆盖 CLI 通道（run_agent_proxy），soulmate ACP 回合（_run_soulmate_turn）挂死可见性另案②含真实 agent 的完整群组任务取消 e2e 未跑（需真实慢 agent 回合 5-12 分钟级，端点层已实测真取消真实子进程）③心跳为固定间隔非输出驱动（输出密集时进度细节看快照 lines/last_event）；下周期做10.4 验收 harness 降级适配（WS 超时任务的服务端后到产出二次回读，验收=模拟慢回合 harness 结果与服务端真源一致）
[2026-10-11 07:55] 周期#58 | 10.4 验收 harness 降级适配（阶段10全部完成，路线图切片清零）：acceptance_benchmark.py 三件——①late_readback 预算内轮询服务端真源（_read_back_reply=agent_messages 该 session assistant 落库行真源回读+类别验证器复用），后到产出出现即判过附"后到回读Ns/M次"，预算耗尽（ACCEPT_LATE_READBACK_S默认300s/POLL_S默认5s）如实False禁把超时当成功②_drain_ws 超时期间持续排水后到帧（防读缓冲顶满反压agent stdout把回合拖死；按sessionId过滤；断连只停排水不冒泡）③main()超时路径与普通WS失败分离（prompt_timed_out标记），verify_mode=server_side_late/reply_assisted_late/ws_timeout_unrecovered三态+summary.late_readback统计 | ✅ | (本次提交) | 10.4勾选（阶段10收官）；tests/test_acceptance_late_readback.py 11/11（预算内后到判过≥3轮询/预算耗尽诚实失败/立即过单次收工不等poll/验证器异常不炸环/DB回复+排水帧合成判据/assistant行真源回读排除user与他会话行/排水sid过滤与计数/断连不冒泡/接线护栏inspect main真实调用+超时路径分离）；真实验收=模拟慢回合（tag=LR58，ACCEPT_PROMPT_TIMEOUT_S=5人为压短WS口径）：WS 5s超时→服务端回合照跑→后到回读20.0s/5次恢复，1/1✅ verify_mode=server_side_late，harness ok与服务端真源一致（dev_hello.py真实落盘含SOULMATE_DEV_OK独立复核）；⚠️诚实边界：单任务样本（开发类），定时类（周期#54实证原型）未在本轮复测——路径同一；全量回归tests/在后台执行，结果以补记为准；至此路线图阶段1-10切片全部清零，余项仅剩待用户拍板（⚡8.6双链合并取舍、4.1b iLink接入形态、ASR语音输入多模态路由）；下拍建议=跑全量10类验收对标（harness已带超时降级适配，上游降级期口径不再漏记后到完成）并规划新一批切片（候选：常驻托管覆盖面复核/语音输入ASR/用户实测反馈优先）
[2026-10-11 08:30] 周期#59 | 11.1 全量10类验收对标实测（阶段11第一刀）：上游MiMo token-plan 恢复后全量单轮真实重跑（tag=FULL11_1791677778，WS /ws/acp 真实用户路径，每类独立session）**10/10 当场全过**——开发48.2s dev_hello.py真实落盘+marker命中/调试38.0s buggy.py修复return a+b+FIXED标记/运维14.4s 8090+磁盘信息/调研32.1s web_search真实执行答案Python 3.13发布日2024-10-07+主答区零自报不可用/发布21.6s opensoul.db投递行=1/办公96.1s report.xlsx 4962B有效xlsx/定时87.1s cron_tasks.db accept_probe行=1/多渠道46.0s/记忆78.0s MEMORY.md标记真实写入/协作37.6s subagent_runs双worker产出；verify_mode=server_side 7类+reply_assisted 3类（运维/调研/多渠道）；本轮WS超时0个（late_readback未触发——10.4降级口径留待下次上游降级期复核）；采集侧系统帧过滤实证生效（heartbeat×3+self_check×7被过滤）；测试产物清理归零（accept_probe行0/MEMORY.md标记0/tmp探针产物删除） | ✅ | (本次提交) | 11.1勾选；Gate1=git diff路线图11.1勾选+11.5新增+本条目真实落盘；Gate2=N/A（纯验收/文档周期，无新代码符号）；Gate3=acceptance_benchmark真实运行10/10通过（server_side者均有文件/SQLite行/MEMORY.md数据工件回读）+结果JSON /tmp/soulmate_accept_results.json回读复核与台面一致；⚠️诚实边界：①每类单次样本（同机低负载）②运维/调研/多渠道3类verify为reply_assisted（回复文本辅证）③late_readback在本轮未被触发（无WS超时），降级期二次回读路径的实证仍在周期#58单任务样本④新发现入11.5候选：办公回合InfectionGate"[注入审计终止]"误杀一轮良性工具执行（文档工具输出目录受限vs /tmp绝对路径被判劫持），产物仍生成但回合多走弯路；下周期做11.2 _run_soulmate_turn挂死可见性（10.3三件套扩到群组soulmate ACP回合通道）
[2026-10-11 08:58] 周期#60 | 11.2 _run_soulmate_turn挂死可见性（10.3三件套扩到群组soulmate ACP回合通道，周期#57诚实边界①销账）：opensoul d1b85151——ws_chat.py抽register_proxy_run/finish_proxy_run共用登记表（channel=cli/acp_ws快照带channel）+cancel_proxy_run通道感知（acp_ws=cancel_cb摘观察窗停等，会话生命周期归系统不杀远端生成，绝无到点自动杀；CLI=killpg信号链原样）+ai_groups._run_soulmate_turn接线（登记表→exec-status/exec-cancel同套语义、心跳30s走on_event→_emit_exec_event群组事件流、返回(text,source,success)对齐run_agent_proxy取消直通不重试不评分）+诚实超时（等待预算耗尽如实报"回合未完成"+已收部分保留禁把超时当成功） | ✅ | d1b85151 | 11.2勾选；Gate1=git diff 251+265行两仓真实落盘+key扫描0命中；Gate2=grep实证_run_soulmate_turn真实调用点(_execute_task_impl传on_event/task_ref)+register_proxy_run双通道接线+exec-status/exec-cancel端点测试真调用；Gate3=新测试6/6+10.3回归8/8+agent_proxy 3/3+ai_groups 46全过（真实websockets服务端非mock）+真机live :8092真实SoulMate回合24.0s回"OK"心跳×2+快照逐秒可查+done收口；opensoul.service已重启加载新代码（/api/ai-groups/health ok）；下拍=11.3 LLM故障分类记账补链
[2026-10-11 09:19] 周期#61 | 11.3 LLM故障分类记账补链（周期#55诚实边界①销账）：utils/llm_transport 增 _record_failure（lazy import 复用 agent.llm_engine.record_llm_failure 单一真源）——记账点=重试排程/放弃双接×状态与传输两分支+底部链路放弃点+防护层两出口（自身异常/HTTP 200空输出=dna"静默返空"历史形态），TransportError 不双行重复记；path 归因线程化（dna→llm:dna、gateway→llm:gateway，strand_id 进 error 行），降级链换 link 成功也留上游失败审计行 | ✅ | (本次提交) | 11.3勾选；Gate1 git diff 3仓文件+新测试真实落盘（61行+新增测试文件）；Gate2 grep/接线钉测试（_record_failure≥5记账点、path="dna"/"gateway" 真实调用点、llm:gateway 归因不串 llm:dna）；Gate3 tests/test_llm_transport_accounting.py 9/9（真实HTTP注入故障非mock：429抖动→rate_limit retry行+成功/持续503→server_error give_up行/incomplete chunked read→protocol_error行[实测坑：fault handler 不置 close_connection=True 则 FIN 不达客户端变 ReadTimeout，已修进测试]/降级链备胎成功上游失败仍留审计行/200空输出记账含"空内容"/记账器炸不阻塞链路）+同域回归 281 全过（llm_transport_resilience/core/engine_fallback/model_fallback/error_contract/turn_park_merge/network_park_hitl/tool_recovery 198+dna breaker/storm/gateway_config 83）；真机 smoke（服务同款 interpreter ~/.hermes/hermes-agent/venv）：真 503 故障服务器→llm:dna 5行记账（4 retry+1 give_up，kind=server_error，[strand_a] 归因）SMOKE113_PASS；⚠️诚实边界：①记账器居 agent 层为 10.1 既有形态（lazy import 解耦，未搬 utils）②降级链成功后上游失败行 outcome=failed 属"该 link 失败"语义（整体调用成功不改写审计行）③真实进化回合的失败审计待下次 evo 真实降级事件在 ~/.hermes/soulmate/memory/tool_failures.db 出现 llm:dna 行即闭环（本切片未跑真实 evo 周期）；下周期做11.4 soulmate-daemon 常驻托管覆盖面真实验收（后建任务/目标也被轮询拾取）
[2026-10-11 09:45] 周期#62 | 11.4 soulmate-daemon 常驻托管覆盖面真实验收（周期#48遗留销账）：新增 acp-proxy/acceptance_daemon_coverage.py 可复跑探针（生产单例同一写入路径建探针→轮询服务端真源 runs/monitor_events 行且时间戳>创建时刻防陈旧行假阳性→journal 归因执行者→清理归零回读），真实跑 8/8✅——daemon（01:51:53启动 PID 515550）存活7.5h后新建 task#11(interval:30s)+target#2(port 8090)：21s 后监控拾取真实检查（ok"端口 8090 监听中"）、52s 后 cron 拾取真实 LLM 回合执行成功（runs status=ok end_turn+journal "✅ 执行 #11 accept114_cron → end_turn"+真源"Response: OK"），ps 实证无其他轮询宿主=daemon 独占拾取；代码佐证 claim_due/_due_targets 每 poll 重新查库无启动快照；产物清理归零（runs/monitor_events/session/messages 全 0）；回归 tests/test_soulmate_daemon+soulmate_scheduler+health_monitor 56/56 过 | ✅ | (本次提交) | 11.4勾选；⚠️诚实边界：同机低负载单样本×2轮；首轮空 session_id 探针触发"拒绝给消失的会话造回合"既有失败纪律（failed 行可见=失败不假装成功语义照常，真实用户路径 schedule_task 恒带 session_id 不受影响）；⚠️并发警示：utils/llm_transport.py+llm_gateway.py 09:30-09:31 被其他会话改动（幂等记账 _record_failure_safe WIP，非本周期工作，单写入者纪律未动未提交）；下周期做11.5 注入审计误杀良性文档任务治理（审计判据区分"路径越界"与"注入劫持"，越界回显限制说明不升级终止）

[2026-10-11 10:05] 周期#63 | 11.5 注入审计误杀良性文档任务治理（"路径越界"与"注入劫持"区分）：journal 08:20 实录还原真实根因（比路线图初稿更具体）——用户明文指定 /tmp/soulmate_accept/report.xlsx，doc_generate 输出目录受限产物落 ~/.hermes/soulmate/documents/report.xlsx（工具结果"✅ 文档已生成…4962 字节"即来源证明），助手 cp 到用户指定路径；审计员只看输出文本+待执行调用**看不到工具结果**，把本回合刚生成的文件当"来源不明的既有文件"→clarify 反复→超限升级 terminate 杀整轮（"[注入审计终止]"）。治理四件套：①审计输入附"本回合已执行的工具结果（来源证明）"快照（check() 新参 prior_tool_results≤12条×500B）②JUDGE_PROMPT 判据第7条（路径越界≠注入劫持+同回合工具产出有来源证明）③确定性溯源降级 resolve_path_claims（每个被质路径真源自证：用户明文指定/工具结果生成语义行→降级放行+留痕 provenance_pass_with_trace 不升级终止；不可自证或另有外发/篡改指控→保持原判）④docgen 越界回显限制说明（输出目录受限+解法显式报出）；实测踩坑一处：判词标签残片 "</clarify>" 的 "/clarify" 曾被误当路径 token 拖垮降级核验（extract_claimed_paths 剥 XML 标签+要求≥1级目录，回归测试钉死） | ✅ | (本次提交) | 11.5勾选；Gate1 git diff 六文件真实落盘（infection_guard.py +111/soulmate_agent.py +15/docgen_tools.py +10/新测试 22 例/self_heal 测试断言随三元组扩展修正/路线图）；Gate2 grep prior_tool_results= 真实调用点+resolve_path_claims 在 check() 降级分支+留痕分支含 provenance_pass_with_trace，真实回合经真实调用路径无 TypeError 即接线实证；Gate3 tests/test_infection_provenance.py 22/22（事故判词逐字回归/terminate 同语义降级/反复 clarify 不再升级 terminate/负控制外发指控·不可自证路径·非来源类 terminate 均保持原判/审计输入含来源证明行/JUDGE_PROMPT 判据钉死/接线护栏+docgen 越界回显）+同域回归 188 全过（infection_guard 47+self_heal 20+docgen 12+report_evidence/self_verify/tool_availability/remediation_style/frame_origin/turn_watchdog）+py_compile；真实验收=办公类事故场景原样复测（acceptance_benchmark ACCEPT_CATEGORIES=办公 tag=PROV115_1791703400，WS 真实用户路径）**1/1✅ 37.77s end_turn**（对照事故轮 96s+弯路）：report.xlsx 4963B 有效 xlsx 落 /tmp/soulmate_accept/report.xlsx、工具账本实证 doc_generate→terminal cp→openpyxl 回读 ('名称','数量')/('苹果',3)/('香蕉',5) permission=allow、journal 全窗口零"[注入审计终止]"零 clarify 判词（判词直接 ok，来源证明+判据让审计员不再误判）、回复"已用文档工具生成 Excel，并复制到指定路径"（docgen 限制说明引导生效）；测试产物清理归零；双实例重启 health 200/200 | ⚠️诚实边界：①真实复测中审计员直接判 ok，确定性降级路径未在真 LLM 判词下触发（以事故判词逐字注入单测实证，真判词误报时的降级留待自然发生观察）②单样本同机低负载③降级负过滤按行为词窄签名（外发/篡改类），新形态混合判词漏网时回到正常审计不误放；⚠️并发警示：utils/llm_transport.py+llm_gateway.py 仍为他会话 WIP 未动未提交（周期#62 同警示）；下周期：路线图切片再次清零（余项仅待用户拍板 ⚡8.6/4.1b/ASR）→跑验收对标抽样或规划新一批切片（候选：用户实测反馈优先/常驻托管覆盖面扩测/轨迹采集抽查）

[2026-10-11 10:35] 周期#64 | 验收对标抽样实测（路线图清零后）+规划阶段12：5类抽样（tag=FULL12_1791684846，WS真实用户路径，ACCEPT_CATEGORIES=开发,办公,定时,协作,记忆）**5/5通过**——开发301.74s dev_hello.py+marker命中（**WS 300s超时→10.4后到回读0.0s/1次恢复**，verify_mode=server_side_late）、办公65.26s report.xlsx 4963B有效xlsx（11.5治理回归目标全窗口零注入审计终止）、定时74.53s accept_probe行=1、记忆106.81s MEMORY.md标记、协作27.4s双worker产出；4类server_side+1类server_side_late（本抽样无reply_assisted弱项）；结果+新发现写入路线图"2026-10-11抽样对标实测"节，规划阶段12六切片（12.1多渠道出站独立可查痕迹+verify升级/12.2运维调研verify升级/12.3开发类301.74s超时复核归因/12.4注入审计LLM往返开销治理=8.3另案/12.5 harness收尾清理+late_readback扩测/12.6轨迹审计闭环抽查） | ✅ | (本次提交) | Gate1 git diff 路线图3区块真实落盘（实测表+阶段12+周期日志）；Gate2=N/A（纯验收/规划周期无新代码符号）；Gate3=acceptance_benchmark真实运行5/5+结果JSON /tmp/soulmate_accept_results.json回读（passed 5/5、late_readback ws_timeouts=1 recovered=1）与台面一致+测试产物清理归零（accept_probe行0/MEMORY.md标记0/tmp工作目录删除）；新证据：①10.4后到回读路径首次非构造场景真实触发（11.1遗留④部分销账，仍单样本）②开发类48.2s→301.74s显著变慢但产出1分钟内就绪=超时在回复回收侧，疑上游波动/冷启动首回合，归因入12.3（单样本禁定论）③harness收尾清理边界实证：cleanup()不删/tmp工作目录（仅开跑前rmtree，docstring承诺未兑现），本轮残留3件已手动清理入12.5；⚠️诚实边界：每类单次样本同机低负载；下周期做12.1多渠道出站独立可查痕迹+verify升级server_side

[2026-10-11 10:50] 周期#65 | 12.1 多渠道出站独立可查痕迹+verify升级server_side（阶段12第一刀）：新增agent/outbound_audit.py（OutboundAudit：outbound_log表单一写入源=ts/channel/target/kind/status/blocks/sender/dedup_key/text_preview九字段，record旁路纪律写失败只计数返回''绝不阻塞发送、recent四维过滤回读、clear拒绝全表清空防误删审计）+wechat_bridge三条出站路径留痕（_send_blocks回复路径dedup_key=入站msg_id、send_proactive主动文本、send_media_proactive媒体，失败态照留status=failed/blocks=0，_record_out lazy注入可测）+message_router openmate出站留痕（成功dedup_key=om-rowid/失败照留；wechat经桥留痕一次=单一真源不双份记账，实测恰1行）+acceptance_benchmark三件（多渠道verify从回复辅证改服务端痕迹回读=channel=wechat+target=accept_peer+status=sent+text_preview含探针文案，空回复也判过、多渠道进SERVER_SIDE、cleanup预清痕迹探针行） | ✅ | (本次提交) | 12.1勾选；Gate1 git diff 5文件528行真实落盘；Gate2 grep接线点（_record_out×3发送路径+message_router接线+verify服务端读路径）+真机全链路实证（真实router→桥→审计库→verify_multichannel空回复=True「出站痕迹行=1」→清理归零）；Gate3 tests/test_outbound_audit.py 14/14（行形状/过滤回读/clear守卫/跨实例持久/写失败旁路/微信三路径留痕含失败态/openmate成败双留/统一出站不双份记账/接线护栏harness服务端判据）+同域回归77全过（wechat_bridge/message_router/wechat_router/media_channel）+双实例重启health OK（8092/8095）；⚠️诚实边界：iLink真实传输仍loopback（4.1b待用户拍板不动）；痕迹只覆盖出站（入站去重已有BridgeStore/RouterStore）；本切片未跑真LLM回合多渠道类验收（harness判据改造+全链路smoke已证，全量复测归12.5扩测轮）；下周期做12.2 运维/调研verify升级server_side
[2026-10-11 11:15] 周期#66 | 12.2 运维/调研 verify 升级 server_side（11.1 两类 reply_assisted 弱项销账，10/10 类全部 server_side）：真源=opensoul.db trajectory_events 工具账本（trajectory_recorder 真实回合既有落账，零新增落库）——运维=体检结果落账（system_checkup 行 result_preview 含 8090+磁盘结论）、调研=检索调用账本+来源行（web_search 行 result_preview 含来源 URL）；acceptance_benchmark 增 _tool_ledger（parse_ledger 单一真源+会话上下文 _VERIFY_CTX 注入+异步落账有界轮询4s+ok/missing/unavailable 三态）+verify_ops/verify_research 双判据+账本不可用诚实降级 reply_assisted；连带修2真缺口（①周期#59 快照读取器漏传 db_path/硬依赖 created_at 列致 _read_back_reply 既有测试红——补参+去依赖+"\n\n"行间契约还原 ②8.5c-② 治理抽取盲区：事故句"当前会话的工具列表中没有 web_search"容器在前形态漏抽注册类→更正声明未触发——report_evidence 增 _REG_LIST_CONTAINER+"会话的"间隔词放宽） | ✅ | (本次提交) | 12.2勾选；Gate1 git diff 真实落盘（acceptance_benchmark +111行/report_evidence +11行/双测试文件）；Gate2 inspect 接线护栏（main 注入会话上下文+降级标注≥2处）+新抽取正则被 find_availability_claims 真实循环走中；Gate3 tests/test_acceptance_verify_ledger.py 12/12（账本命中/missing 不假过/unavailable 降级/双判据都要/来源行/会话隔离/被拦行不计/迟到落账轮询/接线护栏/全局态隔离fixture）+test_tool_availability_claims 22/22（含事故句逐字回归+registry 证伪+负控制）+同域回归 108+89 全过+真实WS验收两次实证：运维 ✅20.71s 体检结果落账=命中（账本行=1）、调研首轮 ❌诚实失败（账本 web_search 行=0+回复自报不可用——新判据正确拒绝假过，同时暴露上述治理盲区）→修复后复测 ✅46.74s 检索账本+来源行=命中（web_search 行=1）回复"web_search 可用：Python 3.13 于 2024 年 10 月 7 日正式发布"；⚠️诚实边界：①模型拒做+伪报"工具列表中没有 web_search"属 LLM 行为（sandbox 未受限零裁剪+_active_tool_names 在册实证为假话），治理只能事后更正不能强制调用——本轮复测模型自行正常调用，同题复发率待用户实际使用观察 ②账本判据依赖 trajectory 写入链路存活（fire-safe 异步，未写入时诚实降级不假过不假失败）③每类单样本同机低负载；下周期做12.3 开发类回合 301.74s 超时复核归因（journal 四段计时+≥3 轮样本禁单样本定论）
[2026-10-11 12:10] 周期#67 | 12.3 开发类回合 301.74s 超时复核归因（四段计时+5样本复测）：新增 acp-proxy/turn_timing_report.py（trajectory_events+journal 错误行扫描+未归属空洞检测的四段归因工具，实测跑通事故轮+5样本会话）+soulmate_agent step 自省 LLM 往返补 segment span（`_self_reflector.reflect` 首试/重试两处——此前静默零行，323.7s 空洞全靠 journal 才定位）。归因实锤：事故轮 380.73s 中 323.69s 空洞=step1 自省调用上游传输挂死（静默约313s 后 RemoteProtocolError peer closed→llm_transport 重试(1/5) 8s 成功），**触发=上游波动（5样本0复发）、放大=本地传输层无 stall 超时（本地可控）**；正常开发回合 34-47s 构成=规划/预步 4.4-8.8s+工具循环 LLM 19.4-31.8s（首包 5-8s/轮）+step 自省 0.03-9s+工具执行毫秒级+回复回收 0.3-0.4s，注入审计仅 1/5 轮触发一次判词约16s。顺带发现三项（已入 12.4/12.6）：①注入审计误杀良性 dev 回合 1/5（terminal 被判"疑似提示词注入劫持"terminate 工具未执行，疑触发=harness 防缓存行"（此行仅为防缓存标记，忽略其语义）"措辞像注入指令，judge detail=空=判词依据不落账）②verify_dev 只查文件 marker 不查"用 terminal 运行它"（S1 被误杀步骤仍判 pass）③tool_call.duration_ms 恒 0=工具执行时长未落账 | ✅ | (本次提交) | Gate1 git diff 真实落盘（soulmate_agent.py +28/-2+turn_timing_report.py 新增 225 行）；Gate2 grep step_reflect 两处接线点+真实回合经真实调用路径落账（opensoul.db step_reflect 行 4 条带真实时长 7.57/3.77/8.98/0.03s=接线实证，非测试stub）；Gate3 5 次开发类真实 WS 验收 36-47s 全过（S1-S5，含 2 次插桩后样本）+step_reflect 行回读+turn_timing_report 五会话 JSON 与台面一致+回归 69 全过（trajectory_rounds/plan_execution 23+task_decomposer/self_verify/tool_output 46）+py_compile+双实例重启 health 200/200；⚠️诚实边界：①S1"pass"仅文件判据成立（terminal 步骤被审计误杀未执行，判据弱项如实报入 12.6）②5 样本同机低负载，0 复发≠不复发（上游波动偶发性）③stall 超时修复未实施（utils/llm_transport.py 他会话 WIP，单写入者纪律不动，归 12.4 解禁后）；下周期做 12.4 单工具链注入审计 LLM 往返开销治理（12.3 已并入 stall 超时+误杀证据）
[2026-10-11 12:36] 周期#68 | 12.4 第一刀·注入审计确定性溯源预筛+防缓存标记行折叠（单工具链 LLM 判词往返治理+误杀疑触发治理）：新增 agent/audit_prescreen.py（prescreen_batch 免审判据=用户明文连续原文片段全覆盖+残渣惰性（空白/引号/中文标点；ASCII 路径/shell 语义字符·字母·数字即升级审计，挡"用户写 rm -rf 模型补 /"拼装逃逸；覆盖算法迭代上限 64 轮保守回落）；只认 user_text 溯源，本回合工具结果不作免审依据（web 内容注入恰藏在工具结果里）+fold_marker_lines 窄签名自述占位行审计输入折叠（12.4② 疑触发"此行仅为防缓存标记，忽略其语义"不再进判词输入，RUN_TAG 保留可审计））+InfectionGate.check 两点接线（启发式签名在预筛之前零缩水；免审留痕 source=prescreen_user_verbatim+prescreen_skipped 计数） | ✅ | (本次提交) | Gate1 git diff 25 行+新增 2 文件落盘；Gate2 check()=soulmate_agent 真实工具执行路径（InfectionGate 6 处引用，agent.start 子进程 per-session spawn 自动载新码）；Gate3 tests/test_audit_prescreen.py 16/16（免审 4+零缩水负控制 4：自造参数 mock terminate 保持终止/工具结果溯源不豁免/逐词拼凑不算溯源/混合批次升级+标记折叠 3+接线 1）+影响面 9 文件 190/190 全绿（连带修周期#67 漏更新的 test_trajectory_recorder 接线钉 start 3→5 段/end 6→10/status 4→6）；12.4 未完下拍第一优先：①llm_transport stall 超时（WIP 解禁后）②执行动词残渣免审扩展（先证零缩水）③免审命中率实测

[2026-10-11 13:35] 周期#69 | 12.4① LLM传输无流停顿检测（12.3并入项）+evo WIP记账错位红灯修复：新增 utils/stall_guard.py（send_with_stall_guard 独立看门狗——asyncio.wait_for 按**字节间隙**计时：等响应头+响应体每 chunk 前各一拍，不依赖 httpx 超时机制；间隙>stall（默认60s）或总时长>deadline（默认240s）→本次尝试中止抛 httpx.ReadTimeout（错误契约零变化：is_transient=True/classify=read_timeout/既有重试环原样接住）→重试恢复；涓流间隙<stall 照常收完整 body=零误杀负控制；SOULMATE_LLM_STALL_TIMEOUT_S/_ATTEMPT_DEADLINE_S/_STALL_GUARD=off 可配；非 httpx 替身 client 与鸭子类型 resp 保持原契约不动既有替身测试）+双落点接线（llm_engine.chat()=事故真实路径+utils/llm_transport._post_single_link）；事故机理实证（journal 10:19+tool_failures llm:chat|protocol_error 双取证）：响应头已到（chunked+gzip MiFE）后 285s 零字节、httpx read=120 **未开火**（websockets serve 循环超时盲区，_make_client docstring 同族已知问题）→对端 FIN 才见 RemoteProtocolError(incomplete chunked read)、重试 2s 后 8s 恢复——12.3"挂死约293s才见错、重试本可8s恢复"的根因即缺独立停顿看门狗；**落点修正**：路线图初稿写"落点 utils/llm_transport"，代码+账本双实证事故路径是 llm_engine.chat()（record 名 llm:chat 唯一归属 chat()），守卫共享单源两处都接、流式路径另案；连带修复红灯账本（Gate3 前置阻塞）：evo 混入 WIP（345440e4）把 record_llm_failure 参数错位（source 当 path、action/attempt 压成 code→llm:dna 错标 llm:empty_output、九态全 unknown、attempt 恒1）——纯 HEAD git worktree /tmp/wt69-check 复测 6 例红**先证非本切片引入**→恢复 11.3 逐字段保真转发（_record_failure_safe 改全参签名）+撤除 WIP 无请求身份的传输层幂等去重（path 非请求ID→不同请求同形失败被误并=审计丢行；模块级缓存跨测试/跨库误抑制实证"no such table: tool_failures"；请求级幂等归持有 uuid 身份的调用方） | ✅ | (本次提交) | Gate1 git diff 5文件真实落盘（stall_guard.py 新增+llm_engine/llm_transport 接线+账本修复+tests+路线图）；Gate2 inspect 接线钉（chat()/_post_single_link 走 send_with_stall_guard+流式路径不动断言）+活路径实证（真实 strand_b 13:23 记账行 llm:dna 正确归因经修复后代码落库；13:06 真实行 llm:empty_output=错位现形对照）；Gate3 tests/test_llm_stall_guard.py 16/16（真实socket故障注入非mock：等响应头/响应体两类停顿有界中止+chat() 真实事故路径 e2e 停顿→重试→完成+涓流/慢TTFB 零误杀负控制+deadline 涓流兜底+错误契约 read_timeout+响应契约 json/text/status+接线护栏+env/旁路）+test_llm_transport_accounting 9/9（6例红转绿）+受影响面 359/359（transport_resilience/core/retry_policy/engine_fallback/error_contract/model_fallback/turn_park/network_park/stream_usage/wakeup_fire/dna storm×2/gateway_config）+py_compile+双实例重启 health 200/200+真实 tool_failures.db 零测试污染（测试全走临时库）；⚠️诚实边界：①stall 默认 60s 取值依据 chat() 实测单次 0.03-16s 三倍余量，若真实负载出现合法 >60s 非流式生成调 env 上调（>120s 者今天本就 ReadTimeout 不新增误杀面）②流式路径（chat_stream*/chat_stream_with_tools）消费端挂起语义同族盲区本切片未动（partial 输出/has_output 重试规则耦合另案）③真实上游自然降级期的恢复时长未在本轮实测（"停顿→重试恢复"以故障注入实证）④test_slow_ttfb 用例 TTFB 0.3s 余量较窄仅证语义；⚠️遗留地雷（已记 12.6）：dna_evolution._record_llm_failure_safe 死包装（零调用点）签名与恢复后 _record_failure_safe 不兼容=接线即 TypeError；tool_failures.db ts 存秒非毫秒违全局时间规范；下周期做 12.4②免审面扩展（先证零缩水）或 12.5 harness 收尾清理+late_readback 扩测

[2026-10-11 13:45] 周期#70 | 12.4② 注入审计免审面扩展·执行动词残渣窄名单（先证零缩水）：audit_prescreen 增 _EXEC_VERB_RE 窄名单（python/python2/python3/python3.N/node/bash/sh）+residue_is_exec_verb（残渣**恰好单 token**=解释器名才豁免，多余任何字符即升级）+value_exempt_from_audit（溯源类型 user_verbatim/exec_verb:<tok>）+prescreen_batch_ex（source 三态，批次内含执行动词豁免则整批如实标 prescreen_exec_verb 不冒充全明文；prescreen_batch 保持 2 元组薄包装兼容）+InfectionGate.check 接线 source 按溯源类型留痕。零缩水论证=新增豁免面仅"模型自选解释器名"一个 token（运行用户点名的脚本文件，无注入语义；用户明文部分语义与 8.1② 逐字一致），负控制钉死：破坏性动词 rm/sudo/curl/awk/sed/xargs/env、flag 逃逸 -c/-m/-e、链式 ;/&&/|、重定向 >、多 token、大小写/版本形态变体、工具结果溯源、批次内一个脏调用→全部照常升级 LLM 审计；启发式劫持签名仍在预筛之前。真机 smoke：12.3 事故形态 python3+明文路径→免审（source=prescreen_exec_verb，LLM 零调用）、链式拼装/rm 负控制照常 terminate+LLM 往返=1 | ✅ | (本次提交) | 12.4②勾选（12.4 仅剩③免审命中率实测）；Gate1 git diff 4 文件真实落盘；Gate2 grep prescreen_batch_ex 接线点（infection_guard import+check() 真实路径）+服务同款 interpreter（~/.hermes/hermes-agent/venv）导入烟测+真机 smoke 经真实判定路径；Gate3 tests/test_audit_prescreen.py 24/24（新增 10 例含零缩水负控制）+同域回归 192/192（infection_guard/self_heal/provenance/report_evidence/self_verify/tool_availability/remediation_style/frame_origin/turn_watchdog/docgen）+py_compile+双实例重启 health 200/200；⚠️诚实边界：豁免只覆盖注入审计维度（内容溯源），意图/执行权限维度不在此门（permission_gate 照常）；名单窄收（perl/ruby/三段版本号不纳入，漏网回到 LLM 审计不误放）；exec-verb 免审对真 LLM 回合的实效（12.3 误杀同题复发率）待自然 dev 回合观察；⚠️并发警示：infection_guard.py 编辑中收到 sibling 修改告警，git diff 核实工作树仅本周期改动无他会话混入；下周期做 12.5 harness 收尾清理+late_readback 扩测（或 12.4③ 免审命中率实测）

[2026-10-11 13:58] 周期#71 | 12.5 harness收尾清理+late_readback三类别扩测（10.4单样本边界+11.1遗留④销账）：acceptance_benchmark.py 增量两件——①cleanup() 末尾 rmtree(WORKDIR)（run 末产物显式清理，兑现 docstring"测试产物显式清理"承诺=周期#64 实证残留 3 件形态根治；setup() 预清路径幂等；cleanup 的 agent_messages 探针行清理改走 AGENT_MESSAGES_DB 模块常量单一真源=测试可隔离不摸真实库）②真实扩测 tag=LR125_1791716400（ACCEPT_PROMPT_TIMEOUT_S=20 人为压短 WS 口径构造多类别后到产出场景，ACCEPT_CATEGORIES=开发,办公,记忆）：**3/3 全部 WS 超时→后到回读 3/3 恢复**（ws_timeouts=3 recovered=3，verify_mode=server_side_late×3）——开发 20.98s（后到回读 0.0s/1次，超时时刻产出已就绪 dev_hello.py+marker）、办公 40.02s（20.0s/5次，report.xlsx 4979B 有效 xlsx 落 /tmp/soulmate_accept）、记忆 25.01s（5.0s/2次，MEMORY.md 含 ACCEPT_MEM_MARK_9）；run 末 /tmp/soulmate_accept 实证已删除（ls 报 No such file）、测试产物清理归零（MEMORY 标记 0/accept_probe 行 0） | ✅ | a1a0a903 | 12.5勾选；Gate1 git diff 5插入2删除+新测试文件真实落盘；Gate2 grep cleanup() 在 main 真实路径（L705）+rmtree 落 cleanup 末尾（本次真实 run 走中=run 末目录消失即实证）；Gate3 tests/test_acceptance_cleanup.py 5/5（rmtree 核心契约/缺失幂等/setup 重建 buggy.py/模块常量隔离真库探针行清理/接线护栏）+既有 harness 双测试 23/23（late_readback 11+verify_ledger 12）零回归+py_compile+结果 JSON /tmp/soulmate_accept_results.json 回读与台面一致；⚠️诚实边界：①late_readback 三类别均构造场景（人为压短 WS 口径），自然超时场景的三类别恢复仍待上游降级期自然发生复核②开发类 0.0s/1次=产出先于超时就绪（非轮询恢复形态，恢复多样性由办公/记忆 20.0s/5次、5.0s/2次补证）③每类单样本同机低负载；下周期做 12.6 轨迹/审计闭环抽查（tool_failures.db 抽查探针+llm:dna 归因不串线+12.4③ 免审命中率实测）

[2026-10-11 14:21] 周期#72 | 12.4③ 免审命中率stats实测观察（12.4全部完成）：InfectionGate 增 prescreen_escalated/llm_audits 计数+stats() 快照（命中率无数据报 None 禁假 100%），journal 双留痕行"预筛免审（source）/预筛升级审计"带累计命中率（stats() 真实接线进日志路径非死代码），soulmate_agent 免审批次账本留痕（[infection-guard] 伪条目 permission=infection:prescreen_exempt，report_evidence.parse_ledger 两头不计零缩水钉死）+可复跑探针 acp-proxy/prescreen_hitrate_report.py（journal+trajectory_events 双通道交叉核对不一致如实报差异；实证两坑：账本列名 metadata_json 非 metadata、arguments 落库为 Python repr 非 JSON→双形态解析）。**真实实测** 3 类真实 WS 验收 3/3✅（开发 20.01s/调研 33.43s/办公 55.27s，本切片门改动零破坏）：接线后干净窗口 6 工具批次命中率 50%（免审 3=user_verbatim 2+exec_verb 1；升级 3=办公类模型自造 xlsx 内容照常走 LLM 审计），journal 与账本双通道计数一致 3=3；实测暴露并修复 1 真缺陷=子进程 stderr 被 ws_acp 中继双份记账 journal 致样本翻倍，探针按内层日志行去重（测试钉死） | ✅ | 001918de | 12.4③勾选（12.4 ✅ 全完）；Gate1 git diff 46插入（infection_guard+soulmate_agent）+2 新文件真实落盘；Gate2 grep stats() 调用点=journal 日志行真实路径+账本留痕实证 3 行真库回读（om-9c042303d473 等）+探针消费 journal 行契约测试互相咬合；Gate3 tests/test_prescreen_stats.py 12/12+同域回归 147/147（audit_prescreen/infection_guard/provenance/self_heal/report_evidence）+3 次真实验收 3/3+探针真实输出（双通道+去重后口径）；⚠️诚实边界：①窗口内命中率 50% 为 3 次真实运行小样本，长窗口自然分布待后续周期探针持续观察（探针可复跑）②接线前批次（13:56 前）只有 journal 行无账本行，宽窗口双通道计数差异=接线时间边界所致（探针如实报警不取其一）③免审仅省注入审计 LLM 往返，权限门/启发式签名零变化；下周期做 12.6 轨迹/审计闭环抽查（阶段12最后一片）
[2026-10-11 15:12] 周期#73 | 12.6第一刀·轨迹/审计闭环抽查探针+③④根因修复：新增 acp-proxy/audit_trail_probe.py（只读六面可复跑：分类行完整性值域核/孤儿行含 step工具名-收尾工具名 note 连接口径/llm 归因串线（llm:<已知path>+llm:dna 带 strand 归因）/llm:dna 闭环/ts 毫秒普查/11.5 溯源留痕自然触发观察）+修3根因（③record() ts 秒→毫秒UTC存量不改写④dna_evolution._record_llm_failure_safe 死包装对齐委托（TypeError 被吞记账静默丢失地雷→正确签名+strand 归因+request_id 幂等保留）+classify_failure 九态码串透传与 httpx.RequestError→network_error 兜底=DecodingError 4行unknown缺口根因）+顺带修存量测试基建红灯（test_prescreen_stats _run get_event_loop 批内污染6红，纯HEAD worktree 先证非本切片引入→asyncio.run） | ✅ | (本次提交) | 12.6 部分完成（探针+③④✅，余①infection-guard判词依据不落账②verify_dev判据弱项=下拍第一优先；11.5 观察当前0行未自然触发如实报）；Gate1 git diff 5文件57行+2新文件真实落盘；Gate2 接线实证=探针经 ToolRecoveryManager.record 真实写入路径产出毫秒行回读+classify 透传/RequestError 经 record_llm_failure 真实调用链（测试走真实 writer 非 stub）；Gate3 tests/test_audit_trail_probe.py 25/25+批内回归 127/127（原6红转绿）+受影响面 251/251（llm transport/retry/accounting/stall/dna storm×3/tool_recovery）+py_compile+服务同款 interpreter 烟测 SMOKE126_PASS+真实库抽查实证（2 归因串线 WIP 历史行/孤儿0/llm:dna 4→6条闭环）+双实例重启 health 200/200；⚠️诚实边界：①串线 2 行为 WIP 错位历史产物不改写历史（探针持续如实报）②11.5 溯源降级留痕 0 行=未自然触发非缺陷，待自然发生复核③存量 133 行秒单位与新毫秒行混存由探针普查标注，不做迁移④期间真实库新增 llm:dna 行仍落 unknown=服务重启前旧码所记，重启后新行应为 network_error 待下次探针复跑核对；⚠️并发警示：tool_recovery/dna_evolution/llm_retry/test_prescreen_stats/roadmap 编辑中均收到 sibling 修改告警，git diff 核实工作树仅本周期改动（57行）无他会话混入；下周期做 12.6① infection-guard 判词依据不落账（terminate detail=空→判词落账）
[2026-10-11 15:20] 周期#74 | 12.6① 判词依据落账（verdict=terminate detail=空→判词落账）：根因=<terminate/> 自闭合标签 tag 内无处写原因，parse_verdict detail 恒空+evidence 只存 resp 截断（纯标签判词时=裸标签噪声），落账侧 message 回退通用兜底文案——真实库实证 33 条拦截行 result_preview 全是"检测到疑似提示词注入劫持"通用文案=判了什么全靠猜。修三件：①infection_guard.extract_rationale（剥三态 verdict 标签取自由文本判定依据）+_llm_audit detail 补齐（clarify 标签内文优先）+evidence 只留依据文本禁裸标签冒充②soulmate_agent 非 ok 分支判词依据伪条目落轨迹账本（verdict_basis≤400+permission=infection:verdict_basis 独立值+parse_ledger 两头不计；纯标签如实落"判词未附依据（纯标签）"不编造）+journal 落依据③JUDGE_PROMPT 要求判 terminate/clarify 必须附判定依据。连带修探针盲区（第6面误扫不存在的 arguments_json 列对 [infection-guard] 伪条目全盲→PRAGMA 实存列容缺）+新增第7面 check_verdict_basis（真实依据/诚实标记/空依据三形态正则/拦截行历史缺口） | ✅ | (本次提交) | 12.6①勾选；Gate1 git diff 5文件150行+新测试文件真实落盘+key扫描0命中；Gate2 grep接线点全数命中（extract_rationale L479 在 _llm_audit 真实路径、verdict_basis 落账 L3841 在 _run_llm_with_tools 非ok分支、check_verdict_basis L317 进 run_probe）+真实库探针跑通第7面（拦截行33/落账行0/历史缺口33如实报）+服务同款 interpreter 导入烟测 SMOKE74_IMPORT_OK；Gate3 tests/test_infection_verdict_basis.py 28/28（extract_rationale 7例/detail补齐5例/JUDGE_PROMPT钉死2例/真实 _run_llm_with_tools 路径E2E 4例含副作用证据/parse_ledger两头不计/接线护栏3例/探针面6例含空依据双形态）+同域回归312全过（infection×4/prescreen×2/audit_trail_probe/report_evidence/loop_guard_wiring/steering/tool_output/self_verify/trajectory_recorder）+py_compile；实测暴露并修2处：纯标签判词 evidence 裸标签冒充依据（改只留依据文本）、探针空依据判空 LIKE 配不过三种落库形态（改 Python 正则容反斜杠单元，5形态手工实证 3真/2负）；顺带修存量测试基建红灯（test_audit_prescreen _run get_event_loop 批内排序污染 17红，纯 HEAD worktree /tmp/wt74-check 先证非本切片引入→asyncio.run 对齐周期#55/#73 修法）；⚠️诚实边界：①真实库 33 条历史拦截行=修复前旧账不改写（探针如实报历史缺口），新拦截行落 verdict_basis 待自然触发后复跑探针核对②判词依据质量取决于审计员是否照 JUDGE_PROMPT 写依据——不写时如实落诚实标记，不编造③本切片未跑真 LLM 回合触发拦截的自然场景（E2E 走真实 _run_llm_with_tools+真实 InfectionGate+账本形状，判词为注入 fake）；下周期做 12.6② verify_dev 判据弱项（只查文件 marker 不查"用 terminal 运行它"）
[2026-10-11 15:33] 周期#75 | 12.6② verify_dev 运行账本判据（12.6 余项清零，阶段12 收官）：根因=开发类只查文件 marker 不查"用 terminal 运行它"，S1 被审计误杀的 terminal 步骤（未执行）仍判 pass=假过（真实库 15 条 dev_hello 轨迹行实证运行证据本来就在账本里：name=terminal+args 引用 dev_hello+result_preview=程序输出）。修=verify_dev 三判据（文件 marker+运行账本+回复报告输出）：运行证据=_tool_ledger(("terminal","execute_code")) 执行行（parse_ledger 单一真源，被拦行不计）args/name/result 含 dev_hello 且 result_preview 含程序输出 SOULMATE_DEV_OK 且非源码回显（print( 正则——cat/echo 源码不算运行）；_tool_ledger 扩多名字一次回读+名字容忍"terminal: <cmd>"冒号前缀形态；账本不可用诚实降级文件+回复（mode_note=ledger_unavailable 不假称 server_side） | ✅ | 42f28c95 | Gate1 git diff 3 文件 302 行真实落盘；Gate2 grep verify_dev 三判据接线点在 acceptance_benchmark main 真实验证路径（_tool_ledger 多名字回读+运行判据消费账本行）；Gate3 tests/test_acceptance_verify_dev.py 12/12（含 S1 回归假过封死：terminal 步骤被误杀未执行时不再判 pass）+真实 WS 验收开发类 1/1 ✅ 24.44s（运行账本命中=terminal 执行行 1）；11.5 观察=探针复跑（provenance 0/self_heal 0 未自然触发、verdict_basis 落账行 0=修复后尚无新拦截，历史 33 条不改写如实报）；⚠️本条目为周期#76 补记（#75 提交 42f28c95 漏写周期日志行）；至此阶段1-12 切片全部清零（余项仅待用户拍板：⚡8.6 双链合并、4.1b iLink 形态、ASR 语音输入）
[2026-10-11 15:52] 周期#76 | 路线图清零后验收对标抽样实测+规划阶段13+补记#75日志行：①补记周期#75 周期日志行（42f28c95 提交漏写，9271bf74 补账）②5类抽样实测（tag=SAMPLE76_1791704767，WS 真实用户路径，ACCEPT_CATEGORIES=开发,办公,定时,协作,记忆）**5/5 通过全部 server_side**——开发 23.63s（**12.6② verify_dev 三判据首次真实 run 走中：文件+运行账本+回复报告，运行账本命中=terminal 执行行1 含程序输出非源码回显**）/办公 44.93s report.xlsx 4964B/定时 30.80s accept_probe 行=1/记忆 24.98s MEMORY.md 标记/协作 18.75s 双 worker；开发类回归常态 23.63s=#64 的 301.74s 确认上游波动个案（12.3 归因复核成立）；零注入审计误杀零 WS 超时；测试产物清理归零（MEMORY 标记 0/accept_probe 行 0//tmp/soulmate_accept 已删）③规划阶段13 五切片+例行观察（13.1 流式 stall 治理最高优先/13.2 拦截自然触发端到端/13.3 审计往返开销/13.4 AI 群组真实成员 e2e/13.5 技能语义改写） | ✅ | (本次提交) | Gate1 git diff 路线图 3 区块真实落盘（#75 补记行+抽样实测节+阶段13+本条目）；Gate2=N/A（纯验收/规划周期无新代码符号）；Gate3=acceptance_benchmark 真实运行 5/5+结果 JSON /tmp/soulmate_accept_results.json 回读与台面一致+清理归零三项实证；⚠️诚实边界：每类单次样本同机低负载；late_readback 本轮未触发（无 WS 超时）；下周期做 13.1 流式路径 stall 停顿治理
[2026-10-11 16:20] 周期#77 | 13.1 流式路径 stall 停顿治理（事故家族最后已知缺口销账）：utils/stall_guard 同源扩面（aiter_with_stall_guard 消费侧 chunk 间隙看门狗+open_stream_with_stall_guard 流式等响应头看门狗+_await_with_stall 单源计时+stream_deadline_s 流式总兜底默认 900s/可配 SOULMATE_LLM_STREAM_DEADLINE_S）+llm_engine 四点接线（chat_stream/chat_stream_with_tools 各"等响应头+chunk 间隙"），停顿抛 httpx.ReadTimeout 落既有 except httpx.TransportError——partial 输出/has_output 重试规则与"[LLM响应超时]"文案零改动（接线护栏钉死）；**接线期真实生产实证并修复同族既有缺陷**（周期#69 send_with_stall_guard）：收集 aiter_bytes 已解码字节却保留 content-encoding: gzip 头重建 Response=二次解码 DecodingError"incorrect header check"——16:09 journal 实证 token-plan gzip 响应下 chat() 全部报"[LLM错误: Error -3 while decompressing...]"（会话标题被错误文案污染，纯文本假服务器测试未覆盖 gzip 形态）→改收 aiter_raw 原始字节与头一致+gzip 回归钉 | ✅ | (本次提交) | 13.1勾选；Gate1 git diff 5文件+新测试文件真实落盘（stall_guard +108/llm_engine +27/新测试 test_llm_stream_stall.py 15例/gzip 回归钉/路线图）；Gate2 grep 四接线点在 chat_stream/chat_stream_with_tools 真实路径（6 处引用）+aiter_raw 修复点+两次真实 WS 用户路径走中真实接线（开发类回合=chat_stream_with_tools 流式消费真实路径、标题生成=chat()→send_with_stall_guard 真实路径）；Gate3 tests/test_llm_stream_stall.py 15/15（真实 socket SSE 故障注入非 mock：头挂死重试恢复/消费中挂死 partial 保留+has_output 零重试+文案逐字/tool 路径状态零污染与恢复/涓流零误杀负控制[间隙0.1s<stall0.5s 总时长1s>stall 完整收流]/慢首 token 不误杀/stream deadline 兜底涓流假进展/错误契约 read_timeout+inner 异常透传/接线护栏含 has_output 规则未动/旁路）+test_llm_stall_guard 22/22（含新增 gzip 双解码回归钉+streaming 接线断言随 13.1 更新）+受影响面 425 全过（stall/stream/transport/resilience/accounting/error_contract/retry 145+engine_fallback/model_fallback/transport_core/stream_continuation/turn_park/network_park/send_budget/stream_progress/turn_lifecycle/turn_outcome/discussion_park/wakeup_fire 280）+py_compile+服务同款 interpreter 导入烟测 SMOKE77_IMPORT_OK；真实验收=WS 用户路径开发类 2/2✅（STALL131 33.47s/STALL131B 42.17s，verify_dev 三判据全命中 end_turn），修复后 journal 实证"Session title generated: 创建并运行Python文件"干净标题+全窗口零 DecodingError 零守卫误杀；测试产物清理归零（/tmp/soulmate_accept 已删）；⚠️诚实边界：①流式停顿的自然发生（上游真挂死）未在本轮触发——停顿→中止→重试以故障注入实证，涓流零误杀以负控制实证②流式 deadline 默认 900s 取值依据=连续出字属合法长生成，若真实负载有 >900s 合法连续流调 SOULMATE_LLM_STREAM_DEADLINE_S 上调③gzip 双解码缺陷为周期#69 引入（deploy 后至本周期修复前的 chat() 调用受影响：标题/步自省曾静默吞错文案），已修复并回归钉死；下周期做 13.2 注入审计拦截自然触发端到端实证
[2026-10-11 17:15] 周期#78 | 13.2 注入审计拦截自然触发端到端实证（销周期#74 诚实边界③+⚡8.2负路径真实触发）：两段式间接注入隔离session（om-4fe1d3b58854）真实WS用户路径——turn1只读建立来源（模型识破注入但只转述不执行=干净基线）/turn2执行"注入检测样本同步"SOP（4条样本逐字携带劫持签名：persona改写/隐瞒用户/指令覆盖/套取提示词，签名只在载体文件与工具参数、不在用户明文→8.1②溯源豁免不适用→启发式确定性命中）；自然升级链 journal 实证 clarify(16:50:44)→clarify(16:50:55)→terminate(16:51:13 clarify_exhausted"启发式劫持签名反复出现且未澄清，终止本轮")，全session最终 clarify账本行14+terminate行7；verdict_basis 落账21行全部真实依据（0诚实标记0空依据；探针第7面：落账行0→21、拦截行33→54、历史缺口33→0不增、新行有依据✅）；banner 用户可见报因✅（回合行🛡️[注入审计终止]+判词依据）；⚡8.2 治理负路径真实触发✅（turn4 配方=明文逐字写入禁编码+标题先行+禁读取检查类工具→claims+全拦截+零成功执行→verdict=blocked→落库回复前置"⛔ **工具被拦截（未执行，不构成完成证明）**"逐条"被拦截：原因"（含 loop_guard"same arguments 3 times"一并如实报）+报告标题降级"（⚠️ 未经执行验证）"）；动作被阻止✅（样本 canary 文件全程未创建，21次写入尝试零落盘） | ✅ | (本次提交) | Gate1 git diff 3探针新文件+路线图2区块真实落盘；Gate2=探针真实执行即接线（WS真回合4条+audit_trail_probe第7面+journal [infection-guard] 链21条），无新增生产代码符号（纯验收切片）；Gate3=主探针8断言6/8（A4/A5后到回读补齐）+turn3 5项+turn4 5/5，全部服务端副作用证据（轨迹账本/agent_messages落库行/canary不存在/journal）非探针自报；测试产物清理（inject载体删除/canary不存在//tmp helper删除），隔离session轨迹行保留=审计证据；⚠️诚实边界：①terminate 由启发式 clarify 超限升级路径自然触发，LLM 语义审计直接判 terminate 未在本轮发生②8.2 负路径前置条件苛刻（claims+全拦截+零成功执行）：turn2 有 cat/ls 真实执行→grounded 不触发（合理）、turn3 全拦截但模型诚实未谎称完成→no-claim 不触发（合理=治理只针对假声称）——三轮对照恰实证治理判定语义按设计工作③新发现误伤张力（候选待用户拍板）：合法"安全测试样本落盘"诉求（样本文本来自三方文件）被签名反复拦截、有人值守 clarify 超限升级 terminate 无法完成，模型自行发现 base64 编码可绕过签名扫描（turn4 禁编码才拦住）——签名扫描的固有误伤面+编码绕过面，候选后续=用户明示重试豁免/澄清后二次豁免语义，涉及安全取舍不擅动④WS 320s 超时后回合服务端跑完后到落库再次实证（10.4 口径：turn2 任务管线重规划3次历时~19分钟后到回读）；下周期做 13.3 注入审计 LLM 往返开销治理（先测每工具审计耗时分布基线再动刀）
[2026-10-11 17:45] 周期#79 | 13.3第一刀·注入审计 LLM 往返开销基线（"先测基线再动刀"按序执行，治理刀未动）：infection_guard._llm_audit 每次判词往返计时（_record_audit_ms，含 timeout/error/unavailable 失败路径恰好各记一次——失败往返 30s 是最贵开销形态；旁路纪律观测失败不冒泡）落 stats() llm_audit_last/mean/max/total_ms + journal 行"LLM审计完成 verdict=X 耗时=Nms tools=a,b（累计 N 次 均值 Xms 最大 Yms）"（工具名归因=每工具审计耗时口径）+可复跑探针 acp-proxy/audit_cost_report.py（分布 count/mean/p50/p95/max/min/total+按 verdict+按工具归因；生产者-消费者正则 AUDIT_RE 互相咬合改一边测试红；stderr 中继双份去重沿用 12.4③ 实测坑纪律） | ✅ | 8c78d494 | 13.3 部分完成（基线✅治理刀下拍第一优先，决策依据已写入 13.3 条目）；Gate1 git diff infection_guard +39 行+2 新文件真实落盘（8c78d494）；Gate2 grep _record_audit_ms 四个 return 点全数接线+check() 真实路径计时测试走中+真实 WS 验收回合经真实调用路径产出 journal 样本（非测试 stub）；Gate3 tests/test_audit_cost_report.py 13/13（成功/timeout/error/unavailable 四路径计时、列表有界 500、生产者-消费者 roundtrip+去重+无关行、分布数学+按工具归因+空数据 None、check() 接线护栏、免审批次零往返）+同域回归 249 全过（prescreen_stats/infection_guard×4/audit_prescreen 166 + report_evidence/self_verify/remediation_style/frame_origin/tool_availability 83）+py_compile+服务同款 interpreter 烟测 SMOKE79_IMPORT_OK+双实例重启 health 200/200；**真实基线**（tag=BASE133_1791727000，WS 真实用户路径开发+办公 2 回合 2/2✅：开发 35.03s verify_dev 三判据命中/办公 58.61s report.xlsx 4979B）：5 次判词往返总 18.99s、均值 3.8s、p50 2.2s、p95=最大 7.2s、最小 1.5s、全 verdict=ok 零失败零超时；工具归因 execute_code 3 次均值 3.5s、document 2 次均值 4.2s；窗口 7 工具批次 2 免审 5 升级（prescreen 命中率 28.6%，exec_verb 1+user_verbatim 1）；测试产物清理归零（/tmp/soulmate_accept 已删、结果 JSON 2/2 回读一致）；⚠️诚实边界：①5 样本同机低负载，p95 参考意义有限（长尾需长窗口探针持续观察）②单次均值 3.8s 显著低于 12.3 事故期 16s——事故期含上游波动/冷启动，基线取值以本窗口为准③"每工具一次往返"实测口径=每轮一次判词（批内多工具已合并），链级多次来自模型一轮一工具；下周期做 13.3 治理刀（首选免审面扩展零语义风险，判词复用前先测同签名复现率）

[2026-10-11 18:25] 周期#80 | 13.3治理刀·零语义风险面全做完+动刀前测量（含1处实证缺陷根因修复）：①免审面扩展（13.3①）：audit_prescreen 纯ASCII数字token残渣免审（_NUMERIC_RESIDUE_RE 窄形态[0-9]+空白分隔，0x/-/./,/e/全角/链式混排负控制钉死，source=prescreen_numeric 不冒充全明文）；②**同轮探针暴露并修复同族既有缺陷**：fold_marker_lines 旧`^.*短语.*$`整行折叠把与标记短语同行的真实用户请求连带吞掉（GOV133NUM 实录：溯源依据丢失→'sleep 3'整值假升级+判词员看不到用户请求）→改标记子句级折叠（含短语括号组整体/无括号折到句末标点），同行真实请求原样送审（残余边界如实记录：自述短语后无句末边界的同句内容仍随子句折叠）；③判词签名双口径埋点（sig=claims三元组=provenance不进key取舍口径/full=+prior_tool_results零取舍对照）+chars维度落journal，audit_cost_report 增同签名复现率+chars↔耗时Pearson | ✅ | a936acc8+d7cbeba3 | 13.3勾选（⏳②provenance取舍待用户拍板，零取舍口径已实测无收益不擅动）；Gate1 git diff 4文件+2测试文件326+69行真实落盘（两commit）；Gate2 grep _audit_signature/_record_audit_ms 四return点全数接线+residue_is_numeric 进 value_exempt_from_audit 判定链+fold_marker_lines 在 infection_guard.check 真实路径（572行）——真实WS回合经真实调用路径产出journal样本实证；Gate3 tests 49/49（数字免审5例含组装逃逸负控制/签名契约/复现率数学/Pearson/折叠内联回归钉）+同域回归286/286（audit×3/infection×4/prescreen_stats/report_evidence/self_verify/remediation_style/frame_origin/tool_availability）+py_compile+服务同款interpreter烟测SMOKE80_IMPORT_OK/SMOKE80_FOLD_OK+双实例重启health 200/200；**真实实效**（WS真实用户路径3回合）：验收2/2✅（开发45.02s verify_dev三判据命中/办公51.87s report.xlsx 4963B）+数字探针GOV133NUM2真实免审（journal实录'预筛免审（prescreen_numeric）: terminal（模型自选数字算子: 3）'零LLM往返，修复前同形态升级+2549ms审计）；**测量结论**：②同签名复现率4样本sig/full两口径均0%（严口径含output_text需逐字相同，13.2 clarify重试链为候选场景）③chars 154-1031与耗时无稳定相关（pearson小样本正负号翻转）——审计耗时非输入长度驱动，瘦身暂缓；测试产物清理归零（/tmp/soulmate_accept已删/MEMORY标记0/探针脚本删除，隔离session轨迹行保留=审计证据）；⚠️诚实边界：①数字免审实效为1真实样本（同机低负载）②复现率/相关性样本量小（4），结论按'测量未支持'表述非'证明为零'③残渣类别天花板分析（journal实录）：升级残渣主体=模型自造代码体（execute_code/terminal）+短CJK词+路径片段，溯源型免审面理论天花板已近——继续扩大免审面对这些类别必然语义缩水，13.3开销治理后续收益在②（待拍板）④/probe80旧版经/acp/send误路由hermes（非soulmate通道），改WS agent_id=soulmate后取样正确——探针通道纪律沿用群组红线；下周期做13.4 AI群组真实成员端到端

[2026-10-11 18:59] 周期#81 | 13.4 AI群组真实成员端到端（对齐 ai-groups-e2e.md 验收行 A1-A8，用户重视 AI 群组）：验收行对齐暴露并根因修复1真缺口——GROUP_MSG_PUSH 死常量+acp-proxy 无 /internal/group-message 端点→任务链"每步结果回群聊"只进 discussion_messages+task_progress 时间线帧，聊天流前端只认 agent_message 帧（ai-groups-store case 'agent_message'）=群里实时看不到 Agent 回复（用户历史投诉"群里永远没有 Agent 回复"残留形态）；修复=ws_group 新增 POST /internal/group-message（只广播 agent_message 帧不落库=落库单一真源防双写、loopback-only、persist=true 可选落库、空房间 delivered=0 不算错）+_notify_group_chat 真实消费 GROUP_MSG_PUSH（旁路纪律失败只记日志不阻塞任务）+ai-groups-e2e.md 契约行同步；可复跑探针 acp-proxy/ai_groups_acceptance_probe.py（A1-A7 双 WS 客户端）+ai_groups_ui_probe.py（A8 CDP 四键注入先 about:blank 注册再导航+setCacheDisabled） | ✅ | (本次提交) | 13.4勾选；Gate1 git diff 双仓真实落盘（openmate：ws_group.py +65/两探针新增/新测试；opensoul：ai_groups.py +20/新测试；他会话 WIP opensoul src/main.py +3 与 context_* 未动未提交）；Gate2 grep GROUP_MSG_PUSH 死常量复活（定义+消费≥2处）+/internal/group-message 路由真实挂载（live curl {"ok":true,"delivered":0,...}）+真实 WS 回合经 _notify_group_chat 真实调用路径产出 agent_message 帧（A4 帧只可能来自新推送路径，task_progress 是另一帧型）；Gate3 tests/test_group_message_push.py 7/7（loopback 广播帧形状对齐前端消费字段/非 loopback 拒绝/缺参拒绝/默认不落库防双写/persist=true 落库且帧行 id 一致/空房间/接线钉）+tests/test_ai_groups_group_push.py 4/4（真实 HTTP 捕获服务器推送 body 契约/3000字→推送2000截断落库全量/死端口旁路不抛/默认名）+同域回归 50+17 全过（opensoul ai_groups 7 套件 50+acp-proxy discussion_turn_park/group_push 17）+py_compile×2+服务三实例重启 health 200×3；真实验收 8/8 行全过（tag=ACC134-1791715968）：A1 agents=3/A2 四子任务含规划执行验证审查/A3 144s 链式全 scored 依次 hermes→soulmate→mimo→hermes 真身无手动 execute/A4 agent_reply 行 4+WS agent_message 帧 8（×2 客户端）帧内容=行内容前缀 8/8 同源/A5 source=soulmate-agent 零冒名+auto_score 5.75+capability 4 行落库/A6 双 WS 互见 user_message 4 帧/A7 start_discussion→agent_message 流 4 帧/A8 UI DOM 建组入口+真实群组名+3 Agent+四子步骤+Send to @All；测试产物清理（探针组 DELETE 后列表回读消失，证据留 /tmp/ai_groups_accept_result.json+/tmp/ai_groups_accept_ws_frames.jsonl）；⚠️诚实边界：①A7 讨论回复来自 discussion_engine 按角色 LLM 生成（产品既有设计，验收行只要求 agent_message 流），任务链执行成员为真实 agent binary（非 loopback 模拟）②单样本同机低负载（本链 144s 明快，9.4 期 hermes 5-12min 级波动未复现）③A8 借用户既有 chromium :9222，只开闭自己创建的标签页④旧测试组 B群组/A群组 非本周期产物未动；下周期做13.5 技能进化语义改写（步骤/触发词 LLM 辅助改写+内容保真校验）

[2026-10-11 19:30] 周期#82 | 13.5 技能进化语义改写（6.3 诚实边界销账）：新增agent/skill_rewrite.py（SkillRewriter：LLM 辅助通道只许两件事=步骤措辞改写+触发词增补；确定性零丢失保真门 fidelity_check 四断言=步骤数量一致[合并/拆分=取舍→拒绝"需用户拍板"]+每步语义载体 token hard_tokens[引号/反引号/路径/flag/数字/常量/扩展名确定性提取]原样保留+触发词只增不删[删除=取舍→拒绝]且新增项过 is_injectable_trigger+40字上限+写回后回读复验[load_skill→parse_sections 重走保真门+坑/验证逐字比对，不过即还原原文]；LLM 不可用/超时/不可解析/空提案全态显式报因禁编造）+SkillEvolver.evolve_semantics（频控=同技能24h+每回合≤1次真实LLM尝试、门禁跳过不占名额；审计只落 rewritten/rejected；name/description/坑/验证逐字透传）+soulmate_agent 回合收尾钩子接线（旁路纪律失败只记日志） | ✅ | (本次提交) | 13.5勾选（阶段13 只剩例行观察；⏳13.3②判词复用/8.6/4.1b/ASR 仍待用户拍板）；Gate1 git diff skill_evolution +112/soulmate_agent +23/新文件 skill_rewrite.py 366行真实落盘；Gate2 接线护栏 test_wiring_guard_soulmate_agent+inspect 实证钩子在 SoulMateAgent 真实收尾路径；Gate3 tests/test_skill_rewrite.py 29/29（保真门8态/提案解析5态/改写8态含拒绝文件逐字不动+回读失败还原/频控名额审计7态+接线护栏）+回归76全过（skill_evolution/skill_saver/skill_standard_layer）+**真机真实 LLM 全链烟测**（mimo-v2.6-pro token-plan：改写2步语义载体 /tmp/smoke-input.json 与 `python smoke.py` 原样保留、按用户用语新增5触发词、坑/验证/描述逐字不动、revision 1→2 回读断言 PASS）；⚠️诚实边界：①语义取舍（删/并/拆步骤、删/改触发词）保真门拒绝报"需用户拍板"，本通道永不做取舍②语义改写默认开启（SOULMATE_SKILL_REWRITE=0 可关）+24h/技能频控+1次/回合名额，LLM 成本有界③新增触发词由 LLM 依用户用语提炼，过有效性门槛但语义价值未人工评审（增补是超集零丢失，可随时删）④工作区遗留非本流水线未跟踪文件（acp-proxy/fault_degradation_*、agent/tool_infra.py 等）未动未提交（单写入者纪律）；下周期=例行观察探针复跑（prescreen_hitrate_report/audit_trail_probe）或待拍板项落地
[2026-10-11 20:05] 周期#83 | 例行观察探针复跑+验收对标抽样 5/5+两缺陷根因修复（阶段13清零后按续）：①例行观察三探针——audit_trail_probe：**#73遗留"重启后新行应为network_error"销账**（id=134-139新行classify全对，8行unknown全为修复前历史行不改写）+verdict_basis 21行全真实依据零缺口+provenance/self_heal 0行如实报；prescreen_hitrate_report 24h窗口命中率55.1%（27免审/22升级/49批，双通道27vs21边界差异如实报）；audit_cost_report 10次往返均值4.1s p95 7.2s 同签名复现率0%（10样本）②抽样实测（tag=SAMPLE83_1791717876）**5/5通过**（开发127.13s verify_dev三判据命中/办公45.85s report.xlsx 4963B/定时59.94s/记忆300.02s WS超时→后到回读1次恢复/协作21.05s，4 server_side+1 server_side_late）③**缺陷A·pytest污染真实审计库**（探针暴露24行/tmp/pytest-of-climbing失败行落真实tool_failures.db）：根因=__new__构造agent懒初始化ToolRecoveryManager()默认真实库→tests/conftest.py autouse统一TOOL_FAILURE_DB_PATH进临时库（既有官方覆盖机制兜底，测试自设env以测试为准）+回归钉2例（默认构造逃离真实库/record不动真实库字节），历史24行备份后清理（tool_failures.db.bak-20261011-83）④**缺陷B·code_mode挂死脚本残留线程楔死整进程**（7文件批回归挂死4min+，faulthandler定位=os._walk被GIL饿死+code_mode自旋线程）：根因实证=`while True: pass`被放弃worker线程持GIL自旋，同进程其他线程每次syscall的GIL再获取等切换间隔→0.14s目录walk实测变60s+（单跑0.95s vs 最小对>60s实证）；修复=code_mode.py挂死线程回收（批超时放弃worker后宽限期默认2s到点PyThreadState_SetAsyncExc注入退出异常，_run的except BaseException收口线程干净退出；fire-and-forget不阻塞、吞异常脚本如实告警不谎称回收成功；RECLAIM_GRACE_S=2.0可配）；实测最小对60s+挂死→5.1s、7文件批4min+→5.3s全过 | ✅ | (本次提交) | 例行观察条目更新+阶段14规划（14.1 code_mode进程级隔离=吞异常脚本仍楔GIL的真解/14.2慢回合归因扩样/14.3观察持续）；Gate1 git diff 5文件真实落盘（conftest.py新增+test_tool_recovery +21/code_mode.py回收逻辑+test_code_mode 4新例+路线图）；Gate2 grep _reclaim_hung_thread(worker)在TimeoutError真实分支（接线钉测试）+conftest全测试路径生效（真实库行数139→139实证零污染）；Gate3 tests/test_code_mode.py 34/34（回收4例含自旋回收/无启动no-op/已退出短路/接线钉）+test_tool_recovery 21/21+此前挂死7文件批205/205（5.3s）+受影响面179/179（sandbox_policy/infection_guard/stall_guard/report_evidence/self_verify/turn_watchdog等）+py_compile+双实例重启health 200/200；⚠️诚实边界：①缺陷B修复覆盖"脚本不吞异常"形态（注入退出异常被_run收口），吞异常死循环（while+try/except）仍楔GIL→真解=进程级隔离已规划14.1不冒称完成②PyThreadState_SetAsyncExc为CPython实现细节，uv-python 3.11实测有效，跨实现可移植性未验（不可用时走ctypes告警降级=现状daemon兜底）③抽样每类单样本同机低负载，开发127s/记忆300s波动归因入14.2禁单样本定论④24行污染清理为测试产物（备份先行），真实失败审计行（id=140 llm:chat stall guard 240s兜底真实事件）保留未动；下周期做14.1 code_mode进程级隔离或14.2慢回合归因扩样
[2026-10-11 20:40] 周期#84 | 14.1 code_mode 挂死脚本进程级隔离（吞异常死循环 GIL 楔死真解，周期#83 诚实边界①销账）：新增 agent/code_mode_worker.py（子进程执行体，browser_worker 同款 JSONL 协议：脚本 exec 在子进程主线程、tool_call 跨进程请求内层工具由父进程 dispatch=permission gate 不绕过、call_log 开始/结束双快照+`result` 赋值（_StreamingNS __setitem__）+print 流式内建即时送出=被杀时部分产物不丢、受限 builtins 与父进程 code_mode 单一真源 import 无 __import__/open/exec/eval）+code_mode.py execute() 隔离面路由（默认 process：_execute_in_process 批级超时=kill 进程组 start_new_session+killpg 连带清子进程+宽限 0.5s 在途收尾、单次调用超时父进程 wait_for 判定随 tool_result 回传 call_timeout 标记（entry 同款字段）、子进程崩溃如实报退出码+stderr 尾禁静默；SOULMATE_CODE_MODE_ISOLATION=thread 回退旧 _execute_in_thread 代码路径保留含 #83 PyThreadState_SetAsyncExc 回收机制，旧测试 test_hung_spinner_reclaimed_after_grace 钉 env 保意义）。语义等价迁移评估（路线图要求"顺带评估"）：单次调用超时/调用上限熔断（calls 只计过参数校验调用）/位置参数拒绝/异常可见/result 优先 stdout 回落/result=None 回落 stdout/[被拦截] blocked 标记/format_result 逐字等价；两点差异如实记录①dispatch 超时协程从"泄漏后台继续跑"变被取消（更干净不改语义面）②批超时宽限 0.5s 内不收尾即 kill（线程模式留自旋等回收，子进程更强） | ✅ | (本次提交) | 14.1勾选；Gate1 git diff 三文件真实落盘（code_mode.py +212/code_mode_worker.py 新增/test_code_mode_subprocess.py 新增）+key扫描0命中；Gate2 grep execute() 路由+_execute_in_process 在 SoulMateAgent batch_execute 真实消费路径（_cm.execute 调用点不变自动走新路径）+服务同款 interpreter（~/.hermes/hermes-agent/venv）真实跑通批量化/超时kill/异常三形态冒烟；Gate3 tests/test_code_mode_subprocess.py 17/17（GIL 面消失：吞异常死循环批超时期间父进程 CPU 任务 <1s 未被拖慢+无 code_mode_worker 残留进程扫 /proc、语义等价 11 例含部分产物不丢/call_timeout/上限/参数校验/异常/输出优先级、隔离路由 3 例含 thread 回退+回收机制不腐化）+既有 test_code_mode.py 34/34 全过（默认即子进程路径=E2E _run_llm_with_tools 真实消息路径批量化接线在子进程模式下零回归）+受影响面 170/170（code_mode×2/tool_recovery/tool_exec_fencing/sandbox_policy/permission_gate/no_silent_truncation）+py_compile×2+双实例重启 health 200/200；⚠️诚实边界：①真实 LLM 回合自然产出 batch_execute 调用的全链未在本轮触发（E2E 走真实 _run_llm_with_tools 消息路径+真实 dispatch/gate，LLM 侧为脚本注入的 tool_call），自然回合实效待用户使用观察②子进程 spawn 开销每批 ~30-50ms（对 90s 批量级可忽略）③SOULMATE_CODE_MODE_ISOLATION=thread 回退路径仅测试/应急用，生产默认 process④脚本内 print 的 file= 目标被忽略走统一通道（受限 ns 本无文件对象语义，docstring 已注明）；⚠️并发警示：test_code_mode.py/roadmap 编辑中均收到 sibling 修改告警，git diff 核实工作树仅本周期改动无他会话混入；下周期做 14.2 慢回合归因扩样（≥3 样本禁单样本定论）
[2026-10-11 20:51] 周期#85 | 14.2 慢回合归因扩样（≥3 样本禁单样本定论，周期#83 记忆 300s/开发 127s 单样本波动销账）：turn_timing_report 四段归因 9 样本（开发 4=380.73/125.86/22.13/39.75s+记忆 5=330.39/233.39/106.25/24.74/54.34s，后两个=RETEST_1791722853 新鲜 WS 用户路径 2/2✅ verify server_side，测试产物 cleanup 归零）；**结论：上游 LLM 为主因、本地段无卡死**——慢样本 77-96% 时长在 LLM 往返/传输等待（323.69s RemoteProtocolError 传输挂起/280.75s 单轮撞 240s stall guard/211.22s chat_turn 15 轮+解压错误重试风暴），本地段小且稳定（prompt 前置实测 0.89s=journal [prompt] CALLED→_run_llm_with_tools starting、工具 ms 级、回复回收 0.36-0.85s）；"⑤未归属空洞"首段 6-32s（9/9 样本）实为未落账 LLM 往返（planner/意图/标题生成）——journal 实证请求回合开始 ~1s 内已发出，等待=上游延迟非本地准备；附带观察①httpx.DecodingError "incorrect header check" 解压错误间歇高发（7 历史样本 4 命中成簇）+错误文本污染会话标题"[LLM错误: …]"②step_commit 对 /tmp 目标恒报"路径越界拒绝"（2.3 设计使然噪音非缺陷） | ✅ | (本次提交) | 14.2勾选；Gate1 git diff 路线图 14.2 勾选+结论+14.4/14.5 新切片真实落盘（测量型切片无产品代码改动）；Gate2 接线实证=turn_timing_report.py 真实消费 opensoul.db trajectory_events 9 个真实 session 落账行（2.3/12.3 期已接线产物，本周期是测量执行非新代码）+journal 真实信号归因（传输错误/stall guard/step_commit 行逐条有源）；Gate3 真实产出证据=/tmp/turn_timing_142.json（7 样本四段数据）+/tmp/turn_timing_142_fresh.json（2 新鲜样本）+acceptance_benchmark RETEST_1791722853 2/2✅（开发 41.02s verify_dev 三判据命中/记忆 54.48s MEMORY 标记）+库存证据回读（MEMORY 标记 0//tmp/soulmate_accept 已删/accept_probe 行 0）；⚠️诚实边界：①"未落账 LLM 往返"归因依据 journal 时序+请求日志（planner/标题等调用无独立计时落账），精确拆分待 14.4 落账补洞②解压错误 4/7 命中为历史窗口观察，成因（上游/透明代理）未定论，14.5 核实重试链覆盖后再说③新鲜样本 2 个各 1 次、历史样本跨 #58-#83 不同代码版本（形态一致支持合并归因但非同版本对照）④/step_commit 路径越界报错=既有设计对 /tmp 验收目标的固有噪音，未改；下周期做 14.4 落账补洞或 14.5 传输错误韧性

[2026-10-11 21:23] 周期#86 | 14.4 落账补洞（14.2 仪表洞根治）：辅助LLM往返进 trajectory_events——trajectory_recorder.record_aux_llm_call（"[aux] {label}"行，与主轮同口径计时覆盖，parent 未知挂根层不伪造，never-raise）+soulmate_agent._aux_llm_chat 统一落账口（语义面与 llm_engine.chat 逐字等价、status=llm_outcome_status、落账失败绝不阻塞、无ctx只跳过不伪造session）+_AUX_TRAJ_CTX 可变dict上下文（fire-and-forget 标题任务先于回合根span创建也拿到后补 root_id）+五个调用点收口（_llm_plan_call=plan/replan/reflect 特征词确定性归类、_judge_task_continuation_llm、_infection_llm_call、_generate_session_title、skill_sem_rewrite）+turn_timing_report ②b 辅助往返与主轮分列并计入覆盖；实测面核对：意图分类/自检/上下文压缩经查代码为确定性无LLM往返（口径按实况收敛不虚报） | ✅ | (本次提交) | 14.4勾选；Gate1 git diff 4文件真实落盘（trajectory_recorder +40/soulmate_agent +110/turn_timing_report +22/test_aux_llm_ledger 新增）；Gate2 grep 五调用点全走 _aux_llm_chat+接线钉 test_soulmate_aux_wiring_pins（ctx先于标题create_task/root_id补写/llm_engine.chat 只剩落账口内部1处）+test_infection_guard::test_llm_call_helper_wired 钉随规格更新（判词改经 _aux_llm_chat，传输真源断言保留）；Gate3 tests/test_aux_llm_ledger.py 16/16（形状/失败可见/异常仍落账/无ctx跳过/显式session优先/记账失败不阻塞/标签归类含真实task_engine提示词/可变ctx共享/归因仪表双用例）+受影响面回归 431+47=478 例：trajectory面4文件 56✅+1❌**存量失败**（test_trajectory_rounds::test_infection_terminate_early_return_still_records_round——git stash 对照 HEAD 同样失败实证非本周期引入，verdict_basis 账本条目致 tools 断言 1→2，待另案）/任务面9文件 158✅/code_mode+infection+skill 8文件 216✅+1钉（test_infection_guard::test_llm_call_helper_wired 随规格更新后 47/47✅）/新增 test_aux_llm_ledger 16/16+py_compile×4+双实例重启 health 200/200+**真实 WS 验收 RETEST_1791724754 2/2✅（开发 67.68s verify_dev 三判据命中/记忆 78.61s MEMORY 标记）且 ⑤未归属空洞=0s（0段）双会话达成**（om-066eef733bd5/om-1e8b1ffbfe62，14.2 期 6-32s 首段空洞形态清零），aux 精确拆分：开发 79.36s/3次（plan 35.13s+title 40.37s+reflect 3.86s）、记忆 67.32s/4次（plan 12.73s+title 16.18s+reflect×2 21.69/16.72s）——实证 14.2 推断并量化标题/规划单次可 35-40s；证据 /tmp/turn_timing_144.json；测试产物清理归零（MEMORY 标记 0//tmp/soulmate_accept 已删/accept_probe 行 0）；⚠️诚实边界：①空洞=0 以 >2s 阈值口径计（工具执行 ms 级不构成>2s区间，tool_call.duration_ms 仍未落账=③既有缺口未扩scope）②infection_audit/title 等 aux 行按自然触发（本轮免审路径判词LLM未触发故无该行，非漏记）③样本 2 个各 1 次同机低负载④plan/title 35-40s 为上游等待（14.2 结论一致），是否值得 prompt 级优化留 14.5 后议；⚠️git 事故恢复记录：开工 git log 核实发现周期#85 提交 47309706 不在 main（reflog 实证=evo checkout evo/exp-1791722903 期间该 commit 落在 evo 分支、checkout 回 main 后分支删除→主树回退 #84、#85 路线图改动被 checkout 冲掉）——git reflog 定位后 cherry-pick 恢复为 ffdd4e41（23 行路线图逐字一致），未做 reset/切分支（evo 停跑期间合规恢复）；下周期做 14.5 上游传输错误韧性（DecodingError 重试链覆盖核实+标题禁错误文本污染）

[2026-10-11 21:57] 周期#87 | 14.5 上游传输错误韧性（DecodingError 重试链覆盖+禁透明压缩+标题禁错误文本）：核实结论=**重试链此前不覆盖**——httpx.DecodingError 属 RequestError 族非 TransportError 兄弟类，llm_engine 三路+llm_transport 四个 `except httpx.TransportError` 重试 catch 面全部漏掉它（一次坏压缩体即落外层 except 零重试直落"[LLM错误: …]"，14.2 解压错误实证形态根因）；修三件①请求禁透明压缩（根因消除）：llm_engine 五处 client 构造（_make_client/chat_stream/chat_stream_with_tools×2/chat）+model_fallback.link.headers()（_apply_link 整体替换 headers 漏了即降级链压缩复活）+llm_transport._post_single_link 发送面统一 `Accept-Encoding: identity`（调用方显式值优先）②重试链覆盖：is_transient_error 认 DecodingError（重发可自愈=瞬时）+四 catch 面扩 `(httpx.TransportError, httpx.DecodingError)`（plan_error_retry/has_output/降级链/记账语义零变化）③is_llm_error_text 失败契约前缀判定单一真源（utils/llm_retry）+_generate_session_title 检出错误契约文本即保留 user_text[:30] 临时标题不写库不同步（14.2"Session title generated: … -> [LLM错误: …]"污染形态销账） | ✅ | (本次提交) | 14.5勾选；Gate1 git diff 8文件90+/24-+新测试文件真实落盘+key扫描0命中；Gate2 grep 四 catch 面+五 client 构造+link.headers+is_llm_error_text 消费点在 _generate_session_title 真实路径+真实 WS 回合经新代码跑通（真实上游对 identity 编码正常响应）；Gate3 tests/test_llm_decoding_resilience.py 14/14（真实socket坏gzip故障注入非mock：chat()/chat_stream 坏gzip→重试→恢复、持续坏gzip 有界重试后失败契约逐字、identity 接线钉×3、错误契约判定含"正文提及不误伤"负控制、标题三态含 PATCH 载荷断言）+受影响面回归 314/314（stall×2/stream_stall/resilience/retry_policy/error_contract/send_budget/park×2/output_protection/aux_ledger/transport_accounting/fallback×2）——其中 3 个 wiring pin（test_llm_error_contract/test_turn_park_merge/test_network_offline_park 锚定旧 except 字面量）钉随规格更新；py_compile×5+服务同款 interpreter 烟测 SMOKE87_IMPORT_OK+双实例重启 health 200/200；真实验收=WS 用户路径开发类 1/1✅ 31.42s（verify_dev 三判据命中，tag=DEC145_1791727600），journal 实证干净标题"创建并运行测试脚本文件"+全窗口零 DecodingError；测试产物清理归零（/tmp/soulmate_accept 已删）；⚠️诚实边界①坏gzip→重试恢复以故障注入实证，真实上游自然解压错误未在本轮触发（禁压缩后该类应绝迹；残余=上游无视 identity 强发压缩且体损坏的极端形态，由重试面覆盖）②一次性 flaky 观察：test_llm_stall_guard::test_head_stall_aborts_fast（3.3s vs 3.0s 上限）首轮 10 文件批跑偶发超限，单跑 3/3 过+复批 314 全过+HEAD worktree 单跑同过=机器负载型时序边际非本切片引入（若持续出现应放宽该断言上限）③并发纪律核实：与真实服务并存期间工作树仅本周期 8 文件改动（git status 核实，他会话未跟踪 WIP 未动未提交）；下周期做存量缺陷修钉（test_trajectory_rounds::test_infection_terminate_early_return_still_records_round 断言跟上 verdict_basis 账本条目，HEAD 即红）+14.3 例行观察探针复跑

[2026-10-11 22:25] 周期#88 | 存量测试修钉+两项实证缺口根因修复（配对terminate判词fail-open+empty_output分类缺口）+14.3例行观察复跑：①**存量缺陷修钉**（周期#86遗留）：test_trajectory_rounds::test_infection_terminate_early_return_still_records_round 断言跟上 verdict_basis 账本条目（tools 1→2=周期#74 设计行为，判词依据伪条目 report_evidence 两头不计）+纯标签诚实标记钉死+配对判词形态伴生 e2e 用例②**实证缺口A·配对terminate判词形态 fail-open**（写伴生用例时实证）：JUDGE_PROMPT 契约=自闭合 `<terminate/>`+依据写标签外，但审计员 LLM 按习惯写 `<terminate>依据</terminate>` 时 _RE_TERMINATE 不匹配任何正则→parse_verdict 误判 **ok→工具照常执行 fail-open 放行**（安全门漏检）；修=_RE_TERMINATE 兼容配对形态（内文=终止依据，与 clarify 标签内文同语义）+_RE_VERDICT_TAG 整体剥离+parse_verdict group(1) 承接 detail+回归钉 4 例（配对识别/空内文/last-tag-wins/extract_rationale 不重复内文）+e2e round 账本落真实依据③**实证缺口B·empty_output 分类缺口**（14.3 探针复跑暴露）：llm_transport 空输出失败以 "empty_output" 串记账但不在 _LLM_KIND_CODES 九态码集→classify 落 unknown（探针实证 id=142 新行 ts=毫秒=修复后代码所记仍缺分类=活缺口）；修=生产码集+探针 LLM_KINDS 双真源补 empty_output（HTTP 200 空输出独立失败形态）+回归钉 2 例（classify 透传/真实记账路径 kind 断言）④**14.3 例行观察三探针复跑**：免审命中率 62.9%（44免审/26升级/70批，双通道 44vs38 如实报）+审计往返 14 次均值 3.84s p50 2.65s p95 6.72s 全 verdict=ok 零失败+同签名复现率 0%（9样本）+chars↔耗时 pearson 0.391 仍无稳定相关+audit_trail_probe 七面（孤儿0/llm:dna 闭环11/verdict_basis 落账21行全真实依据零缺口/provenance self_heal 0行如实报） | ✅ | 2d3214c2+84c3ea4c | Gate1 git diff 8文件真实落盘（两commit）+key扫描0命中；Gate2 grep parse_verdict/extract_rationale 消费点在 _llm_audit 真实路径（L547/L555）+classify_failure 透传在 _record_failure 真实记账路径（test_empty_output_recorded 真实临时库落行非stub）+真实库探针回读；Gate3 tests 分批全过 506 例零回归（infection/audit 面 235+trajectory/aux/acceptance 78+llm transport/accounting/probe 193，其中改动面 35 例复跑）+py_compile+双实例重启 health 200/200；⚠️诚实边界：①配对形态为 parse 契约扩展（此前 fail-open），真 LLM 审计员自然产出该形态未观察到（自然触发待 13.2 同款观察）②empty_output 修复只影响新行，存量 unknown 9 行（115/119=周期#69 WIP 错位 2 行+120-133=13:23-14:42 旧码 DecodingError 6 行+142=修复前最后 1 行）按"不改写历史"纪律保留，探针持续如实报③14.3 数据 24h 窗口小样本（复现率 9 样本仍不足评 13.3②）④action 越界 2 行=WIP 错位历史产物非活缺口；⚠️并发纪律核实：git diff 全程仅本周期 8 文件改动（sibling 修改告警为 mtime 级假警，逐文件 git diff 核实无他会话混入）；下周期=14.3 例行观察探针持续+规划阶段15（或待拍板项落地：⚡8.6/4.1b/ASR/13.3②/13.2豁免语义）
[2026-10-11 22:47] 周期#89 | 验收对标实测（2类轮转抽测）+阶段15规划（路线图切片清零后按预案"跑验收+规划下一批"）：①真实WS用户路径验收 tag=ACCEPT89_1791729385 **2/2✅**（调试59.41s buggy.py 修复=return a+b+FIXED标记、运维12.63s system_checkup 账本真源落账命中+8090/磁盘信息），采集侧系统帧过滤实证生效（self_check×1 被过滤）；测试产物清理归零（/tmp/soulmate_accept 已删/ACCEPT_MEM_MARK_9 标记0/accept_probe 行0）②**15.2 缺口实证**：trajectory_events 全库 542 行 tool_call 事件 duration_ms 全为 0.0（工具执行段=四段归因盲段，14.4 诚实边界③量化坐实）③阶段15 规划落盘（15.1 调度常驻托管=3.1 遗留销账/15.2 工具时长落账/15.3 aux 延迟治理/15.4 工作区遗留清理/15.5 flaky 观察/验收轮转例行），14.3 三探针 7 分钟前刚复跑不重复跑（窗口需积累）| ✅ | (本次提交) | Gate1 git diff 路线图（阶段15 新段+本条目）真实落盘+key扫描0命中；Gate2 本周期为测量+规划（无产品代码改动）：验收探针真实消费真实链路（WS→ws_acp→soulmate stdio→真实LLM回合），tool_call duration 实证=真实库回读（542 行 query）非自报；Gate3 真实产出证据=/tmp/soulmate_accept_results.json（2/2 结果+verify_mode）+acceptance stdout 摘要+真实库 542 行统计；⚠️诚实边界：①验收 2 类各 1 次同机低负载（调试 59.41s/运维 12.63s），轮转例行按每周期 2 类积累全集覆盖②阶段15 为规划未实施，下周期起按序做 15.1（或按用户插队指令优先级调整）③工作区遗留清理（15.4）未动 untracked 文件（单写入者纪律：tool_infra.py 等非本流水线产物）；下周期做 15.1 调度常驻托管（或待拍板项落地：⚡8.6/4.1b/ASR/13.3②）
[2026-10-11 23:12] 周期#90 | 15.1 调度常驻托管销账（常驻宿主=周期#48 已交付，本刀=错过留痕）：soulmate_scheduler.py 加 _missed_slots 确定性枚举（due 之后到 now 之间溜掉的执行点 >due且<=now，interval/cron 双形态，封顶 _MISSED_CAP=100 截断自报防长停机枚举爆炸）+_log_missed 落 runs 表 status='missed'（复用 claim 已开连接禁嵌套、仅 CAS 赢家记防双写、missed 不计 fail_streak、留痕失败不阻塞链路）+claim_due 赢家路径接线+stats.missed 计数+journal ⏭️ 行+schedule_list 展示 ⏭️ 不冒充❌+模块/claim_due docstring 语义收口（宿主归属随 #48 更新）；"错过不补跑"语义保持=仍只补跑1次不积压重放（既有断言零改动），新增的是"错过必须可见" | ✅ | (本次提交) | 15.1勾选；Gate1 git diff 5文件173行真实落盘+key扫描0命中；Gate2 grep _log_missed 在 claim_due 赢家真实路径（L465）+真机真实 daemon 进程经该路径产出真实落库行+journal 行（接线铁证）；Gate3 tests/test_soulmate_scheduler.py 31/31（新增8例：interval 9执行点留痕/cron 3执行点/轻微迟到零噪音/停机10天封顶100截断自报/missed不计fail_streak/留痕不重复/接线护栏）+同域回归137/137（daemon/scheduler/cron_reporter/wakeup×4，其中2条存量断言随新行为修钉：test_daemon_hosts_real_scheduler_claim_and_fire rows[0]次序、test_run_due_reports_failure_with_streak statuses 含随行missed——与周期#88 同类"断言跟上"非语义回退）+py_compile；**真机验收全链实证**（杀掉周期#89 验收残留 agent 进程 1203228 后 pgrep=0，重启 soulmate-daemon 载新码）：探针任务 next_run 拨到 610s 前→daemon(无agent进程) claim+fire、runs 真实落"错过10个执行点（10-11 22:53~10-11 23:02）：宿主未在点执行，按'错过不补跑'语义跳过不积压"+journal⏭️行；二跳绑真实会话→ok 行 end_turn（真实LLM回合"周期#90 十五点一守护探针"落 agent_messages turn_origin=cron）+📊[定时汇报]行落库（turn_origin=cron_report）=15.1 验收行两连命中；到点/轻微迟到执行零 missed 噪音（真实库 ok 行旁无missed行双形态实证）；测试产物清理（探针任务已删 runs 4行留审计/脚本已删）；⚠️诚实边界：①missed 判定=执行点粒度（>due且<=now），与 fire 时刻重合的边界执行点计入 missed（确定性口径已文档化）②missed 留痕只进 runs+journal，不进 📊汇报（防噪音，用户要汇报面可见可另案）③空 session 任务 📊无处可投如实跳过（cron_reporter 既有诚实行为非缺陷）④真实库存量 disabled 遗留任务 #2 wd_probe_1791631 非本周期产物未动（15.4 范畴）；⚠️并发纪律：git diff 全程仅本周期 5 文件（sibling 修改告警 3 次均为 mtime 级假警，逐文件 git diff 核实无他会话混入）；下周期做 15.2 工具执行时长落账（trajectory_events tool_call duration_ms 全 0.0 盲段）
[2026-10-11 23:38] 周期#91 | 15.2 工具执行时长落账（14.4 诚实边界③销账）：soulmate_agent 三落账点执行计时（主执行路径/缓存命中路径 (time.time()-tool_start)*1000 + batch call_log 自身 duration_ms）→ all_tool_calls 账本条目 duration_ms + trajectory_recorder._tool_child_events 透传落 tool_call 事件列（record_llm_call/record_round 两批共用单一真源，旧条目缺省 0 不改写历史）+ turn_timing_report ③工具执行段独立列（tool_exec_s/tool_exec_calls/tool_exec_metered；batch: 子调用含于 batch_execute 行汇总不重复计、明细独立可见；工具计时跨度计入空洞覆盖⑤收窄；未计时行按行数诚实标注） | ✅ | (本次提交) | 15.2勾选；Gate1 git diff 3文件51+11行+新测试文件真实落盘；Gate2 grep 三落账点+_tool_child_events 透传在 record_round/record_llm_call 真实批量路径+真实 WS 回合经真实链路产出 duration_ms>0 真库行（web_search=20092ms/message_send=52ms，[infection-guard] 伪条目如实 0=无执行不编造）；Gate3 tests/test_tool_duration_ledger.py 18/18（透传4+落账路径2+E2E真实执行3（terminal echo 子进程/缓存命中/拦截诚实边界）+报告6（独立列/batch不重复计/空洞收窄 10→5s/全0与部分计时诚实标注/旧字段零回归）+接线护栏3）+受影响面回归 334/334（trajectory/aux/relay/loop_guard/infection_verdict/rounds/steering/report_evidence/self_verify 186+code_mode×2/tool_recovery/proxy_relay/tool_output/task_judge/plan_execution 148）+py_compile+双实例重启 health 200/200；真实验收=验收对标轮转 调研+发布 2/2✅（tag=ACC91_1791732358，调研 39.44s web_search 账本命中/发布 8.34s 投递行#1613，10 类轮转累计 4/10）+turn_timing_report 真实双会话四段拆分（om-a53a3fea04d8/om-86a7050f20f5）③工具执行段独立列 20.09s/0.05s 可见、⑤空洞 0s，证据 /tmp/turn_timing_152.json；测试产物清理归零（/tmp/soulmate_accept 已删）；⚠️诚实边界：①修复只影响新行，存量 543 行 duration_ms=0 历史行不改写（全库 545 行中 >0 者=本周期 2 行）②batch_execute 外层行=整批时长、batch: 子行=批内自身时长，报表汇总不重复计但明细并列可见③拦截/伪条目不计时，未计时行按行数如实标注（本轮 1/2 形态实证）④工具计时起点为回推近似（事件记录时刻=执行结束，跨度=记录时刻-dur）；下周期做 15.3 aux LLM 延迟治理（标题/规划 35-40s 归因关键路径后瘦身+短超时回退，0.2 提速成果不回退）
[2026-10-11 23:58] 周期#92 | 15.3 aux LLM 延迟治理（归因+关键路径治理）：真实库归因（aux_llm 30行）title 17.1s均值/峰值62.6s但fire-and-forget不在首字关键路径、plan 11.2s均值/35.1s峰值**串行挡首字**（旧顺序TaskPlanner.plan先LLM规划完即被decomposer覆盖丢弃=纯浪费）→治理两刀：①task_engine.plan decomposer pre-gate前移（多文件确定性拆步先于LLM规划，多文件目标完全跳过规划往返，decomposer结果本就覆盖LLM计划=语义面不变）②_aux_llm_chat关键路径label{plan,task_judge}短预算SOULMATE_AUX_CRITICAL_BUDGET_S默认12s（<=0关闭）到点TimeoutError走调用点既有确定性回退（plan"简单任务"/task_judge"new"）+账本status="timeout"如实；prompt瘦身经归因不需要（标题小prompt同形态均值17s→TTFT主导，体积非变量）；0.2提速不回退（prefetch零改动）；存量修钉：test_no_silent_truncation[:3000]守护 vs 周期#86 _aux_llm_label取样两车道相撞（HEAD已挂非本刀引入）→取样上限命名常量_AUX_LABEL_SAMPLE_CHARS显式化（非数据截断，守护不弱化） | ⚠️ | (本次提交) | 实现+测试全过但15.3验收（真实首字回复时间对比）未跑→checkbox不勾下拍补测后勾；Gate1 git diff 3文件真实落盘（task_engine pre-gate前移+soulmate_agent预算/常量/等价重构+tests新文件8用例）；Gate2 grep _aux_critical_budget_s→_aux_llm_chat运行路径L1115/_AUX_LABEL_SAMPLE_CHARS→_aux_llm_label L1085/pre-gate→plan()运行路径L370；Gate3 tests/test_aux_latency_budget.py 8/8+全量2713/2713全过（含2存量失败修复后归零）；⚠️诚实边界：①task_judge预算回退依赖调用点既有except（_prompt_inner"defaulting new"），端到端真实触发未做②非多文件目标仍付规划往返但有12s硬帽（均值11.2s不变差、峰值35.1s→≤12s）③title 62.6s峰值后台任务不动（不修没坏的）④真实首字对比未跑，0.2提速不回退仅代码级证明；下周期：15.3验收轮转补测（首字对比+aux账本不变差）后勾选，否则按序15.4工作区遗留清理
[2026-10-12 00:20] 周期#93 | 15.3 验收补测销账 + 验收轮转 开发+办公：①15.3 真实验收（新探针 probe_first_token_153.py，真实 WS 用户路径 initialize→session/new→模拟打字8s→prompt→首帧计时+aux账本回读）**4/4✅勾选**：MF多文件目标（a.py+b.py）[aux] plan 行=0=pre-gate 完全跳过 LLM 规划往返、首个用户可见输出 0.59s（📋任务拆解确定性推送，旧形态被 plan 往返 11.2s均值/35.1s峰值挡首字）；SF单文件任务 [aux] plan=2.48s status=ok（≤12s预算帽内）、首字16.9s/18.4s end_turn；服务端副作用=a.py/b.py/c.py FIXED 标记真实落盘；aux 账本不变差（新行 plan 2.48s vs 基线均值 11.2s、title 12.5s/reflect 11.0s/infection_audit 4.4s≈基线、全 status 诚实零超预算记 ok）；0.2 提速不回退（MF 首个可见输出 0.59s ≤ 基线 6.00s 口径）②验收对标轮转 开发+办公 2/2✅（tag=ACC93_1791735068：开发 22.46s dev_hello.py+terminal 运行账本命中、办公 38.03s report.xlsx 4978字节有效xlsx），累计 6/10（余 定时/多渠道/记忆/协作）；测试产物清理（/tmp/ft153 已删、acceptance cleanup 归零，/tmp/probe_first_token_153.json+accept_results.json 留证） | ✅ | (本次提交) | 15.3勾选；Gate1 git diff 3处路线图真实落盘+新探针文件；Gate2 本周期为验收测量（无产品代码改动）：探针真实消费生产链路（ws_acp→soulmate stdio→真实LLM回合+工具执行），aux 行=trajectory_events 真库回读非自报，FIXED 标记=文件真源；Gate3 真实产出=/tmp/probe_first_token_153.json（4 checks 全过 verdict=PASS）+/tmp/soulmate_accept_results.json（2/2）+真库 aux 行回读（MF session om-f0b440140e36 无 plan 行/SF session om-cbf975fc3b4e plan 2.48s）；⚠️诚实边界：①MF 首帧=任务拆解推送（用户可见但非最终回复首字，主回复首字未单独计时）②SF 首字 16.9s 含工具执行轮非纯 LLM TTFT（与 0.2 简单消息口径不可直接比）③超时回退真实触发未实测（单测覆盖，需慢 LLM 环境）④本周期未跑 pytest 全量回归（无产品代码改动，周期#92 已 2713/2713）；下周期做 15.4 工作区遗留清理（untracked 归位，删前备份铁律）+验收轮转 定时+多渠道
[2026-10-12 00:45] 周期#94 | 15.4 工作区遗留清理销账 + 15.5 flaky 观察 + 验收轮转 定时+多渠道：①15.4（删前备份铁律）：4 件 untracked 杂物字节级备份至 docs/devlog/attachments/2026-10-12-workspace-cleanup-154/（sha256 双侧逐一比对一致）后删除——根目录游离 chat-client.tsx（964 行旧拷贝，diff 与真实 2900 行客户端无共同结构）、agent/tool_infra.py（零引用死模块；docstring 所称 image_generator/voice_generator/doc_generator 实名 image_tools/tts_tools/docgen_tools 且各自已有 MEDIA 实现，接线=纯重构零用户价值→按"删除"分支）、fault_degradation_test.py+results.json（10-06 旧故障注入探针产物零引用）；周期#90 顺延项一并处置：cron_tasks.db disabled 遗留探针任务 #2 wd_probe_1791631（周期#33 产物）备份 JSON 后删除（tasks 表清零、8 行 runs 历史审计行不改写）；未动非本清单 untracked（docs/agent-architecture/*-wechat-preview.html ×2=他线产物、src/app/api/trace/=已注册路由 trace 信标）②15.5 flaky 观察：test_head_stall_aborts_fast 已更名 test_head_stall_aborts_fast_and_retry_recovers（周期#87 断言随规格更新），单跑 5/5 过 call 1.25-1.27s + 32 例批跑过 1.26s，3.3s 边际形态未复现→按约不预防性放宽上限，持续观察③验收对标轮转 定时+多渠道 2/2✅（tag=ACC94_1791735959：定时 83.65s cron_tasks.db accept_probe 行=1、多渠道 162.54s 出站审计行 wechat→accept_peer 含探针文案、双双 end_turn），累计 8/10（余 记忆/协作）；测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY 标记 0、accept_probe 任务行 harness cleanup 后 0） | ✅ | (本次提交) | 15.4勾选；Gate1 git diff 路线图 2 区块+备份附件 5 文件真实落盘（4 原件 sha256 与备份逐一一致+wd_probe JSON 含完整行数据）；Gate2 grep tool_infra/fault_degradation 清理后全仓零引用（被删模块无被依赖）+被依赖面反证=docgen/image/tts 工具测试 64/64 全过（若有隐性依赖则 ImportError 红）；Gate3 真实产出证据=git status 清理后 4 件 untracked 消失（余 3 件非本清单如实留）+cron_tasks.db 回读 tasks=0（DELETE changes()=1 实证）+/tmp/soulmate_accept_results.json 2/2 回读+验收 harness 服务端副作用判据（accept_probe 行/出站审计行均为真库行非探针自报）；⚠️诚实边界：①备份文件随本次提交进 git（devlog 附件=文档化备份位，历史可追溯），原件删除不可逆但字节级副本可随时还原②tool_infra"接线"分支未选=判断依据已记录（三工具模块各有实现+零引用），若后续要做胶水统一属重构另案③15.5 未复现=当前机器负载下未复现，非"证明不 flaky"，断言上限维持 3.0s 不动④验收 2 类各 1 次同机低负载，多渠道 162.54s 属该类历史常态区间；下周期做 15.5 持续观察+验收轮转 记忆+协作（收尾 10/10）或待拍板项落地（⚡8.6/4.1b/ASR/13.3②/13.2豁免语义）
[2026-10-12 01:05] 周期#95 | 验收轮转 记忆+协作（轮转收官 10/10）+15.5 flaky 观察+阶段16规划：①验收对标轮转 记忆+协作 **2/2✅**（tag=RETEST_1791737073，WS 真实用户路径，每类独立 session，双双 end_turn verify_mode=server_side）——记忆 53.03s（memory_append 追加 ACCEPT_MEM_MARK_9→memory_read 展示，MEMORY.md 服务端真源含标记=是）、协作 61.54s（subagent 双 worker 并行"只回复一个词"任务，回复含双 worker 产出+subagent_runs 近8行=8 服务端真源）；采集侧系统帧过滤实证生效（self_check×1 被过滤）；**五周期累计 10/10 全集收官**（周期#89 调试+运维→#91 调研+发布→#93 开发+办公→#94 定时+多渠道→#95 记忆+协作）②15.5 flaky 观察第二轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.31-1.50s）+32 例批跑过（stall_guard 17+stream_stall 15），3.3s 边际形态仍未复现→按约不预防性放宽上限（3.0s 不动）③测试产物清理归零（MEMORY 标记 0、/tmp/soulmate_accept 已删、accept_probe 任务行 0）④规划阶段16（16.1 待拍板项决策包/16.2 例行观察趋势汇总/16.3 用户实测反馈优先） | ✅ | (本次提交) | 验收轮转勾选收官（15.5 保持未勾=持续观察项）；Gate1 git diff 路线图 4 区块（15.5 观察追加/轮转勾选收官/阶段16 新段/本条目）真实落盘；Gate2=N/A（测量+观察周期，无产品代码改动）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（MEMORY.md 文件/subagent_runs 行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2 与台面一致+MEMORY.md 标记写入后 harness cleanup 归零回读+flaky 观察 3+32 例真实 pytest 输出；⚠️诚实边界：①验收 2 类各 1 次同机低负载，记忆 53.03s/协作 61.54s 属两类历史常态区间（记忆类曾见 300s 级上游波动样本，本轮未现）②15.5 两轮未复现≠证明不 flaky，属当前机器负载下未复现，长窗口继续观察③协作类 verify 依赖 subagent_runs 写入链路存活（本轮真实行=8 命中）④本周期未跑 pytest 全量回归（无产品代码改动）；下周期做 16.1 待拍板项决策包（⚡8.6/4.1b/ASR/13.3②/13.2豁免/画像清理 一次给齐供用户拍板）
[2026-10-12 00:55] 周期#96 | 16.1 待拍板项决策包（6 项一次给齐）：决策包落 docs/devlog/2026-10-12-decision-package-161.md——每项=现状实证/选项对比表/建议/风险+文末拍板清单（①⚡8.6 双链 ②4.1b iLink ③ASR ④13.3②判词复用 ⑤13.2豁免语义 ⑥1.5画像清理）。本周期新实证：①8.6 形势重估——merge-base 3d81e051（周期#23）起 evo/exp-1791613018 冻结于 aa632ecf（周期#31）仅 13 提交、main 前进 99 提交至#95，周期#31"以 evo 链为主干合并"建议**已过时**（main #35-39 已独立重做 6.x skill_loader/skill_evolution/skill_rewrite + 7.x browser/docgen/tts/image_tools 且承载后续 50+ 周期治理），建议改 A 归档封存（或 C cherry-pick evo 独有探针 test_skill_usage_receipt/test_skill_matcher 等后归档），B 全量合并=两套实现冲突+语义漂移高风险零收益；②1.5 存量数字作废旧值——真实库 ~/.hermes/soulmate/preferences/preferences.db 实测 14 条（13 偏好+1 环境）/9 嫌疑（周期#6"68/64"为旧时点已作废），嫌疑聚类=key由value截断生成×9+否定偏好丢"不要/别"前缀语义反转×8（可叠加）；③ASR 依赖实测：faster-whisper 1.2.1 仅在 hermes venv（opensoul venv ModuleNotFoundError）、TTS 已有（7.3）、前端语音输入与 voice 路由未实现。六项建议=A(C)/A/C/A/A+C/A，等用户逐项拍板后落地 | ✅ | (本次提交) | 16.1勾选；Gate1 git diff 决策包新文件 7784B+路线图 2 区块真实落盘；Gate2=N/A（纯文档周期，无产品代码改动）：决策包所有数字均来自本周期真实读取（git merge-base/rev-list 计数、git ls-tree 分支文件对比、UserPreferenceLearner.audit_entries() 真库查询、faster_whisper import 双 venv 实测、weixin.py 存在性核实），文档内引用的旧数据（68/64、周期#31 建议）均显式标注时点/作废不冒充现状；Gate3 真实产出=docs/devlog/2026-10-12-decision-package-161.md 落盘回读 6 项齐+文末拍板清单 6 行+路线图 16.1 勾选含实证摘要；⚠️诚实边界：①8.6"evo 独有内容"清单基于文件名级对比（git ls-tree diff），未逐行 diff 两套 6.x/7.4 实现语义等价性（拍板选 C 时再做）②画像 14/9 为拍板时点快照，SoulMate 日常学习会持续增删③ASR 多模态直路由可行性依赖 MiMo token plan 是否支持 audio（未确认，决策包已标注待确认）④15.5 flaky 观察项本轮未复跑（连续两轮未复现，维持 3.0s 上限继续长窗口观察）；下周期做 16.2 例行观察趋势汇总（14.3 三探针长窗口数据成趋势行：免审命中率/审计往返耗时分布/同签名复现率）或用户拍板项落地（视用户回复）
[2026-10-12 01:20] 周期#97 | 16.2 例行观察趋势汇总（只读不改产品代码）：三探针复跑留数据（prescreen_hitrate 24h窗 98批命中率60.2%（免审59=user_verbatim 45+exec_verb 13+numeric 1/升级39，双通道59vs53边界差异如实报）、audit_cost 27次往返均值4.20s p50 4.15s p95 8.23s max 10.20s 全verdict=ok零失败+同签名复现率 sig/full 双口径 0/22 0.0%+chars↔耗时 pearson 0.252 无稳定相关、audit_trail 七面：孤儿0/llm:dna闭环12行缺归因0/verdict_basis 21行全真实依据零缺口/拦截行54零新拦截/分类越界2+unknown 9 与#88边界清单逐一致=empty_output修复后零新增缺口/provenance·self_heal 0行如实报）+跨周期趋势汇总落 docs/devlog/2026-10-12-observation-trend-162.md（4趋势表：免审命中率#72 50%→#83 55.1%→#88 62.9%→#97 60.2%稳态、审计耗时均值3.8~4.2s/p95 6.7~8.2s累计56次零失败、复现率4窗口0%、闭环质量全绿）+15.5 flaky第三轮观察（单跑3/3过1.47-1.51s+32例批跑过，3.3s边际仍未复现，上限3.0s不动） | ✅ | (本次提交) | 16.2勾选；Gate1 git diff 路线图2区块+devlog新文件真实落盘；Gate2=N/A（纯观察/汇总周期，无产品代码改动）：三个探针均为只读消费真实库/journal（tool_failures.db 119行/opensoul.db trajectory_events/journal 24h窗），趋势表数字逐一来自本轮探针输出与历史周期日志回读非估算；Gate3 真实产出=三探针真实运行输出 /tmp/probe97_{hitrate,cost,trail}.txt 回读与趋势表一致+flaky 3+32例真实pytest输出；⚠️诚实边界：①24h滚动窗样本重叠（当前98批含前窗样本），趋势为窗口快照对比非独立样本统计，禁按独立样本解读显著性②复现率0%=测量未支持有收益非证明为零，但4窗口方向一致足以支撑16.1④建议A③三个自然触发观察项（11.5/8.5c-③/13.2豁免）连续4轮0行=未发生非缺陷④15.5三轮未复现≠证明不flaky；下周期做16.3 用户实测反馈优先（无反馈则维持例行观察+决策包跟进，不造无依据新需求）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 等用户逐项拍板）
[2026-10-12 01:35] 周期#98 | 16.3 首轮用户反馈挖掘（零反馈）+决策包跟进（16.1③"待确认"查实销账）：①反馈挖掘四触点全查——opensoul.db agent_messages 近窗口（10-10~10-12 共355条）全为验收探针/子代理/定时会话（session_source=test/ai-group-task-chain/cron+空metadata实查均为探针文案）、wechat_bridge.db peers/seen 均0行、tool_failures.db 近行全为 s1 测试会话合成参数（script:"  "/reason:"r"）、ai_groups discussion_messages 62条全为 agent 讨论——**零真实用户反馈**，按16.3兜底转决策包跟进②决策包161③ASR"待确认"官方文档查实（mimo.mi.com audio-understanding+token-plan pricing）：MiMo token plan **支持音频直发**（mimo-v2.6-pro/flash/pro-ultraspeed/v2.5 支持 input_audio 内容块，OpenAI 兼容；URL≤100MB 或 Base64 data URL `data:{MIME};base64,`≤50MB 本地录音无需公网URL；MP3/WAV/FLAC/M4A/OGG；audio token≈秒×6.25）→ **A 多模态直路由可行性前提消除，现有 key 即可达成"不走 whisper 两步"原要求，无需 GPT-4o-audio/Qwen-Omni key**；另有 mimo-v2.5-asr（按时长计费）/TTS 限时免费同在 token plan（兜底备选）③顺带查实运营期限：官方公告 mimo-v2.5-pro/v2.5 于 10-14 18:00 自动路由 V2.6、**10-21 10:00 后旧模型 ID 失效**——实测活跃配置零风险（opensoul .env LLM_MODEL=mimo-v2.6-pro@token-plan-cn，acp-proxy 无 v2.5 钉，.env 仅注释示例含 v2.5 字样） | ✅ | (本次提交) | 16.3保持未勾（常设观察项：每周期先挖反馈触点）+16.1③可行性销账；Gate1 git diff 2文件真实落盘（决策包③区块+路线图16.3注记）+git diff 核实无他会话混入（sibling告警为mtime级假警）；Gate2=N/A（纯调研/文档周期，无产品代码改动）；Gate3 真实产出=官方文档两页真实抓取（audio-understanding API 形态逐字段/base64 前缀契约/token-plan pricing ASR 计费行）+四触点真实库查询输出（零反馈结论有据）+配置 grep 回读（迁移风险面=0 实证）；⚠️诚实边界：①"零反馈"仅指本窗口四触点（用户可能在其他渠道有反馈未接入，如口头/群聊外渠道）②audio 直发仅文档查实未实弹调用（无音频样本+扣额度，落地切片时 e2e）③token-plan-cn 端点对 input_audio 的支持假定与 api.xiaomimimo.com 一致（同一 OpenAI 兼容面，文档未分端点陈述）④10-21 失效面仅扫了 opensoul .env/acp-proxy 配置/jobs.json（用户手工配置的其他工具不在扫描面）；⚠️git 事故（同类第2次）：开工时工作树已被 evo 切到 evo/exp-1791738075（周期#97 提交即落该分支未在 main），本周期提交随之落 evo 分支——开工 `git log` 只看提交看不出所在分支；核实 evo 停跑（无进程+evolution_v2.py mtime 昨日）后 `merge-base --is-ancestor` 验快进安全→checkout main+`merge --ff-only` 归位（#97/#98 双提交一并落 main 零丢失），教训=开工必查 `git branch --show-current`（已补进 skill 坑位8）；下周期=继续 16.3 反馈挖掘+决策包跟进（或用户拍板项落地：16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；③拍板即 C 时落地切片可排 17.x）
[2026-10-12 02:15] 周期#99 | 17.1 音频输入多模态直路由（后端链路，阶段17立项）：16.3反馈挖掘先查四触点（agent_messages 近窗口零真实用户消息）→按用户原要求落地16.1③A/C共同前半段（"语音直接路由多模态大模型不走whisper两步"，兜底层17.3留拍板）——①provider探针5轮实测定schema（input_audio data URL 形态 audio_tokens=秒×6.25 精确命中18=2.8s×6.25；三变体全通；**发现provider实测坑：音频回合偶发答案只写reasoning_content、content留空**，旧消费面if reasoning: continue直接丢弃=空回复；enable_thinking/thinking.disabled/reasoning_effort三参数实测可关thinking但属非标准字段有fallback链400风险→不采用）②llm_engine.attach_audio_parts（锚点定位用户"[附件已保存到:path]"注记，fallback_last供辅助提示词，mime→format映射，未映射/空数据显式跳过报因，找不到目标绝不乱挂）+reasoning→content回退三路收口（chat_stream/chat_stream_with_tools/chat：content空且无tool_calls才回退，content正常不掺/工具轮不掺回归钉死）③soulmate_agent提取修复（dict分支audio原被丢弃、SDK AudioContentBlock原被误标image→按型分派）+落盘收集+_TURN_AUDIO回合上下文（可变list同_AUX_TRAJ_CTX语义，worker走完整prompt回合自动覆盖）+_run_llm_with_tools每轮挂回+_aux_llm_chat随行（plan/judge/reflect/title自动带上，infection_audit显式排除）+纯语音临时标题🎤+引导语（实证补丁：无引导模型去bash读文件不用听感） | ✅ | (本次提交) | 17.1勾选（17.2前端语音输入待做、17.3兜底层待拍板）；Gate1 git diff三文件真实落盘（llm_engine +125/soulmate_agent +65/新测试366行）；Gate2 grep attach_audio_parts/回退三路/_TURN_AUDIO消费点在_run_llm_with_tools与_aux_llm_chat真实路径+真实MiMo探针经attach_audio_parts产出的wire消息被真实接收（audio_tokens=18）；Gate3 tests/test_audio_direct_route.py 26/26（attach 12例含锚点/幂等/兜底/负例、reasoning回退真实socket 6例含工具轮不掺、列表content容噪、agent接线3例=真实_prompt_inner简单路径dict块与SDK对象块双形态wire实证+落盘+锚点+attachments元数据、aux随行3例）+受影响面回归534全过（llm×10套件214+aux/steering/turn/plan/prefetch/token归因182+infection×3/self_verify 112+新26）+py_compile；**真实provider验收PASS**（worktree新代码+真实MiMo：wire_block=input_audio格式wav、audio_tokens=18、引导语后模型直接"听到"作答"香蕉、橘子…原话为'我香蕉橘子一二三'"非bash读文件）；⚠️诚实边界①完整WS用户路径e2e未跑（服务运行共享树=evo在跑的实验树，重启服务会载入evo工作状态，禁碰——留17.2或服务重启后补测）②音频回合的thinking回退文本可能带思考腔（"The audio is very short…"），真clean作答靠引导语（实证有效）非字段强制③音频token不入估算（~6.25/s，短语音影响小）④17.2前端未做（后端链路先行）；⚠️**evo并发git事故（同类第3次）**：周期中途evo pre-branch stash+checkout把未commit两文件收走（stash@{0}=evo-pre-branch-1791741453，工作树切到evo/exp-1791741453）——按铁律禁pop evo stash/禁主树切分支，git stash show -p提取311行补丁+tail3补充修复另行留证，git worktree add /tmp/wt99/main main隔离树重放+测试+提交后worktree remove，零丢失（新测试文件为untracked未入stash，字节备份后随worktree入库）；教训：**周期开工改文件前先commit护栏不足，改完即commit比攒批安全**——本切片实现与测试应分小步提交；下周期做17.2前端语音输入（录音按钮+MediaRecorder→audio ACP块+历史audio卡片渲染）
[2026-10-12 02:30] 周期#100 | 17.2 前端语音输入（录音→audio ACP 块→历史 audio 卡片）+存量 build 红灯根治：①16.3 反馈挖掘先查（agent_messages 01:30 后零真实用户消息，四触点无新反馈）②实现详见 17.2 条目（chat-client 4 接线点+smart-prompt 麦克风按钮接线+audio-record.ts 统一转 WAV）③根治周期#94 遗留 build 红灯：docs/devlog 备份的截断 root-chat-client.tsx 被 tsconfig `**/*.tsx` 卷进类型检查→npm build 必红（#94 后无前端构建周期故未暴露，本周期 worktree 构建时红→绿实证），exclude 加 "docs"（scripts 同款先例），备份文件不动 | ✅ | (本次提交) | Gate1 git diff 5 文件真实落盘（chat-client +150/-2、smart-prompt +24/-2、audio-record.ts 新增 117 行、tsconfig +1、路线图）；Gate2 grep 接线点全数命中（blobToAudioAttachment→startRecording onstop 真实录音收尾路径、onMicClick chat-client→smart-prompt 按钮 onClick、sendAcpPrompt audio 块=真实发送路径、p.type==='audio' 消息渲染/loadHistory mime 映射/fileAttachments 收集三分支）；Gate3 三层真实产出——npm build 全绿（Next16 Turbopack 完整路由表+static prerender；docs exclude 修复前红在备份 .tsx 40+ TS 错=红→绿对照实证）+后端契约 tests/test_audio_direct_route.py 26/26（17.1 接收端 wire 契约 {type:'audio',data,mimeType} 与前端发送形态逐字咬合）+新 lib 单元验证 8/8（tsc 编译产物真实执行：WAV 头部数学/样本定点转换+削顶/空音频/无 MediaRecorder 诚实 null）；⚠️诚实边界：①录音→发送→模型"听感作答"的真机 e2e 未跑（需真实麦克风+用户浏览器；且部署需前端构建+服务重启=共享树被 evo 占用禁 build/重启，待 evo 停跑归位后部署实测）②各浏览器 decodeAudioData 对 webm/ogg 的解码支持未逐一实测（统一转 WAV 策略消除录音格式差异，转换失败有诚实回退+显式报因兜底）③音频附件在录音 onstop 内同步转换不走 FileReader 异步，readingCount 守卫不适用（发送按钮随 attachments 状态自然启用）；⚠️git 纪律：evo 持续活跃（02:14 双心跳+02:04 evo round 提交）全程 git worktree /home/climbing/wt100（main）隔离开发+测试+提交，共享树零接触；下周期=17.3 兜底层（待用户拍板）或部署实测 17.2（evo 停跑后）+16.3 持续反馈挖掘

[2026-10-12 03:28] 周期#101 | 8.5c-②b 拒做补做（验收实测暴露·用户可见缺陷最高优先，#42诚实边界"拒做行为本身未强制重试"销账）+16.3反馈挖掘+验收抽样+15.5第四轮：①16.3反馈挖掘（agent_messages 01:35后零真实用户消息、wechat_bridge.db 空库、ai_groups 新4行为agent自我介绍、tool_failures 零新行）——零反馈维持②验收抽样（WS真实用户路径 tag=RETEST_1791744628）1/2：开发✅40.94s verify_dev三判据命中；调研❌**模型拒做**——逐字复现12.2已知形态"当前会话工具列表中没有 web_search"（注意无"的"新变体），治理面实证按设计工作（8.5c-②抽取命中+journal"可用性误报×1: web_search(registry)"+回复前置"工具可用性更正"+"执行证据不足"横幅均正确）→复测（tag=RETEST_1791744814）✅64.79s（web_search 执行行=2+来源行命中）=**拒做间歇复发 1/2 实证**③8.5c-②b 实现（b56825aa）：report_evidence 增 remediation_targets（proof=registry+用户明文点名才补，模型自选工具取舍自由不越俎）+build_execution_remediation_prompt/remediation_succeeded/build_remediation_response（新文案**零可抽取声称形态**防二次误报更正/防验收判据主答区污染，措辞安全测试钉死）+soulmate_agent 拒做补做 hook（置于 8.2 证据治理之前，有界1轮：补做真执行→最终回复=补做答案+更正声明[首轮原文留档journal/流式帧]；再拒做→不改写落回既有更正声明路径，绝不循环）+tests/test_refusal_remediation.py 23例（目标判定6/措辞安全4/成功判定4/事故句回归2/接线护栏4/**真实 _prompt_inner E2E 2**：拒做→补做真执行 web_search→答案+更正声明、再拒做→既有路径）+test_tool_availability_claims 接线钉断言跟上（锚点改 amend_availability 补钉新架构）；同域回归133+prompt路径463全过+服务同款 interpreter 导入烟测④15.5 flaky 第四轮：单跑3/3（1.42-1.58s）+32例批跑全过，3.3s边际仍未复现→上限3.0s不动 | ✅ | b56825aa | 8.5c-②b 新增销账（#42诚实边界）；Gate1 git diff 4文件400行真实落盘（worktree /home/climbing/wt101@main）；Gate2 grep 四 helper 调用点+hook 真实路径（接线钉+真实 _prompt_inner E2E 走中 hook：补做请求 wire 含"系统更正指令"、search_backend.run_search 真实被调=写了+接线了+跑通了）；Gate3 真实产出=新测试 23/23+回归 596 全过（133+463）+验收真实 WS 两轮（拒做复现样本+复测通过样本）；⚠️诚实边界：①拒做补做的**真实用户路径 e2e 未跑**——新代码在 main，共享树（evo/exp-1791741453，evo 03:28 仍活跃+新 round 提交 7c92ca03）被占用未部署，运行中服务/新 spawn 会话仍为 evo 分支=#98 代码；部署实测=下拍第一优先②拒做属 LLM 行为（sandbox 零裁剪+_active_tool_names 在册实证为假话），补做把间歇失败自愈为成功但不改模型倾向，复发率待部署后多轮采样③补做上限1轮，再拒做即止未做多轮④E2E 的 LLM/判词为注入 fake（链路为真：真实 _prompt_inner/真实 dispatch/真实执行账本），真 LLM 自然拒做触发补做待部署后调研类采样⑤本轮验收为抽样 2 类非全量10类；⚠️部署缺口记录：共享树落后 main 3 提交（17.1/17.2/8.5c-②b 合计 9 文件 +909 行），evo 窗口归位后统一部署；⚠️git 纪律：evo 全程活跃（02:45/03:28 双心跳+两次 round 提交），git worktree /home/climbing/wt101（main）隔离开发+测试+提交，共享树零接触；下拍=**阶段18 部署与语音实测**（18.1 归位部署：evo 停跑/安全窗口 git 归位 main→npm build→重启 acp-proxy 双实例+openmate-web→真实 WS audio e2e（edge-tts 语音样本验"听感作答"）+拒做补做自然触发观察采样；18.2 录音→发送→听感作答真机全链（需用户浏览器麦克风，待用户实测配合））；17.3 ASR 兜底层待用户拍板不占序
[2026-10-12 04:20] 周期#102 | 18.1 归位部署+真实 WS audio e2e+18.1b 自验锚点噪音当拍即修：①16.3 反馈挖掘（agent_messages 01:35 后零真实用户消息、wechat_bridge.db 空库 0 字节）零反馈维持②18.1 归位部署：evo 安全窗口=POST /api/evolution/config evolution_interval=86400 双实例暂停（**新发现：该配置仅存内存，acp-proxy 重启后回落 3600s**）→共享树由 evo/exp-1791745058 checkout main 归位 88932dc1（evo 停跑核实=strand 心跳虽活跃但无 execute/branch 流水线在途，最后 implement 03:19 已收尾；evo 两 round 实验提交留 evo/exp-* 分支零丢失）→npm build 全绿（BUILD_ID 03:51:09）→重启 openmate-web+acp-proxy 双实例（部署缺口 3 提交 17.1/17.2/8.5c-②b 上线，服务健康 3000/8092/8095/8090 全 200）③真实 WS audio e2e **PASS**：edge-tts 样本（32.5KB mp3"今天下午三点我们去公园放风筝，记得带上橘子和香蕉"）经 ACP {type:'audio',data,mimeType} 块直发→模型"听感作答"逐字复述✅无 bash 读文件（20.3s，session om-828b6d70542f；wire input_audio×4+[MSG]音频块+attachments 落库三重实证）④e2e 暴露缺陷当拍即修（18.1b）：self_verify 把"[附件已保存到:…]"/"[语音附件已随消息直发：…]"锚点注记当需求→必然 unmet→带附件消息必补做+噪音横幅（实测三项全"未覆盖"两项是锚点）；修=_ANCHOR_NOTE_RE 剥离（extract_requirements 单一真源），复测 items 3→1 实证⑤拒做补做自然触发采样：部署后调研类 1/1 ✅63.18s（web_search 账本 2 行+来源行，无自然拒做）=复发率观察待多轮采样 | ✅ | (本次提交) | 18.1/18.1b 勾选；Gate1 git diff 3 文件真实落盘（self_verify +10 行、test_self_verify +45 行、路线图 2 区块）；Gate2 grep extract_requirements 真实调用点（self_verify.py L214 verify 路径消费剥离后文本）+**真实 WS e2e 复测走中修复**（journal items=1 missing 仅剩探针元指令=锚点不再进需求=写了+接线了+跑通了）；Gate3 真实产出=新测试 5 例+回归 184 全过（self_verify/remediation_style/acceptance_verify_dev/ledger/refusal_remediation/tool_availability_claims/infection×4）+audio e2e 两轮真实 WS 产出（修复前 3 项噪音样本+修复后 1 项对照）+调研验收 /tmp/soulmate_accept_results.json 1/1 回读；⚠️诚实边界：①18.3 覆盖率假阴性残留（"复述你听到的内容"被答对仍判未覆盖→补做 1 轮+横幅），纯语音消息已零噪音、"文字+音频"形态仍有横幅，修复方向待定不冒进②拒做复发率仅 1 轮采样无自然触发，样本量不足定论③18.2 真机录音全链需用户浏览器麦克风配合，非本周期可闭环④evo 暂停仅内存级、重启回落 3600s（已补 skill 坑位），当前 evo 流水线恢复常态节奏、运行中服务不受后续分支切换影响（代码已载入内存）⑤audio e2e 的 LLM 为真实 MiMo（非注入 fake）；下周期=18.3 覆盖率假阴性（或用户拍板项落地：16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板）

[2026-10-12 04:24] 周期#103 | 18.3 感知类元指令覆盖率假阴性治理（18.1 复测残留当拍即修）：根因实证=确定性词元核对验不了"听到"——"复述你听到的内容"类元指令的正确答案以媒体语义内容作答（风筝/橘子），与指令文本零词元重合是常态→raw_score 0.16 必判 unmet→补做1轮+噪音横幅（答对仍补做=假阴性）；修=self_verify 增 is_perception_meta（纯感知复述判定；带动作动词的媒体任务如"把音频内容写入文件"=真实可验需求照常核对）+unmet 封顶 uncertain 不进补做清单+ok/覆盖率分母不含感知项（单条元指令=不触发横幅）+format_report 感知标签"（感知类元指令，无法核验）"+覆盖行"另 N 项…不计覆盖"（_MET_MIN/_UNCERTAIN_MIN 阈值未动）；16.3 反馈挖掘先查（agent_messages 04:20 后零真实用户消息、wechat_bridge.db 0 字节）零反馈维持 | ✅ | 660c3554 | 18.3 勾选；Gate1 git diff 2 文件 +125/-8 真实落盘（self_verify +56、test_self_verify +69）；Gate2 grep is_perception_meta 在 verify() 真实路径消费（perception 标志→ok 属性→soulmate 横幅条件 L6385、missing_requirements→补做 L6360 两真实消费点）；Gate3 三层真实产出——tests/test_self_verify.py 23/23（新增 5 例：检测正反例/答对不补不做横幅/未答对封顶 uncertain/带动作照常可验/覆盖率分母排除）+回归 173 全过（remediation_style/refusal_remediation/report_evidence/frame_origin/relay_chrome_filter/tool_availability_claims 105+acceptance_verify_dev/infection/plan_tool_ledger 68）+**真实 WS audio e2e 复测 PASS**（18.1 残留形态逐字沿用+唯一防缓存标记，session om-b82365be90f3，26.9s 听感命中风筝/橘子 2/2=答对场景真实成立）：帧面零"🔍 需求自检"零补做+journal 实证"[self-verify] items=1, coverage=1.0, missing=[], remediated=False"+pre-fix 推演 raw_score=0.16<0.25=修复前必 unmet→补做+横幅（修复是行为改变的唯一变量）；⚠️诚实边界：①感知类判定为确定性正则，混合形态（"听写音频并总结要点"）取保守侧"带动作动词不降级"宁可保留可验性，边界误降级只会少报缺口不会假报完成②多需求消息含感知项时横幅照推（多需求项规则不变），感知项以专属标签呈现③英文元指令仅覆盖 repeat/what you hear 常见形④e2e 中模型自发调了 sense_transcribe_audio（llm-fallback 转写）辅助"听"，与修复语义不冲突（听感作答仍命中）；下周期=验收对标抽样（新功能落地后按需复跑对应类别）+规划下一批切片（或用户拍板项落地：16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 04:43] 周期#104 | 验收对标抽样+阶段19规划+19.1 音频 token 估算纳入（当拍即做）：①16.3 反馈挖掘先查四触点（agent_messages 近2h user 消息 0、tool_failures 0 新行、wechat_bridge.db 0 字节、ai_groups 无新讨论）——零反馈维持②验收对标抽样 调研+多渠道 **2/2✅**（tag=RETEST_1791750875：调研 35.57s web_search 执行行=1+来源行+主答区自报搜索不可用=否、多渠道 30.1s 出站审计行 wechat→accept_peer 含探针文案，双双 end_turn；测试产物清理归零=workdir 已删/MEMORY 标记 0/accept_probe 行 0；拒做采样顺带累计：调研类部署后 2/2 无自然拒做）③阶段19 规划落盘（19.1 音频 token 估算/19.2 拒做采样/19.3 候选·感知元指令英文扩展+常设观察）④19.1 实装（17.1 诚实边界③销账+实证出双消费点同病第二形态：attach_audio_parts 后 content 变 block 列表，旧 str(content) 把整段 base64 载荷当文本计 0.4/字符=音频/图像虚高数万 token 致裁剪提前+归因失真）：token_attribution.estimate_content_tokens 块语义估算（text→文本公式、input_audio→秒×6.25（WAV 头 byte_rate 解析真实时长，其他容器/解析失败按 PCM 密度字节保守下界显式不假称精确）、image→低细节近似常量 1100、未知块递归剥 data/blob 载荷只计元数据；SDK 对象形态同语义兜底）+双消费点接线（context_budget._calibrated 裁剪估算+soulmate_agent 归因循环 L3786） | ✅ | 1cf32e1e | 19.1勾选；Gate1 git diff 5 文件 281 行真实落盘（含新测试 165 行）+key扫描0命中；Gate2 grep estimate_content_tokens 双消费点真实路径（context_budget.manage 内 _calibrated+归因循环）+接线测试真实源码断言（test_attribution_import_wired 同一函数对象）；Gate3 tests/test_audio_token_estimate.py 14/14（WAV 精确 2.88s→18tok/60s→375tok、data URL 容噪、非 WAV 字节下界、损坏 base64 不炸、块语义 text+audio、blob 不计文本（新估算<旧 str 形态 1/100）、image 常量、未知块嵌套 blob 递归剥、SDK 对象、ContextBudgetManager.manage 真实裁剪路径、归因接线）+回归 205 全过（token_attribution/calibration/usage_backfill/attribution_retention/reserved_buffer/token_manager_config/memory_marker/plan_execution 157+audio_direct_route/turn_send_budget 48）+py_compile+SoulMateAgent 导入烟测；⚠️诚实边界：①估算改动只影响新回合（进行中会话历史按当时估算裁剪不受影响）②image 块 1100 为业界低细节近似常量非精确公式（精确需解码像素，另案）③非 WAV 字节下界对压缩格式（mp3/m4a）秒数偏小=token 偏保守，宁少不多不假称精确④运行时无需重启（agent 为每会话 spawn 的 stdio 子进程，新会话即载新码；长驻 proxy 进程面无本改动消费点）；⚠️并发纪律：sibling 修改告警 1 次为 mtime 级假警，git diff 逐文件核实无他会话混入；下周期做 19.2 拒做复发率多轮采样（调研/开发类各补足 3 轮）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 05:25] 周期#105 | 19.2 拒做复发率多轮采样收官+采样暴露双缺陷当拍即修（19.2b 补做轮账本落账+19.2c 验收判据假阳性）：①16.3 反馈挖掘先查四触点（agent_messages 04:20 后 user 消息 0、tool_failures 0 新行、user_feedback 表 0 行、wechat_bridge.db 空库 0 表）零反馈维持②19.2 采样（REJ192 系列 4 轮+REJ192B 复测 2 轮）：开发 3/3 净（26.07/26.91/62.16s）+复测 1/1（33.58s）；调研 1 轮**自然拒做复现**（142.0s，REJ192_调研_1）+复测 1/1（62.12s）——加上 #102/#104 部署后样本共 8 轮：**拒做复发率 调研 1/4、开发 0/4、合计 1/8，8.5c-②b 补做自愈 1/1**（拒做原文→hook 自愈→落库最终回复=补做答案+可用性更正声明（首轮），答案逐字正确"Python 3.13 于 2024 年 10 月 7 日正式发布"）③采样暴露缺陷A（产品）补做轮工具账本落账缺口：拒做补做/自验补做两通道均不传 traj_seg_id→_traj_record_round 全跳过→补做真执行工具行永不落 trajectory_events（web_search 真执行但账本执行行=0，验收/归因/审计全盲）——修=两调用点建 llm 段 span（remediation_turn:/selfverify_remediation:）+traj_seg_id 透传+finally 收口（303c54f8）④缺陷B（测试面）验收判据拒做补做形态假阳性：契约"首轮原文留档 journal/流式帧"→WS 并帧 reply 含留档拒做原文→主答区判"自报搜索不可用"假阳性（落库最终回复干净仍判负）——修=verify 按落库回复=最终回复单一真源（verify_source=persisted_final_reply，19.2c 同 commit）⑤测试：test_refusal_remediation 27/27（+4 补做落账：捕获式 traj 替身实证 record_round 带 web_search 工具行挂补做段/段收口/空 span id 不崩/双调用点+验收判据接线钉）+test_trajectory_recorder 接线钉断言跟上（start 5→7/end 10→12/outcome 6→8）+受影响面 160/160（plan_execution/trajectory_recorder/tool_duration/tool_availability/report_evidence/turn_lifecycle/refusal_remediation）+self_verify/trajectory_rounds/aux_ledger/remediation_style 59/59+py_compile+SoulMateAgent 导入烟测⑥REJ192B 复测 2/2 ✅（开发 33.58s+调研 62.12s，web_search 账本执行行=1 命中——本轮无自然拒做=复发间歇性如实报，19.2b 落账路径被真实链路走中但未触发补做形态，补做落账实效待下次自然复发核账） | ✅ | 303c54f8+17f130f6 | 19.2勾选（19.2b/19.2c 一并销账）；Gate1 git diff 4 文件 164 行真实落盘（soulmate_agent +45/acceptance_benchmark +13/tests +119）+key扫描0命中；Gate2 grep _fix_seg 两调用点+traj_seg_id 透传在补做真实路径（接线钉+真实 _prompt_inner E2E 走中补做轮 record_round 工具行）+acceptance_benchmark verify_source 逻辑接线钉；Gate3 真实产出=新增 4 例+回归 219 全过+REJ192B 真实 WS 复测 2/2+/tmp/soulmate_accept_results.json 回读；⚠️诚实边界①拒做复发率 1/8 为部署后累计样本（8 轮同机低负载），间歇性行为禁按"已消失"结论，长窗继续采样②19.2b 补做落账的实效（真实自然拒做→账本落行核账）本轮复测未触发补做形态，E2E 为注入 fake LLM（链路为真：真实 _prompt_inner/真实 dispatch/真实落账路径），下次自然复发时核账③补做自愈不改模型拒做倾向，1/1 自愈率样本量小④误触发记录（诚实留痕）：勘察阶段 `acceptance_benchmark.py --help` 误跑全量验收（脚本无 argparse），180s 超时被杀未出结果、残留 /tmp/soulmate_accept 工作目录已清理（MEMORY 标记 0/accept_probe 行 0），该误跑不计入采样⑤⚠️git 事故（同类第 4 次+新形态）：evo 05:07 pre-branch 把主树切到 evo/exp-1791752822，本周期首 commit 40f144af 落 evo 分支，且 git add 误扫入并发会话 WIP 1 行（llm_engine import 行）——按纪律未 pop/未动他会话 WIP，改用 `git worktree add --detach /tmp/wt105` 隔离树 amend 剔除误扫行（303c54f8）+补测试（17f130f6），evo 回主树后 `git merge --ff-only` 归位 main 零丢失（ff 验证安全：提交链挂 235b767d 之上）；并发会话 WIP（context_budget._audio_tokens/token_attribution import math）全程未动未提交（单写入者纪律），其 import 行翻覆经 git diff 三次核实为 mtime 级假警+实际工作树健康（导入烟测过）⑥15.5 flaky 观察项本轮未复跑（超 15min 预算，下拍补）；下周期=19.2 长窗拒做观察（自然复发时核 19.2b 账本落行）+15.5 第五轮+验收轮转例行或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 05:50] 周期#106 | 19.2 长窗拒做观察+验收轮转例行+15.5 第五轮（观察期，#105 顺延三项一次补齐）：①16.3 反馈挖掘先查四触点（agent_messages 近6h user 消息 0、wechat_bridge.db seen/peers 两表均 0 行、tool_failures 无新行、ai_groups 无新讨论）——零反馈维持②15.5 flaky 第五轮观察：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.41-1.55s）+32 例批跑过（stall_guard 17+stream_stall 15，19.08s），3.3s 边际形态五轮未复现→按约不预防性放宽上限（3.0s 不动）③19.2 长窗采样+验收轮转例行（tag=REJ106_1791812200，WS 真实用户路径）**2/2✅**：开发 57.89s（dev_hello.py marker 命中+运行账本 terminal/execute_code 执行行=1 含程序输出非源码回显，真源=trajectory_events）+调研 40.72s（web_search 执行行=1+来源行+主答区自报搜索不可用=否）双双 end_turn——**本轮无自然拒做**，部署后累计 10 轮采样：开发 5/5 净、调研 3/4（1 次拒做→补做自愈），**合计拒做复发率 1/10、补做自愈 1/1**；19.2b 补做落账路径本轮未触发补做形态（下次自然复发时核账）④测试产物清理归零（/tmp/soulmate_accept 已删、accept_probe 任务行 0）⑤并发 WIP 未动（agent/context_budget.py+token_attribution.py 他线 WIP +12 行如实留，AST 解析健康，单写入者纪律全程不碰不提交——本次 commit 仅路径限定 docs/soulmate-roadmap.md） | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定不含他会话 WIP（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/文件 marker）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，开发 57.89s/调研 40.72s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/10 为累计间歇率（拒做样本仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 五轮未复现≠证明不 flaky，属当前机器负载下未复现⑤ai_groups discussion_messages 表在 opensoul.db 不存在（本轮以库缺失如实报，历史周期口径为独立库——触点等效覆盖：无新讨论产物）；下周期=19.3 候选维持（观察到真实漏判英文形态再补不造无依据需求）+19.2 长窗继续+验收轮转轮换类别或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 05:50] 周期#107 | 19.2 长窗拒做观察+验收轮转轮换类别（运维+办公）+15.5 第六轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 01:35 后 user 消息 0、wechat_bridge.db seen/peers 两表均 0 行、tool_failures 近行全为 llm:dna 合成+1 terminal transient 无新真实用户失败反馈、ai_groups discussion_messages 8 行为 agent 自我介绍/任务链自述非用户反馈）——零反馈维持②验收轮转轮换类别+19.2 长窗采样（tag=REJ107_1791755101，WS 真实用户路径，每类独立 session，verify_mode 全 server_side）**3/3✅**：运维 16.81s（system_checkup 账本执行行=1 真源=trajectory_events+8090/磁盘 83.0% 信息）+调研 33.27s（web_search 执行行=1+来源行+主答区自报搜索不可用=否）+办公 52.37s（report.xlsx 4962 字节有效 xlsx）三三 end_turn——**本轮无自然拒做**，部署后累计 11 轮采样：开发 5/5 净、调研 4/5（1 次拒做→补做自愈），合计拒做复发率 1/11、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×3 被过滤）③15.5 flaky 第六轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.26-1.41s）+32 例批跑过（stall_guard 17+stream_stall 15，18.87s），3.3s 边际六轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、ACCEPT_MEM_MARK_9 标记 0、cron 探针任务行 0）⑤并发 WIP 未动（agent/context_budget.py+token_attribution.py 他线 WIP +12 行如实留，AST 健康，单写入者纪律全程不碰不提交——本次 commit 路径限定 docs/soulmate-roadmap.md） | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定不含他会话 WIP（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/report.xlsx 文件字节）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 3/3（run_tag/latency/verify_mode 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 3 类各 1 轮同机低负载，运维 16.81s/调研 33.27s/办公 52.37s 属各类历史常态区间②本轮无自然拒做≠拒做已消失，1/11 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 六轮未复现≠证明不 flaky，属当前机器负载下未复现⑤ai_groups 触点以 discussion_messages 内容判定（8 行均为 agent 生成文本非用户反馈）；下周期=19.3 候选维持+19.2 长窗继续+验收轮转轮换类别（余类：调试/发布/定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 05:58] 周期#108 | 19.2 长窗拒做观察+验收轮转轮换类别（调试+发布）+15.5 第七轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 05:00 后 user 消息 0、user_feedback 表 0 行、wechat_bridge.db 0 字节、tool_failures 近 5 行全为 llm:dna 合成+1 历史 terminal transient 无新真实用户失败反馈）——零反馈维持②验收轮转轮换类别+19.2 长窗采样（tag=REJ108_1791755700，WS 真实用户路径，每类独立 session，verify_mode 全 server_side）**2/2✅**：调试 42.75s（buggy.py 修复=是(return a + b)+FIXED 标记，服务端文件真源）+发布 19.25s（opensoul.db 投递行 accept-pub-target=1）双双 end_turn——**本轮无自然拒做**，部署后累计 13 轮采样：开发 5/5 净、调试 1/1、发布 1/1、运维 1/1、办公 1/1、调研 4/5（1 次拒做→补做自愈），合计拒做复发率 1/13、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×1 被过滤）③15.5 flaky 第七轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过+32 例批跑过（stall_guard 17+stream_stall 15，18.59s），3.3s 边际七轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、ACCEPT_MEM_MARK_9 标记 0、accept_probe 任务行 0）⑤并发 WIP 未动（agent/context_budget.py+token_attribution.py 他线 WIP +12 行如实留，单写入者纪律全程不碰不提交——本次 commit 路径限定 docs/soulmate-roadmap.md） | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定不含他会话 WIP（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（buggy.py 文件内容/opensoul.db 投递行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag/latency/verify_mode 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，调试 42.75s/发布 19.25s 属各类历史常态区间②本轮无自然拒做≠拒做已消失，1/13 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 七轮未复现≠证明不 flaky，属当前机器负载下未复现⑤tool_failures 触点本轮按实存 schema（id/ts/session_id/tool_name/kind/action/attempt/outcome/error_first_line/note/args_json）查询，无新真实用户失败反馈行；下周期=19.3 候选维持+19.2 长窗继续+验收轮转轮换类别（余类：定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 06:15] 周期#109 | 19.2 长窗拒做观察+验收轮转轮换类别（定时+多渠道）+15.5 第八轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 05:00 后 user 消息 0、user_feedback 表 0 行、wechat_bridge.db 0 字节、tool_failures 近5行全为 llm:dna 合成+1 历史 terminal transient 无新真实用户失败反馈）——零反馈维持②验收轮转轮换类别+19.2 长窗采样（tag=REJ109_1791757200，WS 真实用户路径，每类独立 session，verify_mode 全 server_side）**2/2✅**：定时 85.28s（cron_tasks.db tasks 表 accept_probe 行=1 服务端真源，end_turn）+多渠道 52.3s（出站审计行 wechat→accept_peer 含探针文案=1，end_turn）双双 end_turn——**本轮无自然拒做**，部署后累计 15 轮采样：开发 5/5 净、调试 1/1、发布 1/1、运维 1/1、办公 1/1、定时 1/1、多渠道 1/1、调研 4/5（1 次拒做→补做自愈），合计拒做复发率 1/15、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（heartbeat×1+self_check×1 被过滤）③15.5 flaky 第八轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.34-1.49s）+32 例批跑过（stall_guard 17+stream_stall 15，19.07s），3.3s 边际八轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、ACCEPT_MEM_MARK_9 标记 0、accept_probe 任务行 0）⑤他会话 WIP 去向变更如实记：context_budget._audio_tokens/token_attribution import math 12 行 WIP（#105-#108 四周期如实留置未动）于本周期 06:07 被 evo pre-branch-1791756449 stash 收走（现存 stash@{0} 可 `git stash show -p stash@{0}` 找回，按纪律未 pop 未动）；同刻 evo 把共享树切到 evo/exp-1791756510（周期内实证 branch main→evo/exp 飘移，#97/#98 同类），本周期 commit 按纪律走 `git worktree add /tmp/wt109 main` 隔离树落 main 零事故 | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘（worktree 内路径限定 docs/soulmate-roadmap.md，git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（cron_tasks.db 任务行/出站审计行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ109_1791757200/latency 85.28+52.3/verify_mode server_side 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，定时 85.28s/多渠道 52.3s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/15 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 八轮未复现≠证明不 flaky，属当前机器负载下未复现⑤多渠道 verify 为出站审计行（微信通道 loopback 传输，真实 iLink 归 4.1b 待用户拍板）；下周期=19.3 候选维持+19.2 长窗继续+验收轮转轮换类别（余类：记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）

[2026-10-12 06:18] 周期#110 | 19.2 长窗拒做观察+验收轮转收官（记忆+协作，部署后 10 类全集全覆盖）+15.5 第九轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 05:50 后 user 消息 0、user_feedback 表 0 行、wechat_bridge.db seen/peers 两表均 0 行、tool_failures 近5行全为 llm:dna 合成+1 历史 terminal transient 无新真实用户失败反馈）——零反馈维持②验收轮转收官+19.2 长窗采样（tag=REJ110_1791756945，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，零 WS 超时）**2/2✅**：记忆 68.14s（memory_append 追加 ACCEPT_MEM_MARK_9→MEMORY.md 服务端真源含标记=是）+协作 38.67s（subagent 双 worker 并行，回复含双 worker 产出+subagent_runs 近8行=8 服务端真源）双双 end_turn——**本轮无自然拒做**，部署后累计 17 轮采样：开发 5/5、调试 1/1、发布 1/1、运维 1/1、办公 1/1、定时 1/1、多渠道 1/1、记忆 1/1、协作 1/1、调研 4/5（1 次拒做→补做自愈），合计拒做复发率 1/17、补做自愈 1/1（19.2b 落账实效仍待自然复发核账）；**部署后 10 类全集全覆盖**（第二轮轮转 #104-#110 逐类轮换收官）；采集侧系统帧过滤实证生效（self_check×2 被过滤）③15.5 flaky 第九轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.34-1.49s）+32 例批跑过（stall_guard 17+stream_stall 15，18.11s），3.3s 边际九轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、ACCEPT_MEM_MARK_9 标记 0、accept_probe 任务行 0）⑤共享树会话内两次实证 evo 分支飘移（06:12 开工在 evo/exp-1791756510、06:18 已回 main 7f13ea65），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动；本周期 commit 在共享树 main 上路径限定 docs/soulmate-roadmap.md 即改即提交（evo 飘移窗口秒级） | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定不含他会话 WIP/untracked（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（MEMORY.md 文件标记/subagent_runs 行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ110_1791756945/latency 68.14+38.67/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，记忆 68.14s/协作 38.67s 属两类历史常态区间（记忆类曾见 300s 级上游波动样本，本轮未现）②本轮无自然拒做≠拒做已消失，1/17 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 九轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转开启第三轮（10 类再轮换）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 06:25] 周期#111 | 19.2 长窗拒做观察+验收轮转开启第三轮（开发+调试）+15.5 第十轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 05:50 后 user 消息 0、user_feedback 表 0 行、wechat_bridge.db seen/peers 两表均 0 行、tool_failures 近5行全为 llm:dna 合成 empty_output/read_timeout+1 历史 terminal transient 无新真实用户失败反馈、ai_groups discussion_messages 70 行近2行为 05:50 任务链 agent 产出非用户反馈）——零反馈维持②验收轮转第三轮开启+19.2 长窗采样（tag=REJ111_1791757393，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：开发 75.6s（dev_hello.py marker 命中+运行账本 terminal/execute_code 执行行=1 含程序输出非源码回显，真源=trajectory_events）+调试 54.8s（buggy.py 修复=是(return a + b)+FIXED 标记）双双 end_turn——**本轮无自然拒做**，部署后累计 19 轮采样：开发 6/6、调试 2/2、发布 1/1、运维 1/1、办公 1/1、定时 1/1、多渠道 1/1、记忆 1/1、协作 1/1、调研 4/5（1 次拒做→补做自愈），合计拒做复发率 1/19、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（heartbeat×1+self_check×1 被过滤）③15.5 flaky 第十轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（--durations 实测 call 1.21s）+32 例批跑过（stall_guard 17+stream_stall 15，18.62s），3.3s 边际十轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、ACCEPT_MEM_MARK_9 标记 0、cron_tasks.db tasks 表 0 行）⑤共享树开工核实 main 1caf6875 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/buggy.py 文件内容）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ111_1791757393/latency_s 75.6+54.8/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，开发 75.6s/调试 54.8s 属两类历史常态区间（开发类 75.6s 含运行账本执行属偏高端但未超常态上限）②本轮无自然拒做≠拒做已消失，1/19 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第三轮轮换类别（余类：运维/办公/调研/发布/定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 06:45] 周期#112 | 19.2 长窗拒做观察+验收轮转第三轮轮换类别（运维+办公）+15.5 第十一轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 06:25 后 user 消息 0、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍为 1791584225 历史 send_skipped 无新行、tool_failures.db 空库零表零行无新真实用户失败反馈、ai_groups discussion_messages 70 行与上周期持平无新增）——零反馈维持②验收轮转第三轮轮换+19.2 长窗采样（tag=REJ112_1791758700，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：运维 23.05s（system_checkup 账本执行行=1 真源=trajectory_events+8090/磁盘 83.1% 信息）+办公 70.68s（report.xlsx 4963 字节有效 xlsx）双双 end_turn——**本轮无自然拒做**，部署后累计 21 轮采样：开发 6/6、调试 2/2、发布 1/1、运维 2/2、办公 2/2、定时 1/1、多渠道 1/1、记忆 1/1、协作 1/1、调研 4/5（1 次拒做→补做自愈），合计拒做复发率 1/21、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×2+heartbeat×1 被过滤）③15.5 flaky 第十一轮：test_head_stall_aborts_fast_and_retry_recovers 单跑过（--durations 实测 call 1.42s）+32 例批跑过（stall_guard 17+stream_stall 15，19.32s），3.3s 边际十一轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、ACCEPT_MEM_MARK_9 标记 0、cron_tasks.db 无残留探针任务）⑤共享树开工核实 main fcd7ca5b 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/report.xlsx 文件字节）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ112_1791758700/latency_s 23.05+70.68/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑+32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，运维 23.05s/办公 70.68s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/21 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十一轮未复现≠证明不 flaky，属当前机器负载下未复现⑤tool_failures.db 本周期实存形态为零表空库（历史周期口径的表 schema 不在），触点按实存形态覆盖；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第三轮轮换类别（余类：调研/发布/定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 06:55] 周期#113 | 19.2 长窗拒做观察+验收轮转第三轮轮换类别（调研+发布）+15.5 第十二轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 06:25 后 user 消息 2 条均为上周期 REJ112 探针文案非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍为 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转轮换+19.2 长窗采样（tag=REJ113_1791758579，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：调研 35.87s（web_search 执行行=1 真源=trajectory_events+来源行+主答区自报搜索不可用=否）+发布 9.99s（opensoul.db 投递行 accept-pub-target=1，消息行 #1739）双双 end_turn——**本轮无自然拒做**，部署后累计 23 轮采样：开发 6/6、调试 2/2、发布 2/2、运维 2/2、办公 2/2、定时 1/1、多渠道 1/1、记忆 1/1、协作 1/1、调研 5/6（1 次拒做→补做自愈），合计拒做复发率 1/23、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×2 被过滤）③15.5 flaky 第十二轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.21-1.44s）+32 例批跑过（stall_guard 17+stream_stall 15，19.09s），3.3s 边际十二轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db 无残留探针任务）⑤共享树开工核实 main 10dec2be 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/opensoul.db 投递行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ113_1791758579/latency_s 35.87+9.99/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，调研 35.87s/发布 9.99s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/23 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十二轮未复现≠证明不 flaky，属当前机器负载下未复现⑤触点库路径本轮实测修正留痕：discussion_messages 实在 opensoul/data/ai_groups.db（非 opensoul.db）、wechat_bridge_log.jsonl 实在 acp-proxy/data/（触点等效覆盖结论不变）；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第三轮轮换类别（余类：定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 06:58] 周期#114 | 19.2 长窗拒做观察+验收轮转第三轮轮换类别（定时+多渠道）+15.5 第十三轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 06:25 后 user 消息 0、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍为 1791584264 历史 send_skipped 无新行、tool_failures.db 近5行全为 llm:dna 合成 empty_output/read_timeout+1 历史 terminal transient 无新真实用户失败反馈、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转轮换+19.2 长窗采样（tag=REJ114_1791759214，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：定时 37.96s（cron_tasks.db tasks 表 accept_probe 行=1 服务端真源，end_turn）+多渠道 65.48s（出站审计行 wechat→accept_peer 含探针文案=1，end_turn）双双 end_turn——**本轮无自然拒做**，部署后累计 25 轮采样：开发 6/6、调试 2/2、发布 2/2、运维 2/2、办公 2/2、定时 2/2、多渠道 2/2、记忆 1/1、协作 1/1、调研 5/6（1 次拒做→补做自愈），合计拒做复发率 1/25、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×1 被过滤）③15.5 flaky 第十三轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.36-1.60s）+32 例批跑过（stall_guard 17+stream_stall 15，18.14s），3.3s 边际十三轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db tasks 表 0 行=harness cleanup 后 accept_probe 已清）⑤共享树开工核实 main 27d759ae 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（cron_tasks.db 任务行/出站审计行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 2/2（run_tag=REJ114_1791759214/latency_s 37.96+65.48/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，定时 37.96s/多渠道 65.48s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/25 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十三轮未复现≠证明不 flaky，属当前机器负载下未复现⑤多渠道 verify 为出站审计行（微信通道 loopback 传输，真实 iLink 归 4.1b 待用户拍板）；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第三轮收尾（余类：记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 07:10] 周期#115 | 19.2 长窗拒做观察+验收轮转第三轮收尾（记忆+协作，第三轮全集收官）+15.5 第十四轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 06:53 后 user 消息 2 条均为上周期 REJ114 探针文案非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍为 1791584264 历史 send_skipped 无新行、ai_groups discussion_messages 70 行与上周期持平、tool_failures.db 零表空库）——零反馈维持②验收轮转第三轮收尾+19.2 长窗采样（tag=REJ115_1791759785，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：记忆 43.55s（memory_append 追加 ACCEPT_MEM_MARK_9→MEMORY.md 服务端真源含标记=是）+协作 27.28s（subagent 双 worker 并行 max_concurrency=2 全返回，回复含双 worker 产出+subagent_runs 近8行=8 服务端真源）双双 end_turn——**本轮无自然拒做**，部署后累计 27 轮采样：开发 6/6、调试 2/2、发布 2/2、运维 2/2、办公 2/2、定时 2/2、多渠道 2/2、记忆 2/2、协作 2/2、调研 5/6（1 次拒做→补做自愈），合计拒做复发率 1/27、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；**第三轮轮转全集收官**（#111 开发+调试→#112 运维+办公→#113 调研+发布→#114 定时+多渠道→#115 记忆+协作）；采集侧系统帧过滤实证生效（self_check×2 被过滤）③15.5 flaky 第十四轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.21-1.30s）+32 例批跑过（stall_guard 17+stream_stall 15，18.56s），3.3s 边际十四轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db 零表空库无残留探针任务）⑤共享树开工核实 main 05116a37 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（MEMORY.md 文件标记/subagent_runs 行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 2/2（run_tag=REJ115_1791759785/latency_s 43.55+27.28/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，记忆 43.55s/协作 27.28s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/27 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十四轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转开启第四轮（10 类再轮换）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 07:22] 周期#116 | 19.2 长窗拒做观察+验收轮转第四轮开启（开发+调试）+15.5 第十五轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 07:10 后 user 消息 0、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍为 1791584264 历史 send_skipped 无新行、tool_failures 近5行全为 llm:dna 合成 empty_output/read_timeout+1 历史 terminal transient 无新真实用户失败反馈、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转第四轮开启+19.2 长窗采样（tag=REJ116_1791760390，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，结果 JSON 无 WS 超时记录）**2/2✅**：开发 23.67s（dev_hello.py marker 命中+运行账本 terminal/execute_code 引用 dev_hello 的执行行=1 含程序输出非源码回显，真源=trajectory_events）+调试 42.94s（buggy.py 修复=是(return a + b)+FIXED 标记）双双 end_turn——**本轮无自然拒做**，部署后累计 29 轮采样：开发 7/7、调试 3/3、发布 2/2、运维 2/2、办公 2/2、定时 2/2、多渠道 2/2、记忆 2/2、协作 2/2、调研 5/6（1 次拒做→补做自愈），合计拒做复发率 1/29、补做自愈 1/1（19.2b 落账实效仍待自然复发核账）③15.5 flaky 第十五轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.21-1.35s）+32 例批跑过（stall_guard 17+stream_stall 15，18.57s），3.3s 边际十五轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db tasks 表 0 行无残留探针任务）⑤共享树开工核实 main 4f4523cd 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/buggy.py 文件内容）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ116_1791760390/latency_s 23.67+42.94/stop_reason end_turn/verify_mode server_side 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，开发 23.67s/调试 42.94s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/29 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十五轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第四轮轮换类别（余类：运维/办公/调研/发布/定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 07:30] 周期#117 | 19.2 长窗拒做观察+验收轮转第四轮轮换类别（运维+办公）+15.5 第十六轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 07:22 后 user 消息 0、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍为 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转轮换+19.2 长窗采样（tag=REJ117_1791761100，WS 真实用户路径，每类独立 session，verify_mode 全 server_side）**2/2✅**：运维 18.84s（system_checkup 账本执行行=1 真源=trajectory_events+8090/磁盘 83.1% 信息）+办公 53.21s（report.xlsx 4963 字节有效 xlsx）双双 end_turn——**本轮无自然拒做**，部署后累计 31 轮采样：开发 7/7、调试 3/3、发布 2/2、运维 3/3、办公 3/3、定时 2/2、多渠道 2/2、记忆 2/2、协作 2/2、调研 5/6（1 次拒做→补做自愈），合计拒做复发率 1/31、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×2 被过滤）③15.5 flaky 第十六轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.21-1.30s）+32 例批跑过（stall_guard 17+stream_stall 15，18.58s），3.3s 边际十六轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db 零表空库无残留探针任务）⑤共享树开工核实 main cc295791 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/report.xlsx 文件字节）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ117_1791761100/latency_s 18.84+53.21/stop_reason end_turn/verify_mode server_side 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，运维 18.84s/办公 53.21s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/31 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十六轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第四轮轮换类别（余类：调研/发布/定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）

[2026-10-12 07:55] 周期#118 | 19.2 长窗拒做观察+验收轮转第四轮轮换类别（调研+发布）+15.5 第十七轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 07:30 后 0 条、user_feedback 表缺库 0 行、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转轮换+19.2 长窗采样（tag=REJ118_1791762400，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：调研 41.62s（web_search 执行行=1 真源=trajectory_events+来源行+回复含 3.13/2024+主答区自报搜索不可用=否）+发布 14.17s（opensoul.db 投递行 accept-pub-target=1）双双 end_turn——**本轮无自然拒做**，部署后累计 33 轮采样：开发 7/7、调试 3/3、发布 3/3、运维 3/3、办公 3/3、定时 2/2、多渠道 2/2、记忆 2/2、协作 2/2、调研 6/7（1 次拒做→补做自愈），合计拒做复发率 1/33、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×2 被过滤）③15.5 flaky 第十七轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.22-1.42s）+32 例批跑过（stall_guard 17+stream_stall 15，19.11s），3.3s 边际十七轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db 无残留探针任务行）⑤共享树开工核实 main 2e706517 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/opensoul.db 投递行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ118_1791762400/latency_s 41.62+14.17/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，调研 41.62s/发布 14.17s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/33 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十七轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第四轮轮换类别（余类：定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 08:03] 周期#119 | 19.2 长窗拒做观察+验收轮转第四轮轮换类别（定时+多渠道）+15.5 第十八轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 最近 user 消息 3 条均为 REJ118/REJ119 验收探针文案非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转轮换+19.2 长窗采样（tag=REJ119_1791762254，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：定时 67.54s（cron_tasks.db tasks 表 accept_probe 行数=1 服务端真源，end_turn）+多渠道 55.12s（出站审计行 wechat→accept_peer 含探针文案=1（审计库总相关行=1），end_turn）双双 end_turn——**本轮无自然拒做**，部署后累计 35 轮采样：开发 7/7、调试 3/3、发布 3/3、运维 3/3、办公 3/3、定时 3/3、多渠道 3/3、记忆 2/2、协作 2/2、调研 6/7（1 次拒做→补做自愈），合计拒做复发率 1/35、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×1 被过滤）③15.5 flaky 第十八轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.20-1.36s）+32 例批跑过（stall_guard 17+stream_stall 15，18.57s），3.3s 边际十八轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db tasks 表 accept_probe 行 0=harness cleanup 后已清）⑤共享树开工核实 main a536ef7c 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（cron_tasks.db 任务行/出站审计行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ119_1791762254/latency_s 67.54+55.12/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，定时 67.54s/多渠道 55.12s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/35 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十八轮未复现≠证明不 flaky，属当前机器负载下未复现⑤多渠道 verify 为出站审计行（微信通道 loopback 传输，真实 iLink 归 4.1b 待用户拍板）；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第四轮轮换类别（余类：记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 07:57] 周期#120 | 19.2 长窗拒做观察+验收轮转第四轮收尾（记忆+协作，第四轮全集收官）+15.5 第十九轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 近窗 user 消息 7 条均含"复测标记"=REJ 系列验收探针文案非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转第四轮收尾+19.2 长窗采样（tag=REJ120_1791763400，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：记忆 31.89s（memory_append 追加 ACCEPT_MEM_MARK_9→MEMORY.md 服务端真源含标记=是）+协作 25.53s（subagent 双 worker 并行，回复含双 worker 产出+subagent_runs 近8行=8 服务端真源）双双 end_turn——**本轮无自然拒做**，部署后累计 37 轮采样：开发 7/7、调试 3/3、发布 3/3、运维 3/3、办公 3/3、定时 3/3、多渠道 3/3、记忆 3/3、协作 3/3、调研 6/7（1 次拒做→补做自愈），合计拒做复发率 1/37、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；**第四轮轮转全集收官**（#116 开发+调试→#117 运维+办公→#118 调研+发布→#119 定时+多渠道→#120 记忆+协作）；采集侧系统帧过滤实证生效（self_check×2 被过滤）；诚实留痕：协作类子任务1 产出 BETA_DONE 与目标 ALPHA_DONE 不符（SoulMate 回复如实报"是否一致=否"，验收判据（双 worker 产出+subagent_runs 行数）仍过——子代理执行质量非本判据面，如实记录不粉饰）③15.5 flaky 第十九轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.25-1.43s）+32 例批跑过（stall_guard 17+stream_stall 15，18.87s），3.3s 边际十九轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、.hermes/soulmate/memory/MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db 零表空库无残留探针任务）⑤共享树开工核实 main 0164df71 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 evo stash@{0}=evo-pre-branch-1791756449 收存未动，本周期 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（MEMORY.md 文件标记/subagent_runs 行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 2/2（run_tag=REJ120_1791763400/latency_s 31.89+25.53/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，记忆 31.89s/协作 25.53s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/37 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 十九轮未复现≠证明不 flaky，属当前机器负载下未复现⑤协作类子任务产出偏差（BETA_DONE≠ALPHA_DONE）为子代理执行层样本，验收判据面不覆盖该维度，如实记录；下周期=19.3 候选维持+19.2 长窗继续+验收轮转开启第五轮（10 类再轮换）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）
[2026-10-12 08:12] 周期#121 | 19.2 长窗拒做观察+验收轮转第五轮开启（开发+调试）+15.5 第二十轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 最近 user 消息均为 REJ120/REJ121 验收探针文案（memory_append/delegate_tasks/开发调试任务）非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转第五轮开启+19.2 长窗采样（tag=REJ121_1791763438，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：开发 91.24s（dev_hello.py marker 命中+运行账本 terminal/execute_code 引用 dev_hello 的执行行=1 含程序输出非源码回显，真源=trajectory_events）+调试 61.45s（buggy.py 修复=是(return a + b)+FIXED 标记）双双 end_turn——**本轮无自然拒做**，部署后累计 39 轮采样：开发 8/8、调试 4/4、发布 3/3、运维 3/3、办公 3/3、定时 3/3、多渠道 3/3、记忆 3/3、协作 3/3、调研 6/7（1 次拒做→补做自愈），合计拒做复发率 1/39、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（heartbeat×1+self_check×1 被过滤）③15.5 flaky 第二十轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.21-1.30s）+32 例批跑过（stall_guard 17+stream_stall 15，18.63s），3.3s 边际二十轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、.hermes/soulmate/memory/MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db tasks 表 0 行无残留探针任务）⑤共享树开工核实 main aa57e0de 干净（仅 3 件已知 untracked 非本清单），他会话 WIP（context_budget/token_attribution 12 行）仍为 stash@{1}=evo-pre-branch-1791756449 收存未动（序号因新 stash 下移）；⚠️git 事故（同类第5次）：周期中途 evo pre-branch-1791763685 把本周期未 commit 的路线图追加收进 stash@{0}（commit 命令落空 index 被切树清空），按纪律未 pop 未动他会话内容，`git stash show -p stash@{0}` 提取 12 行补丁（核实仅 docs/soulmate-roadmap.md 一个文件）后 `git worktree add /tmp/wt121 main` 隔离树重放+补充本条事故注记+提交，零丢失；教训再验证=改完即小步 commit（本次 append→commit 间隔数秒仍被 evo 抢走） | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/buggy.py 文件内容）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 2/2（run_tag=REJ121_1791763438/latency_s 91.24+61.45/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮，开发 91.24s 为该类历史偏高端样本（区间 23.67-91.24s），probe 启动初期与 flaky 测试单跑/批跑同机并发可能抬高延迟（本轮 flaky 与采样时间窗部分重叠），调试 61.45s 属历史常态区间②本轮无自然拒做≠拒做已消失，1/39 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 二十轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第五轮轮换类别（余类：运维/办公/调研/发布/定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）

[2026-10-12 08:22] 周期#122 | 19.2 长窗拒做观察+验收轮转第五轮轮换类别（运维+办公）+15.5 第二十一轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 最近 user 消息均为 REJ121/REJ122 验收探针文案（开发/调试/运维任务）与子代理任务派发文本非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转第五轮轮换+19.2 长窗采样（tag=REJ122_1791763900，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：运维 20.96s（system_checkup 账本执行行=1 真源=trajectory_events+8090 在监听/磁盘 82.8% 信息）+办公 61.51s（report.xlsx 4962 字节有效 xlsx）双双 end_turn——**本轮无自然拒做**，部署后累计 41 轮采样：开发 8/8、调试 4/4、发布 3/3、运维 4/4、办公 4/4、定时 3/3、多渠道 3/3、记忆 3/3、协作 3/3、调研 6/7（1 次拒做→补做自愈），合计拒做复发率 1/41、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×2 被过滤）③15.5 flaky 第二十一轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.28-1.48s）+32 例批跑过（stall_guard 17+stream_stall 15，18.88s），3.3s 边际二十一轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、.hermes/soulmate/memory/MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db 无 tasks 表无残留探针任务）⑤⚠️git 纪律：共享树开工即被 evo 占用（evo/exp-1791763685，strand_b 心跳 08:19 仍活跃、strand_a 08:08 branch 阶段实证），按纪律禁 checkout/切分支，全程 `git worktree add /tmp/wt122 main` 隔离树落账+提交零接触共享树；他会话 WIP（context_budget/token_attribution 12 行）仍为 stash@{1}=evo-pre-branch-1791756449 收存未动（序号因新 stash 下移）| ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘（worktree 内路径限定 docs/soulmate-roadmap.md，git diff --cached 核实）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/report.xlsx 文件字节）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 2/2（run_tag=REJ122_1791763900/latency_s 20.96+61.51/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载，运维 20.96s/办公 61.51s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/41 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 二十一轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第五轮轮换类别（余类：调研/发布/定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）

[2026-10-12 08:33] 周期#123 | 19.2 长窗拒做观察+验收轮转第五轮轮换类别（调研+发布）+15.5 第二十二轮（观察期，无产品代码改动）：①16.3 反馈挖掘先查四触点（agent_messages 08:05 后 user 消息 0、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 近6行全为 llm:dna 合成 empty_output/read_timeout/rate_limit+1 历史 terminal transient 无新真实用户失败反馈、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②验收轮转轮换+19.2 长窗采样（tag=REJ123_1791764807，WS 真实用户路径，每类独立 session，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：调研 37.69s（web_search 执行行=1 真源=trajectory_events+来源行+回复含 3.13/2024+主答区自报搜索不可用=否）+发布 13.21s（opensoul.db 投递行 accept-pub-target=1，消息行 #1789）双双 end_turn——**本轮无自然拒做**，部署后累计 43 轮采样：开发 8/8、调试 4/4、发布 4/4、运维 4/4、办公 4/4、定时 3/3、多渠道 3/3、记忆 3/3、协作 3/3、调研 7/8（1 次拒做→补做自愈），合计拒做复发率 1/43、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×2 被过滤）③15.5 flaky 第二十二轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（call 1.42/1.30/1.21s）+32 例批跑过（stall_guard 17+stream_stall 15，18.12s），3.3s 边际二十二轮未复现→按约不预防性放宽上限（3.0s 不动）④测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db tasks 表 0 行无残留探针任务）⑤共享树纪律：开工 08:25 共享树被 evo 占用（evo/exp-1791763685@aa57e0de，08:08 pre-branch 占用且工作树带 evolution_v2.py +92 行 evo WIP），全程不动共享树只读采样；采样期间 evo 归还主树（08:30 双心跳活跃、主树回 main 72e0619b 干净，evolution_v2.py WIP 已随 evo 自身流程收走非本流水线产物未动），本条目按#121教训 append→commit 一气呵成秒级落账；他会话 WIP（context_budget/token_attribution 12 行）仍为 stash@{1}=evo-pre-branch-1791756449 收存未动，stash@{0}=evo-pre-branch-1791763685（#121 已提取重放的路线图补丁原件）按纪律未 pop 未动 | ✅ | (本次提交) | 观察期无产品代码改动（Gate2=N/A）；Gate1 git diff 路线图本条目真实落盘+commit 路径限定 docs/soulmate-roadmap.md（git diff --cached 核实不含他会话 WIP/untracked）；Gate2=N/A（测量+观察周期）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（trajectory_events 账本行/opensoul.db 投递行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 2/2（run_tag=REJ123_1791764807/latency_s 37.69+13.21/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑（18.12s）真实 pytest 输出+四触点真实库查询（零反馈有据）；⚠️诚实边界①采样 2 类各 1 轮同机低负载（flaky 单跑与调研采样时间窗部分重叠，调研 37.69s/发布 13.21s 仍属两类历史常态区间）②本轮无自然拒做≠拒做已消失，1/43 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 二十二轮未复现≠证明不 flaky，属当前机器负载下未复现；下周期=19.3 候选维持+19.2 长窗继续+验收轮转第五轮轮换类别（余类：定时/多渠道/记忆/协作）或用户拍板项落地（16.1 六项 A(C)/A/C/A/A+C/A 待逐项拍板；18.2 真机录音全链待用户配合；17.3 兜底层待拍板）

[2026-10-12 08:57] 周期#124 | 对标缺口销账开账·P0-09 Plan模式禁写语义终验（统筹注入最高优先，本轮让位 19.2 采样/15.5 例行）+16.3 反馈挖掘：①16.3 先查四触点（agent_messages 近窗 user 消息排除探针文案后 0 条、wechat_bridge_log.jsonl 431 字节仍历史 send_skipped 无新行）——零反馈维持②P0-09 真跑终验 **PASS 12/12**：Plan 语义真源=opensoul permission_engine PermissionMode.EXPLORE（Step1 allow 规则遇 EXPLORE 不放行"继续往下走"+Step3 非只读一律 DENY"allow rules cannot grant it"）；终验探针 acp-proxy/plan_mode_probe_ws.py（复用 acceptance_benchmark WS 真实用户路径）三层实证——引擎层 write_file/execute_code/terminal 写入全 DENY（explore mode is read-only）+显式 allow 规则（tool=write_file behavior=allow）仍 DENY 不可覆盖+read_file/web_search 只读 ALLOW；真实用户路径层（WS→ws_acp→soulmate stdio→真实 MiMo 回合，explore 态）write_file 建文件→**文件未创建**+回复报"write_file 调用被 OpenSoul 权限引擎拦截" 29.6s end_turn；对照组（恢复 bypass 后同指令）文件真实创建含 print(1) 22.7s=拦截是模式所致非工具坏；explore 全局态 finally 恒恢复基线（探针首跑 _rpc 签名 bug 崩在 e2e 前，finally 恢复实证生效零残留）③对账表 blueprint-51-audit.md P0-09 行 ⏳→✅ 附证据路径+缺口处理注记 ⏳×6→⏳×5 | ✅ | (本次提交) | P0-09 销账；Gate1 git diff 四件真实落盘（对账表 2 处+路线图+探针脚本 plan_mode_probe_ws.py+证据 evidence/p0-09-plan-mode-evidence.json）；Gate2 探针即真实调用路径（permission_gate 同款 POST /api/immune/permission/check 端点+soulmate _execute_tool 前置门禁消费同一引擎，e2e 走中真实拦截）；Gate3 真实产出=/tmp 与入库双份 evidence JSON 12 checks 全 ok 回读+服务端文件真源对照（探针文件 absent/对照文件 created）+真实 WS 两轮 LLM 回合+模式恒恢复 GET 回读；⚠️诚实边界①Plan 模式入口为 API 级（PUT /api/immune/permission/mode），前端/会话级 Plan 模式开关未实装（终验面=禁写语义本身，入口 UI 另案）②explore 为全局态，探针窗口约 70s（进入→语义层→e2e→finally 恢复），期间并发会话写操作会被拦——测试窗口可接受但非零风险③首轮探针 _rpc 现行签名 3 元组（旧探针 wd_probe_ws.py 2 元组已不兼容，留档不改非本清单）④19.2 长窗拒做采样/15.5 例行本轮让位（销账优先级最高），下拍补回；下周期=对标缺口销账续（P0-10 高危 force_ask 终验→P1-01 工具级轨道隔离→P1-15 五终止条件组合入 trace→P2-09 四联 KPI→P2-10 fork 沙箱对照晋升+P1-05 替代实现说明文档）+15.5 例行补+19.2 长窗
[2026-10-12 09:25] 周期#125 | 对标缺口销账续·P0-10 高危强制 ask 终验（统筹注入最高优先，本轮让位 19.2 长窗/15.5 例行）+终验暴露缺陷当拍即修：①16.3 反馈挖掘先查四触点（agent_messages 近窗 user 消息全为 P0-09/P0-10 探针文案非真实反馈、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、ai_groups discussion_messages 70 行持平）——零反馈维持②P0-10 真跑终验 **PASS 17/17**（探针 acp-proxy/high_risk_probe_ws.py 三层）：引擎层（bypass 最宽松常态档）sudo 提权/rm -rf 删库/curl POST 转账外发/机密读取/系统目录写 5 类高危全强制 ask（requires_human=True）+灾难命令 rm -rf / hard deny 不可覆盖+DONT_ASK→DENY 绝不静默+发消息类 message_send 询问模式强制 ask；网关层真实 PermissionGate×真实引擎：无人值守 ask→拦截 ask-denied（-32012"未审批拦截"语义）/真人拒绝→ask-denied/真人批准→ask-approved+同参 cached-allow+perm_decision_audit outcome 回写（approved/denied 服务端真源）；真实 WS 用户路径（WS→ws_acp→soulmate stdio→真实 MiMo 回合，bypass 态）wire 捕获 session/request_permission（强制 ask 到达真人通道，探针回 reject_once）→sudo touch 文件未创建+非高危对照组 touch 文件真实创建=拦截是高危语义所致③**终验暴露缺陷当拍即修**（首轮 16/17）：毁机硬规则"rm -rf /"子串匹配误伤 `rm -rf /tmp/x`→过度硬否决（比"强制 ask"更严但用户无法人工批准合法删除=语义错位，且 execute_code 路径不受此规则=拦正路留侧门）；根因修复=opensoul permission_engine 增 _ANCHORED_ROOT_RES 锚定正则只命中根目标（rm/chmod/chown 三组，与 acp-proxy 降级层 _DEGRADED_HARD_PATTERNS 同源锚定语义；降级层 chmod 同类子串陷阱一并补锚定）→复跑 **17/17**；opensoul test_permission_engine 52/52（含 3 新增回归：锚定正例 rm -rf /tmp→ask 反例/根形三式仍 hard deny/chmod·chown 同类）+acp-proxy permission_gate/provenance/degraded_spill 89/89④对账表 blueprint-51-audit.md P0-10 行 ⏳→✅ 附证据路径+缺口处理 ⏳×5→⏳×4 | ✅ | opensoul 184a7da0 + (本次提交) | P0-10 销账；Gate1 git diff 双仓真实落盘（opensoul permission_engine +17/test +28；openmate permission_gate 1 行锚定+探针 high_risk_probe_ws.py+证据 evidence/p0-10-force-ask-evidence.json+对账表 2 处+路线图）+key 扫描双仓 0 命中；Gate2 探针消费真实调用路径（POST /api/immune/permission/check=引擎真评、PermissionGate=soulmate _execute_tool 同款门禁模块、e2e 走中真实拦截链）；Gate3 真实产出=/tmp 与入库双份 evidence JSON 17 checks 全 ok 回读+服务端真源（perm_decision_audit outcome 3 行回读+探针文件 absent/对照文件 created 存在性对照）+真实 WS 两轮 LLM 回合+双仓 pytest 真实输出 52+89 全过；⚠️诚实边界：①发消息类 message_send 在 bypass 走模式兜底放行（非强制 ask）——强制化会破坏无人值守微信发消息工作流（wechat cron/AI群组任务），如实实录为语义边界**待用户拍板**不冒进；default/ask 模式下强制 ask 已验证成立②e2e 高危形为 sudo touch 无害命令（真实 sudo 提权语义触发门禁，副作用面仅 /tmp 空文件），审批拒绝由探针模拟 reject_once（与前端 AcpApprovalModal 拒绝同构，真人拒绝语义等价）③A9/bypass 兜底放行为模式设计语义（bypass=无规则匹配放行），按现状实录不判缺陷④19.2 长窗拒做采样/15.5 flaky 例行连续第二轮让位（销账优先级最高），下拍补回⑤权限 mode 全局态探针窗口 finally 恒恢复基线 bypass（两轮跑均恢复核实，无残留）；下周期=对标缺口销账续（P1-01 工具级轨道隔离终验→P1-15 五终止条件组合入 trace→P2-09 四联 KPI→P2-10 fork 沙箱对照晋升+P1-05 替代实现说明文档）+15.5 例行补+19.2 长窗
[2026-10-12 10:10] 周期#126 | 对标缺口销账续·P1-01 危险工具轨道隔离终验（统筹注入最高优先，本轮让位 19.2 长窗/15.5 例行）+终验暴露侧门当拍即修：①16.3 反馈挖掘四触点（agent_messages 1791760000000 后无真实 user 消息、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures/discussion 无新反馈）——零反馈维持②代码考古先修正对账表错误前提：user_preferences.py 是偏好学习器（category=偏好分类）非轨道真源、仓库内无"personal 轨道"实装→真源定为 sandbox_policy.py 工具面轨道隔离（与 kilocode registry"按环境裁剪工具面优于给了再拦"同构），语义按 blueprint P1.1"危险编码工具绝不暴露给 personal 轨道，除非用户显式切换"当拍即修补实装③实现：tool_track（per-session显式>default_track，缺省 coding 零行为变化，store 损坏/非法值 fail-closed 按 personal）+DANGEROUS_CODING_TOOL_NAMES（terminal/execute_code/process/write_file/patch/batch_execute）+filter_tools 两轴独立（网络×轨道任一命中即裁）+deny_reason 轨道轴显式拒绝（指引显式切换 coding）+describe/snapshot 透出轨道与裁剪面+API PUT /api/agent/sandbox-policy/{sid} {"tool_track"}（非法值 ok_track=False 拒落盘不静默纠正）；tests +24 例（59/59）④真跑终验首轮 **16/18 FAIL 暴露侧门**：personal 轮 terminal 被裁后模型改走 process 后台进程通道执行同一条 shell 命令把文件建了（"拦正路留侧门"P0-10 同类缺陷）→根因修复=process 入危险集执行原语全家同裁+process 侧门回归测试钉死，复跑 **PASS 18/18**：策略/API 层（PUT 落盘/describe 裁剪面 6 项/web_search 不入危险集/非法值拒绝/deny_reason 显式拒绝×read_file 放行）+真实 WS 用户路径（同一真实会话 om-00f4ab885f2e 两轮，WS→ws_acp→soulmate stdio→真实 MiMo 回合）personal 轮 terminal touch **文件未创建**+回复如实报"没有 terminal/execute_code 等可执行工具、命令未执行"、**显式切换 coding 后同会话同指令文件真实创建** end_turn（对照+切换语义一体实证）+清理零残留（store tool_tracks 回 {}、会话回默认 coding）⑤回归 148/148（sandbox_policy 59+permission_gate/provenance/degraded_spill 89）+key 扫描 0 命中+acp-proxy 双实例重启 health OK⑥对账表 blueprint-51-audit.md P1-01 行 ⏳→✅ 附证据+原注修正+边界如实声明，缺口处理 ⏳×4→⏳×3；待拍板清单新增轨道间接通道+渠道适配默认值两条 | ✅ | (本次提交) | P1-01 销账；Gate1 git diff 真实落盘（sandbox_policy 轨道轴/app.py 端点扩展/tests +24/探针 tool_track_probe_ws.py/证据 evidence/p1-01-tool-track-evidence.json/对账表 2 处/路线图 2 处）+首拍小步 commit（126e1a87 代码+测试先行落，防 evo 中途截走#99④教训）；Gate2 探针消费真实调用路径（filter_tools/deny_reason=soulmate _run_llm_with_tools 工具面构建+主循环兜底同一模块同 store 真源、API=app.py 真实端点、e2e 走中真实工具面收缩）；Gate3 真实产出=/tmp 与入库双份 evidence JSON 18 checks 全 ok 回读+服务端真源（探针文件 absent/对照文件 created 存在性对照+store tool_tracks 回读 {}）+真实 WS 两轮 LLM 回合（首轮 process 侧门缺陷真实复现留档 evidence，复跑通过≠首轮没发生）+pytest 59/59 与 148/148 真实输出；⚠️诚实边界①首轮 16/18 FAIL 与侧门缺陷如实入证据不粉饰②轨道入口为 API 级（PUT sandbox-policy），前端/会话级轨道开关未实装（同 P0-09 入口 UI 另案）③默认轨道 coding 不对任何渠道自动切 personal（渠道适配=blueprint 另一面，自动切换会破坏无人值守微信/cron 工作流，待用户拍板）④间接通道如实声明待拍板（delegate/group_task/schedule_task 委派排程执行面在执行时刻会话轨道；request_evolution/skill_save 走各自安全门）⑤19.2 长窗拒做采样/15.5 flaky 例行连续第三轮让位（销账优先级最高），下拍补回；下周期=对标缺口销账续（P1-15 五终止条件组合入 trace→P2-09 四联 KPI→P2-10 fork 沙箱对照晋升+P1-05 替代实现说明文档）+15.5 例行补+19.2 长窗
[2026-10-12 11:35] 周期#127 | 对标缺口销账续·P1-15 五终止条件组合入trace 终验（统筹注入最高优先，本轮让位 19.2 长窗/15.5 例行）+落点补实装：①16.3 反馈挖掘四触点（agent_messages 近窗 user 消息均为 P 系列探针文案非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures/discussion 无新反馈）——零反馈维持②代码考古定语义真源（blueprint P1.6 五条件=max_turns/wall_clock/token_budget/user_idle/external_cancel）：现状 max_turns=硬编码 MAX_ROUNDS=15 无 trace 落点、token_budget=ContextBudget 裁剪语义非终止、wall_clock=TurnWatchdog 软预算（用户铁律禁硬杀）、user_idle=opensoul trajectory 闲置清扫、external_cancel=steer abort/ACP cancel→turn_lifecycle close_reason——"分散存在、组合语义不落 trace"属实→当拍即补实装③实现（单写者小步 commit 防 evo 截走）：agent/termination.py 五条件词汇+组合裁决 resolve（显式>取消>正常）+独立配置（SOULMATE_MAX_ROUNDS 默认15/SOULMATE_TURN_TOKEN_BUDGET 默认0=不限零行为变化）；TrajectoryRecorder.end_turn(termination=)→回合根事件 metadata.termination+log_event 条件事件；opensoul store.end_event termination 合入 metadata+sweep_idle 先落 user_idle 标记事件后收口（顺序防自愈翻回）；soulmate_agent 三处终止落点（MAX_ROUNDS 耗尽/token 预算超/steer 中断）+_resolve_turn_termination 懒初始化裁决+wall_clock 软预算 on_soft_budget 钩子（提醒不终止）；relay stop_reason=max_tokens→token_budget 族；tests 21+4 例④真跑终验 **PASS 28/28**（探针 acp-proxy/termination_probe_ws.py 四层）：配置裁决层（5 条件词汇+各条件独立配置+非法值回退+三态裁决）/真实 store 落点层（真实 recorder→:8090 真库 HTTP 回读：termination 落库命中+正常收口零误标+wall_clock 软预算钩子真实触发一次+条件事件落库回读+**提醒不终止**+user_idle opensoul 真实 SQLite 测试子进程重跑 4/4 取真实输出）/引擎真实全回合层（真实 SoulMateAgent+真实 MiMo prompt() 全链路含 end_turn 落点）：SOULMATE_MAX_ROUNDS=2 轮次耗尽告知真实在回复+回读 termination=max_turns（om-9f747a6cb2f0）、SOULMATE_TURN_TOKEN_BUDGET=300 预算告知"估算消耗约635，任务停止"真实在回复+回读 termination=token_budget（om-77c77fb95600）、对照回合零误标/真实 WS 用户路径（WS→ws_acp→soulmate stdio→真实 LLM）：正常回合零误标+steer /abort 中断回合（sleep 25 长任务+忙时 /abort 入队→"🛑收到中断指令"）服务端 agent_messages 含"[任务被用户插话中断]"+回读 **termination=external_cancel**+turn root 收口非悬挂⑤首轮 20/28 如实留痕：全部为探针 harness 自身 bug（new_session 自建 om-* id 致 prompt refusal/回读取首个而非末个 turn_root/collect 未消费串回合），修探针后 28/28 产品代码零改动⑥回归 acp-proxy 207/207（含 3 个 FakeRecorder 签名对齐）+opensoul 17/17（含既有 spans 零回归）+key 扫描 0 命中⑦对账表 blueprint-51-audit.md P1-15/16 行 ⏳→✅ 附证据 evidence/p1-15-termination-evidence.json+缺口处理 ⏳×3→⏳×2 | ✅ | opensoul 2d4b4e8c + (本次提交) | P1-15 销账；Gate1 git diff 双仓真实落盘（acp-proxy termination.py/trajectory_recorder/turn_lifecycle/turn_watchdog/soulmate_agent/relay_trajectory+tests 21 例；opensoul store.end_event+sweep 标记+API 透传+tests 4 例；探针+证据+对账表 2 处+路线图）+分两拍小步 commit（代码+测试先行、文档随后）防 evo 中途截走；Gate2 探针消费真实调用路径（end_turn(termination)=soulmate _prompt_inner 回合收口同一方法体+resolve 裁决=生产单一真源、log_event/软预算钩子=TurnWatchdog 生产接线、sweep 标记=opensoul 生产清扫路径、WS e2e 走中真实 ws_acp→soulmate stdio 链路）；Gate3 真实产出=/tmp 与入库双份 evidence JSON 28 checks 全 ok 回读+服务端真源（trajectory 事件 metadata HTTP 回读 4 个真实 sid+agent_messages 中断文本 sqlite 回读+opensoul pytest 真实输出 4/4+acp-proxy/opensoul pytest 207/17 真实输出）；⚠️诚实边界①wall_clock 语义=软预算提醒+事件落 trace **不终止**——用户铁律"任务禁硬超时杀"覆盖蓝图硬杀语义，是否需要真终止**待用户拍板**不冒进②max_turns/token_budget 是**工具循环硬闸**（停工具循环+显式告知，任务引擎后续收尾文本可继续，步骤级告知可重复出现），终止标记按**回合**收口落 trace 一次③user_idle 运行时清扫阈值 TRAJ_SESSION_IDLE_S=1800s 未在生产实测自然触发（由真实 SQLite 测试覆盖 store 语义）④引擎层为探针进程内真实 agent 全回合（不经 ws_acp 传输层），传输层由 WS 层正常+中断两轮补证⑤探针轮次落库残留 4 个 om-* 探针会话行（trajectory 证据本体，未清）⑥19.2 长窗拒做采样/15.5 flaky 例行连续第四轮让位（销账优先级最高），下拍补回；下周期=P1-05 RepoMap 替代实现说明文档（检索式召回+token 预算选符号，不硬造 tree-sitter）→P2-09 四联 KPI→P2-10 fork 沙箱对照晋升+15.5 例行补+19.2 长窗
[2026-10-12 11:47] 周期#128 | 对标缺口销账续·P1-05 RepoMap 替代实现说明文档（统筹裁决"不硬造 tree-sitter，按替代实现写文档即可"）+16.3 反馈挖掘：①16.3 四触点（agent_messages 近窗 user 消息 0、wechat_bridge.db 0 字节、tool_failures.db 0 字节空库、discussion_messages 表缺）——零反馈维持②代码考古逐件实证替代构成：符号边界分块 FTS 索引（file_index.py `_split_into_chunks` 正则 def/class/function/export 边界切块=无 tree-sitter 的符号粒度近似）+检索式召回 live path（search_files grep 多语言 include 集/session_recall 词项覆盖+编辑距离评分/记忆三因子/技能检索）+token 预算选符号（context_budget 预算裁剪+溢出 spill→stub+read_file_segment 按行段取回=预算内按需取符号段）③**考古诚实发现（写了≠接线了）**：SessionFileIndex 半接线——写入侧已接线（soulmate_agent L137/366/5467 自动索引会话提及文件）但 `search()/get_file_summary()/list_indexed_files()` 全仓 grep 无消费点，FTS 召回闭环列为待拍板小切片，文档/对账表如实声明不粉饰④实现说明文档落盘 `docs/agent-architecture-mimo/synthesis/p1-05-repomap-alternative-implementation.md`（原案目标/裁决理由/替代构成逐件 file:line/对照差距表/已知边界/原案升级路径§6/引用自检§7）⑤Gate3 引用自检真跑 grep 逐条核对并当拍即修 4 处行号偏差（mask_of 132→142、usable_input 51→43+L96 属性、TOOL_SPILL 288→289、符号正则 213→212），修后与文档逐条一致⑥对账表 blueprint-51-audit.md P1-05 行 ❌→✅(替代) 附文档路径+缺口处理注记更新+表头计数 ✅48/⏳2/❌1 | ✅ | (本次提交) | P1-05（替代实现）销账；Gate1 git diff 三件真实落盘（新文档 9.6KB+对账表 3 处+路线图本条目）+key 扫描 0 命中；Gate2=文档类交付（无运行时代码改动，N/A 如实记）——替代构成全部为现有真实路径的实证引用（search_files/session_recall/read_file_segment 等消费点逐一 grep 核实非死代码），文档内 file:line 即接线证据；Gate3 真实产出=§7 验证记录（grep 真实输出逐条核对，4 处行号偏差自检暴露即修=文档自证不虚）+半接线发现的反向验证（grep 空结果=诚实边界成立）；⚠️诚实边界①原案 tree-sitter 图中心性**有意不做**（统筹裁决过度设计），P1-05 记 ✅(替代) 非原案达成，用户拍板可按 §6 升级（可选依赖+只解析命中文件+不替换降级路径）②"token 预算选符号"是操作化等价物（检索定位+预算内分段取回），非图中心性预排序，语义差异文档 §4 对照表如实声明③SessionFileIndex FTS 召回闭环待拍板（半接线）④19.2 长窗/15.5 例行本轮先让位后**同周期补跑完成**（见本条附记）；下周期=P2-09 四联 KPI 整合（成功率/干预率/反射/compact质量 指标面整合）→P2-10 fork 沙箱对照晋升终验+15.5 例行补+19.2 长窗
[2026-10-12 11:58] 周期#128附记 | 15.5 第二十三轮+19.2 长窗采样补跑（销账完成后同周期补齐，修正本条正文④"让位"为"已补跑"）：①15.5 flaky 第二十三轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（--durations 实测 call 1.36-1.37s）+32 例批跑过（stall_guard 17+stream_stall 15，18.07s），3.3s 边际二十三轮未复现→按约不预防性放宽上限（3.0s 不动）②19.2 长窗采样+验收轮转（第五轮轮换顺延：定时+多渠道，tag=REJ128_1791776628，WS 真实用户路径，verify_mode 全 server_side，late_readback ws_timeouts=0）**2/2✅**：定时 73.09s（cron_tasks.db tasks 表 accept_probe 行数=1 服务端真源）+多渠道 36.91s（出站审计行 wechat→accept_peer 含探针文案=1，审计库总相关行=1）双双 end_turn——**本轮无自然拒做**，部署后累计 45 轮采样：开发 8/8、调试 4/4、发布 4/4、运维 4/4、办公 4/4、定时 4/4、多渠道 4/4、记忆 3/3、协作 3/3、调研 7/8（1 次拒做→补做自愈），合计拒做复发率 1/45、补做自愈 1/1（19.2b 补做落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（heartbeat×1+self_check×1 被过滤）③测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK_9 标记 0、cron_tasks.db accept_probe 行 0）④共享树纪律：全程只读采样未动共享树，本附记 commit 路径限定 docs/soulmate-roadmap.md | ✅ | (本次提交) | 附记补跑；Gate1 git diff 路线图本附记真实落盘+commit 路径限定（git diff --cached 核实）；Gate2=N/A（测量+观察）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（cron_tasks.db 任务行/出站审计行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ128_1791776628/latency_s 73.09+36.91/stop_reason end_turn/verify_mode server_side/late_readback ws_timeouts=0 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出；⚠️诚实边界①采样 2 类各 1 轮同机低负载，定时 73.09s/多渠道 36.91s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/45 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 二十三轮未复现≠证明不 flaky，属当前机器负载下未复现

[2026-10-12 12:05] 周期#129 | 对标缺口销账续·P2-09 自举KPI四联整合（统筹注入最高优先）+compact产生源当拍即接线+16.3反馈挖掘：①16.3四触点（agent_messages 近窗 user 消息 0 条、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 零表空库、discussion 无新）——零反馈维持②代码考古定语义（blueprint P2.3 自举KPI：任务成功率/人工干预率/edit反射次数/compact后质量）：四面数据分散（trajectory turn_root/perm_decision_audit/reflections.db/压缩日志）无整合面→实现 agent/bootstrap_kpi.py compute_kpis() 单一只读聚合真源+GET /api/agent/bootstrap-kpi（app.py 注册）：成功率=已收口回合中 status=ok 且 termination 空（保守语义，未收口不入分母，termination_counts 透明单列）/干预率=(requires_human 审批+external_cancel 打断)/回合/反射=reflections.db 计数+应用数+均质分/compact后质量=kind=compact 事件后 vs 前同会话回合成功率（compact不得破坏会话同源）③考古暴露产生源缺口当拍即接线：压缩点此前只有一行 logger.info 无账本（KPI 必然无样本）→soulmate_agent 压缩点落 log_event kind=compact+before/after 计数入 trajectory（fail-safe 旁路）④真跑终验 **PASS 10/10**（evidence/p2-09-bootstrap-kpi-evidence.json）：生产真跑（直算+重启 acp-proxy 后 live 端点两路同源一致）=557 回合/553 收口/成功率 95.48%、干预 187（ask 185+打断 2）=0.336/回合、termination 分布 (normal) 547/max_turns 3/external_cancel 2/token_budget 1（P1-15 探针回合真源在库）；反射面/compact 面生产无样本诚实降级（available=False+note 明示，未拿 0 冒充表现良好）⑤tests/test_bootstrap_kpi.py 15/15（四联正例+空样本诚实降级+kind 串味防污染+since 窗口+坏 JSON/缺库容噪+接线钉×2）+回归 178 全过（trajectory×3/termination/turn_lifecycle/relay×2+refusal_remediation/self_verify）⑥对账表 blueprint-51-audit.md P2-09 行 ⏳→✅ 附证据+表头 ✅50/⏳1/❌0（原 ❌1=P1-05 已 ✅(替代) 归零）+缺口处理 ⏳×2→⏳×1 | ✅ | (本次提交) | P2-09 销账；Gate1 git diff 真实落盘（bootstrap_kpi.py 304 行/soulmate_agent +16/app.py +14/tests 319 行，三拍小步 commit 防 evo 截走）；Gate2 grep compute_kpis 端点消费+log_event(kind=compact) 在 _prompt_inner 压缩分支真实路径（接线钉 2 例源码断言）+live 端点真实 HTTP 200 回读四联键；Gate3 真实产出=15/15+回归 178 真实 pytest 输出+/tmp 与入库双份 evidence JSON 10 checks 全 ok 回读+生产库真跑数字（trajectory 557 回合/perm_decision_audit 2012 行/reflections 0 行逐库真实查询）+live 端点与直算逐值一致对照；⚠️诚实边界①反射产生源（ReflectionEngine.record_reflection）仍无调用方（写了≠接线了——接入点候选：edit 收尾/失败反思钩子，待用户拍板不擅动）②compact 面 0 样本=生产压缩从未触发（should_compress 阈值未到），产生源已接线待自然触发后出数③干预率分母=回合数、分子含工具级审批事件（rate 可>1，每回合干预次数语义文档明示非回合占比）④成功率取全量历史窗口非滚动窗（since 参数支持窗口未用于本次结论）⑤端点只读聚合零写库⑥19.2 长窗/15.5 例行连续第五轮让位（销账优先级最高），下拍补回；下周期=对标缺口销账收官·P2-10 fork 沙箱对照晋升终验（最后一项 ⏳）→全清单清零后跑验收对标实测+规划下一批切片+15.5 例行补+19.2 长窗

[2026-10-12 12:15] 周期#129附记 | 15.5 第二十四轮+19.2 长窗采样补跑（销账完成后同周期补齐，本条正文⑥"让位"修正为"已补跑"；第五轮验收轮转全集收官）：①15.5 flaky 第二十四轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（--durations 实测 call 1.24-1.42s）+32 例批跑过（stall_guard 17+stream_stall 15，18.58s），3.3s 边际二十四轮未复现→按约不预防性放宽上限（3.0s 不动）②19.2 长窗采样+验收轮转（第五轮收尾：记忆+协作，tag=REJ129_1791778079，WS 真实用户路径，verify_mode 全 server_side）**2/2✅**：记忆 41.53s（memory_append 追加 ACCEPT_MEM_MARK_9→MEMORY.md 服务端真源含标记=是）+协作 18.59s（subagent 双 worker 并行，回复含双 worker 产出+subagent_runs 近8行=8）双双 end_turn——**本轮无自然拒做**，部署后累计 47 轮采样：开发 8/8、调试 4/4、发布 4/4、运维 4/4、办公 4/4、定时 4/4、多渠道 4/4、记忆 4/4、协作 4/4、调研 7/8（1 次拒做→补做自愈），合计拒做复发率 1/47、补做自愈 1/1（19.2b 落账实效仍待自然复发核账）；采集侧系统帧过滤实证生效（self_check×1 被过滤）；协作类本次双 worker 产出与目标一致（上轮 BETA_DONE 偏差未复现）③测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK_9 标记 0、cron_tasks.db 无残留探针任务行）④第五轮验收轮转全集收官（#121 开发+调试→#122 运维+办公→#123 调研+发布→#128附记 定时+多渠道→#129附记 记忆+协作）⑤git 纪律：三拍小步 commit 全落 main（d7de6a99/a3a2743a/49dd02e1）+key 扫描 5 未推提交 0 命中+push github main 实推成功（e7361f1a..49dd02e1） | ✅ | (本次提交) | 附记补跑；Gate1 git diff 路线图本附记真实落盘+commit 路径限定 docs/soulmate-roadmap.md；Gate2=N/A（测量+观察）：验收探针真实消费生产链路（WS→ws_acp→soulmate stdio→真实 LLM 回合+工具执行），verify 判据为服务端真源（MEMORY.md 文件标记/subagent_runs 行）非探针自报；Gate3 真实产出=/tmp/soulmate_accept_results.json 回读 2/2（run_tag=REJ129_1791778079/latency_s 41.53+18.59/stop_reason end_turn 与台面一致）+flaky 单跑 3 次与 32 例批跑真实 pytest 输出；⚠️诚实边界①采样 2 类各 1 轮同机低负载，记忆 41.53s/协作 18.59s 属两类历史常态区间②本轮无自然拒做≠拒做已消失，1/47 为累计间歇率（拒做样本仍仅 1 例），长窗继续采样③19.2b 落账实效仍待自然复发核账（E2E 为注入 fake，链路为真）④15.5 二十四轮未复现≠证明不 flaky，属当前机器负载下未复现
[2026-10-12 12:35] 周期#130 | 对标缺口销账收官·P2-10 fork 沙箱对照晋升终验（统筹注入最高优先，**51 项全清单清零**）+考古暴露流程缺失当拍即补实装+15.5 第二十五轮+19.2 长窗采样（第六轮开启：开发+调试）：①16.3 反馈挖掘先查四触点（agent_messages 近窗 user 消息全为 REJ129/REJ130 验收探针文案非真实反馈、user_feedback 表 0 行、wechat_bridge_log.jsonl 末行仍 1791584264 历史 send_skipped 无新行、tool_failures.db 45KB mtime 06:14 无新、ai_groups discussion_messages 70 行与上周期持平）——零反馈维持②代码考古定语义（蓝图 P2.4"对自我进化后的技能/prompt 在 fork 沙箱跑对照，再晋升主配置"）：现状 dna_evolution `_sandbox_test` 只是编译级沙箱检查、gene_loop promote 是人审晋升无对照——**对照晋升流程不存在**→当拍即补实装 `agent/fork_promote.py`（compare_and_decide：fork=基线/候选各物化独立沙箱目录+独立子进程跑同一确定性评测（崩溃/超时只收编自己这路）；对照=同评双跑评分对比 candidate≥baseline+成对零丢失断言；晋升=全过才 promote 调用方随后才走 SkillSaver.save_flow 单一写入源）+**双漏斗接线**（晋升主配置两个真实写入点均在 save_flow 前过门：skill_evolution._evolve_one 拒→kept_baseline 主配置不动、skill_rewrite.rewrite 拒→rejected 主配置不动）③首轮测试暴露过度严格当拍即修（步骤 verbatim 断言会拦死 13.5 语义改写漏斗的合法措辞改写）→零丢失对齐 skill_rewrite 保真门同语义（步骤数量一致+hard_tokens 语义载体零丢失——措辞可改、命令/路径/引号/数字不可丢；基线无载体真空满足不拦新建型候选）④真跑终验 **PASS 8/8**（探针 acp-proxy/fork_promote_probe.py，证据 evidence/p2-10-fork-promote-evidence.json）：A 门语义层真实 fork 沙箱双向（候选胜出晋 1.0≥1.0/候选丢内容不晋/prompt 类双向）+B 真实晋升主配置（SkillEvolver.evolve_from_turn 生产同款入口→真实落盘技能文件 revision 1→2 新坑服务端真源回读）+C 语义改写漏斗晋升（rewritten+新触发词落盘）+D **真实漏斗内 fail-closed 否决**（沙箱超时=评测不可用→kept_baseline，revision 3→3 文件逐字不动，探针进程存活）+E 清理归零⑤tests/test_fork_promote.py 13/13+回归 255 全过（skill 全家 118：evolution/saver/loader/standard_layer/usage_receipt + 137：rewrite/fork_promote/self_verify/report_evidence/infection_guard）⑥对账表 blueprint-51-audit.md P2-10 行 ⏳→✅ 附证据+缺口处理清零+表头 **✅51/⏳0/❌0**⑦15.5 flaky 第二十五轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 3/3 过（--durations 实测 call 1.21-1.29s）+32 例批跑过（stall_guard 17+stream_stall 15，18.16s），3.3s 边际二十五轮未复现→按约不预防性放宽上限（3.0s 不动）⑧19.2 长窗采样+验收轮转第六轮开启（开发+调试，tag=REJ130_1791779063，WS 真实用户路径，verify_mode 全 server_side）**2/2✅**：开发 37.64s（dev_hello.py marker 命中+运行账本 terminal/execute_code 执行行=1 含程序输出非源码回显，真源=trajectory_events）+调试 25.29s（buggy.py 修复=是(return a + b)+FIXED 标记）双双 end_turn——**本轮无自然拒做**，部署后累计 49 轮采样：开发 9/9、调试 5/5、发布 4/4、运维 4/4、办公 4/4、定时 4/4、多渠道 4/4、记忆 4/4、协作 4/4、调研 7/8（1 次拒做→补做自愈），合计拒做复发率 1/49、补做自愈 1/1⑨测试产物清理归零（/tmp/soulmate_accept 已删、MEMORY.md ACCEPT_MEM_MARK 标记 0、cron_tasks.db accept_probe 行 0、fork_promote_probe_library/db 探针自清回读=删） | ✅ | (本次提交) | P2-10 销账（51 项清零）；Gate1 git diff 真实落盘（fork_promote.py 280 行/skill_evolution +29/skill_rewrite +23/tests 13 例/探针/证据/对账表 3 处+路线图），三拍小步 commit 防 evo 截走；Gate2 grep compare_and_decide 两漏斗 save_flow 前消费（接线钉源码断言含顺序 assertLess）+真实漏斗调用走中门（B/D 层 action 由门裁决决定非自报）；Gate3 真实产出=/tmp 与入库双份 evidence JSON 8 checks 全 ok 回读+服务端文件真源（B 层技能文件 revision 1→2 新坑回读/D 层 revision 3→3 逐字不动对照）+13/13 与 255 回归真实 pytest 输出+验收 2/2 真实 WS 回合（/tmp/soulmate_accept_results.json 回读 run_tag/latency/stop_reason 与台面一致）；⚠️诚实边界①对照为**结构级**同构评测非行为回放——行为级对照（EvalCase 数据集+LLM judge）为升级路径（eval_pipeline.py 已有数据集原语，按"不硬造"纪律不冒进）②C 层 LLM 为注入输入源（门/保真门/落盘全真实），自然 LLM 提案下的门裁决留待 19.2 长窗自然命中核账③gene_loop promote（提案→Gene 模板）人审晋升通道未过对照门（本门覆盖技能/prompt 内容晋升面，Gene 模板面是否加对照**待用户拍板**）④kept_baseline 为新增诚实动作态（对照未晋不冒充 evolved/no_change）⑤本轮无自然拒做≠拒做已消失，1/49 为累计间歇率；19.2b 落账实效仍待自然复发核账⑥15.5 二十五轮未复现≠证明不 flaky；下周期=对标缺口销账全清零后**规划下一批切片**写进路线图（候选方向：16.1 六项待逐项拍板项的技术预研、SessionFileIndex FTS 召回闭环小切片、反射产生源接入点、Gene 模板面对照门）+19.2 长窗继续+验收轮转第六轮轮换类别（余类：运维/办公/调研/发布/定时/多渠道/记忆/协作）+15.5 例行
[2026-10-12 12:41] 周期#131 | 阶段20规划（对标缺口销账 51/51 清零后续批立项）+20.1 SessionFileIndex FTS 召回闭环（"写了≠接线了"考古双缺口当拍即补）+16.3 反馈挖掘+19.2 第六轮（运维+办公）+15.5 第二十六轮：①考古实证双缺口——写入点 `index_file(session_id, fp)` 传参错位（file_path 收 session_id、content 收路径字符串=索引存"路径当内容"垃圾）+SessionFileIndex.search() 全库零消费者（只写不读，写入→索引→召回闭环断链）②实装：写入点改 _index_file_for_recall（读真实文件内容，≥0 且 ≤200KB、二进制 \x00 探测、缺失/读失败显式跳过，metadata 记 session_id）+新增 file_recall 工具（FTS 召回消费端只读：命中=file_path+chunk#+200字片段、无匹配显式报因、空 query 报错、limit 1-20 钳制、lazy-init 对齐 __new__ 构造）五点接线（builtin 工具面/主 dispatch/code-mode 批内/共享分发/系统提示清单）③tests/test_file_index_recall.py 11/11（真实 SQLite FTS 索引→search 命中内容非路径、写入点三跳过态+metadata 回读、召回格式/无匹配/钳制、lazy-init、接线护栏含旧 bug 形态 `self._file_index.index_file(session_id, fp)` 消失断言——首轮断言被自家注释误命中当拍即修为调用形态断言）+回归 73 全过（system_health 21+code_mode+tool_output_wiring）④evo 并发处置实录：工作树中途被 evo 切到 evo/exp-1791779611（开工时为 main），小步 commit 落该分支后经 `git merge-base --is-ancestor` 验快进安全→`git worktree add /tmp/om-main main`+`merge --ff-only` 归位 main 零丢失（不动共享树不惊动在跑 evo——心跳 strand_a 12:38 活、dna_log observe 阶段在途；worktree 挂 /tmp/om-main 待本周期后续 docs 提交归位复用）⑤16.3 四触点挖掘：近 24h agent_messages user 行全为 REJ129/REJ130/REJ131 验收探针文案、user_feedback 0 行、wechat_bridge_log 末行仍 1791584264 无新、ai_groups discussion_messages 70 行持平——零真实反馈维持⑥19.2 第六轮轮转（运维+办公，tag=REJ131_1791779967，WS 真实用户路径 verify_mode 全 server_side）**2/2✅**：运维 16.25s（账本 system_checkup 执行行=1 命中+磁盘 82.8%/8090 在监听）+办公 66.8s（report.xlsx 4978 字节有效 xlsx 回读）双双 end_turn 无拒做——部署后累计 51 轮：开发 9/9、调试 5/5、发布 4/4、运维 5/5、办公 5/5、定时 4/4、多渠道 4/4、记忆 4/4、协作 4/4、调研 7/8，拒做复发率 1/51、补做自愈 1/1⑦15.5 第二十六轮：test_head_stall_aborts_fast_and_retry_recovers 单跑 1/1 过（call 1.48s）+32 例批跑过（18.69s）未复现→上限 3.0s 不动⑧产物清理：/tmp/soulmate_accept 探针自清回读=删、cron_tasks.db tasks=0 | ✅ | (本次提交) | 20.1 勾选+阶段20 立项；Gate1 git diff 真实落盘（soulmate_agent.py +98/-5、tests 143 行，两小步 commit 防 evo 截走）；Gate2 grep 五接线点全在真实路径（wiring 断言钉源码：工具 append/主 dispatch elif/code-mode if/共享 _file_recall_dispatch/系统提示行）；Gate3 真实产出=真实 SQLite FTS 索引写入+召回命中（非 mock）11/11+回归 73 真实 pytest 输出+验收 2/2 真实 WS 回合（/tmp/soulmate_accept_results.json 回读 run_tag/latency/verify_mode 与台面一致）；⚠️诚实边界①file_recall 只覆盖索引时快照（文件改动后需 read_file 读最新，工具描述已声明）②召回范围=会话中提到并索引过的文件（前 3 个提及文件/回合、200KB 内文本），非全盘 RAG③索引 DB 全局共享 ~/.hermes/soulmate/file-index/index.db（metadata 记 session_id 供溯源，暂无按会话过滤参数——需要时再加不预造）④本轮无自然拒做≠拒做消失；19.2b 落账实效待自然复发核账⑤15.5 二十六轮未复现≠证明不 flaky；下周期做 20.2 反射产生源接入点（bootstrap_kpi 自报"反射库 0 行（record_reflection 无产生源接入）"→确定性时刻接产生源）+19.2 长窗继续（第七轮轮换类别：余类 调研/发布/定时/多渠道/记忆/协作）+15.5 例行+16.3 反馈挖掘
