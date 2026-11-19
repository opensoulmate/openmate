# OpenMate 插件规范（plugin-spec v1）

> 2026-11-19 · 一等交付物（用户原话："插件一定要设计好规范，这样才不容易创建出错。"）
> 机器可读规范：`acp-proxy/plugin/plugin_spec.schema.json`（JSON Schema）
> 校验闸门：`acp-proxy/plugin/spec_validator.py`（创建/安装双侧，字段路径报错）
> 脚手架：`acp-proxy/plugin/scaffold.py`（标准骨架一键起插件，生成即过闸门）
> 范本插件：`plugins/bidding`（智能投标——菜单贡献 + 默认未安装 + 市场装/卸全流程）

---

## 〇、定位与铁律

插件 = **特定功能域的可安装单元**（用户原话："像智能投标这种特定功能应该是以插件形式存在，
默认是不安装的，谁使用谁安装。"）。铁律：

1. **声明式**：名称/图标/一级二级菜单/路由/能力/权限/版本全部写在 `plugin.yaml` 清单里；
   前端菜单、页面、工具从清单长出，**宿主零硬编码菜单项**。
2. **菜单贡献是动态生成**（用户原话："插件安装之后生成一级菜单及二级菜单。"）：
   安装动作触发生效（运行时渲染进导航），卸载即移除；同一插件多二级项按 manifest 顺序渲染。
3. **二级功能=工作流可调用单元**（用户原话："创建完成后通过创建工作流可以将插件内的二级功能关联起来。"）：
   每个二级条目声明 function id + 输入/输出 schema，安装后自动注册进流程引擎工具/节点面
   （复用既有 flow tool 注册机制，不另立）；工作流画布可把多个二级功能串成链。
4. **生成≠安装**：草稿/脚手架只产文件；装入必须走确认链（`confirmed=true`），未确认只回预览。
5. **能力零删除**（归位不砍杀）：卸载只收走菜单/工具/流程，插件包目录保留，市场可装可卸。
6. **多语言强制**：菜单标签支持 `label_i18n: {zh, en, ja}`（缺失回落 `label`）。

---

## 一、目录结构（脚手架产出同形）

```
plugins/<id>/
  plugin.yaml          # 唯一清单入口（legacy plugin.json 仅存量兼容）
  main.py              # entry 模块：工具 handler（对话与 UI 共用同一 handler）
  ui/<page>.json       # UI DSL 页面（form | list | report）
  html/                # 可选：自定义 HTML 面板（层②，postMessage RPC）
  flows/               # 可选：流程定义（安装后注册进流程引擎）
  README.md
```

id = 目录名（`^[a-z][a-z0-9-]{1,63}$`），version = `x.y.z`。

## 二、清单字段（全量见 plugin_spec.schema.json）

```yaml
id: bidding                  # 必填；=目录名
name: 招投标工作台             # 必填
version: 0.1.0               # 必填
description: 一句话功能描述
author: soulmate
entry: main.py               # 必填；插件目录内相对路径
permissions: [ui.serve, tool.register, tool.call, data.read, data.write,
              knowledge.read, export.write]      # 白名单外=拒收
ui:
  menu:                      # ★ 菜单贡献（见 §三）
    label: 智能投标
    label_i18n: {zh: 智能投标, en: Bidding, ja: 入札}
    icon: FileText           # PascalCase lucide 图标名
    position: end            # start | end | after:<href>
    entries: [...]           # 二级条目清单（按声明顺序渲染）
  pages:                     # 页面清单（dsl 与 html 二选一）
    - id: parse
      title: 招标文件解析
      dsl: ui/parse.json
tools:                       # ★ 工具 = 工作流可调用单元（见 §四）
  - name: bid_parse
    description: 解析招标文件→结构化关键信息
    handler: handle_bid_parse          # entry 模块内函数名
    permissions: [tool.call, data.read, knowledge.read]
    approval: never                    # never | on-first-use | always
    input_schema:  {type: object, properties: {...}}   # 必填
    output_schema: {type: object, properties: {...}}   # 被 menu.function 引用时必填
flows: []                    # 流程定义文件（相对路径）
```

## 三、菜单契约（一级功能域 + 二级清单）

- `ui.menu.label` = **一级菜单名（功能域名）**；`entries` = **二级清单**（子功能页），按声明顺序渲染。
- 每条 entry：`id` / `label`(+`label_i18n`) / `href`（应用内绝对路由）/ `function`（可选，见 §四）/ `position`。
- **join 机制**：`ui.menu.join: <目标域>` 把本插件的二级条目并入既有功能域
  （内置域 id：chat/knowledge/automation/evolution/system/... 或插件 id）；
  `position: start` 条目渲染在目标域清单最前。目标缺席（如目标插件未安装）→
  **回退独立一级位**（同一功能只在一个层级出现一次）。
