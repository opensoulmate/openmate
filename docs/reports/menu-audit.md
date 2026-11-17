# OpenMate 菜单/页面全面体检与重构方案

> 2026-11-17 · 实测对象：`http://localhost:3000`（Next.js 同套代码服务桌面 Electron/Tauri 壳）。
> 判定基准：**产品设计宪法**（①所想即所见·所见即所得 ②功能=agent 基础能力+流程，禁写死专用系统
> ③插件生态=菜单+页面+后台+流程可自我生成 ④UI 铁律）+ `docs/plugin-system-design.md` +
> `docs/reports/next-sprints.md` + knowledge-brain skill（25 组件三层架构）。
> 处置原则：**归位不砍杀，迁移优先于删除**。本报告只出方案，不动代码（等用户拍板）。

---

## 一、体检方法与证据

| 项 | 做法 | 证据 |
|---|---|---|
| 实测截图 | 登录 :3000 逐路由截图（85 张，含全部菜单项、孤儿路由、死链） | `docs/reports/menu-audit-shots/*.png` |
| 页面实态 | 每页提取渲染后 DOM 文本 + 标题（识别空态/壳页/重叠） | 逐项分析表"实测"列 |
| 路由代码 | 遍历 `src/app/**/page.tsx`（84 个路由）+ `*-client.tsx`（76 个页面实现，共 ~46k 行） | 分析表"代码"依据 |
| 菜单代码 | `top-bar.tsx` / `organ-nav-sidebar.tsx` / `bottom-nav.tsx` / `command-menu.tsx` / `app-shell.tsx` | §2 菜单全景 |
| 登录 | dev 环境凭据（knowledge-brain skill 文档载明的本地开发账号）经 `/api/user/login` 取 token 注入，未在浏览器输入密码 | — |

**口径**：菜单项 = 四套导航（顶栏 / 左栏器官导航 / 底栏 / 命令面板）+ 插件动态菜单 + 孤儿路由（有页面无入口）。
共盘点 **92 个路由引用**（去重 79 个有效路由 + 8 条死链 + 5 个动态详情子路由），**无一漏项**。

---

## 二、菜单全景：四套导航各说各话

