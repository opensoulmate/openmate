# 菜单收敛整体回滚·恢复验收报告

> 2026-11-18 · 用户令："把原来的所有的功能都给我恢复回来"——整体回滚菜单收敛三提交
> （714f65c1 P0 / 79c8f7c5 / 195be878 P1）的菜单收敛部分，宁可乱但全、不要清爽但缺。
> 提交：`bfdedc24`（46 文件，显式路径）。此前"收进一级/二级"方案作废。

## 一、恢复清单（恢复 = 收敛前原样回归）

### 四套导航
| 导航面 | 收敛后（被藏） | 恢复后 |
|---|---|---|
| 底栏 | 8+更多=9 | **56 项原样**（Download/Discovery/Sessions/Spending/Devlog/Marketplace/KB Sharing/Capture/Tags/Body Map/Soma Admin/Admin/Permission/Enterprise/Intelligence/AI Engine/Healer/Topology/Registry/Trajectory/Timeline/Changelog/Docs/Pipeline/Cron/Graph/MCP… 全部回归）|
| 左栏 | 25 | **35 项原样**（Soul/Will/Mate/Vital/Dashboard/Cron/Heredity 回归）+ 任务收发台（新增页保留，1 项）|
| 顶栏 | 5 | **9 件原样**（指标/通知/仪表盘/**下载**/进化/插件/系统/⌘K/设置）|
| ⌘K | 53 | **82 行原样**（Groups/Team/Graph Builder/Will/Diagnostics/Benchmark/Activity 7 条死链命令 + Reflex/Pulse/Nest + 16 主题命令全部回归）|

### 路由/组件（P1 迁入 5 大 hub 后删除的全部复活为活页）
- 23 个 page.tsx 复活：ai-engine / changelog / cron / dashboard / dev-specs / devlog /
  enterprise / healer / heredity / intelligence / marketplace / metrics / monitoring /
  permission / pipeline / registry / soma-admin / soul / tags / timeline / topology /
  trajectory / vital
- 7 个旧组件复活：dashboard-client / enterprise-client / permission-client /
  registry-client / soma-admin-client / topology-client / trajectory-client（+ evolution-timeline）
- system/admin/evolution/workflow/knowledge/docs/skills 撤销 tab 化吸收，恢复收敛前页面
- next.config 撤销 30 条 hub 吸收 301（活页不再被重定向遮蔽）；删除 evolution-overview.tsx

## 二、维持清单（仅保留，不得连带回滚）
- /plugins 数据源 :8090→:8092 真 bug 修复（plugins-client）
- /knowledge Bearer 鉴权真 bug 修复（knowledge-client）
- reflex / pulse / nest 页面（含 83085a76 观察面对接真数据）
- /tasks 任务收发台新增页 + 左栏入口
- 死链 301 保留 7 条（/mate /groups /team /graph-builder /diagnostics /benchmark
  /activity → 真实归宿；清死链是用户认可的收敛本意，点击可达、不藏功能）
- /will 不设重定向：兄弟任务已补建意志器官真页面（flow_engine 观察窗），重定向会遮蔽活页

## 三、验收（对照改造前 85 图）
- **路由全量**：85/85 通过——77 活页 200 + 8 死链 308 到归宿，0 断链
- **入口点击实测**：64 个唯一入口（底栏 56 ∪ 左栏 ∪ 顶栏）逐个点击，全部落地真实内容、无 404
  （逐条记录 nav-click-test.json）；/metrics、/tags 两处为内容里的"404/not found"字样误报，实为真页
- **⌘K**：82 命令行渲染，Will/Groups/Team/Graph Builder/Diagnostics/Benchmark/Activity/主题命令均在
- **截图对照**：docs/reports/menu-audit-shots-restore/（download 与改造前 download.png 逐项一致：
  Download Engine / +New Download / All-Active-Done-Error / No download tasks / 底栏标签序列一致）
- **构建**：tsc 0 错误 + next build 过（bfdedc24 提交内容）；openmate-web 已重启并实测

## 四、并行任务交接（不在本提交）
- `src/app/(app)/will/`（意志观察页）与 src/locales/* 为兄弟任务在途产物，由其自行提交。
  will-client.tsx 曾有 1 处 await-in-setState 语法错误阻塞全树构建，已最小修复（await 提到 setState 前）。
- body-map-client.tsx 于 11:57 被兄弟任务整体重写（器官门户改版，+399/-202），其 5 处 TS2322
  为该重写在途状态，留给兄弟任务收口；不影响 bfdedc24 的构建绿。
