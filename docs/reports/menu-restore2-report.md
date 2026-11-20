# 菜单完整性恢复·范围纠偏验收报告（第 2 次恢复令）

> 2026-11-20 · 事故：commit 520d2bca「菜单层级重排」把顶/底栏收敛成 13 个功能域一级位，
> 用户暴怒："菜单怎么很多都没了"+"器官里面为什么不全"+"一级菜单里面 下载呢"+"监控都哪去了"。
> 用户正解（范围纠偏原话）："我让你只是把器官统一放到二级菜单里面 其他的菜单呢"——
> **只有器官收进二级菜单，其他一切菜单保持 520d2bca 之前原样**。
> 上轮自报"94 入口 0 失败"但用户看到大量缺失——本轮以**渲染 DOM 逐项清点 + 截图 + 真点击**为准。

## 一、改了什么（范围：最小）

| 文件 | 处置 |
|---|---|
| `src/components/top-bar.tsx` | git 恢复 520d2bca~1 原样：**顶栏 9 件直挂**（指标/通知/仪表盘/**下载**/进化/插件/系统/⌘K/设置） |
| `src/components/bottom-nav.tsx` | 恢复 520d2bca~1 直挂形态，仅两处器官改造：①23 个器官直挂项收进左栏二级（原 `/body-map` 位改挂「器官」一级入口）②新增「监控」`/monitoring` 一级直挂（用户点名，原为孤儿路由）。插件一级位动态生成保留 |
| `src/components/organ-nav-sidebar.tsx` | git 恢复 520d2bca~1 原样（**此形态即器官二级菜单正解**：核心 8/底座 8/高级 9=25 器官 + 工具 11）+ 尾部插件贡献分节（安装长出/卸载收走，join 条目并入目标插件分节） |
| `src/components/app-shell.tsx` | git 恢复 520d2bca~1 原样：pageSidebar 不再拼二级导航、默认左栏=OrganNavSidebar |
| `src/components/level2-nav-sidebar.tsx` | 删除（520d2bca 产物，13 域二级渲染；器官二级由 organ-nav-sidebar 承载，插件二级并入其分节） |
| `src/lib/menu-domains.ts` | 「器官」域二级清单补全为 **25 器官一个不少**（核心 8/底座 8/高级 9，与 organ-nav-sidebar / body-map ORGAN_LAYOUT 同一清单）；其他域散落器官条目按归属保留不删；插件 join 基础设施不动 |
| `src/locales/zh.json` `en.json` `ja.json` | 各 +1 key：`nav.monitoring`（监控/Monitoring/監視） |
| 插件机制 | **不动**：plugin.yaml ui.menu / plugin-spec.md / spec_validator / scaffold / install-state / 智能投标插件化全部保留 |

## 二、非器官菜单逐项对照（520d2bca 前基线 = menu-audit 85 路由 + 64 唯一入口）

渲染 DOM 实测（admin 角色，:3000 生产构建 BUILD_ID `5GHWtO4jGL_dON6kT0FAB`）：

- **顶栏 9/9 全部直挂可见**：指标 /metrics、通知 /notifications、仪表盘 /dashboard、**下载 /download**、进化 /evolution、插件 /plugins、系统 /system、搜索 ⌘K、设置 /settings ✅ 与基线逐项一致
- **底栏 34 项直挂可见**（基线 55 − 23 器官 + 监控 + 插件一级位）：

