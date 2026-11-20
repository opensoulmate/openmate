# AML 抄作业落地报告：P0×5 + P1×6 → OpenSoul 记忆系统（抄前 vs 抄后 vs 榜单头部）

> 用户定调（原话）："我的目的不是参赛，而是向他们学习。"
> 归属铁律（2026-11-20 用户纠偏原话）："记忆系统应该是opensoul你别搞错了。"——记忆系统唯一正主=OpenSoul（`opensoul/src/hippo` 族），P0/P1 全部落 opensoul；acp-proxy 侧不养平行记忆系统，镜像已收敛（§六）。
> 研究出处：`aml-memory-study.md` §5 抄作业清单（21 项可抄 + 不抄清单 4 项）；深读源码克隆 `/home/climbing/aml-study/repos/`。
> 基准证据：`evidence/aml-bench-before-2026-11-20.txt` / `evidence/aml-bench-after-2026-11-20.txt`（本机 ollama bge-m3 实测，命令见 §七）。

---

## 一、抄作业清单逐项落地（P0 五项 + P1 六项，逐项标注出处）

| # | 机制 | 出处（哪个系统·哪条机制） | 落点（opensoul） | 实现契约 |
|---|---|---|---|---|
| P0-1 | 邻居轮候选 | **Resurface** METHODS.md §2 邻居扩窗（本地 ruler 实测金证据命中 74.9%→78.8%）+ starwingMem 打包扩轮 + mnemotree `neighbor_chunks` | `src/hippo/memory_hybrid.py` `hybrid_retrieve()` 邻居段 | 命中点同会话（session_id/source_session）前后轮次作独立候选进融合：分=max(既有, 种子×0.5)；种子=top-3 命中；item 带 `neighbors_of` + reason 含 `neighbor` |
| P0-2 | 意图加权 RRF + 日期通道 | **khipu** 五通道·意图权重（temporal/latest/aggregate/who/default）+ 五通道·日期通道 | 同上 `classify_intent()/intent_weights()` | 确定性正则分类；temporal→keyword×1.3/literal×1.3 + 日期通道激活（绝对日期强度 2、相对时间词 1）；default=既有 2.0/1.0 逐字（回归钉保护） |
| P0-3 | 实体+字面守卫通道 | **khipu** 五通道·字面守卫（人名/数字/日期逐字命中直接放行置顶）+ 实体通道（人名→全部片段） | 同上 `literal_anchors()/entity_hints()/_entity_in_content()` | 空白独立段（2-3 字中文段/数字/编号/日期）+ 段内敬称人名（X工/X经理）为锚点，逐字命中→literal 通道（W=2.5）置顶；别名（小X/老X/阿X）→姓氏实体通道（W=1.5，剔除「周三/周围」假名） |
| P0-4 | 确定性时间注解 + 未来降权 | **starwingMem** `timeannot.py`（"yesterday [i.e. 21 February 2023]" 确定性注解）+ future ×1/4 消融 | 同上 `annotate_relative_time()` + `render_pack()` | 相对时间（昨天/上周X/上个月/去年…）以记忆自身 created_at 为参照解析绝对日期（khipu 纪律：绝不看服务器时钟），item 带 `time_annotation`、渲染层后缀「（昨天→2026-11-19）」；未来时间戳记忆分数 ×0.25（`FUTURE_PENALTY`，now 可注入=确定性） |
| P0-5 | rerank 候选池 | **khipu** gte-rerank-v2 前 200 候选（答案轮 top10 79%→91%）；**starwingMem 反例**（消融端到端持平→不用） | 同上 `rerank_fn`/`rerank_pool=200` | 可选通道**缺省关**（消融定去留——starwingMem 纪律）；rerank_fn(q, contents)→scores，只动前 200 名，失败保持 RRF 序且 reason 可见 |
| P1-6 | 写入幂等回执 | **Resurface** `store.py` `(space_id,batch_id)` 幂等 / **mnemotree** chunk+source+回执单事务 / **khipu** `(user_id,request_id)` | `src/hippo/long_term_memory.py` `store(request_id, client_id)` + `memory_write_receipt` 表 | 同 request 重复写=重放回执不双计不重审计；入库回执与记忆**同事务**落库（单事务），拒绝路径独立落回执（decision=rejected_gate/rejected_tags/merged） |
| P1-7 | 配置指纹拒启 | **InsightMem** `coding_meta`（model\|dimensions\|endpoint\|normalization 身份入库，不匹配拒绝启动）/ **Resurface** `write_settings.py` / InvMem embedding 身份 | `src/hippo/memory_vector_index.py` `MemoryVectorIndex(fingerprint=...)` + `MemoryVectorConfigMismatch` | embedding 指纹（model+dims）首次入库；不匹配=拒启并要求显式重建（`allow_rebuild=True`=清表换指纹显式迁移，绝不静默混排两个向量空间）；`_embedding_fingerprint()` 由 settings 取身份 |
| P1-8 | 打包 trace | **mnemotree** GUIDE `recall.trace`（每候选各通道 rank、packing losses、耗时） | `hybrid_retrieve()` `pack.trace` + `to_dict()["trace"]` | 每候选各通道 rank（semantic/keyword/literal/entity/date）+ 打包决策（included/over_budget 含 loss_chars/query_side_ineligible）+ 未来降权清单 + 邻居映射 + rerank 状态 + elapsed_ms；**不进 Answer**（render_pack 不排） |
| P1-9 | 查询侧过滤 | **InsightMem** architecture.md §Search（纯过程叙述只做「不可入选」标记，留在 BM25 语料保住 rank 贡献，过滤只发生在 query 侧） | 同上 `narration_reason()` + pack 阶段 | 应答/过程叙述（"好的"/"让我先试试"…保守正则）→excluded reason=`query_side_ineligible` 但**通道排名保留**（trace.channel_ranks 可见），语料不删不改 |
| P1-10 | 采纳审计（recall utility） | **Rolling-Memory** `internal/hooks/efficacy.py`（24h 实测 46 召回 41 采纳=89.1% 确定性事后审计） | `long_term_memory.py` `memory_recall_adoption` 表 + `mark_recall/audit_adoption/adoption_report` | 每次 recall_pack 自动留痕（recall_id + 逐条 rank，adopted=-1 待审）；`audit_adoption(recall_id, 后续工具调用/回复文本)` 确定性判定（≥12 字逐字片段命中或 token 覆盖≥0.5）；`adoption_report()` 出采纳率账本 |
| P1-11 | 派生记录逐字锚点 | **mnemotree** `ingest/atoms.py`（派生 atom 的引用必须是原文可 `find` 的逐字引文，找不到偏移即拒绝） | `memory_hybrid.verbatim_anchor()/verify_derived_anchors()` + `long_term_memory.store_derived()` | 派生摘要/事实条目必须携带 quotes 逐字 find 于 source_texts 且偏移可复核；否则 DERIVED_ANCHOR_REJECT 审计 + 拒绝入库（防幻觉引文）；通过者锚点随 `metadata["derived_anchors"]` 落库 |

