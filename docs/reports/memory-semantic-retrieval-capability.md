# P1 记忆语义检索 · 能力对比表（我们 vs OMH vs 主流 agent）

> 需求：记忆检索从全文匹配升级为语义检索（embedding 相似度+关键词混合），跨会话回忆更准。
> 验收对标：OMH 可审计长期记忆（`omh-deepdive-vol1-engine-mechanics.md` §5）+ 主流 agent 向量召回。
> 日期：2026-10-15。实测数字均由本机 ollama + bge-m3 真实运行产生（复跑步骤见文末）。

## 一、改造前的"全文匹配真身"（研究结论，file:line 为证）

| 层 | 位置 | 旧检索形态 | 盲区 |
|---|---|---|---|
| SoulMate 本地记忆 | `acp-proxy/agent/memory_retrieval.py` retrieve() | SQLite FTS5 MATCH + LIKE 兜底，relevance=token 覆盖代理 | FTS5 unicode61 对 CJK 整段成一 token；非字面重合查询零候选 |
| SoulMate 片段流 | `acp-proxy/agent/memory_consolidator.py` recall() | `word in content_lower`（按空格切词） | 中文整句查询=单 token 子串匹配，近乎零命中 |
| 系统 LTM（跨会话） | `opensoul/src/hippo/long_term_memory.py` retrieve/three_factor_retrieve | `content LIKE '%query%'`（:1132 注释自证"Use LIKE directly for Chinese text"） | 非字面重合查询**连候选都进不了**（Phase 1 LIKE 过滤） |
| opensoul 三库 recall | `src/hippo/memory_store.py` search() | 字面子串 `needle in content` | 同上（memory_semantic.py 只在 mode=semantic 单通道用，无混合、向量不落盘） |

向量基建查实：opensoul 有 `src/services/embedding.py`（openai/ollama 双 provider、
失败降级契约）+ `memory_semantic.py`（cosine 排序），但**记忆向量从不持久化**
（`Memory.embedding` 查询期懒算、刻意不回载）——没有记忆向量索引 → 已新建。

## 二、交付物

| 交付 | 位置 |
|---|---|
| 记忆向量索引（写入时向量化、hash 失效、跨进程持久、自愈回填） | `opensoul/src/hippo/memory_vector_index.py` + `acp-proxy/agent/memory_vector_index.py`（跨仓镜像同 schema） |
| 混合检索召回包（语义 top-k + 关键词兜底，RRF 融合） | `opensoul/src/hippo/memory_hybrid.py` + `acp-proxy/agent/memory_hybrid.py` |
| embedding 客户端（EMBEDDING_* 配置口径与 opensoul 逐字同款=同一 bge-m3 向量空间） | `acp-proxy/agent/memory_embedding.py`（opensoul 侧复用 `src/services/embedding.py` 新增同步面） |
| 写入决策留痕（保留/拒绝/延后三岔全带书面理由 + review 动词） | `acp-proxy/agent/memory_admission.py` + `memory_retrieval.py`（memory_write_audit 表）；opensoul LTM 既有 gatekeeper 审计（GATE_REJECT/TAG_REJECT/MERGE rule+reason） |
| 召回包预算裁剪（over_budget 留痕不静默丢 + truncated 标志） | 两仓 `hybrid_retrieve()` 同契约 |
| 检索升级接线 | `MemoryRetrievalEngine.retrieve()`（语义候选并入+第4因子）、`MemoryConsolidator.recall()`、LTM `recall_pack()` + `/ltm/search`、`/ltm/context` `mode=hybrid`、SoulMate 调用点 `soulmate_agent.py` ltm/context 传 mode=hybrid |
| 基准+演示脚本 | `acp-proxy/demo_memory_semantic_recall.py`、`opensoul/scripts/e2e_memory_semantic_recall.py` |

## 三、跨会话回忆基准（非字面重合查询集，hit@3，bge-m3 实测）

8 组"事实→改写查询"对（如 事实「决定采用蓝绿部署策略」/ 查询「上次说的上线发布方式是什么」，
token 零重合）：

| 面 | 字面/关键词基线 | hybrid（语义+关键词） |
|---|---|---|
| SoulMate 本地记忆（acp-proxy 引擎） | 0/8 = 0% | **8/8 = 100%** |
| 系统 LTM（opensoul LongTermMemoryStore） | 0/8 = 0% | **8/8 = 100%** |

写入时向量化覆盖率 8/8；对照模式（`--no-embed`）两通道同命中（自证基线公平）。

## 四、能力对比表

