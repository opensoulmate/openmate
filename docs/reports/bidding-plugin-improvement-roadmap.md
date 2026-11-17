# 招投标插件改进路线

> 2026-11-17 · 依据：①《趴一趴市面上智能标书软件》（存 `~/wiki/raw/articles/bidding-software-survey-wechat.md`）②LingYan-Bid-Agent（joychin，MIT）克隆源码精读（sidecar 157 个 py：docx_ops 3645 行 / evidence-rules / assemble_tender / validate_body / freshness / roles）③OpenBidKit_Yibiao + BidMaster-Pro 对标（`openbidkit-yibiao-benchmark.md`）④`plugins/bidding/` 现状盘点（47 测试于 ~/opensoul/.venv 复跑全绿：bidding 13 + plugin_system 34；真实招标文件 LNFH2024036 四工具跑通见 `bidding-verification/`）。
> 调研笔记：`~/wiki/entities/lingyan-bid-agent.md`、`~/wiki/concepts/bidding-agent-core-capabilities.md`、`~/wiki/comparisons/bidding-software-landscape.md`。

## 〇、架构定调（用户钉死原则，本路线的宪法）

**"写标书/写方案 = agent 基础能力；重点是强化 SoulMate 能力，其余仅仅是流程；不应该是一个写死的投标系统。"**
**"领域知识也是知识的一种，应该是在 opensoul 内的。"**

三层归属最终版：

| 层 | 归属 | 内容 | 纪律 |
|---|---|---|---|
| ① 能力 | **SoulMate 基础能力层** | C1 事实锁、C2 留白协议、C3 Decimal 精算、C4 OpenXML 编译（暗标清洗/修订）、C5 Mermaid 入图、C6 压图、C7 TOC/PDF（附属：解析力、证据挂接、文本指纹） | 最高优先；每项必须能被非投标场景复用 |
| ② 知识 | **OpenSoul 知识库** | 仅**跨项目恒定**：法规条文（87 号令/评审 29 禁/112 项中的通用法规类）、评分写法方法论（三档法/小项累加/详细分档的写法规律）、废标案例负样本（审查方法）、行业统计规律；**保质期/置信度=本层条目属性与管理机制** | 走 OpenSoul 检索/记忆体系；**禁止做成插件内数据文件或硬编码规则**；**项目事实（评分公式/封标清单/资格项/★条款等）禁入知识库** |
| ③ 流程 | **插件纯 YAML 编排** | 解析→编制→审查 的流程定义 | 只留编排，零业务代码 |

---

## 一、SoulMate 基础能力层（①，最高优先）

每项给：规格 → **非投标复用场景** → 依据 → plugins/bidding 现状 → 落地规格。

### C1 长文写作 + 全局事实锁（Global Truth Lock）
- **规格**：写任何多章节长文前，Planner 生成**只读《全局事实锁》**（三类：核心数字锁——工期/质保/响应时效/人数；技术路线锁——框架/语言/部署架构；命名实体锁——甲方简称/系统全称/自称口径），人工一键确认后注入全部写作任务的 System Prompt 顶部；后置校验器逐章核对锁值出现且一致，矛盾即点名。
- **非投标复用**：可研报告、申报书、解决方案、合同、白皮书、课程讲义——一切"多章节必须互相不打架"的长文。
- **依据**：文章模块 2（Global Truth Bus）；OpenBidKit globalFactsTaskV2（同思想）；走量 SaaS"上下文失忆症"（第 2 章质保 3 年 vs 第 6 章 1 年 → 虚假应标废标）是头号死穴。
- **现状**：`bid_engine._consistency_audit` 是**事后**雏形（项目名/编号/预算贯穿 + 评分点覆盖），无前置锁、无数字/技术路线/实体锁。
- **落地**：`fact_lock` 工件 schema（`locked_facts: [{key, value, scope, source, confirmed_by}]`）+ prompt 注入器 + audit 扩展（锁值缺失/多值点名）。放 SoulMate 写作协议，不进插件。

