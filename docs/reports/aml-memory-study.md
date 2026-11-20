# AML 记忆大赛精读：参赛内容 + 榜单全量 + 头部记忆系统抄作业清单

> 调研对象：Agent Memory Leaderboard（AML，agentmemoryleaderboard.ai + github.com/AML-memory/agent-memory-leaderboard，CSIG 主办、南大/浙大/Datawhale/CSIG 企标委承办）。
> 时间：2026-11-20（Cycle 2 正式榜单 2026-11-19 发布后次日收割）。今天起 Cycle 1 + Cycle 2 结果均可查。
> 数据来源：官网 /competition /rules /docs /results/cycle-2、agentmemories.ai/results/cycle-1、AML GitHub 仓库评估代码；榜单数字由 HTML 表程序化解析零手工转录，全量原文存 `~/wiki/raw/articles/aml-leaderboard-harvest-2026-11-20.md`。
> 深读对象（本地只读克隆于 `/home/climbing/aml-study/repos/`，未安装任何依赖）：Resurface、mnemotree、starwingMem、InsightMem、khipu、Rolling-Memory、StructMem(LightMem)、InvMem、MemOS、MemoraX。
> 对标基线：我们自己的记忆语义检索（`~/openmate/docs/reports/memory-semantic-retrieval-capability.md`：hybrid RRF + bge-m3 + 写入审计 + 预算裁剪）。

---

## 一、参赛内容速览

### 1.1 赛事骨架

| 项 | 内容 | 出处 |
|---|---|---|
| 主办/承办 | CSIG 主办；南大、浙大、Datawhale、CSIG 企业联络与标准化工作委员会承办；AML 提供统一评测平台 | github README |
| 赛道 | Textual / Coding / Multimodal Memory 三赛道独立评测独立排名 | /competition |
| 组别 | 开源方法榜（Open-source Methods）与商业产品榜（Commercial Products/工业榜）分列；评测类型 × 参赛组别是两个独立维度 | /rules |
| Cycle 1 | 2026-07-29 开赛，2026-08-12 首榜（仅 Textual+Coding） | github README |
| Cycle 2 赛程 | 09-20 开放报名评测 → 10-31 23:59 材料截止（11-01 02:00 关闭提交口）→ 11-04 23:59 正式评测截止 → 11-19 正式公榜（已发布） | /rules /competition |
| 奖金 | 开源方法榜奖池 US$22,000（中文页口径 ¥150,000）：每赛道 一等奖 1 名 ≈$3,000、二等奖 2 名 ≈$1,200、三等奖 3 名 ≈$450、最佳技术创新 1 名 ≈$750；商业产品榜不参与奖金 | /rules /competition |
| 参赛费用 | 免费；参赛方承担自家 API/数据库/带宽/算力，平台承担统一 Answer、Eval 与编排 | /rules |
| 提交频次 | 每个 AML Key 每赛道最多 2 次 Full；第 2 次须在首次 Full 完成满 30 天后发起；断点续跑不计次数 | /competition FAQ |

### 1.2 三赛道差异

| 赛道 | 评测内容 | 规模 | 评测合同 | 特有机制 |
|---|---|---|---|---|
| Textual | 长对话/跨会话事实、多跳关系、时间事件、记忆治理、个性化、规则执行、安全隐私 + Streaming 持续记忆 | 6,000+ 实例、32 数据源、≈300M tokens | `textual-v1` / `ldbd-textual-official-suite-v1` | Streaming 任务按事件序交错调用 Add/Search（S 列单独计分） |
| Coding | 同仓库工程经验的检索/筛选/复用，relevant 与 noisy（含干扰历史）双条件 | 150 基础任务 × 2 条件 = 300 评测单元、≈1.8B tokens | `coding-v1` / `cambench-coding-relevant-noisy-300-v1` | 平台供 coding agent 与 verifier，agent 任务中可多次调 Search，top_k 最大 1000 |
| Multimodal | 图文记忆写入、跨模态检索、证据使用 | ≈5,000 实例、7 数据源、≈800M tokens | `multimodal-v2` / `ldbd-multimodal-suite-v2` | 平台供原图+caption；系统须支持有序 text/image_url 内容数组并如实声明处理的媒体类型 |

数据集出处：BEAM（github.com/mohammadtavakoli78/BEAM）、PersonaMem-v2（github.com/bowen-upenn/PersonaMem-v2）、MemEye（github.com/MinghoKwok/MemEye）、LoCoMo-Refined、LongMemEval-S、CLBench、ScriptMem（AML 仓库 data/ 目录自证）；另有平台自建私有题集防过拟合。

### 1.3 Add/Search API 契约（参赛=自部署这对 API）

写入侧 **Add**（同步；内部可异步但必须处理完才返回 200）：

```json
// 请求
{ "request_id": "eval:run_abc123:locomo_refined:conv-0:chunk-0",
  "messages": [{ "role": "user", "timestamp": 1704067200000, "content": "memory text" }],
  "user_id": "eval:run_abc123:locomo:conv-0",
  "session_id": "eval:run_abc123:sample:0" }
// 响应 HTTP 200
{ "success": true, "request_id": "...", "user_id": "...", "session_id": "..." }
```

- `request_id`/`messages`/`user_id`/`session_id` 全必填，响应须原样回带三个 ID 且 `success` 布尔 true；2xx 但体不合格=契约错误当场失败。
- `timestamp` 可选 Unix 毫秒；`messages` 多模态可为有序 `text`/`image_url` 内容数组。
- **不发送** `metadata`/`app_id`/`agent_id`/`async_mode`；HTTP 202 仅当版本已审核绑定 Add 状态查询地址。
- 幂等/续跑：同一逻辑写入保持同一 `request_id` 与请求体；409 不单独视为成功。

