# OpenMate 插件系统设计

> 2026-11-17 · 状态：设计定稿（PoC 同步落地）。对标依据见 `docs/reports/openbidkit-yibiao-benchmark.md`。
> 2026-11-17 · 运行时落地：§5 流程引擎（`plugin/flow_def.py`/`flow_engine.py`/`flow_api.py`）+ 宿主运行面板（`plugins/flow-console/`，UI DSL list/report 实时页）+ §7 自我开发闭环（`plugin/plugin_forge.py`/`forge_api.py` + SoulMate `plugin_dev`）已实现并端到端验证（示例流程 `plugins/demo-greeter/flows/daily-report.yaml`：定时触发→调工具→人工审批→出报告）。工具级审批 token 语义：流程实例过人工审批闸后放行 approval 类工具。
>
> 第一原则「所想即所见，所见即所得」：用户对 SoulMate 说需求 → 生成可见子系统（菜单+页面+后台+流程）→ 沉淀为常驻入口。插件是"沉淀"的载体。

---

## 0. 总览：三层插件能力

| 层 | 内容 | 渲染/执行方 | 适用 |
|---|---|---|---|
| ① 声明式 UI DSL | JSON 清单描述面板（表单/列表/报告） | **宿主原生组件渲染**，设计令牌统一皮肤 | 80% 的管理类界面 |
| ② 自定义 HTML 面板 | 插件自带 HTML/JS | iframe / web component 沙箱 + postMessage RPC | 强交互/可视化/游戏化界面 |
| ③ 后端能力 = MCP 工具 | Python handler + JSON Schema | MCP 工具注册桥 | **UI 与对话共用**（同一工具，面板按钮调它，SoulMate 对话也调它） |

流程引擎横切三层：流程定义（JSON/YAML）以"工具节点"调用 ③，以"人工审批节点"回到 ① 的表单面板，以"可观测面板"展示运行态。

目录布局（PoC 现状 + 目标）：

```
plugins/<plugin-id>/
  plugin.yaml          # 清单（唯一必需）——id/版本/权限/导航/页面/工具/流程声明
  main.py              # 工具 handler + 可选 register_routes(app)（FastAPI 增量路由）
  ui/*.json            # UI DSL 页面定义
  html/*.html          # ② 自定义面板（可选）
  flows/*.yaml         # 流程定义（可选）
  tests/               # 插件自带测试
acp-proxy/plugin/      # 插件系统宿主侧（装载器/DSL 校验/MCP 桥/HTTP 面）
  yaml_manifest.py     # plugin.yaml 解析与校验
  scanner.py           # 目录扫描 → 描述符 + 导航/路由注册表
  ui_dsl.py            # UI DSL schema 校验
  mcp_bridge.py        # MCP 工具注册桥（注册/列取/调用/审批闸）
  dsl_api.py           # FastAPI 路由工厂：/plugins、/plugins/{id}/ui/{page}、/plugins/{id}/tools/{tool}
src/lib/plugin-ui-dsl.ts           # 前端 DSL 类型 + 归一化
src/components/plugin-ui/          # DSL 渲染器（宿主组件映射）
```

---

## 1. 插件装载器

### 1.1 plugin.yaml 规范

```yaml
id: demo-greeter               # ^[a-z][a-z0-9-]{1,63}$，目录名必须一致
name: 问候演示
version: 0.1.0                 # 语义化版本
description: 一句话描述
author: soulmate
entry: main.py                 # Python 入口（相对插件目录）
permissions:                   # 只允许声明 KNOWN_PERMISSIONS（见 §6）
  - ui.serve
  - tool.register
ui:
  nav:                         # 菜单由此长出，无需改任何前端文件
    label: 问候演示
    icon: Sparkles             # lucide 图标名，宿主 ICON_MAP 解析
    position: "end"            # start | end | after:<href>
  pages:
    - id: home
      title: 问候工作台
      dsl: ui/home.json        # ① DSL 页面
    - id: fancy
      title: 炫彩面板
      html: html/fancy.html    # ② 自定义面板（沙箱 iframe）
tools:                         # ③ MCP 工具声明（handler 在 entry 模块内）
  - name: greet
    description: 生成一条问候语
    handler: handle_greet      # entry 模块内的函数名
    permissions: [tool.call]
    approval: never            # never | on-first-use | always（见 §6）
    input_schema:
      type: object
      properties:
        name: { type: string, description: 问候对象 }
        style: { type: string, enum: [formal, casual] }
      required: [name]
flows: [flows/daily-greet.yaml]   # 流程定义（见 §5）
```