| 导航面 | 代码位置 | 条目数 | 主要问题（实测） |
|---|---|---|---|
| 顶栏 | `top-bar.tsx` | 9（指标/通知/仪表盘/下载/进化/插件/系统/搜索/设置） | 与底栏、左栏三处重复放"仪表盘/插件/系统" |
| 左栏（器官导航） | `organ-nav-sidebar.tsx` | 35（核心 8 + 底座 8 + 高级 9 + 工具 10） | 25 个器官全部顶格一级菜单；**2 条死链**（/will /mate） |
| 底栏 | `bottom-nav.tsx` | 56 基础（3 个 adminOnly）+ 插件动态项 + 用户菜单 3 项 + 终端钮 | **53 个普通用户可见项横向滚动**，用户抱怨"菜单栏那么多功能"的直接来源 |
| 命令面板 ⌘K | `command-menu.tsx` | 83 条命令 | **7 条死链**、17 条主题命令占 1/5、与全部菜单再重复一遍 |
| 插件动态菜单 | `lib/plugin-nav.ts` + `GET /plugins` 清单 `ui.nav` | 随插件增减 | ✅ 符合宪法③（菜单从清单长出），是唯一"自我生成"的菜单 |
| 孤儿路由 | — | 8（/monitoring /dev-specs /onboarding /bidding /plugins/*7 页等） | 2670 行的 /monitoring 无任何菜单入口 |

**结构性结论**：四套导航是四次独立加菜的堆积（第 N 次功能上线就往底栏 append 一条），从未做过信息架构收敛。
左栏 25 器官是把 **架构文档的组件表直接翻译成了菜单**——"菜单=组件清单"而非"菜单=用户任务"，这正是
"很多没用、页面特别少"的根因：入口按内部结构铺，页面按剩余人手填。

---

## 三、逐项分析表

图例：**处置** — 🟢保留补全 / 🟡归位迁移 / 🔵收敛合并 / 🔴死链清理。

### 3.1 顶栏（9 项）

| 入口 | 路由 | 实测有什么 | 缺什么 | 与设计结构 | 处置 |
|---|---|---|---|---|---|
| 指标 | /metrics | Prometheus 指标文本（OpenTelemetry） | 无图表、无告警阈值 | 系统观测，应属 System | 🔵 → system「指标」tab |
| 通知 | /notifications | 通知中心 366 条、类型/时间线/优先级分布图、已读/清除 | "Invalid Date"渲染 bug；与 /echo（推送通道）无联动 | ✅ 用户面功能 | 🟢 保留（修日期 bug） |
| 仪表盘 | /dashboard | CPU/Mem/Disk/QPS 卡 + 25 器官健康卡 + Trajectory/MCP 卡 | 与 /vital /monitoring /system 四处重复系统状态 | 壳页，功能=能力+状态聚合 | 🔵 → system「概览」或首页，不再顶格 |
| 下载 | /download | 下载器 UI（New Download/Torrent Search/引擎/任务列表，0 任务） | **写死专用系统**（下载器）违反宪法②；无后端任务 | ❌ 跑偏 | 🟡 迁移为插件（如 bidding 同款）或 Limb 能力通道 |
| 进化 | /evolution | 进化目标/配置/概览/时间线、双螺旋 A/B、3218 周期 | 与 /healer /intelligence /heredity /changelog /trajectory 五处演进信息分裂 | ✅ 沉淀机制（宪法①"沉淀"半边） | 🟢 保留为**演进 hub**，吸收 5 个兄弟页 |
| 插件 | /plugins | Plugin Management 页——**显示"No plugins"** | **数据源接错**：页面读 `getApiBaseUrl()`(:8090) 的 `/api/plugins` 返回空，真实插件（bidding/business-opportunity/flow-console/gene_loop）在 acp-proxy :8092 `/plugins` | ✅ 宪法③主入口 | 🟢 保留+修数据源 bug（P0） |
| 系统 | /system | System Overview + Organ Health；内部 4 tab（概览/拓扑/注册表/轨迹） | 与独立页 /topology /registry /trajectory **完全重复**（同一组件两处挂） | ✅ 该做 hub | 🟢 保留为**系统 hub**，吸收观测类页面 |
| 搜索 | ⌘K 命令面板 | 唤起命令面板 | 命令面板问题见 3.4 | ✅ | 🟢 保留 |
| 设置 | /settings | 外观/模型配置/模型路由/Agent 管理/工具权限/存储/器官管理/账户/关于（9 组） | 器官管理与 body-map 重叠；花销统计在外（见 /spending） | ✅ | 🟢 保留 |

### 3.2 左栏器官导航（35 项）

**核心 8**

| 入口 | 路由 | 实测有什么 | 缺什么 | 与设计结构 | 处置 |
|---|---|---|---|---|---|
| Soul | /soul | OpenSoul·System Admin：系统健康/服务状态 | 与 /admin /system 语义重叠；按 knowledge-brain 归属原则"Soul=管理员后台"却对全员可见 | ⚠️ 名对位错 | 🟡 归位 /admin「Soul 后台」tab（adminOnly） |
| 皮层 | /cortex | OpenCortex：目标输入→规划（Advance…） | 无调用历史、与对话内规划能力不通 | ✅ 25 组件 Cortex=推理 | 🟡 归位 body-map 能力台；补"对话里调 Cortex"打通 |
| 神经 | /nerve | 事件总线：Active Topics + Recent Events | 只读事件流，无订阅管理/规则 | ✅ Nerve=事件传输 | 🟡 归位 body-map；补订阅/规则（或并入流程触发器） |
| 血管 | /vein | 文件管理（5.8k 字符，浏览/操作） | 与 /workspace 文件浏览器功能重叠 | ✅ Vein=存储 | 🟡 归位 body-map；文件 UI 收敛到 workspace，vein 页只留存储配置 |
| 躯体 | /soma | 外部组件连接器 + 快速上手 | 与 /discovery（发现引擎）/capture（采集）三处分裂同一能力 | ✅ Soma=连接调度 | 🔵 收敛：soma ← discovery + capture |
| 感官 | /sense | 图像识别（OCR）上传/结果 | 只有 OCR 一条道；ASR/行为感知（behavior）未接 | ✅ Sense=感知，已有真引擎 | 🟡 归位 body-map；补 ASR/behavior 通道 |
| 意志 | /will | **404 页面未找到（死链）** | 页面不存在；Will=业务工作流，功能已在 /workflow | 架构有 Will 组件，页面没做 | 🔴 删菜单项（功能归位 workflow）或建跳转 |
| 伙伴 | /mate | **404 页面未找到（死链）** | 页面不存在；Mate=用户伙伴，功能已在 /chat | 同上 | 🔴 删菜单项（功能归位 chat） |

**底座 8**

| 入口 | 路由 | 实测有什么 | 缺什么 | 与设计结构 | 处置 |
|---|---|---|---|---|---|
| 免疫 | /immune | 内容审核 Flagged + 限流配置/统计 | 无审批联动、无安全策略面 | ✅ Immune=安全 | 🟡 归位 body-map；能力并入 Immune MCP 工具 |
| 体征 | /vital | 资源用量/业务指标/网络流量 | 与 dashboard/monitoring 三处重复"系统状态" | ⚠️ 真实但重复 | 🔵 → system「监控」（并入 monitoring） |
| 骨髓 | /marrow | 备份恢复 + 存储路径 | 无备份计划/恢复演练 | ✅ Marrow=备份 | 🟡 归位 body-map；补计划任务（挂流程引擎） |
| 腺体 | /gland | 多 LLM 路由/密钥管理/Token 计量/预算 | 花销明细在另一处 /spending | ✅ Gland=模型供给 | 🟡 归位 body-map；🔵 /spending 并入此页 |
| 基因 | /gene | 模板库（Template Library，2.6k 字符） | 模板与 skill 体系无联动 | ✅ Gene=模板 | 🟡 归位 body-map；模板挂 skill=内核 |
| 回声 | /echo | 消息推送（Message Push） | 只是通道配置，与 /notifications 无闭环 | ✅ Echo=推送 | 🟡 归位 body-map；接通知中心 |
| 镜像 | /mirror | 沙箱测试 + Sandbox List | 空列表为主，无"一键把当前会话丢进沙箱" | ✅ Mirror=沙箱 | 🟡 归位 body-map；接对话/流程的试跑 |
| 突触 | /link | 集成网关 + Connector List | 无连接器配置流（与 OOMOL/n8n 万能连接器调研脱节） | ✅ Link=集成 | 🟡 归位 body-map；补连接器向导 |

**高级 9**

| 入口 | 路由 | 实测有什么 | 缺什么 | 与设计结构 | 处置 |
|---|---|---|---|---|---|
| 海马体 | /hippo | 记忆生命周期 + Create Memory | 无检索/遗忘曲线可视化 | ✅ Hippo=记忆 | 🟡 归位 body-map |
| 反射 | /reflex | 高速应答 + Add Entry | 只有键值 CRUD 壳，无"真接对话快路径"（宪法：禁 CRUD 壳冒充组件） | ⚠️ 壳页 | 🟡 归位 body-map；补真快路径（直连对话缓存应答） |
| 遗传链 | /heredity | 版本演化（2.9k 字符） | 与 /changelog 同一页面族两处挂 | ✅ | 🔵 与 /changelog 合并 → 演进 hub「版本」tab |
| 巢穴 | /nest | 多租户隔离 | 个人使用场景弱（宪法："企业级功能不盲目复制"） | ⚠️ | 🟡 归位 body-map（adminOnly 面向企业时再启） |
| 脉搏 | /pulse | 精密定时 + Create Signal | 与 /cron（定时任务）语义撞车：Pulse=节拍器 vs Cron=任务 | ⚠️ 重复 | 🔵 → 流程 hub「节拍」（Pulse 作为 cron 的触发器类型） |
| 四肢 | /limb | RPA Executor（录制回放，browser-flows 已沉淀） | 与内置浏览器任务运行器（browser-task-runner）入口分散 | ✅ 真能力 | 🟡 归位 body-map；录制/回放入口收到工作台浏览器工具条 |
| 声带 | /voice | TTS 合成/结果/知识库朗读/后端状态（真出音频） | 无音色管理/批量 | ✅ 真能力 | 🟡 归位 body-map；🟢 补全真能力 |
| 视觉 | /vision | 图像生成 + Canvas + History（真出图） | 页面薄（104 行壳+workspace） | ✅ 真能力 | 🟡 归位 body-map；🟢 补全 |
| 心智 | /mind | 人格中心 + 文本情感 Quick Test | 无人格配置持久化/对话注入 | ✅ Mind=人格 | 🟡 归位 body-map；补"人格→对话 system prompt"注入 |

**工具 10**

| 入口 | 路由 | 实测有什么 | 缺什么 | 与设计结构 | 处置 |
|---|---|---|---|---|---|
| 仪表盘 | /dashboard | 同顶栏仪表盘 | 三处重复 | — | 🔵 同 3.1 |
| 知识库 | /knowledge | **空态"No knowledge entries yet"**（后端实有 10 条 feedback 类条目被过滤）+ 左侧 Sharing Requests | 空态 bug：content_type 过滤把条目全滤掉 | ✅ Mate 用户功能 | 🟢 保留+修空态（P0） |
| 搜索 | /search | Search Everything 统一搜索 | 结果面未打通插件/文档/devlog | ✅ | 🟢 保留补全 |
| 知识图谱 | /graph | 实体/关系编辑（18 实体） | 无图谱编排（/graph-builder 是死链） | ✅ | 🟢 保留；🔵 graph-builder 能力并入其编辑模式 |
| 工作流 | /workflow | 流程列表 47k 字符——**大量 Test Workflow/Validate Test 测试草稿重复堆积** | 测试流污染真实列表；与 flow-console 插件（运行观测）割裂 | ✅ 流程=宪法②半边 | 🟢 保留为**流程 hub**（吸收 cron/pipeline/flow-console），P0 清测试流 |
| 定时任务 | /cron | New Job + "No scheduled tasks" | 空；Pulse/Cron 语义分散 | ✅ | 🔵 → 流程 hub「定时」tab |
| 技能市场 | /skills | Skills Marketplace 真数据（Hermes skills 全量+分类） | 与 /marketplace（市场）两处"市场" | ✅ skill=内核（宪法①） | 🔵 与 marketplace 合并「能力市场」（tabs: 技能/插件/Agent） |
| MCP 客户端 | /mcp | 5 服务器 15 工具、连接状态 | 无工具试跑/审批闸配置 | ✅ | 🟢 保留补全（工具试跑=层③验收面） |
| 工作区 | /workspace | 多标签工作台（25k 字符）+ [id] 详情页 | — | ✅ 既有资产 | 🟢 保留 |
| 学习 | /learn | 4 门课程/进度/学习曲线/热力图 + create/[id] | 课程生成依赖 Cortex 规划，未见失败兜底 | ✅ Mate 用户功能 | 🟢 保留补全 |

### 3.3 底栏（56 项 + 插件项 + 用户菜单 + 终端钮）

底栏与左栏/顶栏大量重复（31 个器官/工具项与左栏重复出现），下表列**底栏独有**项（25 个路由）+ 用户菜单/终端钮：

| 入口 | 路由 | 实测有什么 | 缺什么 | 处置 |
|---|---|---|---|---|
| Chat | /chat | 会话树（SoulMate 14/Hermes 53）+ 任务模板 + 检查点 | **缺宪法①主闭环**：说需求→生成子系统→沉淀常驻入口（forge 后端已有，UI 流没接） | 🟢 保留+补全（P2 核心） |
| AI Groups | /ai-groups | 微信式群聊（成员/角色/@）+ 群组测试会话 | 文档内容混进工作区（选中即渲染长文档） | 🟢 保留 |
| KB Sharing | /knowledge-requests | 知识库申请审批（KB Requests） | 与 /knowledge 内 Sharing Requests tab 重复；审批该是流程引擎的审批节点 | 🔵 → knowledge「审批」tab（底座走流程引擎） |
| Pipeline | /pipeline | 智能流水线 + Upload File 处理管线 | 与 workflow 职责不清 | 🔵 → 流程 hub「流水线」tab |
| Capture | /capture | Capture Manager（内容采集） | 采集=OpenSoma 职能（五层集成架构第 2-5 层） | 🟡 归位 soma |
| Tags | /tags | 标签管理（10k 字符） | 知识库的从属功能顶格一级菜单 | 🟡 → knowledge「标签」tab |
| Body Map | /body-map | 身体地图 28/28 Healthy（解剖布局） | 只有健康灯，没有器官工作台入口 | 🟢 保留+升级为**器官门户**（25 器官的唯一菜单入口） |
| Discovery | /discovery | 发现引擎：362 进程/4283 CLI/42 服务（53k 字符） | 与 soma/capture 分裂 | 🟡 → soma「发现」tab |
| Soma Admin | /soma-admin | OpenSoma 管理面板 | /admin 已有 Soma tab，完全重复 | 🔵 → admin「Soma」tab |
| Admin | /admin | 系统管理/权限/企业/Soma 4 tab | 与独立页 /permission /enterprise /soma-admin 三处重复 | 🟢 保留为**管理 hub** |
| Permission | /permission | 角色权限管理 | 同上重复 | 🔵 → admin tab |
| Enterprise | /enterprise | 企业用户/角色/权限/审计 | 同上重复；个人场景低频 | 🔵 → admin tab |
| Sessions | /sessions | 会话浏览管理（1.3k 行） | 与 /chat 会话树重复 | 🔵 → chat 会话管理（或全局会话搜索） |
| Spending | /spending | 花销统计（总花费/AI 回复数） | Gland 已有 Token 计量，分裂 | 🔵 → gland「用量」 |
| Intelligence | /intelligence | System Intelligence（Score: 0） | 空壳评分 | 🔵 → system 概览评分位 |
| AI Engine | /ai-engine | 5 层架构/上下文用量/Harness Routes(6) | 架构视图该在系统页 | 🔵 → system「架构」tab |
| Healer | /healer | 自愈·自我诊断 | 演进家族一员 | 🔵 → evolution「自愈」tab |
| Topology | /topology | 系统拓扑 30 节点 | 与 system「拓扑」tab 重复 | 🔵 → system tab |
| Registry | /registry | 组件注册表 | 与 system「注册表」tab 重复 | 🔵 → system tab |
| Trajectory | /trajectory | 轨迹追踪/自我进化 | 与 system「轨迹」tab、evolution 三处重复 | 🔵 → evolution「轨迹」tab |
| Timeline | /timeline | 时间线 31386 事件 | 巨型事件流独立页 | 🔵 → system/evolution 时间线 tab |
| Changelog | /changelog | **标题就是"Heredity · Version Evolution"**（同 /heredity） | 一鱼两吃 | 🔵 与 heredity 合并 |
| 文档中心 | /docs | 878 篇文档 6 分组 | — | 🟢 保留为**文档 hub**（吸收 devlog/dev-specs） |
| 开发记录 | /devlog | 6 条 devlog 归档 | 文档族从属 | 🔵 → docs「开发记录」tab |
| Marketplace | /marketplace | ClawHub/Hermes 官方技能/OpenMate 社区/百炼 | 与 /skills 双市场 | 🔵 → 能力市场 |
| 用户菜单 | — | 个人资料/设置/退出登录 | — | 🟢 保留 |
| 终端钮 | — | 终端面板开关 | — | 🟢 保留（能力入口） |

### 3.4 命令面板（83 条命令）

- **7 条死链**：/groups /team /graph-builder /diagnostics /benchmark /activity /will（实测全部 404）。
- **17 条主题命令**占总量 1/5（3 基础 + 14 新主题），该收进设置页主题选择器，⌘K 只留"切换主题"一条。
- 重复导航 60 条与三套菜单第四次重复；`workflow-builder` 命令指向 /workflow 与 workflow 命令重复。
- ✅ 有最近使用排序、三语切换。
- **处置**：命令面板=全量入口的正确位置（导航收敛后所有深层功能只在 ⌘K 可达）；P0 删死链、主题命令收敛为 1 条。

### 3.5 孤儿路由（有页面、无菜单入口）

| 路由 | 实测 | 处置 |
|---|---|---|
| /monitoring | **全站最重观测页（2670 行）**：System Gauges/逐 API 延迟（~20 个端点）/分层架构/System Info | 🔵 并入 system「监控」tab（它的"基准测试/架构/活动"tab 正是死链 /benchmark /diagnostics /activity 的归宿） |
| /dev-specs | 34 项规范列表（Architecture/AgentCore/Memory…） | 🔵 → docs「规范」tab |
| /onboarding | Welcome to OpenMate 引导 | 🟡 改为登录后首启弹层（不占菜单），路由保留作帮助入口 |
| /bidding | 重定向 → /plugins/bidding（智能投标项目管理，真数据） | ✅ **宪法③范本**：插件=菜单+页面+后台；🟢 保留重定向 |
| /plugins/bookmarks 等 7 页 | 书签/每日摘要/番茄钟/便签/计算器/代码片段/系统医生 | ✅ 插件页（旧 plugin.json 体系）；🟡 统一走 /plugins/[plugin] DSL 通道 |

### 3.6 死链清单（8 条，全部实测 404）

| 死链 | 出现位置 | 归宿 |
|---|---|---|
| /will | 左栏核心、⌘K | 功能=业务工作流 → /workflow；菜单项删除 |
| /mate | 左栏核心 | 功能=用户伙伴 → /chat；菜单项删除 |
| /groups | ⌘K | → /ai-groups |
| /team | ⌘K | → /ai-groups 群组 或 admin「企业」tab |
| /graph-builder | ⌘K | → /graph 编辑模式 |
| /diagnostics | ⌘K | → /monitoring（→system） |
| /benchmark | ⌘K | → /monitoring 基准测试 tab |
| /activity | ⌘K | → /timeline /notifications |

---

## 四、分类处置汇总

### 4.1 🟢 保留补全（12 个一级入口 + 2 个能力面）

| 页面 | 补全方向（设计要点见 §5） |
|---|---|
| /chat | 所想即所见主闭环：需求→生成子系统→沉淀常驻入口 |
| /workspace | 多标签工作台保持；接流程试跑/插件面板标签 |
| /plugins | 修数据源 bug；插件详情/安装（forge 链）/卸载生命周期 |
| /knowledge | 修空态过滤 bug；4 tab（条目/标签/审批/共享） |
| /learn /graph /search /mcp | 各自补深（见 §5.6） |
| /ai-groups | 修工作区混排 |
| /evolution | 演进 hub：吸收 healer/heredity/changelog/trajectory/timeline |
| /system | 系统 hub：吸收 monitoring/metrics/topology/registry/intelligence/ai-engine/vital |
| /admin | 管理 hub：吸收 permission/enterprise/soma-admin/soul |
| /settings /notifications | 修日期 bug；主题收口 |
| /body-map | 器官门户（25 器官唯一菜单入口） |
| 器官真能力面 | voice/vision/sense/limb（已有真引擎）继续做真 |

### 4.2 🟡 归位迁移（不砍杀，换层/换爹）

| 现状 | 归位到 | 理由（宪法条款） |
|---|---|---|
| 25 器官一级菜单 | body-map 器官门户下的能力台 | ①界面=门把手：组件是能力不是任务；菜单按任务收 |
| /download 下载器 | 插件层（plugin.yaml 插件，同 bidding 范式） | ②禁写死专用系统 |
| /capture /discovery | /soma「发现」「采集」tab | 五层集成架构：Soma=采集发现 |
| /soul | /admin「Soul 后台」（adminOnly） | 归属原则：Soul=管理员后台 |
| /tags /knowledge-requests | /knowledge tabs | 从属功能不顶格 |
| /spending | /gland「用量」 | Gland=Token 计量正主 |
| /sessions | /chat 会话管理 | 会话的正主是对话 |
| /onboarding | 登录首启弹层 | 引导不是常驻功能 |
| 7 个旧插件页 | /plugins/[plugin] DSL 统一通道 | ③三层插件能力统一 |
| /will /mate | workflow / chat | 组件职能已由正主承载 |

### 4.3 🔵 收敛合并（5 大 hub + 3 个双市场/双页合一）

| 合并组 | hub | 吸收（旧路由 301 重定向，归位不砍杀） |
|---|---|---|
| 系统观测 | **/system**（tabs：概览/监控/拓扑/注册表/指标/架构） | /monitoring /metrics /topology /registry /intelligence /ai-engine /vital /dashboard(顶格位) |
| 管理 | **/admin**（tabs：系统管理/权限/企业/Soma/Soul） | /permission /enterprise /soma-admin /soul |
| 演进 | **/evolution**（tabs：目标/配置/概览/自愈/版本/轨迹/时间线） | /healer /heredity /changelog /trajectory /timeline |
| 流程 | **/workflow**（tabs：流程/定时/流水线/运行观测(flow-console)） | /cron /pipeline /pulse(节拍触发器) |
| 知识文档 | **/knowledge**（条目/标签/审批/共享）+ **/docs**（文档/规范/开发记录） | /tags /knowledge-requests /dev-specs /devlog |
| 市场合一 | **能力市场**（tabs：技能/插件/Agent） | /skills + /marketplace |
| 状态合一 | body-map + system | /dashboard 降级为首页信息位 |