### C2 留白与待补协议（Abstention）
- **规格**：写作纪律协议——证据不足标 unknown/待补，禁止行业惯例/模型记忆补全；承诺类数字只出自用户确认的清单，拍板前不写正文；待补项输出**待人工补充清单**（正文禁【待补】占位文字，正规落点=Word 批注）；引用②知识时**过期不使用、低置信走留白**（薄纪律，规则机制在 OpenSoul 侧，见 2.4）。
- **非投标复用**：合同审查、审计报告、申报材料、任何 AI 辅助专业文档（编数字=职业事故）。
- **依据**：LingYan evidence-rules.md（conflicting/unknown/「未发现」限已查范围）+ 关键事实与承诺.md + docx_comment_add + validate_body 判不过占位。
- **现状**：无。generator LLM 自由发挥，可编造工期/人数/业绩。
- **落地**：约束协议（prompt + 确定性校验器扫描占位/无据数字）；批注出口依赖 C4。

### C3 数值精算（Decimal + 中文大写）
- **规格**：LLM 只提参数 → Decimal 精确计算（报价/折扣率/税率/合计）→ 标准中文大写（壹仟贰佰伍拾万元整）→ 回填《开标一览表》模板。**绝对禁止 LLM 自己算钱。**
- **非投标复用**：报销单（invoice-reimbursement / dhcc-reimbursement 同源）、报价单、预算表、发票核对、财务单据。
- **依据**：文章坑 1（浮点精度丢失）。
- **现状**：price_calc 全 float；无大写转换（正文只写"人民币（详见商务标）"）。
- **落地**：`money.py` 纯函数（Decimal 计算 + digit→大写）+ 单测（如 13356305.2 → 壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角）。最小的一步，先做。

### C4 Word/PDF 确定性编译（OpenXML + 暗标清洗 + 修订溯源）
- **规格**：LLM 只出 JSON AST → 确定性 Python（python-docx/lxml）渲染 docx（样式/多级编号/TOC 域/分节符/横向表格/图片/题注）；**暗标清洗步**（docx 元数据擦除——作者/公司/修订历史、样式规整、禁空行/彩色像素等规则按②知识执行）；可选**修订标记输出**（w:ins/w:del + "拒绝全部修订后与原文逐字一致"自校验 + Word 批注）。
- **非投标复用**：公文、合同、简历、任何格式强约束交付（政府/央企）；修订溯源=任何需人审的 AI 产出文档；元数据擦除=方案文档敏感信息处理（本机已有血泪教训）。
- **依据**：文章模块 5；LingYan docx_ops（修订标记/自校验/合册压平）；OpenBidKit 独立 .NET OpenXmlHelper（JS 做不好 OOXML 的旁证）。
- **现状**：`exporter.py` 439 行 lxml+zipfile 直接 OpenXML（TOC 域/多级编号/分节/横向表格）=雏形；无图片、无修订、无清洗。且 `_write_docx`（python-docx）与 exporter **双实现并存**。
- **落地**：统一 AST→docx 编译器（以 exporter 的 OpenXML 路线为基，吸收 _write_docx 封面/表格能力）；`scrub` 步骤。修订溯源是 P2 大件（LingYan docx_ops 3645 行，MIT 可借思想/移植）。

### C5 Mermaid 入图管线（流程图/架构图/甘特图）
- **规格**：模型只给语义拓扑（mermaid 语法或节点+连线 JSON）→ 本地渲染 PNG（支持灰阶，暗标打印安全）→ InlineShape 嵌入 + 自动"图 X-X"题注；表格（合并格/三线表）由 C4 编译器拼装（承诺数据用表格不用图片——可编辑可校验，LingYan 口径）。
- **非投标复用**：产品说明书、技术方案、课件、周报图表、任何文档插图。
- **依据**：OpenBidKit Mermaid 内联编译（正则捕获→本地渲染→InlineShape）；LingYan docx_diagram_insert（表格拼装图示）/mermaid 渲染（高度 18cm 封顶）。
- **现状**：generator.py 只发 `<!--MERMAID/-->` 标记，无渲染无插入；exporter 无图片通道。
- **落地**：mmdc（mermaid-cli）或 matplotlib 渲染器 + 图片元素进 C4。

### C6 压图算子（Pillow）
- **规格**：扫描件/证书图/渲染图插入前压缩：长边 1600px、RGB、JPEG 82%、**单张 150-300KB**，总包控在电子标上传限（50-100MB）内。
- **非投标复用**：一切带图文档交付、附件上传、图床入库。
- **依据**：文章坑 2（几十张高清扫描件把 Word 顶到 300MB+ 传不上去）。
- **现状**：无图片管线，自然无压缩。
- **落地**：Pillow 纯函数算子，挂在 C4/C5 的图片插入通道上。

