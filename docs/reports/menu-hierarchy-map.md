# 菜单层级归属表（顶/底=一级 · 左侧=二级）

> 2026-11-19 · 用户定稿（原话）："顶部菜单和底部菜单为一级菜单，左侧为二级菜单明白没。"
> 分组原则（原话）："同类功能的就是一个一级菜单，比如智能投标是一级菜单，内部左侧二级菜单包含
> 商机发现、招标文件编写、投标文件等等。"——一级按**功能域**聚合，左侧二级列域内各功能页。
> 架构原则（原话）："但是像智能投标这种特定功能应该是以插件形式存在，默认是不安装的，谁使用谁安装。"
> 机制（原话）："插件安装之后生成一级菜单及二级菜单。"（manifest 声明、动态生成、卸载即收走、禁手写死）
> 左侧铁律（原话）："左侧菜单栏不要什么工具，那些一级菜单的东西为什么要在二级菜单再重复放一遍"——
> ①只显示当前一级模块的子功能 ②一级项绝不允许在左侧重复出现 ③不搞复读一级名的分组大标题；
> 同一功能只在一个层级出现一次。
> **恢复令铁律：只调层级归属，功能零删减（归位不砍杀）。** 本表逐项可核，验收=64 入口逐个真点击全落地。

---

## 〇、口径

- **一级位（L1）**：顶栏 + 底栏上的功能域入口（主导航/模块入口）。内置核心域常驻；插件贡献域随插件安装生成、卸载消失。
- **二级（L2）**：左侧栏，跟随当前一级模块渲染**该域子功能清单**（平铺、无分组大标题、零一级项重复）。
  会话页 / AI 群组页专属侧栏不动（用户铁律"对话页和AI群组页不改造"）；各页 pageSidebar 作"上下文导航"
  保留在二级菜单下方（功能零删减），其中出现的导航项不再是菜单重复项（同一功能只在一个层级出现一次）。
- **实测基线**：底栏 65 项（55 基础 + 10 插件动态，2026-11-19 实测 DOM）+ 顶栏 9 件 = **74 个现有一级入口**；
  恢复期 `nav-click-test.json` 的 64 唯一入口集为其子集（含 /pipeline，不含 /tasks、插件页）。
  本次 74 个逐个归位 + 64 基线逐个点击，只多不少。
- **重复合并**：同一功能出现在多套导航（顶/底/左/⌘K）的，归属表合并为一行（"合并"列注明），**任何入口不删**——
  迁入左二级或保留 ⌘K 全量面，点击全落地。

---

## 一、一级位总表（13 内置核心域 + 插件贡献域）

| 一级位 | 边 | 域 id | 首页 | 二级条目 | 性质 |
|---|---|---|---|---|---|
| 对话 | 底栏 | `chat` | /chat | 3 | 内置核心 |
| AI 群组 | 底栏 | `ai-groups` | /ai-groups | 1 | 内置核心（专属侧栏不动） |
| 知识管理 | 底栏 | `knowledge` | /knowledge | 9 | 内置核心 |
| 学习 | 底栏 | `learn` | /learn | 2 | 内置核心 |
| 自动化 | 底栏 | `automation` | /workflow | 6 | 内置核心 |
| 器官 | 底栏 | `organs` | /body-map | 14 | 内置核心（器官/躯体域） |
| 工作台 | 底栏 | `workbench` | /workspace | 3 | 内置核心 |
| 能力市场 | 底栏 | `market` | /marketplace | 12 | 内置核心 |
| 通知 | 顶栏 | `notify` | /notifications | 2 | 内置核心 |
| 进化 | 顶栏 | `evolution` | /evolution | 6 | 内置核心 |
| 系统管理 | 顶栏 | `system` | /system | 20 | 内置核心 |
| 设置 | 顶栏 | `settings` | /settings | 2 | 内置核心 |
| 搜索 ⌘K | 顶栏 | — | — | — | 命令面板（全量入口面，不动） |
| 智能投标 | 底栏（插件贡献） | `plugin:bidding` | /plugins/bidding | 7 | **插件贡献域·默认未安装** |
| 商机管理 | 底栏（插件贡献·回退位） | `plugin:business-opportunity` | /plugins/business-opportunity/home | 1 | join 智能投标域；目标缺席回退独立一级位 |