**收敛后导航终态**（底栏 ≤9 项）：对话 · AI 群 · 知识库 · 学习 · 流程 · 能力市场 · 工作台 · 系统 · 更多（器官/管理/进化）。
左栏按上下文注册（chat=会话树、knowledge=目录、其余=收藏+常用）；**所有深层功能经 ⌘K 全量可达**。

---

## 五、页面设计要点（设计稿级，仅方案）

### 5.0 导航重构总稿

- **顶栏**：品牌 · 健康点 · ⌘K 搜索 · 通知(角标) · 设置 → 5 件。
- **底栏**：§4.3 终态 9 项；插件动态项继续由 `plugin-nav` merge（宪法③"菜单自我生成"的唯一活样本，保持）。
- **左栏**：注册制四态不动（chat/ai-groups/pageSidebar/默认）；默认态从 35 项器官导航改为「收藏 + 最近 + 器官门户入口」。
- **UI 铁律核查**（每页对照）：低饱和同色系分层 · 超椭圆（`ui/super-ellipse.tsx` n=3，禁自造）· 选中无下划线（`bg-primary/10 text-primary`+rounded）· 改现有组件禁建新页 · 布局组件只做壳（PageLayout/MainPanel 契约：滚动容器 `h-full overflow-y-auto`）。