**装载流程**：`scanner.scan_plugins(plugins_dir)` 逐目录：
1. 校验目录名 == `id`；解析 `plugin.yaml`（PyYAML），字段级校验（id 正则、semver、permissions 白名单、position 语法、icon 合法）；
2. 解析成功 → `PluginDescriptor`（清单+路径+状态），解析失败 → 记入 `errors` 不中断其它插件加载；
3. `dsl_api.build_plugin_router(descriptor, tool_registry)` 生成 FastAPI 子路由并由宿主 `include_router`（**路由注册**：`GET /plugins/{id}/ui/{page}` 供 DSL、`POST /plugins/{id}/tools/{tool}` 供 UI 调工具、`GET /plugins` 汇总清单）；
4. **导航注册**：清单 `ui.nav` 进导航注册表 → `GET /plugins` 响应里带 `manifest`，前端 `plugin-nav.ts` 既有逻辑自动把菜单项 merge 进底部导航（position 语义已存在，不改前端）。

**与既有系统的兼容**：现存 `plugin.json` + `__init__.py::register_routes`（bidding、gene_loop）继续由 `app.py::load_plugins` 原路径加载；`plugin.yaml` 是新规范，装载走 `plugin/scanner.py`。两者并存于 `/plugins` 列表（scanner 输出补入 app.py 的 `load_plugins` 与 `/plugins`，属增量粘合）。旧 `plugin/loader.py`（模块路径式 PluginLoader + hooks）保留用于 hook 型插件；新插件优先 yaml 清单式。

**生命周期**：`discovered → validated → registered → enabled/disabled → uninstalled`。安装（解压到 plugins/）**必须用户确认**，装载器不下载、不自动安装任何插件（铁律）。

### 1.2 增量注册约定

- 插件增删**不改宿主代码**：菜单、页面、工具全部从清单长出。
- 插件想要非标 API：entry 模块导出 `register_routes(app)`（FastAPI `APIRouter`），装载器调用；但**跨插件数据访问一律走 MCP 工具**，不允许 import 其它插件模块。
- 严格增量：宿主只新增文件 + 对 `app.py`/`[plugin]/page.tsx` 做最小粘合补丁；禁全量重写。

---

## 2. UI DSL 规范（层①）

设计目标：**JSON 清单 → 宿主原生渲染**。插件不写一行前端代码也能得到专业皮肤（低饱和配色、超椭圆设计语言、选中项无下划线——即宿主 `globals.css` 设计令牌 + `ui/` 组件的既有外观）。

### 2.1 页面结构

```jsonc
{
  "version": 1,
  "page": { "title": "问候工作台", "description": "可选" },
  "panels": [ /* Panel，按序纵向排布；后续版本支持 grid 布局 */ ]
}
```

### 2.2 Panel 类型（最小集：三种）

**form — 表单面板**：数据录入/工具触发。

```jsonc
{
  "type": "form", "id": "greet-form", "title": "生成问候",
  "fields": [
    { "name": "name", "label": "问候对象", "widget": "text", "required": true, "placeholder": "输入姓名" },
    { "name": "style", "label": "风格", "widget": "select", "default": "formal",
      "options": [ {"value": "formal", "label": "正式"}, {"value": "casual", "label": "随意"} ] }
  ],
  "submit": { "label": "生成", "tool": "demo-greeter__greet", "target_panel": "greet-result" }
  // submit.tool = MCP 工具名；结果注入 target_panel（list/report 的数据源绑定）
}
```
widget 最小集：`text | textarea | number | select | checkbox | date`。

**list — 列表面板**：表格/记录流。

```jsonc
{
  "type": "list", "id": "greet-log", "title": "问候记录",
  "columns": [ {"key": "time", "label": "时间"}, {"key": "greeting", "label": "内容"} ],
  "source": { "tool": "demo-greeter__greet_log", "args": {} },  // 数据来自 MCP 工具
  "empty_text": "暂无记录"
}
```

**report — 报告面板**：结构化结论。