| 入口 | 路由 | 状态 |
|---|---|---|
| 会话 | `/chat` | ✅ 直挂保留 |
| AI 群组 | `/ai-groups` | ✅ 直挂保留 |
| 知识库 | `/knowledge` | ✅ 直挂保留 |
| 学习 | `/learn` | ✅ 直挂保留 |
| 知识图谱 | `/graph` | ✅ 直挂保留 |
| 知识库共享 | `/knowledge-requests` | ✅ 直挂保留 |
| 定时任务 | `/cron` | ✅ 直挂保留 |
| 工作流 | `/workflow` | ✅ 直挂保留 |
| 技能市场 | `/skills` | ✅ 直挂保留 |
| MCP客户端 | `/mcp` | ✅ 直挂保留 |
| 工作区 | `/workspace` | ✅ 直挂保留 |
| 采集 | `/capture` | ✅ 直挂保留 |
| 标签管理 | `/tags` | ✅ 直挂保留 |
| 身体地图→「器官」 | `/body-map` | ✅ 直挂保留（标签改「器官」，25 器官二级入口） |
| 躯体 | `/soma` | ⤵️ 收进器官二级（左栏） |
| 发现引擎 | `/discovery` | ✅ 直挂保留 |
| 皮层 | `/cortex` | ⤵️ 收进器官二级（左栏） |
| 血管 | `/vein` | ⤵️ 收进器官二级（左栏） |
| 基因 | `/gene` | ⤵️ 收进器官二级（左栏） |
| 体征 | `/vital` | ⤵️ 收进器官二级（左栏） |
| 腺体 | `/gland` | ⤵️ 收进器官二级（左栏） |
| 海马体 | `/hippo` | ⤵️ 收进器官二级（左栏） |
| 反射 | `/reflex` | ⤵️ 收进器官二级（左栏） |
| 遗传链 | `/heredity` | ⤵️ 收进器官二级（左栏） |
| 脉搏 | `/pulse` | ⤵️ 收进器官二级（左栏） |
| 神经 | `/nerve` | ⤵️ 收进器官二级（左栏） |
| 感官 | `/sense` | ⤵️ 收进器官二级（左栏） |
| 免疫 | `/immune` | ⤵️ 收进器官二级（左栏） |
| 骨髓 | `/marrow` | ⤵️ 收进器官二级（左栏） |
| 回声 | `/echo` | ⤵️ 收进器官二级（左栏） |
| 镜像 | `/mirror` | ⤵️ 收进器官二级（左栏） |
| 突触 | `/link` | ⤵️ 收进器官二级（左栏） |
| 巢穴 | `/nest` | ⤵️ 收进器官二级（左栏） |
| 四肢 | `/limb` | ⤵️ 收进器官二级（左栏） |
| 声带 | `/voice` | ⤵️ 收进器官二级（左栏） |
| 视觉 | `/vision` | ⤵️ 收进器官二级（左栏） |
| 心智 | `/mind` | ⤵️ 收进器官二级（左栏） |
| Soul | `/soul` | ⤵️ 收进器官二级（左栏） |
| Soma管理 | `/soma-admin` | ✅ 直挂保留 |
| 系统管理 | `/admin` | ✅ 直挂保留 |
| 权限管理 | `/permission` | ✅ 直挂保留 |
| 企业管理 | `/enterprise` | ✅ 直挂保留 |
| 会话管理 | `/sessions` | ✅ 直挂保留 |
| 开销 | `/spending` | ✅ 直挂保留 |
| 智能分析 | `/intelligence` | ✅ 直挂保留 |
| AI 引擎 | `/ai-engine` | ✅ 直挂保留 |
| 自愈系统 | `/healer` | ✅ 直挂保留 |
| 系统拓扑 | `/topology` | ✅ 直挂保留 |
| 组件注册表 | `/registry` | ✅ 直挂保留 |
| 轨迹 | `/trajectory` | ✅ 直挂保留 |
| 时间线 | `/timeline` | ✅ 直挂保留 |
| 版本日志 | `/changelog` | ✅ 直挂保留 |
| 文档中心 | `/docs` | ✅ 直挂保留 |
| 开发记录 | `/devlog` | ✅ 直挂保留 |
| 市场 | `/marketplace` | ✅ 直挂保留 |
| 监控 | `/monitoring` | 🆕 补一级直挂（用户点名，原为孤儿路由） |
| 商机管理 | `/plugins/business-opportunity` | 🔌 插件贡献一级位（安装长出/卸载收走） |