### 5.1 /chat = 所想即所见主入口（宪法①落地面）

现状缺口：只有任务模板和普通对话，"生成子系统→沉淀常驻入口"的闭环没有 UI。
设计：输入框 ✨ 旁新增「生成工作台」流——
1. **说需求**（自然语言）→ SoulMate 调 `plugin_dev`（forge 已有：draft→dry-run→确认→安装）；
2. **实时可见**：右侧工作台新标签预览 UI DSL 面板（列表/表单/报告三型已够 80%），随对话实时刷新；
3. **沉淀**：确认安装 → `plugin.yaml ui.nav` 自动 merge 进菜单（机制已存在）→ 成为常驻入口；
4. **回改**：对已沉淀入口说"改一下 XX" → forge 增量修订（同名覆盖语义已有）。
补全顺带：会话树保留；模板按钮收进 ✨ 菜单。

### 5.2 /system 系统 hub

tabs：概览（现 dashboard 指标卡+健康点）/ 监控（并入 monitoring：API 延迟表、System Gauges、基准测试）/ 拓扑 / 注册表 / 指标（Prometheus）/ 架构（5 层+Harness 路由）。每个 tab 是现有组件搬家（布局组件只做壳），旧路由 301。

### 5.3 /plugins 插件工作台

- 数据源改指 acp-proxy :8092 `/plugins`（P0 bug 修复）；
- 列表=插件卡（id/版本/权限/状态）；详情页复用 /plugins/[plugin]（DSL+HTML 面板+流程+工具四 tab，对齐 plugin-system-design §0 三层）；
- 操作：安装（zip 导入+权限预览+用户确认，生成≠安装铁律）/ 启停 / 卸载 / 打包导出（next-sprints §三 第 1 步）；
- forge 入口收在这里 + 对话 ✨ 双入口。