插件 join（二级并入既有域，随插件安装生成）：流程运行台 → 自动化域；进化回流 → 进化域；
商机发现 → 智能投标域（bidding 未安装时回退独立一级位「商机管理」，同一功能永远只在一个层级出现）。

---

## 二、归属表：74 个现有一级入口逐个归位

### 2.1 顶栏 9 件

| # | 现入口 | 路由 | 归入一级位 | 二级条目 / 去向 | 合并/备注 |
|---|---|---|---|---|---|
| 1 | 指标 | /metrics | 系统管理 | 二级「指标」/metrics | 顶栏指标位让位于域，路由照旧 |
| 2 | 通知 | /notifications | 通知 | 域首页「通知中心」 | 顶栏通知位=通知域一级位 |
| 3 | 仪表盘 | /dashboard | 系统管理 | 二级「仪表盘」/dashboard | 与左栏仪表盘重复→合并一行，不删 |
| 4 | 下载 | /download | 工作台 | 二级「下载」/download | 下载器=工作台功能 |
| 5 | 进化 | /evolution | 进化 | 域首页 | 顶栏进化位=进化域一级位 |
| 6 | 插件 | /plugins | 能力市场 | 二级「插件管理」/plugins | 插件管理=能力市场域子功能 |
| 7 | 系统 | /system | 系统管理 | 域首页「概览」/system | 顶栏系统位=系统管理域一级位 |
| 8 | 搜索 ⌘K | — | （保留） | ⌘K 命令面板 | 全量入口面不动 |
| 9 | 设置 | /settings | 设置 | 域首页 | 顶栏设置位=设置域一级位 |

### 2.2 底栏 65 项

