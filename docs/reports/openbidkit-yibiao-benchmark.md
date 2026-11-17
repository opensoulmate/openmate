# 对标报告：OpenBidKit_Yibiao（易标投标工具箱）+ BidMaster-Pro

> 2026-11-17 · 为 OpenMate 插件系统 + 招投标插件（第一路）做深度对标。
> 依据：克隆精读 `FB208/OpenBidKit_Yibiao`（client/ 171 个 TS 文件 + electron 服务层约 60 个 .cjs，单文件动辄 2000–3000 行）与 `guangshu100/BidMaster-Pro`（Python 全栈，core/ + services/ 共约 1.5 万行）。

---

## 一、OpenBidKit_Yibiao 总体架构

| 层 | 实现 | 取舍 |
|---|---|---|
| 桌面壳 | Electron 41+（Main/preload CommonJS，Renderer ESM TS）| 纯本地客户端，所有数据在用户机上，**明文声明"本机可信边界，层间不重复校验"**——省掉大量鉴权/校验代码 |
| 前端 | React 19 + Vite + Radix + 全局 CSS（不用 Tailwind）| `features/<feature>/` 功能域 + `shared/` 跨功能层，feature 间不互相引用 |
| 业务存储 | SQLite（`userData/workspace/yibiao.sqlite`）为结构化状态唯一权威；配置走 `user_config.json`；localStorage 只存 UI 偏好 | 草稿/正文/流程状态全部落库 → **页面卸载不丢任务** |
| 后台任务 | `taskService.cjs` 统一编排 20+ 命名任务（bidAnalysis、outline、globalFacts、contentGeneration、rejectionCheck、blindCheck…），持续写 SQLite Store | "后台任务恢复"是核心卖点：耗时流程全部可断点续跑 |
| Word 导出 | 独立 .NET `openxmlhelper`（OpenXmlHelper.csproj）+ PowerShell 受限 HTML 转 Word | JS 生态做不好 OOXML，直接拉一个 C# helper 进仓库 |
| 在线服务 | 独立 Cloudflare Worker（analytics/）：埋点、公告、更新日志、**插件市场**、依赖按需下载、Agent 失败诊断 | 客户端零后端；插件分发复用这套 Worker |

### 插件系统（对我们最有参考价值的一块）

- **分发**：插件市场挂在 analytics Worker（`/plugins`、`/plugins/download`），插件为 zip 包；本地 `userData/plugins/` 解压安装，`plugin-states.json` 记录启用状态；版本比较 + 批量更新 + 更新失败记录。
- **运行模型**：插件跑在 **可信 Electron Main 环境**（`pluginContext.cjs` 直接给出 `app`、`ipcMain`、各业务 Store 的 getter、任务事件订阅），外加插件专属 `store`（`plugin-configs/<id>.json`）与 `logger`。配置窗口用独立 BrowserWindow（contextIsolation 开、nodeIntegration 关）。
- **并发控制**：`acquirePluginOperation` 为每个插件的操作加独占锁（Symbol token），防止安装/更新/卸载竞态。
- **取舍**：①插件无沙箱、无权限声明校验（"客户不会自己攻击自己"）——开源桌面单机场景说得通，**OpenMate 有对话 Agent + 网络服务，不能照抄**；②插件与主程序服务强耦合（直接拿 Store getter），升级主程序要同步改插件上下文 API——我们要用"MCP 工具 = 稳定契约"替代直接服务注入。

## 二、OpenBidKit 标书生成流水线（technical-plan）

任务链（全部走 taskService 后台 + SQLite Store 断点续跑）：

```
文档解析(doc2markdown/MinerU) → 标书分析(bidAnalysisTask：无效投标/废标项/评分办法/关键信息)
  → 目录生成(outlineGenerationTaskV2) → 全局事实(globalFactsTaskV2：项目名/金额/日期等一致性事实表)
  → 正文编排(contentGenerationTask·planning) → 逐节生成(generating，小节粒度断点)
  → 批量转 Word(word-converting) → 全文一致性审计(auditing) → 表格清理(table-cleaning)
  → 格式自检(layout-checking) → 商务模版填写(business-filling) → 导出修复(export-repair)
```

关键实现取舍：