### 5.4 /body-map 器官门户（25 器官的家）

- 上半：解剖图（已有）+ 健康灯（已有 /api/health/all）；
- 下半：25 器官卡片 → 点击进**器官能力台**（统一模板，替代 25 个散页的重复壳）：
  **状态**（健康/错误）· **能力**（该组件注册的 MCP 工具列表，可试跑）· **流程挂点**（可作为流程节点的事件/工具）· **日志/数据**（各器官自己的真内容：vein 文件、hippo 记忆、gene 模板…）。
- 真能力器官（voice 出音频、vision 出图、sense OCR、limb RPA）在能力台放"试跑"面板——满足"组件功能=真能力，禁 CRUD 壳"。

### 5.5 五大 hub 表结构（收敛页统一语言）

每个 hub：**顶部 4 列统计卡（CountUp）+ tab 行 + 列表/详情双栏**，全部复用现有 `PageLayout`/表格/`ui/` 令牌；旧页面组件整体搬入 tab 不重写（改现有组件禁建新页）。

### 5.6 零散补全清单

- /knowledge：修 content_type 过滤（feedback 条目应可见或切 tab）；条目卡加 source_path 直读（knowledge-brain 既有能力）。
- /search：结果聚合补插件数据/文档/devlog 来源。
- /mcp：每工具加"试跑"（层③验收面）+ 审批闸状态（approval: never/on-first-use/always）。
- /notifications：修 "Invalid Date"；接 /echo 推送通道测试按钮。
- /learn：课程生成失败兜底（Cortex 不可用时本地大纲）。
- /ai-groups：工作区文档混排修复（群聊页不该渲染长文档）。
- /workflow：测试流清场（DELETE /flows/{flow_id} 已有）+ 流程列表默认隐藏 draft 测试流。