检索侧 **Search**：

```json
// 请求
{ "query": "Which answer best matches the memory?", "options": ["A. ...", "B. ..."],
  "user_id": "eval:run_abc123:locomo:conv-0", "top_k": 100 }
// 响应
{ "data": [{ "id": "mem_1", "content": "remembered fact text", "score": 0.87,
             "created_at": "2026-07-01T12:00:00Z" }] }
```

- `query` 按原文检索（多模态同 Add 的内容数组），**不得替换为最终答案或使用金标**；`options` 仅选择题顶层传入、开放题不发；`top_k` 正式评测固定 100（Coding 最大 1000）；返回条数超 `top_k`=契约错误。
- **不发送** `filters`/`rerank`/`keyword_search`。响应 `data` 数组必填，每条 `id`+`content` 必填非空，`score`（越大越相关）与 `created_at` 可选，其余字段忽略。
- 鉴权：`Token`/`Bearer`/`X-Api-Key`（`none` 仅公开 smoke）；Health 为免鉴权 GET，2xx 即正常。URL 不得含凭据、不得指向私有/回环/链路本地地址。
- 重试：Add 遇 408/409/425/429/500/502/503/504/524 有界重试≤32 次；Search 遇 408/425/429/5xx 有界指数退避，409 及其余永久 4xx 不重试。429 尊重 Retry-After≤60s，缺省等 60s。错误响应规范：400/422 格式、401 鉴权、403 越权、404 资源。
- **数据纪律**：评测数据仅用于本次评测，不训练不分析不外传，30 天内删除，不留日志。

### 1.4 评估流程与指标口径（smoke → formal eval）

流程：提交准入申请（固定版本+接口+材料）→ 平台审核签发 Eval Key（次日 19:00 前）→ **Smoke**（独立兼容性链路 Add→Search→Answer→Evaluate；每小时 1 次、每赛道本届≤30 次，仅私有不可公榜）→ **Full 前置 8 项勾选清单**（smoke 通过/契约正确/模型规则知晓/容量确认/部署信息完整/原创披露/实质性提交/无操纵）→ **Full**（固定套件+统一 Top K，受理后冻结版本与评测合同）→ 平台复核（版本/结果/合规）→ 公榜。

责任边界（分数可比的前提）：参赛系统只做 Add/Search；**回答模型、提示词、评分器、数据集组合、Top K、汇总规则全部由平台固定**。流程图：`Participant Add/Search → Platform Answer/Agent → Evaluation/Verifier → Private Results → Review → Public Leaderboards`。

指标口径（按榜单列）：

- **Textual**：Overall + 7 能力列 + S。Cycle 1 列名：A Explicit Fact Recall / B Compositional Inference / C Temporal & Event Reasoning / D Memory Governance / E Personalization & Care / G Context Learning & Execution / H Safety & Privacy。Cycle 2 改名并增列：B Relational & Multi-hop Reasoning、C Temporal & Event Sequencing、G In-context Learning Rules & Workflow Execution、H Epistemic Safety & Privacy、**S Streaming**。打分=平台统一 Answer 后按基准二元判分（AML 仓库 `data/locomo-refined/pipeline.py` 的 ACCURACY_PROMPT 自证口径：Inclusion+Non-contradiction 原则、时间粒度严格对齐（HOUR↔HOUR，禁相对↔绝对换算）、多事实列表必须全覆盖且多答=WRONG、偏好类命中任一即对）。
- **Coding**：Overall Resolved (%)，拆 Relevant/Noisy × New Feature/Bug Fix 6 个子列 + 6 个成本列（Input Tokens/Output Tokens/Return Size/Write Time/Search Time/Total Time，档位制 Low/Medium/High、Compact/Balanced/Verbose、Fast/Medium/Slow）。
- **Multimodal**：Overall + A Retrieval / B Relational Reasoning / C Temporal Reasoning / D Continual Learning / E Personalization / F Evidence Governance。
- 复现纪律：成绩绑定 Commit/镜像/API 版本 + Run/Suite/Pipeline 标识；同合同完整结果才可比，跨期跨赛道百分数**不可直接比较**；缺样本不可跳过/缩分母/补零；Answer 模型与 coding agent 均不见金标。
- 模型限制：开源方法榜（学术榜）Embedding 限 `text-embedding-v4`、LLM 组件限 `gpt-4o-mini`、Reranker 不限；商业产品榜（工业榜）无模型限制，自训练/开放权重模型建议走工业榜。
- 评审红线：Search 不得生成最终答案或把答案伪装成记忆记录；不得跨 user_id/任务/样本共享记忆；复用论文/仓库必须披露原作者与全部改动；禁硬编码、数据泄漏、提示词注入、人工实时答题、刷榜。未披露重复代码可判抄袭（平台优先保护已披露先提交者）。

**参赛要点一句话**：把一个"只做记忆"的 Add/Search 服务部署到公网，接口比 AML 契约**松不能紧**（Resurface 首轮因校验比契约严吃 422 直接失败，见 §4.1），检索只交证据不交答案，其余全交给平台。

---

## 二、榜单全量收割

> 全量表（含所有分项列）见 `~/wiki/raw/articles/aml-leaderboard-harvest-2026-11-20.md`。下表为各榜 Top-10 的 Overall。Cycle 2 六榜：工业 125+27+10 条、学术 89+20+18 条；Cycle 1 四榜：工业 15+15、学术 50+43。