> 结论：**基线非器官项 0 缺失、0 降级**；仅 23 器官按用户要求收进二级；新增项=「监控」（用户点名）+ 插件贡献一级位。

## 三、器官二级菜单 = 25 器官一个不少（用户原话"器官里面为什么不全"）

「器官」一级入口（底栏 → `/body-map`）+ 左侧 organ-nav-sidebar 二级：

- 核心 8：Soul /soul、皮层 /cortex、神经 /nerve、血管 /vein、躯体 /soma、感官 /sense、意志 /will、伙伴 /mate
- 底座 8：免疫 /immune、体征 /vital、骨髓 /marrow、腺体 /gland、基因 /gene、回声 /echo、镜像 /mirror、突触 /link
- 高级 9：海马体 /hippo、反射 /reflex、遗传链 /heredity、巢穴 /nest、脉搏 /pulse、四肢 /limb、声带 /voice、视觉 /vision、心智 /mind

与 `body-map-client.tsx` ORGAN_LAYOUT 25 器官清单**逐项一致（25/25）**；渲染 DOM 实测 25/25 全在（桌面+移动）。
工具组 11 项（仪表盘/知识库/搜索/知识图谱/工作流/任务收发台/定时任务/技能市场/MCP客户端/工作区/学习）随原样保留。

## 四、插件菜单贡献（用户单独批准的部分，保留且实测有效）

- 清单驱动 L1：底栏一级位动态生成（实测：商机管理 `/plugins/business-opportunity` 一级位长出——join 目标 bidding 未安装时回退独立一级位）。
- 二级并入左栏分节（实测渲染：商机管理→「商机发现」、流程运行台→「流程运行台」、进化回流→「进化回流」）；join 并入目标插件时合节（如 商机发现→智能投标）。
- install-state / 市场装/卸 / plugin-spec 三件套（schema+validator+scaffold）不动。

## 五、验收（自报不可信 → 渲染 DOM + 真点击 + 截图 + vision 自查）

1. **渲染 DOM 逐项清点**（桌面 1440×900 + 移动 390×844）：顶栏 9/9、底栏 33+插件位、左栏 25 器官+11 工具+3 插件分节，与上表逐项 diff **零缺口**（menu-restore2-shots/ + nav-click-test.json）。
2. **真点击抽验 51 入口**（>20 要求）：50 PASS + `/mate` 301→/chat（next.config 有意重定向，上次恢复令维持清单）；逐个点击落地真实内容、0 404、0 空白（nav-click-test.json 逐条记录）。
3. **用户点名项专项**：`/download` 一级直挂可见+真点进（Download Engine/全部-进行中-已完成-错误/+新建下载/暂无下载任务，与改造前 `menu-audit-shots-restore/download.png` 控件逐项一致）；`/monitoring` 一级直挂可见+真点进（监控页：总器官数/平均响应/CPU/内存卡+系统仪表，无 404/空白）。
4. **截图**（每面展开态，docs/reports/menu-restore2-shots/）：desktop-bodymap-full / desktop-sidebar-bottom-plugins / bottom-bar-left|mid|right / mobile-bodymap / click-download-page / click-monitoring-page。
5. **vision 自查**：顶栏/底栏/左栏逐项可读、无叠字遮挡、无空白异常；左栏底部插件三分节清晰可见。
6. **构建**：`tsc -p tsconfig.json --noEmit` 0 错误 + `next build` 绿（BUILD_ID `5GHWtO4jGL_dON6kT0FAB`），生产服务已重启实测。

## 六、备注

- `/mate` 无页面、301→/chat 为维持清单既有约定（next.config 注释："Mate=OpenMate 进程本体非器官页，301 保留指向对话主入口"），菜单条目保留=点击可达不藏功能。
- `/dev-specs`、`/onboarding`、`/pipeline` 维持 520d2bca~1 前的入口状态（流水线=知识库导入面入口，2026-11-19 归位定稿），未回退。
- 底栏滑动面板顺序仍从 baseNavItems 单源派生（swipeable-panels 零改动，自动对齐）。