---

## 六、补全优先级

| 级 | 内容 | 工作量 | 依据 |
|---|---|---|---|
| **P0 止血**（≤1 周） | ① 8 条死链清理/改指向 ② /plugins 数据源修复（:8090→:8092）③ 底栏 56→9、左栏 35→收藏+门户（旧路由 301）④ /knowledge 空态修复 ⑤ /workflow 测试流清理 ⑥ notifications 日期 bug | S×6 | 用户点名痛点直击；全部是改指针/过滤，不动架构 |
| **P1 收敛**（1–2 周） | 五大 hub 合并（system/admin/evolution/workflow/knowledge+docs）+ 能力市场合一 + body-map 器官门户 | M | 归位不砍杀：组件搬家+301，零功能损失 |
| **P2 补全**（2–4 周） | ① chat「生成工作台」所想即所见闭环（forge UI 化）② 器官能力台模板+真能力补全（reflex 快路径/mind 人格注入/sense ASR/limb 录制入口）③ mcp 试跑/审批面 | M–L | 宪法①③的正面战场 |
| **P3 生态** | HTML 沙箱层②（next-sprints §二）+ 插件市场三步走（打包→内网仓库→社区）+ 监控深化（monitoring 并入后的告警阈值） | L | next-sprints 既定排序 |

> 与 `next-sprints.md` 的关系：本方案 P0/P1 是**信息架构还债**，不与 Sprint A/B/C 抢占同一开发资源的判断留用户拍板；若同意，建议 P0 与 Sprint A 并行（P0 多为前端指针级修改）。

---

## 附录 A：证据索引（截图 ↔ 路由）

截图目录：`docs/reports/menu-audit-shots/`（85 张，每张=登录态实测渲染）。
文件名=路由（`/`→`_`）：如 `plugins_bookmarks.png`=/plugins/bookmarks、`learn_create.png`=/learn/create。
死链 8 张（will/mate/groups/team/graph-builder/diagnostics/benchmark/activity.png）= 404 实锤。
重点佐证：`plugins.png`（No plugins 空态）、`knowledge.png`（空态）、`workflow.png`（测试流污染）、
`changelog.png`（标题 Heredity·Version Evolution）、`system.png` 与 `topology.png`/`registry.png`/`trajectory.png`（重复）、
`admin.png` 与 `permission.png`/`enterprise.png`/`soma-admin.png`（重复）、`monitoring.png`（孤儿重页）、`bidding.png`（插件范本）。

## 附录 B：重复矩阵（同一功能多处挂的实锤）

| 功能 | 重复出现在 | 处置 |
|---|---|---|
| 系统状态/健康 | dashboard · vital · monitoring · system · body-map · 左栏健康点 | → system+body-map 两处 |
| 版本演化 | heredity · changelog（同标题） | → evolution「版本」 |
| 拓扑/注册表/轨迹 | 独立页 ×3 + system tabs ×3 | → system tabs |
| 权限/企业/Soma 管理 | 独立页 ×3 + admin tabs ×3 | → admin tabs |
| 定时/节拍 | cron · pulse | → 流程 hub |
| 采集/发现 | capture · discovery · soma | → soma |
| Token 花销 | gland · spending | → gland |
| 市场 | skills · marketplace | → 能力市场 |
| 标签/知识审批 | tags · knowledge-requests · knowledge 内 tab | → knowledge tabs |
| 会话管理 | chat 会话树 · sessions | → chat |

---

*方案定稿待用户拍板；拍板后按 P0→P3 迁移，全程 301 重定向（归位不砍杀）。*


---

## 附录 C：25 器官三判定（设计合理性 / 实际有用性 / 处置）

> 2026-11-18 用户拍板补充口径。判定维度三条：①**设计合理性**——对照设计宪法（功能=agent 基础能力+流程·禁写死专用系统、所想即所见、三层架构/插件自我生成）判当初功能设计本身合不合理；②**实际有用性**——实测它有什么真功能/真数据/被谁用（curl 探测 :8090 各器官后端接口，2026-11-18），**且须含未来战略价值维度**（企业能力类不得以"当前空数据/当前单用户"误杀——现状问题≠设计问题）；③**处置**——合理有用→**留**（归位整合）、设计错→**改**（写明错在哪+该是什么样）、没用→**删**（含路由/菜单/组件，此条用户明示破例"归位不砍杀"）。
> 判定基准：一个"器官"应是 **agent 基础能力（MCP 工具+流程挂点）**，而非独立写死的 CRUD Web 系统。故核心试金石=它是"能力"还是"壳"。
> **判定补充（2026-11-18 用户反例改判）**：有用性判定必须含**未来战略价值**。企业能力类（权限/多租户/审计）不得因"当前空数据/当前只有默认租户"判死——那是现状问题不是设计问题；企业投入时必然要开发完整权限体系。据此**撤销对 nest 的删除**，改判保留+补全。reflex/pulse 的删除维持（纯空壳、无战略价值）。