### 2.1 Cycle 2（2026-11-19 公榜）

**Textual · Closed-source/Commercial（125 条）**

| # | 系统（机构） | Overall | # | 系统（机构） | Overall |
|---|---|---|---|---|---|
| 1 | MemoraX（深圳忆纪元） | 69.26 | 6 | Mnemon | 61.65 |
| 2 | AIDBS-Groundata（阿里云 DMS） | 64.32 | 7 | QMEM | 61.51 |
| 3 | MemFoundry（千问事业部） | 63.65 | 8 | QuietLattice | 61.48 |
| 4 | LinkMem（灵心纪元） | 63.23 | 8 | memorySystem | 61.48 |
| 5 | Gedo Memory（GEDO PTE） | 62.28 | 10 | Silent Context Engine（OPPO） | 61.04 |

**Textual · Open-source Methods（89 条）**

| # | 系统（版本） | Overall | # | 系统（版本） | Overall |
|---|---|---|---|---|---|
| 1 | Resurface（submission-1） | 61.22 | 6 | uuyz-aml-memory v1.0.0 | 60.35 |
| 2 | mnemotree v1.0.0 | 60.81 | 7 | 赏金猎人 v0.6 | 60.26 |
| 3 | starwingMem v1.3 | 60.56 | 8 | Cordmem V1.0 | 60.12 |
| 4 | PaiMem v0.1.0 | 60.41 | 9 | LMC-5（五维活体记忆）AML.2 | 59.97 |
| 5 | RAW-RAG-AML v0.1.0 | 60.38 | 10 | plain-memory 0.10.yq | 59.95 |

**Coding · Commercial（27 条）**：1 MemoraX 68.00 · 2 atlas-harness（海致星图）63.00 · 3 Mem-Pro（MirrorWorld Lab）62.67 · 4 memory-service 61.33 · 5 Rolling-Memory 61.00 · 6 Atlas-Harness（海致图库）60.67 · 6 mempalace 60.67 · 8 Mem-Classic 59.33 · 8 ne2 59.33 · 10 太一连山 59.00。

**Coding · Open-source Methods（20 条）**：1 InsightMem 63.33 · 2 Aranea-Agents Memory 62.00 · 3 ZfaiOpen 61.00 · 4 LCM-X 60.67 · 5 RE-call / TencentDB / uuyz-aml-memory-coding 59.67 · 8 EviBind 59.00 · 9 Chronicle Memory 58.67 · 10 Mandol 58.33。

**Multimodal · Commercial（10 条）**：1 MemoraX 53.68 · 2 LinkMem 49.85 · 3 memorysum-vl（Shopee）42.35 · 4 RE-call 41.76 · 5 Rolling-Memory 41.06 · 6 MemBraince 36.60 · 7 mempalace 36.40 · 8 R-Memory 32.95 · 9 Harness-coding 29.01 · 10 ZfaiOS 25.97。

**Multimodal · Open-source Methods（18 条）**：1 Khipu（结绳）mm-2 43.26 · 2 starwingMem v1.3 42.62 · 3 Multimodal-RAW-RAG-AML 42.17 · 4 TraceMem-MM 41.22 · 5 AUREL-Mem-Lite 38.97 · 6 StructMem v0.2.0 38.43 · 7 ASMEM 38.40 · 8 mem-pro 38.21 · 9 CueBridge 38.20 · 10 Prism-Swift 36.58。

### 2.2 Cycle 1（2026-08-12 公榜，仅 Textual/Coding）

**Textual · Commercial（15 条）**：1 MemoraX v0.5 58.02 · 2 MemOS v2.0.1 45.89 · 3 NTES-MEMORY-SMART 44.21 · 4 AML-Eval-FLASH 43.65 · 5 Cognee v1.4.0 42.61 · 6 NTES-MEMORY-MQ-LAYER 42.11 · 7 TencentDB v2.0.0 41.48 · **8 Mem0 v3 41.40** · 9 MemPalace v3.5.0 39.48 · 10 Vectorize Hindsight Cloud 38.54。

**Textual · Open-source（50 条）**：1 InvMem 45.06 · 2 Refind 44.97 · 3 ActiveMemoryIndex 44.84 · 4 Hybrid Search v2.0 44.57 · 5 ChronoHybridMem 44.33 · 6 Hybrid Episodic Memory 44.28 · 7 Chronicle 44.19 · 8 FlowGrid_AML_Retriever 43.98 · 9 LLLMemoryAgent 43.06 · 10 aml-memory-mvp 42.95（第 19 名 Mem0 42.07，第 22 名 SQLite-FTS-Baseline 41.79，第 35 名 just-a-BM25 38.47）。

**Coding · Commercial（15 条）**：1 MemoraX 62.00% · 2 claude-mem 52.00% · 2 hs v3 52.00% · 2 MemOS 52.00% · 5 memory-dense/Anonymous PIE/mem0-api 51.33% · 8 Cognee 50.67%。

**Coding · Open-source（43 条）**：**8 队并列第一 52.67%**（AM-Link、AMC-Memory、aml-memory-baseline、aml-memory-mvp、causal-memory、Hybrid Episodic Memory、Memoria_v2、nano-memory_chris）。

### 2.3 榜单观察（带出处的判断）