| # | 现入口 | 路由 | 归入一级位 | 二级条目 | 备注 |
|---|---|---|---|---|---|
| 1 | Chat | /chat | 对话 | 域首页「会话」 | 会话树专属侧栏不动 |
| 2 | AI Groups | /ai-groups | AI 群组 | 域首页「群组」 | 专属侧栏不动 |
| 3 | Knowledge | /knowledge | 知识管理 | 「知识库」 | 与左栏合并 |
| 4 | Learn | /learn | 学习 | 「课程」 | 与左栏合并 |
| 5 | Graph | /graph | 知识管理 | 「知识图谱」 | 与左栏合并 |
| 6 | KB Sharing | /knowledge-requests | 知识管理 | 「共享审批」 | |
| 7 | Cron Jobs | /cron | 自动化 | 「定时任务」 | 与左栏合并；用户例"cron/流程/任务" |
| 8 | Workflow | /workflow | 自动化 | 「流程」（域首页） | 用户例"cron/流程/任务" |
| 9 | Skills | /skills | 能力市场 | 「技能市场」 | 与左栏合并 |
| 10 | MCP Client | /mcp | 能力市场 | 「MCP 客户端」 | 与左栏合并 |
| 11 | Workspace | /workspace | 工作台 | 「工作区」（域首页） | 与左栏合并 |
| 12 | Capture | /capture | 器官 | 「采集」 | 归躯体（Soma=采集发现） |
| 13 | Tags | /tags | 知识管理 | 「标签」 | 知识库从属功能归位 |
| 14 | Body Map | /body-map | 器官 | 「躯体地图」（域首页） | 器官域门户 |
| 15 | Soma | /soma | 器官 | 「躯体」 | |
| 16 | Discovery | /discovery | 器官 | 「发现」 | 归躯体 |
| 17 | Cortex | /cortex | 器官 | 「皮层」 | 器官导航归位 |
| 18 | Vein | /vein | 工作台 | 「血管（存储）」 | 文件管理归工作台 |
| 19 | Gene | /gene | 能力市场 | 「模板库」 | 模板=能力沉淀 |
| 20 | Vital | /vital | 系统管理 | 「体征」 | 系统监控归系统管理 |
| 21 | Gland | /gland | 系统管理 | 「模型网关」 | |
| 22 | Hippocampus | /hippo | 知识管理 | 「记忆」 | 记忆管理归知识域 |
| 23 | Reflex | /reflex | 器官 | 「反射」 | |
| 24 | Heredity | /heredity | 进化 | 「遗传链」 | 版本演化归进化域 |
| 25 | Pulse | /pulse | 自动化 | 「节拍」 | 时间驱动循环归自动化 |
| 26 | Nerve | /nerve | 自动化 | 「神经（事件总线）」 | 事件触发归自动化 |
| 27 | Sense | /sense | 器官 | 「感官」 | |
| 28 | Immune | /immune | 系统管理 | 「免疫（安全）」 | |
| 29 | Marrow | /marrow | 系统管理 | 「骨髓（备份）」 | |
| 30 | Echo | /echo | 通知 | 「消息推送」 | 推送通道归通知域 |
| 31 | Mirror | /mirror | 器官 | 「镜像（沙箱）」 | |
| 32 | Link | /link | 器官 | 「突触（集成）」 | |
| 33 | Nest | /nest | 系统管理 | 「巢穴（租户）」 | 企业租户归系统管理 |
| 34 | Limb | /limb | 器官 | 「四肢（RPA）」 | |
| 35 | Voice | /voice | 器官 | 「声带」 | |
| 36 | Vision | /vision | 器官 | 「视觉」 | |
| 37 | Mind | /mind | 器官 | 「心智」 | |
| 38 | Soul Management | /soul | 系统管理 | 「Soul 后台」 | Soul=管理员后台（归属原则） |
| 39 | Soma Management | /soma-admin | 系统管理 | 「Soma 管理」 | adminOnly |
| 40 | Admin | /admin | 系统管理 | 「管理」 | adminOnly |
| 41 | Permissions | /permission | 系统管理 | 「权限」 | adminOnly |
| 42 | Enterprise | /enterprise | 系统管理 | 「企业」 | |
| 43 | Sessions | /sessions | 对话 | 「会话管理」 | 会话的正主是对话 |
| 44 | Spending | /spending | 系统管理 | 「花销」 | |
| 45 | Intelligence Analysis | /intelligence | 系统管理 | 「智能评分」 | |
| 46 | AI Engine | /ai-engine | 系统管理 | 「AI 引擎」 | |
| 47 | Healer | /healer | 系统管理 | 「自愈」 | C.2 裁定：自愈归 Immune 面 |
| 48 | Topology | /topology | 系统管理 | 「拓扑」 | |
| 49 | Registry | /registry | 系统管理 | 「注册表」 | |
| 50 | Trajectory | /trajectory | 进化 | 「轨迹」 | |
| 51 | Timeline | /timeline | 进化 | 「时间线」 | |
| 52 | Changelog | /changelog | 进化 | 「版本记录」 | 与 heredity 同页族，各自保留 |
| 53 | 文档中心 | /docs | 知识管理 | 「文档中心」 | |
| 54 | 开发记录 | /devlog | 知识管理 | 「开发记录」 | |
| 55 | Marketplace | /marketplace | 能力市场 | 「插件市场」（域首页） | |
| 56 | 招标文件解析 | /plugins/bidding/parse | **智能投标（插件）** | 「招标文件解析」 | 插件 manifest 页，动态生成 |
| 57 | 评分办法配置 | /plugins/bidding/scoring | 智能投标（插件） | 「评分办法配置」 | 同上 |
| 58 | 标书生成工作台 | /plugins/bidding/generate | 智能投标（插件） | 「标书生成工作台」 | =投标文件编制 |
| 59 | 废标风险审查报告 | /plugins/bidding/review | 智能投标（插件） | 「废标风险审查报告」 | =投标审查 |
| 60 | 报价策略 | /plugins/bidding/price | 智能投标（插件） | 「报价策略」 | |
| 61 | 知识库管理 | /plugins/bidding/knowledge | 智能投标（插件） | 「知识库管理」 | |
| 62 | 商机管理 | /plugins/business-opportunity/home | 智能投标（插件） | 「商机发现」 | join；bidding 未装回退一级位「商机管理」 |
| 63 | 流程运行台 | /plugins/flow-console/home | 自动化 | 「流程运行台」 | join 自动化域，动态生成 |
| 64 | 智能投标 | /plugins/bidding | **智能投标（插件）** | 域首页（项目管理） | 插件 L1 位；默认未安装 |
| 65 | 进化回流 | /plugins/gene-loop | 进化 | 「进化回流」 | join 进化域，动态生成 |

