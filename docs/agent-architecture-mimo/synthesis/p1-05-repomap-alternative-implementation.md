# P1-05 RepoMap 替代实现说明（检索式召回 + token 预算选符号）

> 对账项：`blueprint-51-audit.md` P1-05（RepoMap tree-sitter 图中心性选符号）
> 裁决来源：对账表「缺口处理」统筹裁决——**不排期硬造 tree-sitter 图中心性（过度设计）**，
> 以「检索式召回 + token 预算选符号」作为**替代实现**立项补文档（本文件即交付物）。
> 落盘：2026-10-12 周期#128。全部代码引用以当日仓库实况为准（`acp-proxy/`），已逐条 grep 实证（见 §7）。

## 1. 原案目标与替代裁决

**RepoMap 原案**（aider 式）：tree-sitter 解析全仓 → 符号定义/引用构图 → 图中心性（PageRank 类）
排序 → 把"最重要的 N 个符号"在 token 预算内注入上下文，让模型开改动前先有全仓地图。

**替代裁决**：原案的**目标**（有限上下文窗口内，把与当前任务最相关的代码符号/片段给到模型）
保留；**手段**（tree-sitter 全仓解析 + 调用图 + 中心性排序）不硬造。理由：

1. **过度设计**：SoulMate 的任务面以"检索到目标符号→按需读取"为主，全仓静态图的维护成本
   （多语言语法、增量重建、图退化）远超其在本产品任务分布上的收益；
2. **依赖与覆盖成本**：tree-sitter 需逐语言 grammar，本仓目标语言面（py/ts/tsx/js/go/rs/java…）
   会引入一等公民的解析依赖与持续维护面；
3. **现有替代面已覆盖目标场景**（下文 §3 逐件实证），差距（§4）如实声明，**用户可拍板要求原案**（§6）。

## 2. 替代实现总览（两个半边）

| 半边 | 语义 | 实装 |
|------|------|------|
| 检索式召回 | 不建全仓图，用检索命中"相关符号/片段" | `agent/session_recall.py`（会话史）、`search_files`（ripgrep/grep）、`agent/file_index.py`（符号边界分块 FTS 索引）、记忆/技能检索 |
| token 预算选符号 | 不预选"全仓 top-N 符号"，改为**按需分段取回 + 预算裁剪**：模型在 token 预算内逐段拉取目标符号段 | `agent/context_budget.py`（预算/裁剪/校准）、`agent/tool_output_handler.py` 溢出 spill + `read_file_segment` 分段读回 |

即：**"选符号"的操作化 = 检索定位符号边界 + 在 token 预算内按行段取回**，而非图中心性预排序。

## 3. 构成件实证（file:line 以 2026-10-12 仓库为准）

### 3.1 符号边界分块的即时索引 — `agent/file_index.py`（SessionFileIndex）

- `class SessionFileIndex`（L40）：SQLite **FTS5** 全文索引（`file_chunks_fts`），库在
  `~/.hermes/soulmate/file-index/index.db`；
- `_split_into_chunks()`（L199）：**符号边界分块**——在 `^(def |class |function |export |async def )`
  行（外加注释块/空行）处切块，块上限 2000 字符。这是**无 tree-sitter 的符号粒度近似**：
  正则识别定义行代替 AST，块内容即"符号段"；
- `search()`（L120）：FTS `MATCH` + `ORDER BY rank LIMIT ?`，FTS 失败降级 LIKE；命中块回传
  `SearchResult(content[:500], score=|rank|)`——**天然带内容预算**（500 字截取）；
- **接线实况（如实声明）**：写入侧已接线——`soulmate_agent.py` L137 `from agent.file_index import
  SessionFileIndex`、L366 构造、L5467 会话中提到的文件自动 `index_file()`；另
  `architecture_enhanced.py` L49/147/308 消费 `get_stats()`。但 **`search()/get_file_summary()/
  list_indexed_files()` 当前无消费点**（全仓 grep 仅定义处命中）——即索引**半接线**：
  召回主力不依赖它（见 3.2），它当前是"已建索引、检索面待接线"的状态。此项如实列为
  §4 差距，不粉饰为"已闭环"。

### 3.2 检索式召回（live path，真接线）

| 通道 | 实装 | 消费点 |
|------|------|--------|
| `search_files` 工具 | `soulmate_agent.py` L4304 `grep -rn -i --include=…`（多语言 include 集） | 模型工具面常驻（L2338 系统提示条目），按 pattern 召回代码行 |
| 会话史检索 | `agent/session_recall.py`：`SessionRecallEngine.search()`（L291）+ 评分族 `mask_of/words_of/approximate_title/_osa_distance`（词项覆盖+编辑距离，**纯检索式打分，无图**）+ `excerpt()` 摘录 + `rank()` 排序 | `search_chat_history` 工具 `execute_tool()`（L521），soulmate L1479-1493 lazy-init 消费（含跨 agent 二次授权） |
| 记忆召回 | `agent/memory_retrieval.py` 三因子评分（关联/时近/重要）| 系统提示"相关历史记忆"注入 |
| 技能检索 | `skill_manager.search_skills()`（触发词过滤防误配）| `_run_llm_with_tools` 技能注入 |

### 3.3 token 预算选符号/选段

