# OpenMate 待办池盘点与优先级建议（next-sprints）

> 2026-11-17 · 依据：《取长补短清单》`docs/reports/pi-agent-yibiao-integration.md`（15 抄 + 3 不抄）、
> `ROADMAP.md`、`docs/plugin-system-design.md`。目的：给下一个/下几个 sprint 排序，按**性价比**（价值÷工作量）。
>
> 前置状态：Pi 清单已落 3 件（#7 防跑飞三件套、#12 task_complete 免总结、#3 三段式提交修复，
> commit `db137835`）；同日清理：demo 测试流清走、`demo-greeter` 样板归位 `examples/`、
> `DELETE /flows/{flow_id}` 落地（测试流可彻底清、面板零残留）。

---

## 一、Pi 取长补短剩余 12 项（性价比降序）

工作量：S=≤2 天，M=3–5 天，L=>5 天。归层：①能力=SoulMate 内核 ②知识=OpenSoul ③流程=插件/流程引擎。

| 序 | 原 # | 抄什么 | 归层 | 工作量 | 性价比 | 排序理由 / 依赖 |
|---|---|---|---|---|---|---|
| 1 | 15 | 依赖锁版本防御（精确锁版本 + 供应商错误文案兼容层） | ①（工程纪律） | S | ★★★★★ | 半天立规矩，零运行时风险；不先做，后面每项升级都可能被 Pi 小版本 breaking 偷袭。**建议第一个做** |
| 2 | 10 | 结构化人机对话工具（ask-user 2–5 选项 + report-failure 终止需确认） | ① | S–M | ★★★★★ | 直接治两个现行痛点：clarify 阻塞、Agent 私自放弃任务。通用工具一对，全插件受益 |
| 3 | 2 | 收尾边界（agent_before_settle 追加+continue / agent_settled 才算完） | ①+③ | M | ★★★★☆ | 与已落 #3 三段式修复天然成环：「产出→校验→不过退回同会话修复」缺的就是 settle 挂点 |
| 4 | 8 | 受保护文件基线（manifest+sha256 副本、settle 前还原） | ① | M | ★★★★☆ | 项目有文件截断/误改历史痛点；工作区工具纪律改造，风险低收益直接 |
| 5 | 6 | LLM 代理补三件事（错误归一 / 用量统计入账 / 请求排队） | ① | M | ★★★★☆ | 网关已定版，增量补三件；用量入账是成本可观测的地基，排队治高峰超时 |
| 6 | 1 | 钩子面（beforeToolCall/afterToolCall/finishTurn/prepareRequest/steering/followUp） | ① | L | ★★★☆☆ | 六类钩子是 #9、#5 的挂点；整批 terminate 语义已随 #12 落（summary_control），本项重点是钩子链（可 block/改参/覆盖结果） |
| 7 | 11 | json-validation 预置 Schema 工具（四阶段校验、路径越界校验、错误回传） | ①+③ | M | ★★★☆☆ | OpenBidKit 有现成模板（piJsonValidationTool.cjs），照抄功能；Schema 映射走插件任务配置 |
| 8 | 9 | 固定工具清单 + 阶段门禁（setAllowedTools 语义，拦截替代改清单） | ③+① | M | ★★★☆☆ | **依赖 #6 钩子面**（beforeToolCall 拦截）；清单恒定还省前缀缓存钱 |
| 9 | 14 | 子代理治理（并发乘数×2 超出排队 + 轮数上限统一施加 + runChildTask 沿用限制） | ③ | M | ★★☆☆☆ | 流程引擎并行节点的运行时基线；等并行节点真用起来价值才显形 |
| 10 | 5 | 阶段交接压缩（业务摘要提示词模板 + compaction_pending 断点补偿） | ①+③ | M–L | ★★☆☆☆ | 长流水线省钱显著，但依赖 compact() 支持自定义压缩指令，动面偏大 |
| 11 | 13 | 上下文文件单点注入（agentsFilesOverride：工作区约定/任务知识进系统提示词） | ② | M–L | ★★☆☆☆ | 跨仓：依赖 OpenSoul 取数接口（路线图 2.3）；知识层就绪前做会空转 |
| 12 | 4 | 会话 JSONL 树 + 持久任务状态机（append-only、id/parentId/fork） | ① | L | ★★☆☆☆ | 存储格式换骨，迁移成本最高；与 Pi 互通的收益未被验证，放最后观察 |

**明确不抄不变**（3 项）：无沙箱放权模型（必须 MCP 工具契约+权限声明+安装确认）、
正则匹配错误文案自动重试（用结构化错误码）、多文件手工同步 UI 注册。

