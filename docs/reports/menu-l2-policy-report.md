# 左侧二级菜单修复·验收报告（器官二级仅器官上下文）

> 2026-11-20 · 报障（原话）："操你妈 为什么菜单点击的时候左侧都会默认跳出来25个组件的二级菜单"。
> 用户正解："**器官二级菜单只在「器官」一级入口激活时显示**（及器官详情页），其他页面左侧显示
> 各自的二级子菜单（该域有子功能时）或不显示侧栏/紧凑态（叶子页面如下载/监控），绝不能见谁都弹
> 25 器官"。参照三层架构原话："顶部菜单和底部菜单为一级菜单，左侧为二级菜单"——左侧=**当前一级的**
> 二级清单，不是全局常驻器官表。

## 一、病灶

2ea547ef 恢复时把 organ-nav-sidebar（25 器宫+工具+插件分节）作为 app-shell 的**默认兜底左栏**——
所有未注册 pageSidebar 的页面（会话管理/开销/系统拓扑/插件页等）左侧都弹出 25 器官表。

## 二、修复（左侧=当前一级的二级清单）

| 上下文 | 左侧显示 | 实现 |
|---|---|---|
| 「器官」一级入口（/body-map）+ 器官详情页（25 器官路由） | 器官二级菜单：**25 器官全集**（核心 8/底座 8/高级 9）+ 工具组 + 插件分节 | `organ-nav-sidebar.tsx`（+`isOrganRoute()` 导出），仅器官上下文挂载 |
| 页面自注册二级/紧凑导航（下载/监控/学习/知识库/工作流/进化等） | 页面各自的子功能清单（pageSidebar 原样） | app-shell 原 pageSidebar 分支 |
| 插件一级上下文（/plugins/<id>/*） | 该插件的二级条目（join 并入它的条目合节，如 商机发现→智能投标） | 新增 `context-l2-sidebar.tsx` `l2RowsFor()` |
| 内置域 join 条目（流程运行台→自动化域、进化回流→进化域） | 域内页面二级清单顶部「二级菜单」块 + 页面自注册导航 | 同上（`domainForPath` + `join` 解析） |
| 叶子页（会话管理/开销/系统拓扑/时间线/版本日志/系统管理/市场…） | **不显示侧栏**（整栏不渲染，内容全宽） | app-shell `hasSidebar` 门：无二级=无侧栏 |

顶栏/底栏/插件机制零改动（2ea547ef 的直挂形态原样）。

## 三、验收（逐页真点 + 渲染 DOM + 截图 + vision）

| 验收点 | 结果 | 证据 |
|---|---|---|
| 点下载→左侧不是器官表 | ✅ 左侧=下载自身紧凑导航（全部任务/种子搜索/设置），0 器官 | menu-l2-policy-shots/download-own-nav.png（vision 复核："完全没有出现 25 个器官/组件列表"） |
| 点器官→左侧 25 个全齐 | ✅ /body-map+/soul+/hippo+/pulse+/will 五路由 DOM 清点 25/25（missing=[]） | bodymap-organ25.png + menu_dom_audit 左栏策略 organ25×5 |
| 点学习→左侧是学习的子功能清单 | ✅ 生成新课程 + 课程列表 | learn-own-nav.png |
| 插件二级随插件上下文 | ✅ /plugins/business-opportunity/home 左侧=[商机发现]，无器官表 | plugin-l2.png |
| 域 join 条目 | ✅ /workflow 左侧「二级菜单：流程运行台」+ 流程导航；/evolution//timeline//changelog 带「进化回流」 | workflow-join-row.png |
| 叶子页不显示侧栏 | ✅ /sessions /spending /topology /admin /marketplace 整栏不渲染、内容全宽 | spending-no-sidebar.png |
| 绝不见谁都弹 25 器官 | ✅ 19 路由策略清点：非器官上下文 organ=0 全过 | menu_dom_audit.py 左栏策略 |

## 四、menu_dom_audit 基线口径同步更新（scripts/menu_dom_audit.py）

- 新增 `SIDEBAR_POLICY`（19 路由 × organ25/own-nav/plugin-l2/domain+join/leaf 期望）+
  `ORGAN_25`（25 器官 href 全集）+ `check_sidebar_policy()`（逐路由真打开、等水合稳定、
  侧栏 shell 内清点 hrefs）；铁律入脚本：**非器官上下文侧栏出现器官链接即 FAIL**。
- 基线重建（`--update-baseline`，docs/reports/menu-baseline/）：顶栏 9 / 底栏 31(role=user) /
  左栏 39（7 分节）/ 弹出菜单 3 + 覆盖55 + 左栏策略 19 条。
- 重跑比对：**GREEN ✓ 与基线逐项一致（0 差异 0 警告）· 覆盖55: 52/52（3 adminOnly 按角色跳过）·
  左栏策略 19 路由全过**（输出见运行日志，报告 JSON 存 last-run/）。

## 五、构建

`tsc -p tsconfig.json` 0 错误 + `next build` 绿（BUILD_ID `T5cIC0T1HB2FcmJe2lyk6`）；
openmate-web（systemd user 服务）自动重启后实测。