```jsonc
{
  "type": "report", "id": "greet-result", "title": "结果",
  "sections": [
    { "kind": "kv", "title": "概览", "items": [ {"label": "条数", "value": 3} ] },
    { "kind": "text", "title": "说明", "content": "…" },
    { "kind": "bars", "title": "分布", "items": [ {"label": "正式", "value": 2} ] }
  ]
}
```

### 2.3 渲染约定

- 渲染器 `src/components/plugin-ui/panel-renderer.tsx` 把 panel type 映射到宿主组件（form→`ui/input`+`ui/button`，list→原生表格样式，report→`ui/badge`/`ui/separator`），皮肤全部来自设计令牌，插件无法注入样式（防皮肤破坏）。
- DSL 只描述"是什么"，不描述"长什么样"。需要"长什么样"时走层②。
- 校验双端：宿主装载时 `plugin/ui_dsl.py` 拒绝非法 DSL（不加载该页面）；前端 `plugin-ui-dsl.ts` 归一化 + 兜底渲染未知字段（前向兼容）。
- **数据绑定唯一来源是 MCP 工具**：panel 的 `source`/`submit.tool` 都是工具名——保证面板可做的事，对话也能做。

---

## 3. 自定义 HTML 面板（层②）

- 载体：iframe（默认）或 web component 壳，`sandbox="allow-scripts"`（同源受限），内容来自插件 `html/` 目录，只读静态文件。
- 通信：postMessage RPC（请求 `{id, method, params}` → 响应 `{id, result|error}`）。宿主暴露的 method 白名单 = 该插件声明的 MCP 工具 + `ui.*` 事件（toast、刷新面板、读写插件 store）。
- 设计令牌通过 `postMessage('theme', tokens)` 注入 CSS 变量，保证与宿主皮肤一致（低饱和/超椭圆）。
- 沙箱边界：HTML 面板**不能**直接 fetch 宿主 API、不能碰其它插件数据；一切能力走 RPC → MCP 工具（权限闸在工具层，一处把关）。
- PoC 不实现层②运行时，仅在清单/装载器预留 `ui.pages[].html` 字段与校验。

---

## 4. MCP 工具注册桥（层③）

**定位**：插件的后端能力统一注册为 MCP 工具——SoulMate 对话可调（function calling），UI 面板可调（HTTP `POST /plugins/{id}/tools/{tool}`），流程引擎可调（工具节点）。一份 handler 三处用。

### 4.1 契约

- **命名**：MCP 工具名 = `<plugin-id>__<tool-name>`（`[a-zA-Z0-9_-]`，双下划线分隔命名空间）。
- **声明**：plugin.yaml `tools[]`（name/description/handler/approval/input_schema）。
- **handler 签名**：`def handler(args: dict, ctx: ToolContext) -> dict`（可为 async）。`ctx` 提供：插件 store（`plugin-data/<id>/`）、logger、`ctx.request_approval(reason)`（见 §6）。
- **返回**：dict → JSON 序列化为 MCP `tools/call` 的 `content[{type:"text"}]`；异常 → `isError: true` + 错误文本。

### 4.2 桥的职责（`plugin/mcp_bridge.py`）

1. **注册**：扫描产物的 tools 声明 → 加载 handler（importlib 按文件路径载入 entry 模块）→ `PluginToolRegistry.register()`。
2. **列取**：`list_tools()` 输出 MCP `tools/list` 形状（name/description/inputSchema），可直接喂给 SoulMate 的 function-calling 工具表或外部 MCP client。
3. **调用**：`call_tool(name, args)` 做：命名解析 → 参数按 input_schema 校验（缺必填即拒）→ 权限/审批闸 → handler 执行 → 结果包装。
4. **卸载**：插件卸载/禁用时注销其全部工具（对话与 UI 同步失去能力，不留悬空工具）。

> 现有 `mcp-client/registry.py`（外部 MCP Server 连接池）不受影响：插件工具是**进程内虚拟 server**，后续如需对外暴露，注册桥可实现 `tools/list`+`tools/call` 两个方法冒充一个本地 MCP server 接入既有连接池。

---

## 5. 流程引擎定义格式

声明式 JSON/YAML：**触发器 → 节点（含工具/判断/循环/人工审批）→ 动作**，带可观测面板与失败重试。定义文件放 `plugins/<id>/flows/*.yaml`，清单 `flows:` 声明。