> 批次边界（如实）：研究 P1 清单还有 **12 契约宽容输入**（面向参赛 Add API 前置层）与 **13 single-flight 并发**（embedding 并发闸）不属本轮六项，留待后续拍。

## 二、基准：扩集 22 条（8 非字面 + 5 时间类 + 5 实体类 + 4 别名类）抄前 vs 抄后

验收脚本：`opensoul/scripts/e2e_memory_semantic_recall.py`（两轮：轮A 回归钉=原 8 条×原 8 行库；轮B=22 条扩集库，hit@3/MRR@3 分档）。真实 bge-m3。

| 轮 | 档 | 抄前 hit@3 / MRR@3 | 抄后 hit@3 / MRR@3 | 变化 |
|---|---|---|---|---|
| 轮A 回归钉（防退化） | 非字面 8×8 | **8/8 = 100% / 0.875** | **8/8 = 100% / 0.875** | ✅ 不退化（验收硬门） |
| 轮B 扩集 | 总 22 条 | 20/22 = 91% / **0.811** | **21/22 = 95%** / 0.803 | hit@3 **+1**；MRR 基本持平 |
| 轮B | 非字面 8 | 7/8 / 0.729 | 7/8 / 0.708 | 残差同一条（见下） |
| 轮B | 时间类 5 | 4/5 / 0.700 | **5/5** / 0.700 | ✅ 日期通道救回「上线变更是哪天做」 |
| 轮B | 实体类 5 | 5/5 / 1.000 | 5/5 / 0.900 | 「320」查询两行逐字并列，名次互换（仍 hit@3） |
| 轮B | 别名类 4 | 4/4 / 0.875 | **4/4 / 1.000** | ✅ 实体通道使小王/老周/阿赵/海康全 rank-1 |
| 轮B | 字面基线 | 0/22 = 0% | 0/22 = 0% | （对照不动） |