### 2.3 左栏 organ-nav 35 项（器官导航归入对应一级模块下）

25 器官全部按**功能归属**去重合并（多数即 2.2 同行，"合并"=同一行）：soul/vital/gland/marrow/immune/nest→系统管理；
heredity→进化；echo→通知；hippo→知识管理；gene→能力市场；will/pulse/nerve→自动化；vein→工作台；
cortex/soma/sense/mirror/link/reflex/limb/voice/vision/mind + body-map/sense→器官（躯体）。
工具组 11 项（dashboard/knowledge/search/graph/workflow/tasks/cron/skills/mcp/workspace/learn）——
**"工具"分组名删除**（一级名目不在二级复读）：search→知识管理「统一搜索」、tasks→自动化「任务收发台」、
其余与底栏同行合并。/will「意志」→自动化「意志」（活页，flow_engine 观察窗）；/mate「伙伴」→对话「伙伴」
（301→/chat，产品本体自述条目）。

---

## 三、各域二级清单（左栏实际渲染，平铺、零一级项重复）

> 标签 = `src/lib/menu-domains.ts` i18n 实际标签（nav.* 键）；域标签（nav.dom.*）与页标签不同名（铁律②）。

- **对话**（/chat）：会话 /chat · 会话管理 /sessions · 伙伴 /mate（/chat 页=会话树专属侧栏不动；深层页入口=⌘K+本清单）
- **AI 群组**（/ai-groups）：群聊 /ai-groups（专属侧栏=会话树/群组树，不动）
- **知识管理**（/knowledge）：知识库 · 知识图谱 /graph · 标签管理 /tags · 共享审批 /knowledge-requests ·
  统一搜索 /search · 文档中心 /docs · 开发记录 /devlog · 规范 /dev-specs · 海马体 /hippo
- **学习**（/learn）：课程 /learn · 创建课程 /learn/create
- **自动化**（/workflow）：流程 /workflow · 定时任务 /cron · 任务收发台 /tasks · 流水线 /pipeline ·
  脉搏 /pulse · 意志 /will · 神经 /nerve · ＋插件 join「流程运行台」
- **器官**（/body-map）：躯体地图 /body-map · 躯体 /soma · 发现引擎 /discovery · 采集 /capture · 皮层 /cortex ·
  感官 /sense · 镜像 /mirror · 突触 /link · 反射 /reflex · 四肢 /limb · 声带 /voice · 视觉 /vision · 心智 /mind
- **工作台**（/workspace）：工作区 /workspace · 下载管理 /download · 血管 /vein
- **能力市场**（/marketplace）：插件市场 /marketplace · 技能市场 /skills · 插件管理 /plugins ·
  MCP客户端 /mcp · 基因 /gene · 书签 /plugins/bookmarks · 每日摘要 /plugins/daily-digest ·
  番茄钟 /plugins/pomodoro · 便签 /plugins/quick-notes · 计算器 /plugins/smart-calc ·
  代码片段 /plugins/snippets · 系统医生 /plugins/system-doctor