1. **MemoraX 是三赛道全冠**（C2：69.26/68.00%/53.68），且 C1 就是双料第一——商业闭源，官网自报 LoCoMo-Refined 82.65%、SWE-ContextBench 45%、Mem-Gallery 76.33%（memoraxai.com，自报口径无法核验）。
2. **C2 竞争烈度暴涨**：学术文本榜 C1 冠军 45.06 → C2 冠军 61.22（合同不同不可直接比，但 89 条 vs 50 条、头部密集在 59-61 说明方法整体上了一个台阶）；C1 冠军 InvMem 是"朴素 RAG 基线"（vanilla-rag-memory），C2 头部全是精细检索工程。
3. **大厂组队下场**：阿里云 DMS（AIDBS-Groundata 文本 #2）、千问事业部（MemFoundry #3）、OPPO（三席）、Shopee、阶跃、网易（NTES 系列 C1 三席）、海致星图（coding #2）。
4. **旧知名系统的相对位次**：MemOS 从 C1 工业文本 #2（45.89）掉到 C2 #94（52.96）；Mem0 C1 工业 #8（41.40）→ C2 #106（50.82）——不是退步而是榜单扩员+头部进化；Mem0 在 C1 学术文本仅 #19（42.07）。
5. **朴素上限的启示**：C1 学术榜 SQLite-FTS-Baseline 41.79（#22）、just-a-BM25 38.47，与冠军 45.06 差距不到 7 分——纯词法基线在（当时的）学术榜上有体面位置；到 C2 后 Resurface 61.22 靠"BM25+dense+邻居+rerank"拉开。

---

## 三、头部记忆系统逐个深读（五轴：写入提取 / 索引 / 检索 / 遗忘 / 审计）

### 3.1 Resurface（C2 学术文本 #1，61.22）— github.com/lilysttail/aml-textual-memory

- **写入提取**：每轮对话存为结构化记录（谁说/何时/哪个会话/第几轮），**文本逐字保留不改写**；同步写入、`(space_id, batch_id)` 幂等、单事务；整快照可原子换代（`store.py:replace_snapshot`）。输入校验只按契约做，能宽容的全宽容（缺 role 默认 user、读不出的 timestamp 算"无时间"、缺文本的消息跳过而非拒整批）——这是 2026-10-29 用一次正式评测失败换来的教训（首轮 `ADD_API_CONTRACT_MISMATCH · HTTP 422`，平台对 422 不重试）（METHODS.md §2a）。
- **索引**：SQLite FTS5+BM25（bm25 越负越好映射为越大越好；时间词 `_ts_index_words` 也进 FTS，使"去年三月"类查询可词法命中）+ dense 通道（`text-embedding-v4`，写入期向量化，`dense_index.py` 按 space 建代际索引，带内存预算 `_fit_budget`、单飞向量缓存、embedding 并发闸）。
- **检索**：关键词 + dense 多路 RRF 融合（k=60，`rerank.py:merge_rrf`），可选向量 rerank（对候选池重嵌入打分）；**邻居候选**（命中点同会话前后轮次、窗口序）默认开——本地 ruler 实测金证据命中 74.9%→78.8%（+3.9pt，代价 +10.3% 字符）；返回排序纯相关性。
- **遗忘**：无自动遗忘；有 `wipe` 工具级清库与快照换代。
- **审计/工程纪律**：`write_settings.py` 记住写入时的 tokenizer/写入格式，**不匹配拒绝启动**；拒绝的请求只记字段名和问题类型不记原文；METHODS.md 把"借了什么"（FTS5+BM25 方向、RRF 2009、单飞缓存、OpenAI 兼容请求形、LoCoMo-Refined）逐项列进 THIRD_PARTY_NOTICES；**决策可复核**：dense 通道是否开启用预注册三门槛决策规则在锁定的 734 题上一次性验证（+10.72pp，2,202 答案），不重跑不挑指标（§5a）。

### 3.2 mnemotree（C2 学术文本 #2，60.81）— github.com/kurcontko/mnemotree

- **写入提取**：source-first——**每条消息原文照存，不做 LLM 重写摘要**；chunk+source+双索引+幂等回执**单事务**提交。派生的"atoms"是**可选**检索记录，且其引用必须是能在原文 passage 中 `find` 到的**逐字引文**（`ingest/atoms.py`：quote 找不到偏移即 ProviderError；单批≤64 个 atom；原文 span 始终是证据本体）。
- **索引**：FTS5 BM25 + sqlite-vec cosine（1024 维 DashScope text-embedding-v4），namespace 级隔离。
- **检索**：RRF 融合 + 可选 rerank；`neighbor_chunks` 在源消息内扩窗；**精确 token 预算打包**（默认 8,000 tokens），`recall.trace` 记录每个候选、各通道 rank、**打包损失（packing losses）**与耗时（GUIDE.md）。
- **遗忘**：`forget(id)` 删 source+chunks+双索引；无自动遗忘。
- **审计**：trace 全量可观测；`mnemotree-evidence evaluate/ablate/ablate-context/ablate-retrieval` 内置消融对比 CLI；MCP 侧 `forget` 标记为需客户端审批。公开基准数字：LoCoMo 1,540 题 72.1（vs Mem0-graph 68.4/Mem0 66.9/Zep 66.0/LangMem 58.1，Mem0 论文 Table 2 口径）、LongMemEval-S 60.8、BEAM 各长度段均胜其 RAG 基线。

### 3.3 starwingMem（C2 学术文本 #3 + 多模态 #2）— github.com/seuclkzsd/starwingMem