- 范本：
  - `plugins/bidding`：一级「智能投标」+ 6 个二级（parse/scoring/generate/review/price/knowledge）；
  - `plugins/business-opportunity`：`join: bidding` + 二级「商机发现」（bidding 未安装时回退一级位「商机管理」）；
  - `plugins/flow-console`：`join: automation`；`plugins/gene_loop`：`join: evolution`。

## 四、二级功能 = 工作流可调用单元（IO 契约）

- 每个二级条目可声明 `function: <tools[].name>`——该工具即此二级功能的**工作流单元**。
- 契约：工具必须声明 `input_schema` + `output_schema`（JSON Schema 双侧齐备）；
  校验器不合规=拒收（`ui.menu.entries[i].function 的工具 'x' 缺 IO schema`）。
- 安装后工具经 MCP 桥注册（全名 `<plugin-id>__<tool-name>`），**自动进流程引擎工具/节点面**
  （flow `tool` 节点直连，既有机制接上不另立）；工作流画布把多个二级功能串链即业务流水线
  （如 商机发现→招标文件解析→标书生成 一条流跑完）。
- UI DSL 的 `submit.tool` / `source.tool` 引用同一全名（对话与 UI 共用同一 handler）。

## 五、生命周期（默认未安装，谁使用谁安装）

| 阶段 | 行为 | 接口 |
|---|---|---|
| 打包 | 插件包落在 `plugins/<id>/`（能力零删除，全量页/工具/流程保留） | — |
| 默认态 | `plugins/install-state.json` 显式 `false` = 可用未安装：不装载、不入 /plugins、菜单不长出 | — |
| 市场预览 | `GET /plugins/market`（包清单 + 安装态 + 菜单预览） | 市场页 |
| 安装 | `POST /plugins/market/{id}/install`（`confirmed=true`）：安装态翻转 + 热装载（工具进桥、页面/路由注册、流程进引擎、**L1/L2 菜单即刻长出**） | 市场页一键安装 |
| 卸载 | `POST /plugins/market/{id}/uninstall`（`confirmed=true`）：热卸载 + 安装态翻转（**菜单/工具/流程即刻收走**，包保留） | 市场页一键卸载 |

> 状态文件缺省语义：未列出的插件 = 已安装（存量零迁移）。
> legacy `plugin.json` 插件同门禁（装载/清单/菜单全走 install-state）。

## 六、校验闸门（创建/安装双侧）

- **创建侧**（SoulMate 生成草稿时）：`validate_manifest_dict(data)`——只查声明（文件可尚不存在）。
- **安装侧**（scanner 装载 / 市场安装）：`load_yaml_manifest(dir)` 全量（声明 + entry/页面/流程文件存在性）。
- 报错带**具体字段位**（如 `ui.menu.entries[1].function 未声明为工具: 'report_run'`），拒收并可自动修正。

```bash
# 校验插件目录（安装侧）
cd ~/openmate/acp-proxy && python -m plugin.spec_validator ../plugins/bidding
# 校验 manifest 字典（创建侧，SoulMate 流用）
python -m plugin.spec_validator --dict manifest.json
```

## 七、脚手架（防手写出错）

```bash
cd ~/openmate/acp-proxy
python -m plugin.scaffold demo-greeter --name "示例插件" --label "示例域" \
  --entries greet:问候面板 report:汇报面板
# → plugins/demo-greeter/{plugin.yaml, main.py, ui/*.json, README.md}，生成即过校验闸门
```

产出即合规：每条二级条目自带 function 工具（含 IO schema）+ DSL 页面 + handler 桩。
**SoulMate 自创建接口**（预留，创建对话流等 spec 落地后接续）：
`plugin.scaffold.generate_skeleton(id, name=..., label=..., join=..., entries=[...])`（纯函数）
→ `plugin.spec_validator.validate_manifest_dict()` → `plugin.forge_api`（draft→dry-run→confirmed install）。

## 八、验收清单（改插件规范必过）

1. `python -m plugin.spec_validator` 全绿（含反例：坏 id / 缺 entry / function 指向不存在工具 / 缺 IO schema 全部拒收）。
2. 脚手架产物：校验通过 + DSL 页面 `validate_dsl_page` 通过 + handler 可执行。
3. 默认态菜单无「智能投标」→ 市场安装 → 一级菜单出现且全套二级真点可用 → 卸载 → 菜单消失。
4. 二级功能可被流程引擎 `tool` 节点调用（`<plugin-id>__<tool>`）。