- **通知**（/notifications）：通知中心 /notifications · 回声 /echo
- **进化**（/evolution）：遗传链 /heredity · 版本日志 /changelog · 轨迹 /trajectory ·
  时间线 /timeline · ＋插件 join「进化回流」（/evolution 域首页不二级复读，铁律②）
- **系统管理**（/system）：系统总览 /system · 仪表盘 /dashboard · 监控 /monitoring · 指标 /metrics ·
  系统拓扑 /topology · 组件注册表 /registry · 体征 /vital · 智能分析 /intelligence · AI 引擎 /ai-engine ·
  腺体 /gland · 开销 /spending · 骨髓 /marrow · 免疫 /immune · 自愈系统 /healer ·
  管理后台 /admin · 权限管理 /permission · 企业管理 /enterprise · Soma管理 /soma-admin ·
  Soul管理 /soul · 巢穴 /nest
- **设置**（/settings）：新手引导 /onboarding（/settings 域首页不二级复读；设置分节=页内上下文导航）
- **智能投标**（插件贡献域，安装后）：商机发现 /plugins/business-opportunity/home（join·首位）·
  招标文件解析 · 评分办法配置 · 标书生成工作台 · 废标风险审查报告 · 报价策略 · 知识库管理（按 manifest 顺序）

> 验收结果见 `docs/reports/menu-hierarchy-verify.md`（94 入口真点击 0 失败、64 基线 64/64、零一级项重复、零叠字）。

---

## 四、85 路由全量清点（零失联页）

口径：menu-audit 85 图基线（77 活页 + 8 死链）＋此后新增（/tasks /will 活页）。301 死链保留（点击可达）。

| 路由 | 归属（一级位 → 二级） | 状态 |
|---|---|---|
| /chat | 对话 → 域首页 | 活页 |
| /sessions | 对话 → 会话管理 | 活页 |
| /mate | 对话 → 伙伴 | 301 → /chat |
| /ai-groups | AI 群组 → 域首页 | 活页 |
| /groups /team | ⌘K 死链 301 → /ai-groups | 301 |
| /knowledge | 知识管理 → 知识库 | 活页 |
| /graph | 知识管理 → 知识图谱 | 活页 |
| /graph-builder | ⌘K 死链 301 → /graph | 301 |
| /tags | 知识管理 → 标签 | 活页 |
| /knowledge-requests | 知识管理 → 共享审批 | 活页 |
| /search | 知识管理 → 统一搜索 | 活页 |
| /docs | 知识管理 → 文档中心 | 活页 |
| /devlog | 知识管理 → 开发记录 | 活页 |
| /dev-specs | 知识管理 → 规范 | 活页 |
| /hippo | 知识管理 → 记忆 | 活页 |
| /learn /learn/create /learn/[id] | 学习 → 课程/创建/详情 | 活页 |
| /workflow | 自动化 → 流程（域首页） | 活页 |
| /cron /cron/[id] | 自动化 → 定时任务/详情 | 活页 |
| /tasks | 自动化 → 任务收发台 | 活页 |
| /pipeline | 自动化 → 流水线 | 活页（旧路由直达） |
| /pulse | 自动化 → 节拍 | 活页 |
| /will | 自动化 → 意志 | 活页（flow_engine 观察窗） |
| /nerve | 自动化 → 神经（事件总线） | 活页 |
| /body-map | 器官 → 躯体地图（域首页） | 活页 |
| /soma /discovery /capture | 器官 → 躯体/发现/采集 | 活页 |
| /cortex /sense /mirror /link /reflex /limb /voice /vision /mind | 器官 → 对应二级 | 活页 |
| /workspace /workspace/[id] | 工作台 → 工作区/详情 | 活页 |
| /download | 工作台 → 下载 | 活页 |
| /vein | 工作台 → 血管（存储） | 活页 |
| /marketplace | 能力市场 → 插件市场（域首页） | 活页 |
| /skills | 能力市场 → 技能市场 | 活页 |
| /plugins | 能力市场 → 插件管理 | 活页 |
| /mcp | 能力市场 → MCP 客户端 | 活页 |
| /gene | 能力市场 → 模板库 | 活页 |
| /plugins/bookmarks /daily-digest /pomodoro /quick-notes /smart-calc /snippets /system-doctor | 能力市场 → 插件工具 7 页 | 活页 |
| /notifications | 通知 → 域首页 | 活页 |
| /echo | 通知 → 消息推送 | 活页 |
| /evolution | 进化 → 域首页 | 活页 |
| /heredity /changelog /trajectory /timeline | 进化 → 遗传链/版本记录/轨迹/时间线 | 活页 |
| /system | 系统管理 → 概览（域首页） | 活页 |
| /dashboard /monitoring /metrics /topology /registry | 系统管理 → 仪表盘/监控/指标/拓扑/注册表 | 活页（monitoring 原孤儿页归位） |
| /vital /intelligence /ai-engine /gland /spending /marrow /immune /healer | 系统管理 → 对应二级 | 活页 |
| /admin /permission /enterprise /soma-admin /soul /nest | 系统管理 → 管理/权限/企业/Soma 管理/Soul 后台/巢穴 | 活页 |
| /diagnostics /benchmark /activity | ⌘K 死链 301 → /monitoring（→system）/monitoring/timeline | 301 |
| /settings | 设置 → 域首页 | 活页 |
| /onboarding | 设置 → 新手引导 | 活页（原孤儿页归位） |
| /bidding | 插件域首页（智能投标） | 301 → /plugins/bidding（插件默认未安装→未装载态提示，安装即活） |
| /plugins/bidding /plugins/bidding/[page] | 智能投标（插件贡献域） | 随安装生成 |
| /plugins/business-opportunity/home /plugins/flow-console/home /plugins/gene-loop | 插件 join 二级 | 随安装生成 |
| /login /auth/oauth / | 认证/根跳转 | 不占菜单 |