### 建议切法
- **Sprint A（快赢+止血）**：序 1、2、4 —— 全是 S–M，三件套落地后对话侧体验与文件安全立竿见影。
- **Sprint B（闭环）**：序 3、5 —— settle 边界接上三段式修复成环；LLM 网关补账。
- **Sprint C（基建）**：序 6、7、8 —— 钩子面打底，门禁与 Schema 工具跟进。

---

## 二、HTML 面板层②（插件自定义面板运行时）

现状：`plugin.yaml ui.pages[].html` 字段与校验已预留（`yaml_manifest.py`，页面文件须存在），
运行时未实现（`plugin-system-design.md` §3）。

- **做**：① `html/` 静态文件只读服务；② iframe 沙箱壳 `sandbox="allow-scripts"`（同源受限）；
  ③ iframe↔宿主 postMessage 桥，把面板内的动作接到 MCP 工具（与 DSL 面同一审批闸）；④ 导航/清单与 DSL 页同形状长出。
- **为什么排这里**：DSL（层①）已覆盖 list/report/form 三类面板，层②是「长什么样」的自由度升级；
  不阻塞主线，但插件作者一旦要画复杂界面（招投标可视化、图表面板）就会顶到天花板。
- **建议**：Sprint C 之后插队做，工作量 M；验收用 `examples/` 再加一个 html 面板样板（不进运行时）。

---

## 三、插件市场（ROADMAP v1.0，2026-12）

现状：自我开发闭环（forge：草稿→dry-run→确认→安装）已落地；社区侧为零。

- **拆三步走**（别一口吃成 v1.0）：
  1. **插件打包/导入导出**（S）：目录→zip+清单校验+权限预览，安装复用 forge 确认链（生成≠安装铁律不变）。
  2. **本地/内网仓库**（M）：发布上传、检索、版本、卸载（补 ROADMAP 短期「Plugin 系统完善」的卸载/生命周期）。
  3. **社区市场**（L）：评分、签名信任链、沙箱审计——依赖 HTML 面板层②与权限模型定稿。
- **为什么现在只排第 1 步**：市场价值=插件供给量，供给量=插件好不好写；先把打包/安装摩擦降到零，
  比先建站更划算。

---

## 四、SoulMate 能力深化候选（Pi 清单之外）

| 候选 | 来源 | 说明 | 工作量 |
|---|---|---|---|
| Agent 自编程能力（读规范→生成代码→自检） | ROADMAP v0.5 | forge 已给雏形（draft+dry-run），深化=自检用例自动生成+失败自动修复（可挂 repair_loop） | L |
| 知识库集成（向量检索 + RAG） | ROADMAP v0.5 | 与 Pi #13 上下文文件注入同向，OpenSoul 侧先备好取数接口 | L |
| 多模型支持完善（OpenAI/Claude/DeepSeek/Qwen/Ollama） | ROADMAP v0.5 | 路由+降级策略；可与 LLM 代理（Pi #6）同一 sprint 顺手做 | M |
| 会话历史持久化（数据库存储） | ROADMAP v0.3 | 与 Pi #4 会话 JSONL 树同向——建议合并成一个存储方案再动 | M–L |
| 空转诊断的跨会话复用（诊断结论进长期记忆） | 已落 #7 延伸 | run_guard 诊断结论目前会话内有界；沉淀成教训库（lesson）可让后续会话避开同坑 | S |

---

*排序是建议不是圣旨：若用户侧出现新的高频痛点（如对话卡死、费用失控），把对应项直接提到 Sprint A。*

---

## 落地状态（扫尾回写）

- **序 9（Pi#14 子代理治理）✅** `53720b28`：SubagentGovernor（并发乘数×2 超出排队/统一轮数 100/runChildTask 沿用）
  + 流程引擎 parallel 节点运行时基线（分支=合成 tool 节点复用重试/软预算，失败隔离聚合 on_error）。
- **序 10（Pi#5 阶段交接压缩）✅** `5a1c24e6`：业务摘要提示词模板 + 自定义压缩指令（compress-handoff
  事件→set_compression_instructions）+ compaction_pending 断点补偿（失败置位→下轮 force 重压）。
- **序 11（Pi#13）**：等 OpenSoul 取数接口（路线图 2.3）对接，未动。
- **序 12（Pi#4 会话 JSONL 树）**：垫底观察，未动。
- **插件市场三步走**：step 1 `c90f29e7`、step 2 `8b464fdc`、step 3 最小可行 `818beac1`
  （HTTP 远端源拉取安装=仓库目录布局即协议；社区门户留接口不擅选外部服务）。