- **写入提取**：原文照存；>350 词消息切 300 词段（50 词重叠）；写入时一次 DashScope 调用同取 dense+sparse 向量并建 BM25（bm25s+词干化）；向量缓存 SQLite，重启回放不重计费；并发写同文本 single-flight，失败集体 500 交平台重试。
- **索引**：三路（dense/sparse/BM25）+ 会话级索引；内存 LRU 上限（文本 20 万 passage），淘汰只清内存，**向量缓存重建零成本**。
- **检索**：加权 RRF（dense 1 / sparse 0.5 / BM25 1，k=60）+ **会话级 RRF**（会话分=会话内 top5 消息分之和，会话内消息再加 `0.5/(60+会话排名)`）；时间规则（问题里"Now is…"先剥离，未来消息 ×1/4 降权，时间范围内消息 ×2 加权）；相对时间后追加确定性绝对日期注解（`timeannot.py`："yesterday [i.e. 21 February 2023]"）；窗口打包 16k token（tiktoken o200k_base）+ 同会话前后扩轮；建议/偏好类问题先给"top-20 原话摘要"再给窗口（规则分类 `qtype`）。
- **遗忘/治理**：Streaming 平台回写反馈**只存不索引**（消融：入索引 LoCoMo −3.8 显著）；选项只当 BM25 检索词不入记忆；LRU 淘汰。
- **审计**：README 的"主要取舍"表=完整消融账本（重排模型：证据覆盖 +1.6 但端到端持平→不用；时间排序 LongMemEval −6.1→不用；gpt-4o-mini 筛证据/判空：不显著或误杀 72%→不用；文本窗口加 `<past_conversation>` 标签 +3.8pt [0.0,+7.4]→v1.3 采用；多模态 CJK 字二元组使中文命中代理指标 0.057→0.272→采用），全部带样本量与置信区间。
- **编码路径特招**：纯 BM25+规则零模型；代码感知分词（`path:`/`file:` 精确 token、snake/camel 拆词、限定名整体+分段、中文二元组）；检索单位=一条历史轨迹，两路 BM25 RRF（片段路 top5 求和 + **关键文档路**：首条 user + 最后 assistant 总结 + ≤8 段编辑摘录）；`rel_cut=0.5` 过滤；返回"历史任务卡片"≤5 张 8k token，卡片标题明确"做法不一定适用于当前任务"。Search 内部出错返回空 200（不让记忆故障打断 agent 解题），与文本路径 422 策略刻意不同。

### 3.4 InsightMem（C2 学术 Coding #1，63.33%）— github.com/Kline-song/InsightMem

- **写入提取**：不可变消息日志；消息分型（issue prompt/assistant 文本/thinking/tool call/tool result）；token 界 chunk 切分（320/重叠 40/6000 字符帽）；全量 chunk 单次批量嵌入；**单事务**提交（Search 永不见半截请求）。
- **索引**：`source_messages`/`source_chunks`/`index_views`/`view_sources`/`view_embeddings` 五表；检索视图=源切片+代码展开标识符视图。
- **检索**：三腿 RRF（源 chunk BM25 / 代码展开视图 BM25 / dense cosine，每腿 200 候选，rrf_k=60）；相邻切片合并（≤4000 字符）；**去重不删除而是把重复体移到唯一项之后**；纯过程叙述消息只做"不可入选"标记（**留在 BM25 语料里保住 rank 贡献**，过滤只发生在 query 侧）；排序=相关性+查询词覆盖率决胜，裸工具调用降权；检索确定性（同库同查询同结果，与写入时序无关）。
- **遗忘**：无自动遗忘（有 `purge_user.py` 运维脚本）。
- **审计**：`coding_meta` 存检索 profile 指纹+embedding 身份（model|dimensions|endpoint|normalization|sanitize），**不匹配拒绝启动**，换配置必须重建库；`request payload hash` 防幂等冲突。

### 3.5 Khipu/结绳（C2 学术多模态 #1，43.26；文本 #11）— github.com/Claude-Ovo/khipu（现 Lv999APTITUDE/khipu）

- **写入提取**：原文照存 + 自描述切段（`[2023-05-08 (Mon)] Caroline: <原文>`，段自带日期/星期/说话人）；timestamp 缺失时继承邻居，绝不看服务器时钟；`(user_id, request_id)` 幂等；逐字重复标 duplicate 但保留。规则建实体表（人名+别名归一）+ 日期表 + BM25 + 向量。
- **检索**：**五通道准入**——BM25 / 向量 / 实体通道（问到的人名→其全部片段按新到旧）/ 字面守卫（人名、数字、日期逐字命中直接放行）/ 日期通道；"只有这五扇门能放行，记忆年龄和长度不能"；RRF k=60 按**意图加权**（temporal/latest/aggregate/who/default 规则判定）；BM25+向量双命中的置前；任务请求时塞一小配额"规则片段"（"from now on…"类祈使句）；top20 后补邻居轮；**整体打包绝不截断**（60k token 预算）。
- **多模态**：每图 Add 时一次性 gpt-4o-mini 描述+OCR（描述进文本索引，**不用图像向量**），Search 命中后把**原图**作为证据随文本返回。
- **遗忘**：无自动遗忘。
- **审计**：`tests/manifest.py` 每次官方 run 前留部署清单（commit/包版本/模型设置）；ATTRIBUTION.md 逐项声明四个借源项目（twig-memory/MemoryConstellations/Ombre-Brain/write-him-back）各取了什么改了什么；**负结果也公布**（伪相关反馈二跳无增益、按时间排序 −3 分不用）；本地 ruler 数字：加向量通道后 single-session-preference turn@10 20%→63%，rerank 使精确答案轮 top10 79%→91%。

### 3.6 Rolling-Memory（C2 工业 Coding #5 + 多模态 #5）— github.com/rocareer/rolling-memory