1. **Agent + 程序双轨**：编排/planning 阶段先让 Agent 产出 content plan（每节的知识条目引用、表格用途、字数目标），**程序校验并落库后再进入生成**；正文生成逐小节提交，提交前程序校验图片引用（`data-yb-asset-ref` 必须指向真实存在的工作区文件，占位图不允许作为最终结果）。Agent 可以胡写，程序兜底验收。
2. **字数分配是算术不是 LLM**：`allocateContentWordTargets` 按目录叶子数把总字数上下限摊到各节，增量生成时只给未完成节分配。
3. **暂停/恢复是一等公民**：`pauseIfRequested()` 撒在每个阶段边界；恢复时 `completed_stages` 集合决定跳过哪些阶段，已写出的正文保留、只补缺失小节。
4. **一致性审计是必做阶段**：正文扩写完后强制跑"全文事实冲突检查+修复"（对应我们的流程引擎"节点后置校验"）。
5. **prompt 不进组件**：所有可复用 Prompt 放 `src/shared/prompts/`，任务专用 Prompt 与 service 同目录（`feasibilityReportPrompts.cjs` 等）——避免组件里硬编码大段 Prompt。

## 三、知识库（knowledge-base）

- **入库流水线**（SQLite `knowledge_items/knowledge_blocks/knowledge_item_blocks`）：`copy_source → convert_markdown → build_blocks → extract_first_items → extract_supplement_items → merge_candidates → match_batches → recover_missing → save_result`。
- **分块策略**：先按文档结构切 raw blocks，再 `mergeSemanticBlocks` 语义合并——目标块 ~500 字；表格独立成块不合并；语义标题块保留边界；超大块（>8000 字）单独走上下文限长切分。
- **条目抽取**：两轮 LLM（首轮 + 补充轮）抽"知识条目"（title/resume/content），再 LLM 批量把条目匹配到目录节点（match_batches），失败块走 recover_missing 重试 2 次。
- **检索**：**没有向量库**——正文生成时按 content plan 里选中的 knowledge_item_ids 注入；匹配靠 LLM。单机、资料量几千份的场景下这个取舍成立（免嵌入模型依赖），代价是无法语义召回未被匹配到的材料。BidMaster-Pro 反而是向量+BM25 混合（见第四节）。
- 上下文预算：`DEFAULT_CONTEXT_LENGTH_LIMIT=400000` × 0.8 作为知识上下文上限，分段时预留 20% 给任务说明（策略 B）。

## 四、废标检查（rejection-check）与查重（duplicate-check）

### 废标检查
- 双层结构：**分析层**（bidAnalysisTask 的 `runInvalidBidAndRejectionItemsExtraction`）按概念边界区分"无效投标"vs"废标项"，且严格区分"招标文件明确提到的（保留关键句）"vs"此类标书还可能涉及的（经验补充，每节限 3–5 条，禁止泛泛清单）"；**检查层**（rejectionCheckTask）对投标包分片段滚动检查。
- **滚动状态摘要**是核心机制：超长投标包切片段（0.55 上限比例），每片段把 `pendingRisks/confirmedRisks/resolvedRisks/submittedEvidence/factRegister` 压缩成滚动摘要传给下一片段，摘要带 id 约束（"更新 pendingRisks 只能引用摘要中已有 id"）——解决长文档 LLM 检查的失忆问题，且状态可落盘可恢复。
- 检查输入三件套：检查依据（招标文件提取物）+ 自定义检查项 + 当前状态摘要。错别字级检查单独小片段（typo 0.7 比例）。

### 查重
对我们最有启发的一块——**取证级元数据 + 结构级对比 + 句子级 n-gram**，三层：

1. **元数据取证**（2869 行的一半在干这个）：docx 的 RSID 编辑会话指纹、OLE 属性集（CPG/code page 解析）、PDF XMP/Trailer ID/结构指纹、WPS 信号字段——判断"同一份材料改出来的标书"。
2. **大纲结构对比**：目录/标题/语义三路提纲（extractCatalogOutline/extractHeadingOutline/extractSemanticOutline 置信度择优），过滤"招标要求句"白名单（exact/strict 标准键/skeleton 骨架键/field 格式字段/near 近似五级匹配，near 用字符 bigram 倒排索引 + containment≥0.9 且 Dice≥0.82 判定）；然后 normalized_path 全等=重复组（score 1.0）、normalized_title 全等=重复组（0.95）、跨文件标题 bigram Dice≥0.86=相似组；文件两两相似度 = path 重合 0.45 + title 重合 0.35 + 顺序 LCS 0.2，按 0.75/0.55/0.35 分 high/medium/low 风险。
3. **句子级查重**：`duplicateSentences`（char-bigram Dice + LCS），配图片查重（duplicateImages）。
- **取舍**：全部确定性算法（bigram/LCS/containment），零 LLM 依赖 → 快、可复现、离线可用；阈值写死在代码里（0.86/0.82/0.88…），按长短句分档。缺点是同义改写抓不到——但查重场景本来就以"模板复用/围标"为主，够用。

## 五、图文生成