| 件 | 实装 | 语义 |
|----|------|------|
| `agent/context_budget.py` | `TokenBudget`（L78）：`usable_input()`（输入限额优先，kilocode overflow.ts 语义）、`available_for_history`、`compression_trigger_at`（0.75 阈值）；`ManagedMessage.estimate_tokens()`（L123，中英混合估算）；`ContextBudgetManager.manage()`（L145）按预算裁剪 + `calibration_factor` 由 provider 回填 estimate_gap 校准（L140 注释）、`model_history_target()`（L207） | **预算真源**：可给历史/符号段的 token 上限、裁剪与压缩触发 |
| 工具输出溢出层 | `soulmate_agent._process_tool_output()`（L1241）：超阈值（`TOOL_SPILL_CHARS` 8000 / `TOOL_SPILL_LINES` 2000，L288）→ 全文落盘 + **stub 入 context**（`agent/tool_output_handler.py`，降级 `degraded_spill` L1266 仍全文可读回） | 大结果不挤爆预算，context 只留 ~2KB 指针 |
| 分段取回 | `read_file_segment` 工具（定义 L2515，系统提示 L2336）：按 `path/start_line/end_line` 读回溢出文件的指定行段 | **"token 预算选符号"的操作落点**：模型按 stub 线索只取目标符号段（def/class 行区），预算内取多少读多少，等价达成"选符号进上下文"而不预建全仓符号表 |

### 3.4 端到端工作方式（替代案如何达成 RepoMap 目标）

1. 任务进入 → 检索层定位相关符号/片段（`search_files` 定义行 pattern、`search_chat_history`、
   记忆/技能）；
2. 命中面大 → 溢出层把全文外置，context 只进 stub；
3. 模型按需 `read_file_segment` 在 **token 预算内**逐段取回目标符号段；
4. 预算压力由 `ContextBudgetManager` 统一裁剪/压缩（估算带校准因子）。

## 4. 与原案的对照与诚实差距

| RepoMap 原案能力 | 替代案达成情况 |
|------------------|----------------|
| 符号定义/引用提取 | ✅ 近似：正则符号边界分块（file_index）+ grep 定义行；非 AST 精确 |
| 全仓调用图 | ❌ 无（裁决不建）。跨文件影响分析靠检索命中 + 模型推理 |
| 图中心性选 top-N 符号 | ❌ 无。等价物=检索排序（FTS rank/词项覆盖评分）+ 预算内按需取段 |
| token 预算内注入 | ✅ `context_budget` + spill/stub + `read_file_segment` |
| 索引面 | ⚠️ 半接线：`SessionFileIndex` 写入已接线、`search()` 暂无消费点（§3.1）——如需 FTS 召回闭环，接线（如 search_files 的 FTS 先行召回）另立小切片 |

## 5. 已知边界（如实声明）

1. file_index FTS 检索**未接线**（半接线状态），本替代案的召回主力是 grep + 会话史/记忆/技能检索；
   若用户希望 FTS 召回闭环，可作为下批小切片（**待拍板**）；
2. `_split_into_chunks` 的 `line_start` 为近似值（`len(chunks) * 100`），仅当 chunk 内定位参考，
   精确行号以 `search_files`/`read_file_segment` 真源为准；
3. 符号识别是正则近似（如 Python 装饰器/嵌套定义、TS 重载签名不保证切在最佳边界）；
   需要精确符号图时按 §6 升级。

## 6. 若用户拍板要求原案的升级路径（不实施，仅留路标）

tree-sitter 作**可选依赖**（缺依赖自动回落本替代案）→ 逐语言增量解析（只解析检索命中文件而非全仓）
→ 定义/引用边构图 → 中心性排序 → 与 `read_file_segment` 同一取段出口按预算注入。
关键约束：原案不得替换替代案的降级路径（无 tree-sitter 环境必须照常工作）。

## 7. 验证记录（本文件引用的真实性自检，2026-10-12 周期#128）

| 断言 | 验证命令（acp-proxy/ 下） | 结果 |
|------|--------------------------|------|
| SessionFileIndex L40/L120/L199 | `grep -n "class SessionFileIndex\|def search\|def _split_into_chunks" agent/file_index.py` | 40/120/199 命中 ✅ |
| 符号边界正则 | `grep -n "def \|class \|function \|export \|async def " agent/file_index.py`（L212） | 命中 ✅ |
| 写入侧接线 | `grep -n "_file_index" agent/soulmate_agent.py` | L137/366/5467 命中 ✅ |
| search() 无消费点 | `grep -rn "file_index.search\|get_file_summary\|list_indexed_files" acp-proxy/` | 仅定义处=半接线 ✅（与 §3.1/§5 一致） |
| session_recall 评分族 | `grep -n "def mask_of\|def words_of\|def approximate_title\|def search\|def execute_tool" agent/session_recall.py` | 142/151/194/291/521 命中 ✅ |
| context_budget 预算件 | `grep -n "class TokenBudget\|def usable_input\|def manage\|def model_history_target" agent/context_budget.py` | 78（TokenBudget）/43（模块级 usable_input，TokenBudget.usable_input_tokens 属性 L96）/145/207 命中 ✅ |
| 溢出层+分段读回 | `grep -n "_process_tool_output\|read_file_segment\|TOOL_SPILL_CHARS" agent/soulmate_agent.py` | L1241/L2336,L2515/L289（阈值注释与 L1251 构造）命中 ✅ |

（grep 结果与本文 file:line 引用逐条一致；本文档为替代实现的实现说明交付物，不新增运行时代码。）
