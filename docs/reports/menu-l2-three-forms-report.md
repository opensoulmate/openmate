# 左栏三形态定稿·验收报告（join 块拆除 + 器官栏恰 25 行 + 统一布局）

> 2026-11-20 · 用户令（原话）：
> ①"你在leftpanel里面加什么二级菜单 流程运行台"——**join/拼接进左栏的条目一个不留，全删**；
> ②"左侧为二级菜单"=当前一级自己的二级清单，**不许发明任何"拼接块/二级菜单块"结构**，
>   仅此三种：器官页=25 器官、各域页=该域原生子功能（下载/学习等原有 sidebar）、叶子页=无侧栏；
> ③"还有左侧菜单栏不要什么工具，那些一级菜单的东西为什么要在二级菜单再重复放一遍"——
>   **器官栏 11 工具全清，25 器官一个不多一个不少**；工具移出后的归属落位钉住不丢；
> ④"把25个组建的布局都调整一下，整个布局风格要保持一致"——**左栏统一布局模板**
>   （同宽、同分组样式、同间距、同滚动行为），不许各页各画一套。

## 一、拆了什么（我方发明物，全部拆除）

| 发明物 | 处置 |
|---|---|
| 域 join 条目块（流程运行台→/workflow、进化回流→/evolution /timeline /changelog） | **全删**（context-l2-sidebar.tsx 整文件删除；「二级菜单」拼接块结构废除） |
| 插件上下文「二级菜单」块（/plugins/<id>/* 左栏商机发现等） | **全删**（左栏三形态不含插件块） |
| 器官栏尾部插件贡献分节（商机发现/流程运行台/进化回流） | **全删** |
| 器官栏 11 工具组（仪表盘/知识库/搜索/知识图谱/工作流/任务收发台/定时任务/技能市场/MCP客户端/工作区/学习） | **全清**（一级菜单的东西不在二级重复） |
| 器官栏健康计数头（🟢/🔴 计数+刷新钮） | 清（零其他内容；行内状态圆点保留=行装饰） |

## 二、左栏只许三形态 + 统一布局

| 形态 | 页面 | 左栏 |
|---|---|---|
| ① organ25 | /body-map + 25 器官页 | **恰 25 器官行**（核心 8/底座 8/高级 9），零工具零其他内容 |
| ② own-nav | /download /monitoring /learn /knowledge /workflow /evolution 等有原生 sidebar 的域页 | 页面原生子功能（pageSidebar 原样） |
| ③ leaf | /sessions /spending /topology /timeline /changelog /admin /marketplace /plugins/* 等 | 无侧栏（整栏不渲染，内容全宽） |

统一布局模板（`src/components/l2-layout.tsx`）：L2_SHELL（同滚动容器）/ L2_LIST（同间距）/
L2_GROUP（同分组标题）/ L2_ROW（同行样式），器官栏与各域页左栏共用同一套 token；
25 器官行含 4 字功能说明+健康圆点（行内装饰）。实测各页左栏**同宽 256px**（6 页核对）。

## 三、11 工具归属落位（"归属落位要钉住不丢"）

| 工具 | 落位 |
|---|---|
| 仪表盘 /dashboard | 顶栏一级直挂（原有） |
| 知识库 /knowledge、知识图谱 /graph、工作流 /workflow、定时任务 /cron、技能市场 /skills、MCP客户端 /mcp、工作区 /workspace、学习 /learn | 底栏一级直挂（原有） |
| **搜索 /search、任务收发台 /tasks** | **底栏一级直挂（本次补挂**——工具=一级菜单的东西，从二级移出后落一级） |

插件条目零删减：插件二级面（左栏 join 块）废除后按 plugin-spec「目标缺席回退独立一级位」
落**底栏动态一级项**（= 520d2bca 前"插件动态项"原样）：智能投标/商机管理、流程运行台
(/plugins/flow-console/home)、进化回流 (/plugins/gene-loop) 实测均在底栏直挂。

## 四、验收（真点 + 渲染 DOM + 截图，1440×900）

| 验收点 | 结果 | 证据 |
|---|---|---|
| 器官页左栏**数出来正好 25 行**（/body-map /soul /hippo） | ✅ rows=25，missing=[] extra=[]（一个不多一个不少） | menu-l2-policy-shots/organ-25-rows.png |
| /workflow /evolution /timeline /changelog **无流程运行台/进化回流** | ✅ /workflow /evolution 左栏 0 条 join 内容；/timeline /changelog 无侧栏 | no-join-{workflow,evolution,timeline,changelog}.png |
| **各页左栏样式一致**（同宽同容器） | ✅ 6 页左栏均 256px（统一布局"同宽"），同滚动容器/间距 token | unified-download.png / unified-learn.png |
| join/拼接内容全局禁出 | ✅ 19 路由扫描「流程运行台/进化回流/商机发现/二级菜单块」左栏 0 命中 | menu_dom_audit 左栏策略 |
| 零删减（工具落位+插件条目） | ✅ 11 工具逐一钉住；底栏 38 项含 /search /tasks + 插件动态项 | bottombar-tools-fallback.png |

## 五、menu_dom_audit 基线同步（scripts/menu_dom_audit.py）

- 口径改三形态（organ25=恰 25 行集合全等 / own-nav / leaf）；**删 domain+join、plugin-l2 口径**；
  L2_BANNED_STRINGS（流程运行台/进化回流/商机发现/拼接块）任何左栏禁出=FAIL；
  统一布局"同宽"断言（各侧栏宽度差 ≤4px）；**TOOLS_11 归属落位钉住**（顶/底栏逐一核对）。
- 基线重建后重跑比对：**GREEN ✓ 0 差异 0 警告**（覆盖55: 52/52 + 工具落位 11/11 +
  左栏策略 19 路由；顶栏 9 / 底栏 35 / 左栏 25）。

## 六、构建

tsc 0 错误 + next build 绿（BUILD_ID `sC4U53tkE_6Qdx_85AVJ-`），openmate-web 服务重启后实测。