- **残差（如实）**：「上次说的上线发布方式是什么」（gold=蓝绿部署策略）在 22 行库被 T1 事实「灰度发布定在 2026-11-18…」的「发布」语义撞车挤出 top-3——dense 通道语义混淆，抬手=khipu 的 gte-rerank cross-encoder 重排；`rerank_fn` 工程面已就位（P0-5），缺省关待消融（starwingMem 纪律）。
- **MRR 小幅回落（如实）**：字面守卫把「320」两行并列置顶（gold 与干扰行同为逐字命中），名次由 memory_id 定序——保 hit@3、rank-1/2 互换。属机制的固有并列行为，非退化。
- 留证：`evidence/aml-bench-before-2026-11-20.txt`、`evidence/aml-bench-after-2026-11-20.txt`（逐查询 ✓/✗ + rr@3 全量）。

## 三、对比表：抄前 vs 抄后 vs 榜单头部

| 维度 | 榜单头部（出处） | 我们抄前 | 我们抄后 | 判定 |
|---|---|---|---|---|
| 检索通道 | 2-5 路：BM25+向量底 + sparse/实体/字面/日期（starwingMem/khipu） | 语义+关键词 2 路 RRF | **5 路**：semantic+keyword+literal+entity+date（khipu 五通道形） | 追平头部通道形 |
| 通道加权 | 意图加权（khipu 五意图） | 固定 2.0/1.0 | **意图加权**（temporal/latest/aggregate/who/default） | 追平（khipu） |
| 会话/邻居结构 | 邻居轮扩展（Resurface +3.9pt/四家都有） | 无 | **邻居轮候选**（种子×0.5，neighbors_of 可观测） | 追平 |
| 时间处理 | 相对→绝对注解 + future 降权（starwingMem）、日期通道（khipu） | recency/decay 因子 | **确定性时间注解 + future ×0.25 + 日期通道** | 追平 |
| rerank | gte-rerank top200（khipu 79%→91%）；starwingMem 消融后不用 | 无 | rerank_fn 可选（缺省关，消融定夺） | 工程面追平，模型待接 |
| 打包 trace | 候选/通道 rank/打包损失/耗时（mnemotree） | over_budget 留痕 | **+ 全量 pack.trace**（rank/损失/耗时/邻居/意图） | 追平+我们原有逐条 reason 仍领先 |
| 写入幂等 | (space_id,batch_id)/(user_id,request_id)+单事务（全员） | 无显式回执 | **幂等回执同事务** | 追平 |
| 配置指纹 | 不匹配拒启（Resurface/InsightMem/InvMem） | hash 失效自动重算 | **指纹拒启+显式迁移** | 追平 |
| 查询侧过滤 | 过程叙述不可入选、rank 贡献保留（InsightMem） | 无 | **query_side_ineligible** | 追平 |
| 采纳审计 | 确定性事后审计 89.1%（Rolling-Memory） | 无 | **memory_recall_adoption 账本** | 追平 |
| 派生逐字锚点 | quote find 不到即拒（mnemotree atoms） | 无 | **store_derived 严格门** | 追平 |
| 写入决策审计 | 头部皆无三岔 | **三岔全带理由** | 不变 | **我们领先** |
| 指令形/凭据防护 | 无专门机制 | **deferred_review+类凭据两层拒收** | 不变 | **我们领先** |
| 召回包可观测 | trace 无逐条排除理由 | **逐条 reason+degraded 诚实** | +trace 双全 | **我们领先** |
| CJK | 头部英文向量+事后补二元组 | **bge-m3+CJK bigram 原生** | 不变 | **我们领先** |
| 版本化/回滚 | 皆无 | **memory_versions rollback** | 不变 | **我们领先** |
| 平台分数 | C2 六榜实测（MemoraX 69.26 / Resurface 61.22） | 未参赛 | 未参赛 | 差距未知（定调不参赛） |

## 四、不抄清单（4 项保持不抄，逐项理由）

1. **khipu「规则片段注入」通道**（祈使句进 prompt）：与我们 deferred_review「记忆≠注入指令」立场相逆；虽其仅在任务请求+小配额下无害，抄它等于给记忆系统开指令注入侧门。**不抄**。
2. **starwingMem 三赛道自动识别**（按内容形态猜赛道）：那是「同一 Key 接口不可改」的赛场特化；产品里是隐式魔法、行为不可预测。**不抄**。
3. **Rolling-Memory 纯词法路线**（反 dense）：其论据（错误码/端口/路径等精确符号）已由我们 keyword 通道覆盖；全盘去向量不适用——我们已实测语义通道 0%→100%（非字面查询纯词法 0/22）。
4. **InvMem bge-small 级弱向量**：学术榜模型限制（text-embedding-v4 上限）下成立；我们 bge-m3 多语更强，不必回头。