```yaml
id: daily-greet
name: 每日问候流
version: 1
triggers:
  - type: cron            # cron | event | manual
    cron: "0 9 * * 1-5"
  - type: manual          # 面板按钮/对话里"跑一下"
nodes:
  - id: fetch-need
    type: tool
    tool: demo-greeter__greet_log   # MCP 工具名
    args: { limit: 5 }
    retry: { max: 3, backoff: exponential, base_seconds: 2 }
    timeout_seconds: 60
  - id: need-check
    type: decision
    when: "$.fetch-need.count > 0"   # 表达式只读上游节点输出（jsonpath 子集）
    then: compose
    else: finish
  - id: compose
    type: loop
    over: "$.fetch-need.items"
    body: send-one
  - id: send-one
    type: tool
    tool: demo-greeter__greet
    args: { name: "$item.name", style: formal }
  - id: confirm
    type: approval        # 人工审批闸：暂停流程，等用户在表单面板批准/驳回
    form: home            # 引用 UI DSL 页面 id（form 面板）
    timeout_hours: 72
    on_timeout: fail
  - id: finish
    type: action
    actions:
      - type: notify      # notify | emit-event | update-panel
        channel: in-app
        message: "每日问候完成"
observability:
  panel: flow-run         # 宿主统一渲染流程运行面板（节点状态/耗时/重试/输入输出摘要）
  retain_runs: 50
```

语义要点：

- **节点输出寻址** `$.<node-id>.<field>`；`$item` 为 loop 当前项。表达式只允许取值/比较/布尔组合，**不允许任意代码**（安全+可观测）。
- **失败重试**在节点级声明（max/backoff/timeout）；重试耗尽 → 按节点 `on_error: fail | skip | goto:<node>` 处理，流程实例状态落库（SQLite/JSON 文件），支持断点续跑（对标 OpenBidKit 任务恢复）。
- **人工审批节点**：流程挂起 + 生成审批记录（谁/何时/什么上下文），用户在 DSL 表单面板批准/驳回后恢复；超时策略可配。
- **可观测面板**为宿主内建（不需插件写 UI）：按流程实例列出节点状态（pending/running/ok/failed/waiting-approval）、重试次数、耗时、输出摘要——与 Agent 轨迹面板同一设计语言。
- 引擎运行时（第二期）：与 `workflow`/`pipeline` 既有页面的关系在实现时对齐；本设计先定**定义格式**，保证流程定义可移植。

---

## 6. 权限 / 审批闸

### 6.1 权限模型

- 清单 `permissions` 白名单声明，装载时校验（未知权限 → 拒绝加载）。在既有 `plugin/models.py::KNOWN_PERMISSIONS` 基础上扩展（增量）：
  - `ui.serve`（提供 DSL/HTML 面板）、`tool.register`（注册 MCP 工具）、`tool.call`（调用其它工具）、`flow.register`（注册流程）、`data.read`/`data.write`（自有插件数据目录）、`knowledge.read`（知识库检索）、`export.write`（导出文件）、`notify.send`。
- 运行时最小授权：工具 handler 只拿 `ToolContext`，ctx 的每个方法内部再查权限（声明 ≠ 全给）。
- 跨插件隔离：无相互 import、无共享存储；交互只能通过 MCP 工具（对方的权限声明管对方）。

### 6.2 审批闸

- **安装确认（铁律）**：任何插件安装/升级必须用户显式确认（列清单 diff + 权限列表 + 来源）。SoulMate 生成的插件也一样——生成≠安装。
- **工具级审批**：工具声明 `approval: never | on-first-use | always`。`always`/触发时，桥挂起调用并产生审批请求（含调用方、参数摘要、风险说明），用户批准后放行（token 一次性）。对标 OpenBidKit 的"完全可信插件"模式，这是 OpenMate 与它最大的安全差异点——因为我们的插件会被对话 Agent 调用。
- **流程审批节点**：见 §5，供业务性人工把关（区别于安全审批）。

---

## 7. 自我开发闭环（SoulMate 自产插件）

产品第一原则的落点：用户说"我需要一个招投标功能"→ SoulMate 生成子系统 → 沉淀为常驻入口。

