# P1-代码工程能力：大仓库理解 + 测试驱动自主修复循环

> 2026-11-19 · 交付物：`agent/repo_index.py`、`agent/fix_loop.py`、
> `soulmate_agent.py` 接线（repo_index / fix_loop 双工具）、
> `tests/test_repo_index.py`、`tests/test_fix_loop.py`（23 用例全绿）

## 1. 大仓库理解（repo_index，AST 语义级）

纯 Python 自实现静态索引（SQLite 落 `acp-proxy/data/repo-index/<repo>.db`，
增量重建按 sha256 跳过未变文件）。回答"X 在哪实现 / 谁调 Y"靠索引，不把全仓塞上下文。

语义保证（对标 OMH LSP 环路 `lsp_definition/lsp_references` 的语义定位标准，
按 AST 归属而非字符串匹配）：

| 语义 | 规则 | 反例（不会误伤） |
|---|---|---|
| 别名 | `from m import f as g` 后 `g()` 归属 `m.f`；`import m as mm; mm.f()` 归属 `m.f` | — |
| 同名遮蔽 | 作用域链最近绑定优先：参数/局部赋值/lambda/推导式变量遮蔽模块级同名符号 | 函数参数 `save(4)` 不算模块函数 `save` 的引用 |
| 跨文件同名 | 引用归属到**定义所在文件**的符号键 `file:qualname` | `o.py` 的 `save()` 不算到 `m.py:save` 头上 |
| 类作用域 | 方法体内裸名跳过类作用域（Python 语义） | 方法里的裸 `x` 不算类属性 `C.x` |
| 属性归属 | `C.m()`（类）、`mm.f()`（模块）、`self.m()`（最近类） | `other_obj.m()` 解析不出→不猜（宁缺勿错） |
| self.X 类型线索 | `self.impl = Engine()` → `self.impl.start()` 归属 `Engine.start`；按类隔离 | 别的类同名 `self.impl = Fake()` 归属 `Fake.start` |
| 调用位 | 只有 Call 函数位算 caller | `print(save)` 不算调用 `save` |

工具面：`repo_index(action=build/def/refs/callers/file_map/stats)`。

真刀（本仓库 acp-proxy，1141 文件 / 16,285 符号 / 194,178 使用点，建索引 35.6s）：

```
find_def('_file_recall_dispatch')
  → agent/soulmate_agent.py:1989 SoulMateAgent._file_recall_dispatch
find_callers('_file_recall_dispatch')
  → agent/soulmate_agent.py:1731（code-mode 批内路由）
  → agent/soulmate_agent.py:6245（主循环路由）
find_callers('index_file', target_file='agent/file_index.py')
  → agent/soulmate_agent.py:1981  self._file_index.index_file(   ← self.X 类型线索
find_refs('SessionFileIndex')
  → architecture_enhanced.py:150 / soulmate_agent.py:407 / soulmate_agent.py:1998 / tests/test_file_index_recall.py:22
```

已知边界（显式声明）：动态派发（getattr/星号导入）不解析；实例属性若经
中间函数/容器赋值则类型线索断（宁缺勿错，报"解析不出"而非猜）；非 Python
语言只进文件地图不进符号图。

## 2. 测试驱动自主修复循环（fix_loop，四道验证闸门）

流程：**复现（红）→ 修 → 定向绿 → 回归全量 → 报告**。环内自校验，测试红不交付。

1. **复现闸**：复现测试必须先红——基线就绿 = 没有可修的 bug，拒伪修复。
2. **定向绿闸**：修复后复现测试仍红 → 打回；经 RepairLoop 三段式自动换策略
   （normal → change_strategy → minimum），stalled 超限 → 诚实 stop（禁死循环、禁假称完成）。
3. **回归闸**：定向绿后跑回归全量，**对照基线红名单**——新增红 = 修复破坏别处 → 回炉；
   预存红不阻塞但如实进报告。