### C7 TOC 页码更新 + PDF 终稿
- **规格**：python-docx 无布局引擎，生成后用无头 LibreOffice（`soffice --headless`）或 Windows pywin32 唤醒 Word 执行 `TablesOfContents(1).Update()`，顺带导出带书签、排版锁死的 PDF 终稿（电子标常要求 Word 转 PDF+电子签章）。
- **非投标复用**：一切 Word 交付（报告/合同/公文）的页码与 PDF 导出。
- **依据**：文章坑 3。
- **现状**：仅 TOC 域占位，页码不更新、无 PDF。
- **落地**：soffice 渲染节点脚本（工作量小）。

### 能力层附属项（归①，不在 C1-C7 编号内）

- **文档解析力（拆标 + 项目事实抽取）**——上一轮定调归能力层：锚点定位三关键位置、跨页合并格转 HTML（保 rowspan/colspan）提评分树、多源（主文件+补遗/答疑）角色化摄入+冲突标 conflicting 交人裁决；**产出项目事实工件**（见 2.1）存项目工作区随项目走。"从招标文件解析出评分表/清单"是解析力，解析结果是项目事实——两者都不在知识库。复用：合同要点/政策文件/审计底稿抽取。现状 parse_tender 雏形；落地 `doc.parse_structured` + project_facts schema，评分表先验/★▲语义查②方法论不硬编码。
- **证据挂接（双轨证据拼装）**——结构化资信资产库（业绩/人员/证书 + evidence_images）按条件选证 → 自动业绩表+图片组贴扫描件，正文数字与扫描件同源一致。复用：申报材料、审计底稿、法务卷宗、售前案例库。资产 schema 入②OpenSoul，SoulMate 出"选证+贴图"工具。
- **文本独特性指纹（去同构/防雷同）**——n-gram/词频指纹+相似度检查（章节间/对素材/对历史标书）+ 写作约束（实体锚定重写、句式结构轮换）。复用：内容查重、公众号原创检测、去 AI 腔。现状 dedup.py TF-IDF 雏形；约束并入 C1 写作协议。

### 四大坑 → 去向对照

| 坑 | 去向 |
|---|---|
| Decimal 报价/大写 | C3（①） |
| Pillow 压图 ≤300KB | C6（①） |
| LibreOffice TOC 页码+PDF 终稿 | C7（①） |
| 废标负样本库 | ②知识（见二层） |

---

## 二、知识分账：项目事实（随项目走）× OpenSoul 知识库（②，仅跨项目恒定）

**分账原则（用户修正 2026-11-17）："评分公式、封标清单、这些是每个项目不同的，肯定是要根据招标文件分析出来的。"**

- **项目事实**——每项目从**自己的**招标文件解析产出（能力层附属项"文档解析力"），存**项目工作区、随项目走**，**禁止入 OpenSoul 知识库**：本项目评分办法/评分公式实例与参数、封标清单（投标有效期/保证金/签署/份数/递交截止）、资格项、★▲条款、本项目废标条款、本项目格式要求、补遗/澄清覆盖记录。
- **OpenSoul 知识库只收跨项目恒定**：①法规条文（87 号令/评审 29 禁/112 项中的通用法规类）②评分写法方法论（三档法/小项累加/详细分档的写法规律，真实评分案例作范例）③废标案例负样本（作为审查方法）④行业统计规律（高频资质/典型分值分配）。
- 原"7 项入库清单"的分账结果：**评分公式/封标清单/资格项/★条款/本项目废标条款/本项目格式要求 → 项目事实（见 2.1），不入库**；暗标格式规则库拆两半（本项目格式要求→项目事实；新点/广联达平台级清标规则→OpenSoul 法规类，scope=平台）；compliance.py 70 条同理拆（通用法规类审查模式→知识，具体天数/金额参数→项目事实）。
- 与 C1 事实锁的分工：事实锁=**我方承诺**（工期/质保/人数/技术路线）；project_facts=**招标方要求**。两者都为一致性服务，方向不同，勿混。

### 2.1 项目事实 = 解析产出规格（SoulMate 解析力产出，不入库）