| 能力维度 | OMH（可审计长期记忆） | 主流 agent（Mem0 / Zep / MemGPT / generative-agents） | 我们（SoulMate + opensoul LTM） |
|---|---|---|---|
| 检索通道 | BM25 + RRF（纯词法，memory_recall_support.py:51-53） | Mem0：semantic+keyword+entity 多信号；其余主打向量 top-k | **语义(cosine) + 关键词(CJK bigram 覆盖) RRF 混合**（semantic 2.0 / keyword 1.0，k=60） |
| 非字面重合查询回忆 | 词法上限（改写查询漏） | 向量召回 ✓ | ✓ **实测 8/8（0%→100%）** |
| 向量持久化/索引 | 不适用（无向量） | Mem0 向量库 ✓ | ✓ SQLite `memory_vectors`（memory_id/content_hash/model），跨进程/跨会话 |
| 写入时自动向量化 | 不适用 | Mem0 写入 embed ✓ | ✓ store() 咽喉后置 + 内容 hash 失效重算 + 召回期自愈回填 |
| 写入决策留痕（保留/拒绝/延后+书面理由） | **✓✓ 三岔** auto_approved/pending_review/blocked + review_reason（memory_admission.py:534,961） | Mem0 history 表仅 ADD/UPDATE/DELETE 事件，无三岔理由 | ✓ SoulMate 三岔（auto_approved/rejected/deferred_review）全带理由落 memory_write_audit + `review_memory(approve/reject/defer)` 动词带 reason；LTM gatekeeper 两岔（rule+reason） |
| 指令形记忆防护（记忆≠注入指令） | ✓ 拒收 instruction_shaped_summary（:326-336） | 无专门机制 | ✓ deferred_review：**入库但不进召回**，待人工复核放行（比"拒收"多可恢复路径） |
| 类凭据拒收 | ✓（:308-311） | Mem0 有基本 secret 过滤 | ✓ 两层（LTM secret_detected 审计 + SoulMate credential_like 拒收，reason 不抄原文） |
| 召回包：included/excluded 每条带 reason | ✓✓（pack 合同 :474-505） | 无（黑盒 top-k） | ✓ rrf_fused(通道) / over_budget / unranked_no_embedding |
| 召回包预算裁剪 + over_budget 留痕 | ✓✓ limit(1-6)+max_chars 双预算，超预算留 excluded（:786-811） | LangChain 等 token 静默截断 | ✓ 双预算 + `reason: over_budget` 留痕 + truncated 标志（两仓同 pack 契约） |
| 通道降级诚实（禁静默降级） | 不适用 | 多数静默回退 | ✓ pack.degraded + channels.semantic=degraded/unavailable + reason，keyword 兜底**可见** |
| 会话去重（served_this_session） | ✓（:422-431） | 无 | 未实现（见"不抄清单"） |
| 新鲜度理由（12 种） | ✓✓（memory_recall_support.py:125-138） | 无 | 未实现（见"不抄清单"） |
| 版本化/回滚 | revision → history/ | Mem0 history | ✓ LTM memory_versions 既有（rollback_version） |
| CJK 检索 | BM25 分词弱 | 取决于模型 | ✓✓ CJK bigram 关键词 + bge-m3 多语向量 |
| 跨会话验证/基准 | 文档语义，无公开数字 | 无同类公开数字 | ✓ 本文基准可复跑（演示脚本即验收钩子） |

## 五、不抄清单（诚实边界）

- **OMH 的 12 种新鲜度理由 + review_due 周期**：我们有 recency/decay 因子但无"复核到期"流——涉及后台调度，P2 再议。
- **served_this_session 会话去重**：SoulMate 有 memory_echo 回声守卫（同目的不同机制：防记忆回声写入），包级会话去重未做。
- **pin ≤12 预算不独占**：无 pin 概念。
- **OMH 权限面（principal/受众）**：acp-proxy 有 cross_agent_recall_auth（另线），未并入本包。
- **brain /recall 三库面**：既有 mode=semantic（memory_semantic.py）未合并进 recall_pack（该面契约是 503 禁静默降级，与 pack 的 keyword 兜底语义不同，合并需产品决策）。
- 基准是 8 组小样本自建集（非公开数据集）；数字用于回归钉（0%→100% 的结构性修复证明），不是 SOTA 刷榜。

## 六、用户可复跑步骤

```bash
# 0. 前置：本机 ollama 已拉 bge-m3（opensoul/.env 的 EMBEDDING_* 同款配置）
ollama pull bge-m3   # 已有则跳过

# 1. SoulMate 本地记忆面基准+演示（写入审计 + 召回包裁剪同场演示）
cd ~/openmate/acp-proxy
python3 demo_memory_semantic_recall.py            # 期望：hybrid 8/8，验收: 通过
python3 demo_memory_semantic_recall.py --no-embed # 对照：无语义通道（公平对照，不产语义主张）

# 2. 系统 LTM 面基准+演示
cd ~/opensoul
PYTHONPATH=. .venv/bin/python scripts/e2e_memory_semantic_recall.py

# 3. 测试基线（本路相关测试全绿；acp-proxy 仓另有 11 个既有失败——browser_tools×8 /
#    output_guard×2 / token_attribution×1，经 git HEAD 干净工作树对照复证为基线已有，
#    与记忆改造无关）
cd ~/opensoul && PYTHONPATH= .venv/bin/python -m pytest
cd ~/openmate/acp-proxy && python3 -m pytest tests/test_memory_three_factor.py \
  tests/test_memory_semantic_hybrid.py tests/test_memory_write_audit.py -q
```

## 七、更新（2026-11-20）：AML 抄作业落地 + 记忆系统归属收敛

对比表升级为 **「抄前 vs 抄后 vs 榜单头部」**，见 **`aml-memory-adoption.md`**（P0×5 + P1×6
逐项出处落地、扩集 22 条基准抄前抄后留证、不抄清单 4 项理由）。要点：

- 落点归属（用户纠偏原话「记忆系统应该是opensoul」）：机制全部落 `opensoul/src/hippo`
  族；acp-proxy `memory_hybrid.py` 镜像**收敛为薄客户端**（检索统一走
  `/api/hippo/ltm/search`，本地只留 TTL 薄缓存+纯词法工具，禁本地平行排名）；
  SoulMate 记忆读写 write-through `/api/hippo/ltm/add`。
- 基准真身=`opensoul/scripts/e2e_memory_semantic_recall.py`（两轮：回归钉 8×8
  100% 保持 + 扩集 22 条 20/22→21/22，别名类 MRR@3 0.875→1.000、时间类 4/5→5/5）。
  上文 §六 的 `acp-proxy demo_memory_semantic_recall.py` 命令已改为薄客户端冒烟
  （`--smoke`），基准主张以 opensoul 面为准。
- 本页 §三/§四 的「vs OMH」表为 2026-10-15 口径，保留作历史；当前口径以
  `aml-memory-adoption.md` §三为准。