### 核心 8

| 器官 | 设计合理性（vs 宪法） | 实际有用性（实测证据） | 处置 |
|---|---|---|---|
| **soul** /soul | ❌ **名对位错**：Soul=管理员后台，却做成对全员可见的一级器官页；健康/服务状态本属系统域。合理样：/admin「Soul 后台」tab（adminOnly） | 有真数据（health/all、knowledge/graph/agent 健康聚合），但与 /admin /system 语义重叠 | **改** → /admin「Soul」tab（adminOnly） |
| **cortex** /cortex | ✅ **能力对**（Cortex=推理/规划是核心 agent 能力，plan/think/graphrag 真引擎）；❌ 但做成孤立"目标输入→规划"页，未接对话主闭环（违所想即所见）。合理样：作为可被对话调用的规划能力+能力台面板 | 真引擎（/api/cortex/{plan,agent,think,graphrag}，POST 推理），有调用价值 | **留**（能力保留）+改：打通"对话里调 Cortex"，归位 body-map 能力台 |
| **nerve** /nerve | ✅ **合理**：Nerve=事件总线是事件驱动流程的基础能力；但页面只读事件流、缺订阅/规则管理。合理样：事件总线+订阅/规则喂流程触发器 | 真数据（events 有真实事件 evt_5001…soma.network_closed_connection） | **留**（真能力）+补订阅/规则，归位 body-map |
| **vein** /vein | ⚠️ **能力对、UI 重复**：Vein=存储是基础能力；但文件浏览/操作与 /workspace 文件浏览器两套 UI 重复（违收敛）。合理样：Vein=存储后端能力（配置/路径），文件 UI 收敛到 workspace | 真数据（files 有 tagged.txt 等真实文件） | **留**（存储能力）+改：文件 UI→workspace，vein 只留存储配置 |
| **soma** /soma | ✅ **合理**：Soma=连接调度（五层集成架构）；但与 /discovery /capture 三处分裂同一能力（违收敛）。合理样：Soma=集成中枢，吸收 discovery+capture | capabilities 真（knowledge/rag/event/ocr/asr 全 true），但组件注册表空（total 0）；吸收 discovery(362 进程)/capture 后有真数据 | **留**（收敛）← discovery + capture |
| **sense** /sense | ✅ **合理**：Sense=感知是基础能力，且是**真引擎**（tesseract OCR/ASR）；但只接 OCR 一条道，ASR/behavior 未接。合理样：感知能力（OCR/ASR/行为）作 agent 工具 | 真引擎（engines.ocr/asr available=true，tesseract chi_sim/eng） | **留**（真能力）+补 ASR/behavior 通道，归位 body-map |
| **will** /will | ❌ **设计错**：Will=业务工作流，功能已在 /workflow，另立页面=重复；合理样：Will=workflow 能力，不单开页 | 页面 404（死链），无独立功能 | **删**（删菜单项，功能归位 /workflow） |
| **mate** /mate | ❌ **设计错**：Mate=用户伙伴，功能已在 /chat，另立页面=重复；合理样：Mate=chat 伙伴能力，不单开页 | 页面 404（死链），无独立功能 | **删**（删菜单项，功能归位 /chat） |

### 底座 8

| 器官 | 设计合理性 | 实际有用性 | 处置 |
|---|---|---|---|
| **immune** /immune | ✅ **合理**：Immune=安全（审核/限流/审计/入侵）是基础能力；缺审批联动。合理样：安全能力并入 Immune MCP 工具+流程审批闸 | 真数据（intrusion/threats 有真实入侵记录：path_traversal/high/100.76.232.37） | **留**（真能力）归位 body-map，能力并入 Immune MCP 工具 |
| **vital** /vital | ❌ **设计错**：体征=系统状态，却与 dashboard/monitoring/system 四处重复（违收敛）；合理样：vital=指标能力喂系统 hub，不单开页 | 真数据（cpu 4.7%/mem 43.5%/disk 91.3% 指标） | **改** → system「监控」tab（并入 monitoring） |
| **marrow** /marrow | ✅ **合理**：Marrow=备份是基础能力；缺备份计划/恢复演练。合理样：备份能力挂流程引擎做计划任务 | 真数据（backups 有 backup_1792059812 等真实备份） | **留**（真能力）+补计划任务，归位 body-map |
| **gland** /gland | ✅ **合理**：Gland=模型供给（LLM 路由/密钥/Token 计量）是核心能力；但花销明细分裂在 /spending。合理样：gland=模型供给正主，吸收 /spending | 真数据（providers：openai/mimo-v2.6-pro） | **留**（真能力）+并 /spending→gland「用量」，归位 body-map |
| **gene** /gene | ✅ **合理**：Gene=模板（可复用模式）契合宪法①沉淀/skill=内核；缺与 skill 联动。合理样：模板挂 skill 体系 | 真数据（templates：知识图谱构建流水线等） | **留**（真能力）+模板挂 skill，归位 body-map |
| **echo** /echo | ✅ **合理**：Echo=推送通道是基础能力；但只做通道配置，与 /notifications 无闭环。合理样：echo=推送能力接通知中心闭环 | 真数据（channels：console 通道）但弱 | **留**（真能力）+接通知中心闭环，归位 body-map |
| **mirror** /mirror | ✅ **设计对**：Mirror=沙箱试跑（层③验收面）是真能力概念；但空列表、无"一键把会话丢进沙箱"。合理样：接对话/流程试跑 | 沙箱空（sandboxes []），但模板真（Agent调试沙箱）；概念有效 | **留**（能力对）+改：接对话/流程"一键试跑"，归位 body-map |
| **link** /link | ✅ **设计对**：Link=集成网关（五层集成架构）；但无连接器配置流。合理样：万能连接器+向导 | 连接器空（connectors []/events []），概念有效 | **留**（能力对）+补连接器向导，归位 body-map |

### 高级 9