## 五、我们仍领先的 7 点（对头部逐项确认，不变）

写入决策三岔审计 / 指令形记忆 deferred_review / 召回包逐条 reason+degraded 诚实 / CJK 原生 / memory_versions 回滚 / 类凭据两层拒收 / 可复跑回归钉（本轮已升级为"回归钉+扩集分档+抄前抄后留证"）。

## 六、归属收敛（2026-11-20 用户纠偏，迁移优先于删除）

- **opensoul=记忆系统唯一正主**：P0×5 + P1×6 全部落 `opensoul/src/hippo` 族（memory_hybrid.py / memory_vector_index.py / long_term_memory.py），基准以 opensoul 面为准。
- **acp-proxy 镜像收敛**：`acp-proxy/agent/memory_hybrid.py` 的本地混合排名实现（RRF/语义/关键词融合=opensoul 的跨仓镜像）**迁移归一进 opensoul**，原文件收敛为薄客户端——`search_opensoul()` 统一调 `POST /api/hippo/ltm/search`（mode=hybrid）+ TTL 30s 薄缓存（会话暂存）+ `keyword_relevance` 纯词法工具；OpenSoul 不可达=如实降级空包（degraded+reason），**禁本地平行排名兜底**。
- **SoulMate 记忆读写/检索统一调 OpenSoul API**：`memory_retrieval.retrieve_pack()`→薄客户端；`soulmate_agent.py:7699` 召回点→`retrieve_opensoul()`（统一 API）；`store()`→write-through `POST /api/hippo/ltm/add`（本地行=会话暂存/薄缓存）。测试隔离：conftest autouse fixture 把 `SOULMATE_OPENSOUL_URL` 指向不可路由端口（禁写真实 :8090 生产 LTM）。
- **收敛剩余债（下一步，如实）**：① `MemoryRetrievalEngine.retrieve()` 本地三因子排序仍在缓存读取面（有测试钉着；生产召回已不走它，退役/迁移待拍）；② `memory_consolidator` 片段流本地打分待并 opensoul；③ dream 管线 anchors 自动产出待接 prompt（`store_derived` 严格门已就位，dream 带 anchors 即走校验）；④ study P1-12/13 不在本轮六项（见 §一 批次边界）。

## 七、测试与复跑

```bash
# 基准（抄前/抄后同命令，两轮：回归钉 8×8 + 扩集 22）
cd ~/opensoul
PYTHONPATH=. .venv/bin/python scripts/e2e_memory_semantic_recall.py            # bge-m3 实测
PYTHONPATH=. .venv/bin/python scripts/e2e_memory_semantic_recall.py --no-embed # 纯关键词对照

# 机制契约测试（P0×5+P1 全覆盖，34 例）
PYTHONPATH= ~/opensoul/.venv/bin/python -m pytest tests/test_memory_aml_adopted.py tests/test_memory_hybrid.py tests/test_memory_vector_index.py -q   # 59 passed

# 全量测试基线
PYTHONPATH= ~/opensoul/.venv/bin/python -m pytest

# acp-proxy 收敛面（薄客户端契约，98 passed）
cd ~/openmate/acp-proxy && python3 -m pytest tests/test_memory_semantic_hybrid.py tests/test_memory_write_audit.py tests/test_memory_three_factor.py tests/test_persistent_memory.py tests/test_memory_echo_wiring.py tests/test_memory_marker.py -q
```

全量结果（2026-11-20 实测，34:30）：**7994 passed / 126 failed / 2 errors**。126 个失败
全部为**既有失败**（test_sessions_*/advisory/branch_summary/message_tree/ws_chat/
marketplace 等非记忆子系统；test_advisory_review×10 经 git stash 对照复证、其余经
pre-change worktree（37aa5852，PYTHONPATH 强制旧码）整批复跑 **同为 126 failed**
逐数吻合）——本改动 **零新增失败**；记忆族全绿（59+68+79+17+44+17+9+27+21+35）。

*本文数字均来自 2026-11-20 本机实测（证据文件见 §头）；机制出处 file: 级来源见 `aml-memory-study.md` §3/§5 与本表「出处」列。*
