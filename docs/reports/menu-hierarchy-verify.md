# 菜单层级重排 · 验收报告

> 2026-11-19 · 对照 `docs/reports/menu-hierarchy-map.md`（归属表）逐项验收。
> 铁律口径：恢复令"功能零删减（一个功能都不能丢）" + "只调层级归属"。
> 实测环境：`http://localhost:3000`（新构建 BUILD_ID `VDJUQ_r3d04m2X__QQ242`）+ acp-proxy :8092（acp-proxy-a 重启装载）。

---

## 一、入口点击实测（恢复令铁律）

- **94 个唯一入口逐个真点击全落地，0 失败**（一级位 13 次 + 二级 81 次；逐条记录
  `nav-click-test.json` 同款口径，`/tags` 一处为内容含"not found"字样误报，实为 10k 字符真页）。
- **64 基线路由（menu-audit-shots-restore/nav-click-test.json）覆盖 64/64，零失联**。
- 85 路由清点（含 /tasks /will 新增页 + 8 条死链 301）全归属，见归属表 §四。
- 对话域深层页（会话管理/伙伴）因"会话页专属侧栏不动"铁律无侧栏入口——**⌘K 补位**
  （新增 `nav-sessions`/`nav-mate` 两条命令，纯增量），点击落地 ✓。

## 二、一级/二级结构实测（DOM 断言 + vision 双查）

- **底栏一级位 = 9**（对话/AI 群组/知识管理/学习/自动化/器官/工作台/能力市场 + 插件贡献位）；
  **顶栏一级位 = 通知/进化/系统管理/设置 + ⌘K**；原 64+9 个一级入口全部归入二级（归属表逐行）。
- **逐域"左二级零一级项重复"断言全过**（organs/workbench/market/notify/evolution/system/settings 7 域
  自动断言 leaks=[]）；vision 复核 desktop-workflow / desktop-system：左二级=当前域子功能清单，
  无一级名目复读、无分组大标题。
- **零叠字**：desktop（workflow/system/plugins/body-map）+ mobile（workflow/knowledge）6 张截图
  vision 自查，无文字叠压/碰撞；移动端唯一截断为 /workflow 页自有工具条"+Ne"按钮（存量页内 UI，非菜单）。
- **会话页/AI 群组页专属侧栏不动**：/chat=会话树、/ai-groups=群组侧栏，逐项点击落地未改造。
- 截图证据：`docs/reports/menu-hierarchy-shots/`（6 张）。

## 三、⌘K 全量扫

- 89 行渲染（85 命令 + 4 页内按钮）：导航全量 + 快捷操作 + 16 主题命令 + 死链命令（恢复令保留原样）。
- 新增 2 条（会话管理/伙伴）补对话域深层页入口——**纯增量，零删改**。

## 四、插件贡献域全流程（智能投标范本）

| 步骤 | 结果 |
|---|---|
| 默认态 | 底栏无「智能投标」✓（`plugins/install-state.json` bidding=available；不装载不入清单） |
| 市场一键安装（/plugins 页 Install 真点击） | 底栏长出一级位「Bidding（智能投标）」✓（manifest 动态生成，零硬编码） |
| 一级点击 | → /plugins/bidding 域首页 ✓ |
| 全套二级真点可用 | L2 = 商机发现（business-opportunity join，position=start 首位）+ 招标文件解析/评分办法配置/标书生成工作台/废标风险审查报告/报价策略/知识库管理（manifest 顺序）——**7/7 点击落地** ✓ |
| 一键卸载（Uninstall→Confirm 真点击） | 一级菜单消失 ✓；「商机管理」回退独立一级位（join 目标缺席自立，同一功能只在一个层级） ✓ |
| 能力零删除 | 插件包 `plugins/bidding/` 全套页/工具/流程保留（卸载只收菜单/工具/流程） ✓ |
| 生成≠安装 | `confirmed=false` 只回菜单预览，菜单不长出（API 实测） ✓ |

## 五、插件规范（一等交付物）

- `docs/plugin-spec.md`（规范正文）+ `acp-proxy/plugin/plugin_spec.schema.json`（机器可读 schema）。
- 校验闸门 `spec_validator.py`：创建/安装双侧、字段路径报错；反例实测——坏 id / 缺 entry /
  function 指向不存在工具 / 缺 IO schema **全部拒收** ✓。
- 脚手架 `scaffold.py`：标准骨架一键生成（manifest+handler 桩+DSL 页+README），生成即过闸门，
  产出 DSL 过 `validate_dsl_page` ✓。SoulMate 自创建接口=纯函数 `generate_skeleton()`（预留）。
- 二级功能=工作流可调用单元契约落地：`ui.menu.entries[].function` → `tools[].name`（input_schema +
  output_schema 双侧必填，校验器把关）；安装后经 MCP 桥进流程工具面（既有 flow `tool` 节点直连）。

## 六、构建与提交

- `tsc -p tsconfig.json --noEmit` 0 错误；`next build` 绿（BUILD_ID 存在核验）。
- git 显式路径提交（runtime 数据/截图目录外的产物一并点名）。

## 七、已知边界（如实记录）

1. 对话域二级（会话管理/伙伴）在 /chat 不可见——**用户铁律"对话页不改造"所致**，入口=⌘K + /sessions 页二级，已在归属表注明。
2. legacy `plugin.json` 插件的 HTTP 路由随进程常驻（FastAPI 无法摘路由），卸载后由
   install-state 门禁 404 自惰（菜单/工具/流程即刻收走）——规范 §五 已注明。
3. 二级菜单与页内上下文导航（pageSidebar，如 /workflow 流程列表）在左侧同栏上下排布：
   二级=子功能清单（零一级重复）在上，上下文导航在下（"子项/上下文导航"双语义，功能零删减）。