4. **报告闸**：只有 1-3 全过才 `status=fixed`；状态被绕改也会被报告闸自查降级；
   失败路径出 `status=failed` 诚实报告（含失败原因/轮次/策略轨迹/复跑验收步骤）。

工具面：`fix_loop(action=start/check/status/report)`；状态持久化
`data/fix-loops/<id>.json` 可中断续跑；`TestDrivenFixLoop.run(fix_fn)` 全自主驱动。

### 真刀验收（真实仓库 ~/fixloop-lab，真实 pytest 子进程）

仓库：`textkit` 文本库（git 仓库，预存失败测试
`tests/test_frequency.py::test_most_common_respects_limit` 复现真 bug：
`most_common` 的 `ranked[: limit - 1]` off-by-one 少返回一个元素）。
基线：1 red（预存）+ 4 green。走完整循环（工具分发入口
`SoulMateAgent._fix_loop_dispatch` 实测）：

```
① start        → 复现闸通过：复现测试确认红（exit=1）。基线既有红 1 个。
② check 第1轮   → 错误修法（删 reverse=True）：定向绿闸拒绝（第1轮）：测试红不交付。
                  退回同会话修复（模式 normal）。
③ check 第2轮   → 正确修法（恢复排序 + [:limit]）：定向绿闸 + 回归闸通过（第2轮）。
④ report       → status=fixed，2 轮，策略轨迹 normal → normal，基线既有红 1 个照旧。
```

**用户可复跑验收步骤**（机器上现成可跑）：

```bash
# A. 复跑真刀验收（修复后的最终状态 + 全绿验证）
cd ~/fixloop-lab
python3 -m pytest -q tests/test_frequency.py::test_most_common_respects_limit   # 定向：绿
python3 -m pytest -q                                                            # 回归：5 passed
git log --oneline                                                               # 初始版本(带预存红测试) + 修复提交

# B. 从零重放完整循环（含拒伪/拒红闸门）——见 tests/test_fix_loop.py
cd ~/openmate/acp-proxy
python3 -m pytest -q tests/test_fix_loop.py tests/test_repo_index.py            # 23 passed

# C. 大仓库理解真刀（acp-proxy 自身 1141 文件）
cd ~/openmate/acp-proxy
python3 -c "
import sys; sys.path.insert(0, '.')
from agent.repo_index import RepoIndex
ri = RepoIndex('/home/climbing/openmate/acp-proxy',
               db_path='data/repo-index/acp-proxy.db')
ri.build()
for h in ri.find_callers('_file_recall_dispatch'): print(h.brief())"
```

其中 `tests/test_fix_loop.py::test_end_to_end_real_pytest` 就是"预存失败测试走
完整循环"的自动化重放（真 pytest 子进程：第 1 轮改错被拒 → 第 2 轮修复 → 双绿）。

## 3. 我们 vs Claude Code 式循环（能力对比）