- **AI 生图**：9 种画面风格预设（写实摄影/设备特写/建筑效果图/3D 渲染/轴测插画/剖视插画/爆炸分解图/技术线稿/扁平插画），每种带 usage+style hint，`buildImageStylePrompt` 把 hint 追加到 prompt 并统一附加"避免品牌标识/水印/营销元素"。未指定风格不加默认 hint（防止覆盖 Agent 的画面设计）。
- **图片生命周期**：figure 化管理（figure id 唯一性校验、`data-yb-generation` 标记生成方式、`data-yb-size` 画框尺寸），原图只复用不重新生成；生成方式开关只约束待生成图；生成后压缩入库；正文完成校验所有引用真实存在。
- **Mermaid/HTML 转图**：本地隐藏 BrowserWindow 渲染——Mermaid 680px 宽（≈A4 正文宽）×3 倍像素输出；HTML 配图 1240px 设计宽 ×2 倍输出，含四种画框比例（square/wide/tall/panorama）、最小字号 24px 防缩印不可读、超高分段截屏（>8192px 切片）。全程可暂停轮询。
- **取舍**：不用在线图表服务、不让 AI 直接产图代替流程图——Mermaid 源码由 Agent 产、图片由本地确定性渲染，**图的内容可编辑、可复现**；AI 生图仅用于配图，且风格受限（防跑偏）。

## 六、BidMaster-Pro 亮点清单

| 亮点 | 实现 | 对 OpenMate 的价值 |
|---|---|---|
| 4 阶段流水线 + 人工闸门 | `multi_agent_pipeline_skill.PIPELINE_STAGES`：解读→大纲→内容→检查→排版，每阶段带 `gate: *_review` | 流程引擎"人工审批节点"的现成语义 |
| Skill 引擎 | `SKILL.md`（YAML frontmatter）+ `skill.py`（Skill 基类，execute(ctx)->SkillResult），SkillLoader 目录扫描 + importlib 动态载入，registry 注册 | 与我们"插件 = 清单+代码"同构；manifest 直接用 YAML 的先例 |
| 21+ 项检查技能 | check/skills/：保证金、签字盖章、有效期、一致性、重复率、废标项、资格、价格合理性、强制性要求、联合体、电子投标上传、样例报告、AI 文本检测、白名单过滤、风险评分…每项一个 skill 文件 | 招投标插件"废标审查"的规则目录直接对照 |
| 混合检索 | `HybridRetriever`：向量 + BM25 + RRF(k=60) 融合，语义重排，公司画像过滤 | 9450 份招标文件知识库的检索方案候选（比 OpenBidKit 的纯 LLM 匹配更适合大规模语料） |
| LLM 网关 | llm_gateway：多供应商热切换 + `json_repair`（LLM 输出修复） | 工具输出解析兜底，值得抄 |
| MCP 暴露 | `services/mcp/server.py` FastMCP：解读/大纲/合规检查/全量检查等核心能力注册为 `@mcp.tool` | "后端能力=MCP 工具，UI 与对话共用"的直接佐证 |
| Agent 框架 | registry/pool/supervisor/circuit_breaker/checkpoint/memory/message_bus | 流程引擎失败重试/熔断的参考语义 |
| OCR/文档引擎 | MinerU 扫描件 OCR + ONNX 章节分类器 + 分格式 parser | 9450 份存量招标文件（PDF/Word 混杂）预处理需要 |
| 商机监控 | news/：抓取器注册表（sources.yaml）+ 分类 + 去重 + 评分聚合 | 投标插件的"标讯订阅"功能雏形 |

## 七、结论：OpenMate 插件系统应采纳 / 应规避

**采纳**
1. 插件 = YAML 清单 + 目录（BidMaster SkillLoader 与 OpenBidKit plugin.json 都证明可行）；装载器做目录扫描、生命周期、路由/导航注册。
2. 后台任务断点续跑（SQLite Store + 阶段完成集）作为流程引擎的运行时基线。
3. 查重用确定性算法三层（元数据/结构/句子 n-gram），不烧 LLM；废标检查用"滚动状态摘要"处理长文档。
4. 图文：Agent 产源码（Mermaid/HTML）→ 本地确定性渲染出图；AI 生图限风格清单。
5. 生成流水线"Agent 产出 → 程序校验落库 → 再放行下一步"的双轨验收。
6. 知识入库的语义分块（~500 字、表格独立、标题保界）。

**规避**
1. OpenBidKit 插件跑在完全可信主进程、直接摸业务 Store——OpenMate 插件必须走 **MCP 工具契约 + 权限声明 + 安装确认**，不给服务直注。
2. 阈值/规则硬编码在 3000 行单文件里——我们的检查规则要走数据（规则清单文件/流程定义），可被招投标插件复用和用户扩展。
3. 前端菜单/路由手工四处同步（AGENTS.md 自己都列了 5 个文件）——UI DSL 要做到"清单即菜单即页面"。
4. 无向量检索的纯 LLM 知识匹配——9450 份语料下要上混合检索（向量+BM25+RRF）。