```yaml
# <项目工作区>/project_facts.yaml —— 随项目走，禁入 OpenSoul 知识库
project_facts:
  scoring:             # 评分表：[{item, score, type, criteria, level_desc, 出处}]
  scoring_formulas:    # 本项目价格评分公式实例与参数（最低价法/平均价法/线性插值哪种、限价、权重）
  seal_checklist:      # 封标清单：投标有效期/保证金/签署/份数/递交截止/开标时间…
  qualifications:      # 资格项（资质/业绩/人员硬性要求）
  starred_clauses:     # ★/▲ 条款（★=实质性响应项，▲=重要项）
  disqualifications:   # 本项目废标条款
  format_requirements: # 本项目格式要求（暗标字体/行距/装订…）
  addenda:             # 补遗/澄清覆盖记录（含 conflicting 状态与用户裁决结果）
```

### 2.2 OpenSoul 入库清单（四类恒定知识）

**知识条目形状**（统一封装；valid_until/confidence 即 OpenSoul 侧保质期/置信度机制的载体，见 2.4）：

```yaml
id: bidding/law/regulation-87          # 域/类型/标识
type: law | methodology | negative-sample | stats | template-text
domain: bidding
title: 政府采购货物和服务招标投标管理办法（87号令）
content: <结构化正文/表格/正则>
metadata:
  source: <出处：法规原文/技能资产/企业废标通报/招标文件学习库统计>
  valid_from: 2026-01-01                # 时间锚点 → freshness 计算
  valid_until: null                     # null=长期（不告警）
  confidence: verified | candidate | reference
  scope: 通用 | 辽宁公共资源 | 新点平台   # 适用范围
tags: [法规, 废标]
```

| # | 恒定知识 | 来源 | type | 状态 |
|---|---|---|---|---|
| 1 | **法规条文**：87 号令、政府采购法及实施条例、招投标法体系、评审 29 禁、112 项清单中的**通用法规类**条目、平台级暗标清标规则（新点/广联达口径，scope=平台） | 法规原文 + 技能资产筛选 | law | 迁移（从 skills/bidding 只筛通用法规类） |
| 2 | **评分写法方法论**：三档法/小项累加/详细分档的写法规律 + 真实评分案例（作范例） | real-world-scoring-examples.md + 9450 份招标文件提炼 | methodology | 迁移/扩充 |
| 3 | **废标案例负样本**（作为审查方法）：企业 3 年丢分项/废标通报 → 正则+审计规则模式 | 企业内部（文章坑 4） | negative-sample | 新建（缺） |
| 4 | **行业统计规律**：高频资质要求分布、典型分值分配（价格 30+技术 50+商务 20 式） | 9450 份招标文件统计 | stats | 新建（缺） |
| 5 | （附）施工工艺套话库：国标工艺模板文本——不随项目变，按恒定原则保留入库 | 9450 份提炼 | template-text | 新建（缺）；如收紧口径则只入"高频句式规律"（并入 #4）不出全文模板 |

**明确不入库**（原清单误列，已改列 2.1 解析产出）：~~评分公式~~、~~封标清单~~、~~资格项~~、~~★▲条款~~、~~本项目废标条款~~、~~本项目格式要求~~。

### 2.3 SoulMate 写标书时如何取数（检索接口与数据流向）

1. **解析阶段**：解析力（能力层附属项）从本项目招标文件抽事实 → 写 `<项目工作区>/project_facts.yaml`；解析先验（评分表长什么样/★▲语义/合并格处理）查 OpenSoul #2 方法论。
2. **写作/报价/审查阶段**：项目参数（评分公式实例/封标清单/资格/★条款/格式要求）**一律读 project_facts.yaml，不再检索知识库**；写法查 #2，审查规则查 #1+#3（Red-Team 负样本），行业分布参考 #4。
3. 接口：插件能力位 `knowledge.read`（plugin-system-design.md §能力清单）→ OpenSoul `/api/search?q=…&user_id=`（FTS5，返回 confidence）或 `/api/knowledge` 按 `domain=bidding&type=…` 过滤；Hermes 场景走 MCP `search_knowledge`。
4. 检索结果强制带 confidence + freshness 标注（机制在 OpenSoul 侧，见 2.4），过期规则命中提示复核而非直接采用；**项目事实不回流知识库**（防项目数据污染跨项目知识）。
5. 结构化企业资产（证据挂接的业绩/人员/证书）是企业级恒定事实，入 OpenSoul（entities/relations + metadata.evidence_images），同样不建私有库。