```
用户需求
  → SoulMate 生成插件草稿（plugin.yaml + main.py + ui/*.json + flows/*.yaml，落在 plugins/<id>/）
  → 自检：装载器 dry-run（清单/DSL 校验 + 测试跑通）+ 单测结果附在草稿里
  → 用户确认安装（看清单 diff、权限、截图级页面预览——DSL 可静态渲染预览）
  → 装载：菜单长出来 + 页面渲染 + 工具进对话
  → 沉淀：插件目录进 git（显式路径提交），成为常驻入口；后续对话可增量改该插件
```

约束：SoulMate 生成代码遵守本仓库铁律（增量改码、Python+TS、显式路径提交、py 测试用 `~/opensoul/.venv/bin/python`）。生成的插件必须自带 tests/，dry-run 不过不能提交确认。

---

## 8. 招投标插件规格（第二路实现本体，此处定规格）

> 资产来源：`~/.hermes/skills/productivity/bidding/`（5 子技能 + 21 份参考文档 + 3 脚本）与 bidding-* 子技能；9450 份招标文件知识库在 `~/Downloads/招标文件学习/`。对标补充见 benchmark 报告（OpenBidKit 流水线/查重/废标检查 + BidMaster 21 项检查）。

### 8.1 菜单与页面（UI DSL）

| 菜单 | 页面 id | 面板 | 数据/工具 |
|---|---|---|---|
| 招投标 | projects | list（项目列表）+ form（新建项目） | `bid__project_list/create` |
| | analysis | report（招标分析：评分办法/技术参数/废标条款/品牌推荐/竞争分析/负偏离风险） | `bid__analyze_tender` |
| | bid-doc | form（生成配置）+ list（章节树）+ report（得分点覆盖） | `bid__generate_outline/chapter` |
| | review | report（审查报告 🔴🟡🟢）+ list（偏离表核验/证明材料） | `bid__review_document` |
| | dedup | list（重复组）+ report（元数据取证/结构相似度） | `bid__dedup_check` |
| | flow-run | 宿主内建流程面板 | 投标流程实例 |

### 8.2 MCP 工具（对话与 UI 共用）

| 工具 | 职责 | 内化自 |
|---|---|---|
| `bid__analyze_tender` | 解析招标文件→关键信息/评分办法/废标条款/无效投标项（"明确提到 vs 经验补充"分界） | bidding-analysis + OpenBidKit 废标提取 prompt 边界 |
| `bid__score_estimate` | 按评分办法预估得分（综合评分法权重/价格分公式：最低价法/平均价法/线性插值） | references/scoring-methodology.md |
| `bid__generate_outline` | 三级提纲，覆盖全部评分得分点 | bidding-bid-preparation |
| `bid__generate_chapter` | 逐章生成/扩写（流式），知识库引用注入 | 同上 + OpenBidKit 双轨验收 |
| `bid__review_document` | 10 步审查：废标风险/偏离表/证明材料/封标清单 | bidding-review + checklists.md + sealing-checklist.md（112 项） |
| `bid__negative_deviation` | ★/▲/一般条款统计与负偏离分析 | references/negative-deviation-analysis.md |
| `bid__compliance_check` | 评审因素合规（5 原则 + 29 种禁止情形） | references/evaluation-factors-compliance.md |
| `bid__dedup_check` | 标书查重（元数据取证+结构对比+句子 n-gram，确定性算法） | 对标报告 §四 |
| `bid__export_word` | Word 导出（TOC 域/横向表格/多级编号） | plugins/bidding/exporter.py 既有实现 |
| `bid__draft_challenge` | 质疑函/投诉生成 | bidding-challenge |

### 8.3 知识与流程

- **知识库**：9450 份招标文件 → 入库流水线（解析/分块~500 字/条目抽取/目录匹配），检索用混合方案（向量+BM25+RRF，见对标 §六）；评分办法案例（real-world-scoring-examples.md）作为 few-shot 池。
- **流程模板**（flows/）：`tender-to-bid`（解析→提纲→逐章生成→审查→导出，含人工审批节点：提纲确认、终稿确认）；`daily-bid-monitor`（cron 抓标讯→筛选→通知）。
- **规则即数据**：审查清单/废标条款/评分公式落 YAML 规则文件（插件数据目录），用户可增改——规避对标项目"阈值硬编码在 3000 行单文件"的坑。

### 8.4 分期

1. **第二路（本期后）**：插件本体 —— 清单+工具+三个 DSL 页面（analysis/review/dedup）。
2. 之后：流程模板、知识库接入、查重算法移植（确定性三层）、Word 导出增强。