| 能力 | Claude Code / RooCode 式自主修复循环 | SoulMate fix_loop（本次交付） |
|---|---|---|
| 复现前置 | 靠提示词约定/agent 自觉（常先改后测） | **程序闸门**：复现测试基线就绿直接拒修（拒伪修复） |
| 测试红不交付 | 靠 agent 自律 + 用户复核 | **确定性状态机**：红=check 拒绝，report 闸自查，绕改状态也出 failed |
| 回归全量 | 跑一遍看绿 | 跑一遍**对照基线红名单**：新增红打回、预存红不误伤、逐 id 对账 |
| 失败换策略 | LLM 自发换思路（不保证） | RepairLoop 三段式确定性升级：normal→change_strategy→minimum→stop |
| 死循环防护 | step/context 上限 | max_rounds + stalled 计数双上限，超限=诚实失败报告 |
| 自校验留痕 | 对话记录 | data/fix-loops/*.json 全轮次历史（复现输出/判定/策略轨迹），可断点续跑 |
| 交付物 | 改动 diff + 总结 | 结构化报告：结论/轮次/策略轨迹/基线预存红/**复跑验收步骤** |
| 代码理解（定位修复点） | 内置 LSP/语义搜索（业界天花板，依赖 LSP server） | 自实现 AST 语义索引：def/refs/callers、别名/同名不误伤、self.X 类型线索（零 LSP 依赖） |
| 已知差距（诚实） | 多语言 LSP、跨仓库、重命名重构 | 重命名重构未做；非 Python 仅文件地图；动态派发不解析；实例属性间接赋值线索断 |

对齐 OMH 结论（omh-deepdive vol1 §6 LSP 环路）：lsp_nav 的 `ScopeMismatch`/
`StaleFile`"编辑动作先过路径边界"哲学在 fix_loop 落为"测试红先过验证闸门"——
**先有证据（红测试/绿回归）再交付，程序兜底验收，agent 可以胡写但骗不过闸门**。

## 4. 变更清单

| 文件 | 内容 |
|---|---|
| `agent/repo_index.py` | 新增：AST 语义索引（符号/作用域/绑定/使用点），SQLite 增量落盘 |
| `agent/fix_loop.py` | 新增：测试驱动修复循环（四闸门 + RepairLoop 策略升级 + 状态持久化） |
| `agent/soulmate_agent.py` | 接线 ×5：code-mode 路由、dispatch 方法、系统提示、工具面 schema、主循环路由 |
| `tests/test_repo_index.py` | 14 用例：语义保证（别名/同名/属性/self.X）+ 增量 + 接线护栏 |
| `tests/test_fix_loop.py` | 9 用例：四闸门 + 自主驱动 + 持久化 + 真 pytest 端到端 + 工具面端到端 |

提交：`1c6fe2b6`（openmate，显式路径提交——同文件在途的兄弟路 hunks 未混入）；
验收仓库：`~/fixloop-lab` 两提交（初始版本含预存红测试 + fix 提交）。

## 5. 测试基线说明（如实报告）

本机环境装好 pytest/python-docx 后全量跑基线（214 个测试文件，约 3500+ 用例），
以下问题**与本路变更无关**（A/B 实证：失败集在本路提交前 0c9481e7 与本路提交
1c6fe2b6 上逐条 identical，零新增失败）：

1. **预存挂死 ×2**（同根因：`_SilentServer` 真 TCP 静默服务端上读超时不触发，
   `asyncio.run` 永不返回；文件在本路提交中零改动，机械上不可能由本路引入）：
   `tests/test_trajectory_recorder.py::test_read_timeout_visible_drop_and_no_retry`
   与 `::test_stats_reconcile_sent_ok_dropped`。跑全量时需 `--deselect` 这两个。
2. **预存失败**（环境相关/陈旧断言，19 条 failure/error 在 A/B 两侧完全一致）：
   `test_browser_tools.py` 全文件 8 条（浏览器 worker 环境）、
   `test_docgen_tools.py` xlsx 3 条、`test_deps_lock.py` 1 条、
   `test_output_guard.py` 2 条（`[:200]` 陈旧接线断言 vs 已演进的
   `EVIDENCE_PREVIEW_CHARS`，属其它在途路的演进，不在本路范围）、
   `test_flow_collab_e2e.py` 2 条 ERROR（real pulse 计时）等。

验证方法（可复跑）：对 `0c9481e7` 与 `1c6fe2b6` 各开干净 worktree 跑同一批测试，
`diff` 两侧 FAILED/ERROR 清单须为空。本路 23 个新增用例全绿，且在仅含本提交的
干净 worktree 复跑通过——提交自洽，不依赖任何兄弟路在途代码。

尾段 20 个测试文件（trajectory_rounds → ws_session_recovery）补跑：354 passed，
0 failed（仅 deselect 上述 2 个挂死项）。全量口径：3500+ 用例，除预存问题外全绿。