- 定位：编码 agent 本地记忆工具（Claude Code/Codex/…），**零 LLM 抽取、零向量、零云**，Go 单二进制（CGO_ENABLED=0）+ 单 SQLite，一个 MCP `recall` 工具。
- **写入/索引**：索引受支持的会话源与挂载文件，纯词法+统计信号；三层记忆增长（raw→consolidation→assets，`internal/memory` + `internal/hooks/episodic.py`）。
- **检索**：`recall` 返回记录+**源坐标**（精确文件/符号定位），目标是替 agent 省掉 3-5 轮盲搜 grep（100k-400k 上下文每省一轮=省一次全量重读计费）。
- **审计（最有特色）**：evaluation hooks + 评估卡默认开；**确定性事后采纳审计**（recall 命中是否被后续工具调用/回复实际采纳）——24h 实测 46 个召回 41 个被采纳（89.1%），33 张去重回顾卡 27 张省下共 116 轮盲搜（≈4.3 轮/命中），6 张诚实报 0；token 经济学专文给出可证伪算法与 7 天自用账本（docs/token-economics.md）。
- 反 dense 的论证：工程查询多为精确词法锚点（错误码/端口/路径），向量平滑语义反而糊掉 `err_code_4012` 这类近邻符号；前端已有大模型，记忆层职责是毫秒级交逐字证据。

### 3.7 StructMem（C2 学术多模态 #6；C1 学术文本 LightMem-StuctMem #43）— github.com/zjunlp/LightMem

- 与上面的"逐字流"相反的**结构化抽取流**：事件级抽取（factual 组件 who/what/when/where + relational 组件人际/因果/时序依赖 + 时序绑定）；跨事件摘要（时间窗缓冲相关事件→合成摘要存独立 summary 检索库（Qdrant））；`summarize(retrieval_scope, time_window, top_k)` 周期性语义固化。主打时序推理与多跳。

### 3.8 InvMem（C1 学术文本 #1，45.06）— github.com/wenxiaof345-ctrl/vanilla-rag-memory

- "朴素 RAG 基线"：bge-small-en-v1.5 + SQLite 持久化向量 + FAISS 离线链路；hybrid 检索（`MEMORY_LEXICAL_WEIGHT=0.5`）、result window（`MEMORY_RESULT_WINDOW_SEED_K=20`）、option view；embedding 身份=模型+查询前缀，换模型必须新库重写。教训：**契约合规+朴素混合检索就能拿 C1 学术第一**；C2 后此分数（45.06）已被头部拉开 16 分。

### 3.9 MemOS（C1 工业文本 #2/编码 #2，C2 工业文本 #94）— github.com/MemTensor/MemOS（12.5k★ Apache-2.0）

- 记忆操作系统：统一 add/retrieve/edit/delete API；记忆**图结构**（可检视可编辑，非黑盒向量库）；多模态（文本/图像/工具轨迹/persona）；**Multi-Cube 知识库管理**（多记忆立方隔离/受控共享/动态组合）；MemScheduler 异步摄入（高并发毫秒级）；自然语言反馈修正记忆；主打 35.24% token 节省与跨任务技能复用。C1 由主办方代评（Organizer-evaluated），C2 自部署 API 后位次大降（52.96/#94）。

### 3.10 MemoraX（两期工业全冠）— memoraxai.com（深圳忆纪元，闭源）

- 只有产品口径：三赛道全冠（C2 69.26/68.00%/53.68）；自报 LoCoMo-Refined 82.65%、SWE-ContextBench 45%（其论文口径）、Mem-Gallery 76.33%。无架构可深读，作为"分数天花板参照"记分。

---

## 四、横向对比：我们 vs 榜单头部

> 我们的基线：opensoul `memory_hybrid.py` + acp-proxy `memory_hybrid.py`（语义 cosine + CJK bigram 关键词 RRF，semantic 2.0/keyword 1.0，k=60，bge-m3）、`memory_vectors` 写入期向量化（content_hash 失效+自愈回填）、写入三岔审计（auto_approved/rejected/deferred_review 全带理由）、召回包逐条 reason + 双预算裁剪（over_budget 留痕+truncated）、degraded 诚实、memory_versions 回滚、8/8 非字面重合基准（0%→100%）。