### 2.4 保质期与置信度 = OpenSoul 知识库的条目属性与管理机制（原"能力层 C7"改列本层）

**"保质期应该是给 opensoul"**——答案保质期/置信度不是 SoulMate 能力，是知识库自身的治理机制：

- **条目属性**：`valid_from/valid_until`（时间锚点）、`confidence: verified|candidate|reference`（置信分级）——即 2.2 条目形状的 metadata 字段。
- **管理机制**（OpenSoul 侧实现，参考 LingYan freshness.py/roles.py）：过期检测（valid_until 已过=expired）、临期提醒（≤90 天=expiring）、陈旧提醒（落款/签订≥3 年=stale，「长期/永久」不告警）、置信度分级打标（人工确认=verified 优先于 LLM 抽取=candidate）、过期/临期条目主动提醒维护者。
- **SoulMate 只保留一条薄纪律**：引用②知识时**过期不使用、低置信走 C2 留白协议**（不硬着头皮用过期事实，也不因此自造）——判断依据来自检索结果附带的标注，不在 SoulMate 内做保质期计算。
- 复用：客服 FAQ、售前问答、运维 runbook——一切知识库都需要这套治理。

---

## 三、投标流程层（③，插件纯 YAML，零业务代码）

`plugins/bidding/` 只留流程定义 + 工具注册清单（指向①能力与②知识检索）：

```yaml
name: bidding-flow            # plugins/bidding/flows/bid.yaml
steps:
  - id: parse                 # 拆标：锚点定位三关键位置（须知前附表/评标办法前附表/采购需求★▲）
    capability: doc.parse_structured          # 跨页评分表 HTML 保留 rowspan/colspan
    knowledge: [bidding/methodology/*]        # 只查解析先验（★▲语义/评分表结构），不查项目数据
    output: project_facts                     # 项目事实落项目工作区（2.1），随项目走
    sources: multi            # 主文件+补遗/答疑：角色化摄入，冲突标 conflicting 交人裁决
  - id: facts                 # 全局事实锁
    capability: write.fact_lock               # C1
    confirm: human
  - id: outline               # 目录按评分项组织，挂来源 ID（MAND/REQ/SCORE 式血缘）
    capability: write.outline
  - id: write                 # 逐节生成：留白纪律+证据挂接+去同构
    capability: write.long_doc                # C1/C2 + 附属项（证据挂接/文本指纹）
    facts: project_facts                      # 项目参数读工作区，不检索知识库
    knowledge: [bidding/methodology/*, bidding/template-text/*]
  - id: review                # 合规审查：检查算子 + Red-Team 负样本
    capability: doc.audit
    facts: project_facts                      # 本项目封标清单/★条款/废标条款=审查依据
    knowledge: [bidding/law/*, bidding/negative-sample/*]
  - id: compile               # 确定性编译+暗标清洗+Mermaid入图+压图+TOC/PDF
    capability: doc.compile                   # C4/C5/C6/C7
    knowledge: [bidding/law/*]                # 平台级清标规则（scope=平台）；本项目格式要求读 project_facts
```

**现有 plugins/bidding（3519 行）处置表**：

| 现有 | 处置 |
|---|---|
| bid_engine.parse_tender（前附表/评分表/★▲参数/废标条款抽取） | 通用结构化抽取→①解析力；抽取语义先验（★▲/评分表结构）→②方法论；**本项目抽取结果→项目事实 project_facts**；编排→③YAML |
| build_outline / _section_paragraphs（9 章节模板） | 模板段落→②template-text（跨项目恒定写法素材）；生成→C1；**删硬编码章节**（这就是"写死的投标系统"本体） |
| generate_bid 断点续跑（checkpoints JSON） | 思想保留→流程引擎通用断点（OpenBidKit/LingYan 同款），③引用 |
| _consistency_audit | 升级为 C1 事实锁审计 |
| _write_docx + exporter.py 双实现 | 合并为 C4 编译器（exporter OpenXML 路线为基） |
| compliance.py 70 条规则 | **拆两半**：通用法规类审查模式→②law；项目参数（具体天数/金额）→项目事实；执行→①审计算子 |
| dedup.py | 升级为附属项文本指纹 |
| price_calc | 升级为 C3（Decimal+大写） |
| parser/generator/outline.py（LLM 版，MiMo 硬编码） | 并入 C1 写作协议；模型路由归 SoulMate，删硬编码 |
| store.py knowledge 表 | 迁②OpenSoul；插件只留流程状态 |

