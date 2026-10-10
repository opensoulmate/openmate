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
- **7.x 半缺**：~~浏览器自动化/docx/pdf/xlsx生成/TTS~~ **7.1 已补（周期#36）**：browser 工具面（Playwright/CDP）；**7.2 已补（周期#37）**：document 文档生成（docx/pdf/xlsx）；**7.3 已补（周期#38）**：text_to_speech 语音合成（edge-tts+espeak-ng双后端）；**7.4 已补（周期#39）**：image 图像生成/编辑（Pillow编辑八动作+SOULMATE_IMAGEGEN_API_URL→OpenAI兼容images API真实生成/未配置PIL程序化兜底显式标注）；剩余 Hermes 对标工具缺口=send_file+process 进程管理（已排阶段9切片9.1/9.2）

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
- [ ] 9.1 send_file 文件下发工具：对标 Hermes send_file 语义（path+message→📎文件卡片），
  与 MEDIA 标签下发通道（fileviewer-media-tag-pattern）对齐不双轨；文件不存在/空 path/
  越界显式报因；产物经既有 MEDIA: 渲染面到前端（预览/下载/复制三键卡片）
- [ ] 9.2 process 后台进程管理工具：对标 Hermes process 语义（list/poll/log/kill/write/
  submit 有界子集），后台任务可见可管（用户极度重视可观测性）；禁硬杀长任务（到点只报
  状态不 SIGKILL 既有铁律对齐）；子进程生命周期归系统（观察窗哲学）
- [ ] 9.3 文生图真实后端 e2e：SOULMATE_IMAGEGEN_API_URL 配置文档化+真实 images API 端到端
  实证（周期#39 只实证了 PIL 兜底与失败态；真实 AI 生成待可测配置），诚实多态保持
- [ ] 9.4 AI 群组真实成员端到端（后续区提级）：群组任务链路已通（周期#21/ai-groups-e2e），
  补真实多 agent 成员参与的端到端验收
- [ ] 待用户拍板（不占阶段顺序）：⚡8.6 双链合并取舍、4.1b iLink 接入形态、
  ASR 语音输入多模态路由

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