| 维度 | 榜单头部（出处） | 我们 | 判定 |
|---|---|---|---|
| 存储形态 | 逐字原文流+结构化记录是 C2 头部共识（Resurface/mnemotree/starwingMem/khipu/InsightMem 全员"verbatim"） | LTM 事实条目+片段流（consolidator 有改写摘要） | **可抄**：主证据链也走逐字流 |
| 检索通道 | 2-5 路：BM25+向量为底，头部加 sparse（starwingMem）、实体+字面守卫+日期（khipu）、代码展开视图（InsightMem） | 语义+关键词 2 路 RRF | **可抄**：sparse/实体/字面通道 |
| RRF | k=60 是全场公约数（Resurface/mnemotree/starwingMem/khipu/InsightMem） | k=60 | 持平 |
| 通道加权 | 意图加权（khipu 五意图）；固定权重 1/0.5/1（starwingMem） | 固定 2.0/1.0 | **可抄**：意图加权 |
| 会话/邻居结构 | 邻居轮扩展（四家都有）+ 会话级 RRF（starwingMem） | 无邻居、无会话级融合 | **可抄**（Resurface 实测 +3.9pt） |
| 时间处理 | future 降权/范围加权/相对→绝对注解（starwingMem）、日期通道（khipu）、时间词进 FTS（Resurface） | recency/decay 因子 | **可抄**：确定性时间注解 |
| rerank | gte-rerank-v2 前 200 候选（khipu，答案轮 top10 79%→91%）；starwingMem 消融后不用 | 无 | **可抄**（学术榜 reranker 不限） |
| 打包 | 精确 token 预算+packing loss trace（mnemotree）、16k 窗口+扩轮（starwingMem）、整体不截断 60k（khipu） | 双预算裁剪+over_budget 留痕+truncated | 持平偏可抄：补 packing trace |
| 写入幂等 | `(space_id,batch_id)`/`(user_id,request_id)` 幂等+单事务（全员） | store() 咽喉后置、无显式幂等回执 | **可抄**：幂等回执 |
| 配置指纹 | tokenizer/profile/embedding 身份入库、不匹配拒启（Resurface/InsightMem/InvMem） | content_hash 失效重算（自动迁移式） | **可抄**：拒启=显式重建，防静默错配 |
| 写入决策审计 | 头部基本无三岔审计（mnemotree forget 待批） | **三岔全带理由+review 动词** | **我们领先** |
| 指令形/凭据防护 | 无专门机制（khipu 反而有"规则片段"注入通道，仅任务请求+小配额） | deferred_review 入库不进召回+类凭据两层拒收 | **我们领先** |
| 召回包可观测 | trace=候选/通道 rank/打包损失/耗时（mnemotree）；无逐条排除理由 | 每条 reason+通道可见+degraded 诚实 | **我们领先**（可互补抄 trace） |
| 召回采纳审计 | Rolling-Memory 确定性事后采纳审计（89.1% 采纳率）+ROI 账本 | 无 | **可抄**（高价值） |
| CJK | starwingMem 补字二元组才把中文命中 0.057→0.272；bm25s 主打英文词干 | bge-m3 多语+CJK bigram 原生 | **我们领先** |
| 版本化/回滚 | 无（快照换代是 Resurface 的重建手段） | memory_versions rollback | **我们领先** |
| 基准实证 | 消融账本+置信区间+预注册决策门（starwingMem/Resurface/khipu） | 8 组自建小样本回归钉 | **可抄**：消融方法论 |
| 平台分数 | C2 六榜实测 | 未参赛，无平台分 | 差距未知 |

---

## 五、抄作业清单（可抄 21 项，带出处）

### P0（检索质量直接可换分，1-3 天/项）

1. **邻居轮候选**：命中点同会话前后轮次作独立候选进融合/打包。出处：Resurface METHODS.md §2（74.9%→78.8% 金证据命中，+10.3% 字符）；starwingMem 打包扩轮；mnemotree neighbor_chunks。落点：两仓 `hybrid_retrieve()` pack 阶段。
2. **意图加权 RRF**：按查询意图（temporal/latest/aggregate/who/default）调通道权重。出处：khipu README 五通道+意图权重。落点：`memory_hybrid.py` fusion 处加规则分类器（可先做 temporal/latest 两档）。
3. **实体+字面守卫通道**：人名/数字/日期逐字命中直接放行置顶。出处：khipu 五通道准入。落点：keyword 通道旁加 literal guard。
4. **确定性时间注解**：返回证据的相对时间后缀绝对日期（"昨天 [即 2026-11-19]"），未来时间降权。出处：starwingMem `timeannot.py` + future_penalty/time_boost 消融（日期自然写法 LoCoMo +2.4）。落点：召回包渲染层。
5. **rerank 候选池**：对 RRF 前 200 候选做 cross-encoder 重排（学术榜 reranker 不限）。出处：khipu（答案轮 top10 79%→91%）；starwingMem 反例（端到端持平→以消融定夺）。落点：pack 前可选通道，消融后定去留。

### P1（工程正确性与可运维，3-5 天/项）

6. **写入幂等回执**：`(user_id, request_id)`/`(space_id, batch_id)` 幂等+单事务，重复写不双计。出处：Resurface store.py、mnemotree 单事务、khipu。落点：`memory_write_audit` 旁加 receipt 表。
7. **配置指纹拒启**：tokenizer/embedding 模型/维度/检索 profile 指纹入库，不匹配拒绝启动并要求重建。出处：Resurface `write_settings.py`、InsightMem `coding_meta`、InvMem。落点：`memory_vector_index.py`（model 变更目前是 hash 失效自动重算——改为显式迁移）。
8. **打包 trace**：pack 里记录每候选各通道 rank、packing loss、耗时。出处：mnemotree GUIDE（recall.trace）。落点：pack 契约加 `trace` 可选段（不进 Answer）。
9. **查询侧过滤**：过程叙述/低质片段只做"不可入选"标记，保留其 rank 贡献，不删改语料。出处：InsightMem architecture.md §Search。落点：acp-proxy 片段流过滤逻辑。
10. **采纳审计（recall utility）**：确定性事后审计"召回的条目是否被后续工具调用/回复采纳"，产出命中率/省轮账本。出处：Rolling-Memory `internal/hooks/efficacy.py`（89.1% 采纳率口径）。落点：SoulMate 工具调用日志×召回包 join，新表 `memory_recall_adoption`。
11. **派生记录逐字锚点**：consolidator 产出的摘要/事实条目必须携带能在原文 find 到的引文偏移，否则拒绝入库。出处：mnemotree `ingest/atoms.py`。落点：LTM 摘要管线+acp-proxy consolidator。
12. **契约宽容输入**：校验只做契约要求的最低限，宽松兜底（缺 role= user、坏 timestamp=无时间、空消息跳过）；拒绝只记字段与原因不记原文。出处：Resurface METHODS §2a（422 不被平台重试=直接死）。落点：如参赛的 Add API 前置层。
13. **single-flight 并发**：同文本并发写只算一次 embedding，失败集体可见失败交上游重试；查询向量并发闸。出处：starwingMem 写侧、Resurface embedding gate。落点：`memory_embedding.py`。