| 器官 | 设计合理性 | 实际有用性 | 处置 |
|---|---|---|---|
| **hippo** /hippo | ✅ **合理**：Hippo=记忆是核心能力（宪法①沉淀半边）；缺检索/遗忘曲线。合理样：记忆生命周期+检索可视化 | 引擎真（memories/sessions/decay config），当前数据空（total 0）但能力不可或缺 | **留**（真能力）+补检索/遗忘曲线，归位 body-map |
| **reflex** /reflex | ✅ **设计对（战略核心子系统）**：Reflex=**事件驱动快速应激层**——外部事件/信号/MCP 调用/Electron 上报即时捕获→轻量判断/路由/安全拦截/快速回调，不进 LLM 重循环（窗口变化/快捷键/报错/工具返回/权限校验/异常熔断等高优先突发）。被动触发求快 | 页面=**子系统观察面板**（当前壳）；战略核心价值高（快速应激=agent 实时能力关键），功能实现在另一路接真数据 | **留·战略核心**（撤销删）：恢复路由/组件，页面定位=子系统观察窗；功能实现另线推进 |
| **heredity** /heredity | ❌ **设计错**：版本演化却与 /changelog 同页两挂（标题都是"Heredity·Version Evolution"，一鱼两吃）；合理样：演进 hub「版本」tab | 真数据（版本演化）但重复 | **改**（与 /changelog 合并）→ evolution「版本」tab |
| **nest** /nest | ✅ **设计对（企业战略能力）**：多租户/权限是企业投入必需品（多用户·RBAC·资源级权限·租户隔离·审计）；宪法"企业级功能不盲目复制"指别盲目复制复杂度、不等于不做。合理样：完整权限体系 | 现状仅 default 租户＝**现状问题非设计问题**；**未来战略价值高**（企业使用必然要完整权限管理） | **改判·保留+补全**（撤销删）：企业权限管理=战略必需品，排期开发完整体系（多用户/角色 RBAC/资源级权限/租户隔离/审计日志），**权限体系排 P2 首位** |
| **pulse** /pulse | ✅ **设计对（战略核心子系统）**：Pulse=**时间驱动后台自主循环**——心跳唤醒空闲 Agent、周期自检服务/队列/记忆一致性、自主推进长任务/超时恢复/僵尸回收，脱离一问一答的主动感知。主动轮询求持续（与 Reflex 被动求快互补分工） | 页面=**子系统观察面板**（当前壳）；战略核心价值高（自主持续运行=agent 长任务关键），功能实现在另一路接真数据 | **留·战略核心**（撤销删）：恢复路由/组件，页面定位=子系统观察窗；功能实现另线推进 |
| **limb** /limb | ✅ **合理**：Limb=RPA 录制回放是真能力（browser-flows 已沉淀）；入口分散。合理样：录制/回放入口收到工作台浏览器工具条 | 概念真（templates：表单填写等 RPA 模板），当前 tasks/history 空 | **留**（真能力）+录制/回收入工作台浏览器工具条，归位 body-map |
| **voice** /voice | ✅ **合理**：Voice=声带/TTS 是基础能力，**真出音频**（edge-tts）；缺音色管理/批量。合理样：TTS 能力+音色管理 | 真数据（outputs 有真实音频：edge-tts zh-CN-XiaoxiaoNeural） | **留**（真能力）+补音色管理/批量，归位 body-map |
| **vision** /vision | ✅ **合理**：Vision=视觉/图像生成是基础能力，**真出图**；页面薄。合理样：图像生成+Canvas+History | 真数据（generations：flowchart 部署步骤等真实出图） | **留**（真能力）+补全，归位 body-map |
| **mind** /mind | ✅ **合理**：Mind=人格/情感是基础能力（宪法①个性化）；缺人格持久化/对话注入。合理样：人格→对话 system prompt 注入 | 真数据（personalities：default 默认助手） | **留**（真能力）+补人格→对话 system prompt 注入，归位 body-map |

### C.1 处置汇总（三判定落点）

- **留（合理有用/战略→归位整合，20 个）**：cortex / nerve / vein / soma / sense / immune / marrow / gland / gene / echo / mirror / link / hippo / **nest** / **reflex** / **pulse** / limb / voice / vision / mind —— 均为真能力或战略能力，归位 body-map 器官门户能力台。其中 **nest**（企业权限=战略必需品）、**reflex**（事件驱动快速应激层）、**pulse**（时间驱动后台自主循环）为**战略核心**，页面=子系统观察面板，功能实现另线接真数据；cortex/vein/mirror/link/sense/limb 附带接线补全。
- **改（设计错→写明改造方向，3 个）**：soul（→/admin「Soul」adminOnly）、vital（→system「监控」tab）、heredity（→evolution「版本」tab，与 changelog 合并）。另 will/mate 为死链，菜单项删、功能归位 workflow/chat。
- **删（仅死链，2 个）**：**will / mate**（页面不存在、功能已由 workflow/chat 承载，删菜单项+跳转）。

> 两轮反例改判（2026-11-18）：有用性判定须含**未来战略价值**维度，企业/战略能力类不得以"当前空数据/当前单用户/当前是壳"误杀——现状问题≠设计问题。① nest 曾以"仅 default 租户"误删，用户指出企业投入必然要完整权限管理→撤销删、**保留+补全**，权限体系（多用户/RBAC/资源级权限/租户隔离/审计）排 **P2 首位**；② reflex/pulse 曾以"CRUD 壳/空数据"误删，用户摆出原始设计：Reflex=事件驱动快速应激层（被动触发求快）、Pulse=时间驱动后台自主循环（主动轮询求持续），二者分工是**战略核心子系统**，菜单页只是观察窗→撤销删、**留·战略核心**（壳先恢复原状，功能实现另线）。reflex/pulse/nest 三者删除全部撤销，无器官级删除（仅 will/mate 死链清理）。权限体系（多用户/RBAC/资源级权限/租户隔离/审计）排进 **P2 首位**。
