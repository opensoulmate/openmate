# Sprint C 候选盘点与开工方案（HTML 面板层② / 插件市场）

> 2026-11-17 · 承接《next-sprints.md》二、三节。目的：两项候选落地前盘点现状缺口、
> 定切法与验收，避免开工即返工。Sprint B（#6/#1/#11/#9）已完成，钩子面与治理
> 工具组是本两项的直接地基。

---

## 一、HTML 面板层②（插件自定义面板运行时）

### 现状盘点（以源码为准）

| 件 | 状态 | 证据 |
|---|---|---|
| 清单字段 `ui.pages[].html` | ✅ 已预留 | `plugin/yaml_manifest.py` PageSpec（dsl/html 二选一校验：「需且仅需声明 dsl 或 html 之一」，路径存在性校验已挂） |
| 层① DSL 面板运行时 | ✅ 已跑 | `plugin/ui_dsl.py` + `plugin/dsl_api.py`（list/report/form 三类面板） |
| 设计定版 | ✅ 已定 | `docs/plugin-system-design.md` §3：iframe `sandbox="allow-scripts"`（同源受限）、postMessage RPC（`{id, method, params}`→`{id, result\|error}`）、宿主 method 白名单=该插件 MCP 工具 + `ui.*` 事件、theme 令牌注入 CSS 变量 |
| html/ 静态只读服务 | ❌ 未实现 | 设计明言「PoC 不实现层②运行时」 |
| iframe 沙箱壳 | ❌ 未实现 | 前端无对应容器组件 |
| postMessage 桥（接 MCP 工具+审批闸） | ❌ 未实现 | — |
| 导航/清单与 DSL 页同形状长出 | ❌ 未实现 | `src/lib/plugin-nav.ts` 目前只读 plugin.json nav，不区分 dsl/html 页 |

### 开工切法（M，四步增量，每步可独立验收）

1. **html/ 静态只读服务**（S）：`plugin/routes.py` 增只读静态路由（插件目录内
   `html/` 白名单，路径越界拒绝——可直接复用 #11 `check_path_bounds` 的越界
   语义）；不进对话工具面。
2. **iframe 沙箱壳**（S）：前端面板容器组件，`sandbox="allow-scripts"`（不给
   allow-same-origin 之外能力），加载静态服务 URL。
3. **postMessage 桥**（M 核心）：请求 `{id, method, params}` → 响应
   `{id, result|error}`；**method 白名单=该插件声明的 MCP 工具 + `ui.*` 事件**
   （toast/刷新/插件 store 读写）；工具调用走既有 `plugin/mcp_bridge.py` +
   **同一审批闸**（与 DSL 面 `dsl_api.py` 完全同闸，不另建权限面）；
   theme 令牌 `postMessage('theme', tokens)` 注入 CSS 变量。
4. **导航长出**（S）：plugin-nav 对 dsl/html 页同形状注册（清单驱动，零手工
   同步 UI——「不抄多文件手工同步」不变）。

**验收**：`examples/` 再加一个 html 面板样板（如带按钮→调一个样板 MCP 工具的
最小面板），不进运行时；桥白名单穿透测试（未声明 method 必须拒绝）。

### 与 Sprint B 的接点（现成地基，勿重建）
- `plugin/hooks.py` 已接线（#1）：面板动作可先过 `before_tool_call` 钩子
  （审计/拦截）再达工具；
- `AllowedToolsGate.make_hook()`（#9）：阶段门禁可直接挂桥上；
- `json_validation`（#11）：面板产出 JSON 的验收器现成。

---

## 二、插件市场（ROADMAP v1.0 三步走）

### 现状盘点

| 件 | 状态 | 证据 |
|---|---|---|
| 自我开发闭环 forge | ✅ 已落 | `plugin/plugin_forge.py` + `plugin/forge_api.py`：草稿（spec 模板/LLM 自写）→ dry-run → **confirmed=true 才安装**（生成≠安装铁律）→ 丢弃 |
| 清单校验/装载 | ✅ 已落 | `yaml_manifest.py`（含 hooks/permissions 合法性校验）+ `scanner.py`/`loader.py`（装载/卸载/unregister_plugin_hooks 清理） |
| 插件打包/导入导出 | ❌ 为零 | 全仓无 zip/export/import 能力（只有 Python module import） |
| 本地/内网仓库、社区市场 | ❌ 为零 | — |