### P2（差异化能力，1-2 周/项）

14. **会话级 RRF**：会话分=会话内 top5 之和，会话排名反哺消息分。出处：starwingMem memsys。落点：`memory_hybrid.py`。
15. **代码感知检索模式**（coding 赛道）：path:/file: 精确 token、snake/camel 拆词、限定名双索引、关键文档卡（首条 user+最终总结+编辑摘录）、rel_cut 过滤、轨迹卡返回。出处：starwingMem `codemem.py`、InsightMem code-expanded view。落点：OpenMate 编码记忆子域。
16. **消融决策门方法论**：改动上不上线用预注册门槛+锁定数据+一次性验证+置信区间，负结果也留档。出处：Resurface §5a、starwingMem 取舍表、khipu 负结果。落点：`demo_memory_semantic_recall.py` 扩成回归+消融双套件。
17. **Streaming 反馈"存而不索引"**：平台/用户回写的反馈消息只存档不进检索索引（消融 −3.8 印证）。出处：starwingMem §文本路径 6。与我们 memory_echo 回声守卫互为印证，可把守卫扩到"反馈类消息不入索引"。
18. **多模态写入期 caption+OCR**（未来多模态赛道）：每图一次性 gpt-4o-mini 描述+OCR 进文本索引，检索命中后原图作证据返回；图像不进向量。出处：khipu `app/mm.py`。落点：OpenMate 图文记忆（P2）。
19. **记忆自足性包装**：每条返回证据自带日期头/说话人/会话标签，对无上下文读者自足。出处：khipu ATTRIBUTION（write-him-back 原则）+ 星期几自然日期。
20. **LRU 索引淘汰+向量缓存重建**：索引内存超限淘汰最久未用用户，重建零成本。出处：starwingMem。落点：`memory_vector_index.py` 已有持久化向量，补 LRU 策略。
21. **失败降级按场景分级**：检索内部出错时编码场景返回空 200（不阻塞 agent），文本场景 422 显式失败。出处：starwingMem 代码路径容错。落点：`fault-degradation-testing` 降级契约补场景矩阵。

### 不抄清单（诚实边界）

- khipu 的"规则片段注入"通道（祈使句进 prompt）：与我们 deferred_review"记忆≠注入指令"立场相逆，仅在任务请求+小配额下无害，不抄。
- starwingMem 的三赛道自动识别（按内容形态猜赛道）：那是应对"同一 Key 接口不可改"的赛场特化，产品里是隐式魔法，不抄。
- Rolling-Memory 的纯词法路线：其论据（精确符号）由我们 keyword 通道覆盖，全盘去向量不适用（我们已实测语义通道 0%→100%）。
- InvMem 的 bge-small 级弱向量：学术榜模型限制下成立，我们 bge-m3 更强不必回头。

---

## 六、我们已领先的点（对头部逐项确认）

1. **写入决策三岔审计**（保留/拒绝/延后全带书面理由+review 动词）——头部开源系统皆无此面；Mem0 history 仅事件无理由。
2. **指令形记忆 deferred_review**：入库但不进召回、待人工放行——比"拒收/过滤"多可恢复路径，头部无专门机制。
3. **召回包逐条 reason+通道可见+degraded 诚实**：mnemotree 有 trace 但无逐条排除理由，其余为黑盒 top-k。
4. **CJK 原生**：bge-m3+CJK bigram vs 头部英文向量+事后补二元组（starwingMem 0.057→0.272 是补出来的）。
5. **版本化/回滚**（memory_versions+rollback_version）——头部皆无。
6. **类凭据两层拒收+secret_detected 审计**（理由不抄原文）。
7. **可复跑回归钉**（非字面重合 8/8，0%→100% 结构性修复证明）——注意头部已进化到"消融账本+CI"级别，此项优势是方法论级而非数据级。

## 七、如参赛（Cycle 3）checklist

1. 对外 Add/Search API（HTTPS 公网，鉴权 Token/Bearer/X-Api-Key，health 免鉴权 GET 2xx），输入校验比契约**松不能紧**（清单 §5-12）。
2. 请求/响应逐字段对齐（三 ID 回带、success 布尔、data 数组、超 top_k 判错），`request_id` 幂等。
3. 学术榜模型红线：Embedding=text-embedding-v4、LLM 组件=gpt-4o-mini（我们的 bge-m3+ollama 路线只能走工业榜）；自训练/开放权重→工业榜。
4. 材料：固定 commit/版本、来源披露（借了谁改了什么）、容量/超时/限流说明、评测专用 Key。
5. Smoke（每小时 1 次、每赛道≤30）→Full 前 8 项勾选→Full（每赛道 2 次，第二次隔 30 天）；评测期保 API 可用；数据 30 天删除。
6. 赛后把六榜当回归基准：我们的 8 组小样本扩成 LoCoMo-Refined/LongMemEval-S 公开集消融账本（§5-16）。

---

*本文数字均来自 2026-11-20 程序化收割（原始全量表：`~/wiki/raw/articles/aml-leaderboard-harvest-2026-11-20.md`）与本地只读克隆源码（file: 级出处见 §3）。MemoraX/MemOS 产品口径为自报，已在文中标注。*