**结论**：85 基线路由 + 新增 /tasks /will 全部有归属，**零失联页**；8 条死链 301 点击可达。

---

## 五、插件贡献域（智能投标为范本）

1. **manifest 声明**（`ui.menu`）：L1（label/icon/position）+ L2 条目清单（按声明顺序渲染）+ 每个二级功能的
   工作流契约（function id + 输入/输出 schema）。见 `docs/plugin-spec.md`。
2. **动态生成**：安装动作触发生效（运行时把该插件 L1/L2 渲染进导航），卸载即移除；**禁手写死菜单项**。
3. **默认未安装**：默认菜单里没有智能投标（`plugins/install-state.json` 声明 bidding=available）；
   插件包（全套页/工具/流程）保留在 `plugins/bidding/`，市场可装可卸——能力零删除。
4. **join 机制**：插件二级可并入既有功能域（流程运行台→自动化、进化回流→进化、商机发现→智能投标）；
   目标域缺席时回退独立一级位（商机管理），同一功能永远只在一个层级出现一次。
5. **验收**：默认态菜单无智能投标 → 市场安装 → 一级菜单出现且全套二级真点可用 → 卸载 → 菜单消失。

---

## 六、重排验收口径

1. 64 基线入口 + 74 现有一级入口逐个真点击全落地（恢复令铁律：一个功能都不能丢）。
2. ⌘K 全量扫（82 命令行渲染、逐组可点）。
3. 逐视口检查左侧=当前模块子功能清单，**零一级项重复**、零复读分组标题。
4. 会话页 / AI 群组页专属侧栏逐像素不动。
5. 桌面 + 移动双视口截图 vision 自查，零叠字。
6. 插件安装/卸载全流程真点（智能投标范本）。
7. tsc + next build 绿；git 显式路径提交。