### 开工切法（按「先降摩擦再建站」排序不变）

1. **打包/导入导出**（S，先行）：插件目录→zip + 清单校验（复用
   `yaml_manifest` 全套校验）+ **权限预览**（hooks/permissions 白描给用户）；
   导入=解包→校验→**复用 forge 确认链安装**（同一 confirmed 闸，铁律不变）。
2. 本地/内网仓库（M）：发布上传、检索、版本、卸载（补 ROADMAP 生命周期）。
3. 社区市场（L）：评分、签名信任链、沙箱审计——依赖层②与权限模型定稿。

**验收（step 1）**：一个真实插件（如 examples/demo-greeter）打包→换机导入→
权限预览→确认安装→装载成功/拒绝路径各一测；zip 内路径越界（zip-slip）必拒。

### 与 Sprint B 的接点
- 错误归一/依赖锁（#15/#6）：市场侧将来接外部源时，供应商/网络错误走
  `error_codes` 结构化码；
- hooks 面（#1）：市场插件的 hooks 声明已受 `HOOK_POINTS` 校验约束。

---

## 三、建议排期

- **先做插件市场 step 1（S）**：摩擦归零比面板自由度更急（供给量=市场价值）。
- **HTML 面板层② 随后插队（M）**：四步切法可拆进 2 个快迭代；第 3 步（postMessage
  桥）是唯一硬骨头，先出桥白名单契约测试再写实现。
- 两项均延续铁律：纯 Python+既有栈、增量改码、git 显式路径提交、通用能力禁
  投标专用。

---

## 四、开工状态（Sprint C 落地回写）

| 件 | 状态 | 提交 | 备注 |
|---|---|---|---|
| 市场 step 1（zip 打包导入导出） | ✅ 已落 | `c90f29e7` | forge 闭环扩展 export_zip/preview_zip/install_zip；zip-slip 必拒矩阵（含符号链接/炸弹上限）；确认链复用逐字保留（confirmed=false 绝不落 plugins/<id>）；23 测试 |
| 层② step ① 静态只读服务 | ✅ 已落 | `4ac7a0c7` | GET /plugins/{id}/html/{path}，越界必拒，nosniff+no-cache |
| 层② step ③ postMessage 桥 | ✅ 已落 | `4ac7a0c7` | html_panel_rpc：白名单=声明工具+ui.*，白名单外必拒（穿透测试）；同一审批闸（approval_required→approved 重发）；/html-methods 契约面 |
| 层② step ② iframe 壳 | ✅ 已落 | `b56f2396` | HtmlPanelFrame：sandbox=allow-scripts、消息源防冒充、theme 注入；render-test 15/15 |
| 层② 验收样板 | ✅ 已落 | `b56f2396` | examples/html-panel-demo（按钮→add 工具+reset 审批闸演示）不进运行时，3 条样板 E2E |
| 层② step ④ 导航长出 | ✅ 已落 | `32e9ebff` | **动态插件页宿主**：PluginDslView 清单驱动（frontend.pages→dsl/html 页型分支，多页页签）+ derivePluginNavItems 页级菜单（装即逐页长出/卸即收走，legacy 回落不变）+ 页级路由 [plugin]/[page]；render-test 21/21 |
| 市场 step 2（本地仓库） | ✅ 已落 | `8b464fdc` | PluginRepo（发布/检索/版本/yank/装入，版本不可变）+ repo_api HTTP 面 + **卸载生命周期**（uninstall 镜像门禁 + hot_unloader + 路由组自惰）；顺手修白描真值 bug（approval!=never）；16 测试含 HTTP 全链 |
| 市场 step 3（社区） | 未动 | — | 按三步走顺序候场 |

*Sprint C 正式开工时按本盘点逐项对照，状态变化回写本文件。*