---

## 四、差距清单（对照 plugins/bidding 现状，按性价比排序）

验证口径：47 测试绿（~/opensoul/.venv，2026-11-17 复跑）；真实文件端到端已跑通。
⚠️ 仓库内数据矛盾待修：README"70+ 规则" vs compliance.py docstring"300+ 条" vs **实际 70 条**（已数）。

| 序 | 项 | 现状 | 归层 | 性价比 |
|---|---|---|---|---|
| 1 | C3 Decimal 精算+大写 | ❌ 缺（float 裸算） | ① | **P0** 纯函数小改，消除废标级风险 |
| 2 | C1 全局事实锁 | ◐ 仅事后审计雏形 | ① | **P0** 中量，根治头号死穴 |
| 3 | C2 留白/待补协议 | ❌ 缺（LLM 可编数字） | ① | **P0** prompt+校验器起步 |
| 4 | 知识迁移（**只迁**通用法规类/方法论/负样本/统计规律；项目类改列解析产出） | ❌ 缺 | ② | **P0** 纯数据搬迁，立即解锁流程层可配置 |
| 5 | C4 编译器统一+暗标清洗 | ◐ exporter 雏形 | ① | P1 合并双实现+scrub |
| 6 | C5 Mermaid 入图管线 | ❌ 仅标记解析 | ① | P1 渲染+插入 |
| 7 | C6 压图算子 | ❌ 缺 | ① | P1 与图管线同批，纯函数 |
| 8 | 多源摄入（补遗/澄清角色化+冲突登记） | ❌ 单文件解析 | ①解析力+③流程 | P1 覆盖策略用 LingYan 保守式（冲突标+人裁决，勿做自动覆盖） |
| 9 | C7 TOC 页码更新+PDF 终稿 | ❌ 仅 TOC 域 | ① | P1 soffice 脚本，工作量小 |
| 10 | 证据挂接（业绩库+选证贴图） | ❌ knowledge 表非结构化 | ①附属+② | P2 需②schema 先行 |
| 11 | 保质期/置信度管理机制 | ❌ 缺（搜索仅有 confidence 字段） | **②OpenSoul**（SoulMate 仅 C2 薄纪律） | P2 检索层补 freshness/过期提醒 |
| 12 | 文本指纹+实体锚定重写 | ◐ TF-IDF 章节查重 | ①附属 | P2 防串标雷同 |
| 13 | C4 修订溯源纵深（w:ins/w:del+自校验+批注） | ❌ 缺 | ① | P2 大件（LingYan 3645 行为参照，MIT） |

## 五、下一轮开发规格（第一批 P0，可直接开工）

1. **money.py**（①C3）：Decimal 报价计算 + 中文大写转换，纯函数 + 单测（13356305.2 → 壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角；0.7→柒角整等边界）。
2. **fact_lock**（①C1）：`locked_facts` schema + prompt 注入器 + audit 扩展（锁值缺失/多值/矛盾点名），接到 SoulMate 长文写作协议（不限投标）。
3. **知识迁移脚本**（②）：**只迁四类恒定知识**——skills/productivity/bidding 与 compliance.py 里的通用法规类条目（87 号令/29 禁/112 项法规类）、评分写法方法论（含真实案例范例）、审查方法模式 → 按 2.2 条目形状封装入 OpenSoul `/api/knowledge`（domain=bidding，带 confidence/scope/valid_*）；负样本与行业统计规律同步新建。**评分公式/封标清单/资格项/★条款等项目类一律不迁**（将由解析力产出为 project_facts）。验证 `knowledge.read` 检索回读 + freshness/confidence 标注。
4. 顺手修数据矛盾：compliance.py docstring"300+"改"70"（或补足规则至宣称值，二选一）。

**红线**：此后不在 plugins/bidding 新增任何业务实现代码——新逻辑一律归①SoulMate 能力或②OpenSoul 知识，插件只加 YAML。
