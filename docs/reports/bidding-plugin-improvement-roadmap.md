# 招投标插件改进路线

> 2026-11-17 · 依据：①《趴一趴市面上智能标书软件》（存 `~/wiki/raw/articles/bidding-software-survey-wechat.md`）②LingYan-Bid-Agent（joychin，MIT）克隆源码精读（sidecar 157 个 py：docx_ops 3645 行 / evidence-rules / assemble_tender / validate_body / freshness / roles）③OpenBidKit_Yibiao + BidMaster-Pro 对标（`openbidkit-yibiao-benchmark.md`）④`plugins/bidding/` 现状盘点（47 测试于 ~/opensoul/.venv 复跑全绿：bidding 13 + plugin_system 34；真实招标文件 LNFH2024036 四工具跑通见 `bidding-verification/`）。
> 调研笔记：`~/wiki/entities/lingyan-bid-agent.md`、`~/wiki/concepts/bidding-agent-core-capabilities.md`、`~/wiki/comparisons/bidding-software-landscape.md`。

## 〇、架构定调（用户钉死原则，本路线的宪法）

**"写标书/写方案 = agent 基础能力；重点是强化 SoulMate 能力，其余仅仅是流程；不应该是一个写死的投标系统。"**
**"领域知识也是知识的一种，应该是在 opensoul 内的。"**

三层归属最终版：

| 层 | 归属 | 内容 | 纪律 |
|---|---|---|---|
| ① 能力 | **SoulMate 基础能力层** | 长文写作+全局事实锁、表格/流程图/配图（Mermaid 管线）、Word/PDF 确定性编译（OpenXML+暗标清洗）、证据挂接、Decimal 精算、答案保质期/置信度 | 最高优先；每项必须能被非投标场景复用 |
| ② 知识 | **OpenSoul 知识库** | 评分公式/112 项封标清单/评审 29 禁/废标负样本/真实评分案例/暗标格式规则/工艺套话 | 走 OpenSoul 检索/记忆体系；**禁止做成插件内数据文件或硬编码规则** |
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

### C2 留白与待补出口（Abstention）
- **规格**：写作纪律协议——证据不足标 unknown/待补，禁止行业惯例/模型记忆补全；承诺类数字只出自用户确认的清单，拍板前不写正文；待补项输出**待人工补充清单**（正文禁【待补】占位文字，正规落点=Word 批注）。
- **非投标复用**：合同审查、审计报告、申报材料、任何 AI 辅助专业文档（编数字=职业事故）。
- **依据**：LingYan evidence-rules.md（conflicting/unknown/「未发现」限已查范围）+ 关键事实与承诺.md + docx_comment_add + validate_body 判不过占位。
- **现状**：无。generator LLM 自由发挥，可编造工期/人数/业绩。
- **落地**：约束协议（prompt + 确定性校验器扫描占位/无据数字）；批注出口依赖 C3。

### C3 Word/PDF 确定性编译（OpenXML + 暗标清洗 + 修订溯源）
- **规格**：LLM 只出 JSON AST → 确定性 Python（python-docx/lxml）渲染 docx（样式/多级编号/TOC 域/分节符/横向表格/图片/题注）；**暗标清洗步**（docx 元数据擦除——作者/公司/修订历史、样式规整、禁空行/彩色像素等规则按②知识执行）；可选**修订标记输出**（w:ins/w:del + "拒绝全部修订后与原文逐字一致"自校验 + Word 批注）；LibreOffice headless 更新 TOC 页码 + 导出带书签 PDF 终稿。
- **非投标复用**：公文、合同、简历、任何格式强约束交付（政府/央企）；修订溯源=任何需人审的 AI 产出文档；元数据擦除=方案文档敏感信息处理（本机已有血泪教训）。
- **依据**：文章模块 5 + 坑 3；LingYan docx_ops（修订标记/自校验/合册压平）；OpenBidKit 独立 .NET OpenXmlHelper（JS 做不好 OOXML 的旁证）。
- **现状**：`exporter.py` 439 行 lxml+zipfile 直接 OpenXML（TOC 域/多级编号/分节/横向表格）=雏形；无图片、无修订、无清洗、无 PDF。且 `_write_docx`（python-docx）与 exporter **双实现并存**。
- **落地**：统一 AST→docx 编译器（以 exporter 的 OpenXML 路线为基，吸收 _write_docx 封面/表格能力）；`scrub` 步骤；soffice 渲染节点脚本。修订溯源是 P2 大件（LingYan docx_ops 3645 行，MIT 可借思想/移植）。

### C4 表格/流程图/配图生成（Mermaid 入图管线）
- **规格**：模型只给语义拓扑（mermaid 语法或节点+连线 JSON）→ 本地渲染 PNG（支持灰阶，暗标打印安全）→ InlineShape 嵌入 + 自动"图 X-X"题注；表格（合并格/三线表）由编译器拼装（承诺数据用表格不用图片——可编辑可校验，LingYan 口径）；扫描件/证书图插入前 **Pillow 压缩**（长边 1600px、RGB、JPEG 82%、单张 150-300KB，总包控在上传限 50-100MB 内）。
- **非投标复用**：产品说明书、技术方案、课件、周报图表、任何文档插图。
- **依据**：OpenBidKit Mermaid 内联编译（正则捕获→本地渲染→InlineShape）；LingYan docx_diagram_insert（表格拼装图示）/mermaid 渲染（高度 18cm 封顶）；文章坑 2（300MB Word 传不上去）。
- **现状**：generator.py 只发 `<!--TABLE/--><!--MERMAID/--><!--IMAGE/-->` 标记，无渲染无插入；exporter 无图片通道。
- **落地**：mmdc（mermaid-cli）或 matplotlib 渲染器 + 图片元素进 C3 + Pillow 压缩算子。

### C5 证据挂接（双轨证据拼装）
- **规格**：结构化资信资产库（业绩表/人员表/证书表 + evidence_images 扫描件路径）→ 按评分条件选证 → 自动生成业绩一览表 + 图片组渲染指令贴扫描件——**正文数字与扫描件同一数据源，天然一致**。
- **非投标复用**：申报材料、审计底稿、法务卷宗、售前案例库、CRM 投标档案。
- **依据**：文章模块 3（bid_performances/bid_personnel 表结构 + image_group 渲染指令）。
- **现状**：store.py `knowledge` 表（category/title/content/tags，关键词 LIKE）——非结构化，无证据图挂接。
- **落地**：资产 schema 入②（见二层）；SoulMate 提供"选证+贴图"工具消费检索结果。

### C6 数值精算（Decimal + 中文大写）
- **规格**：LLM 只提参数 → Decimal 精确计算（报价/折扣率/税率/合计）→ 标准中文大写（壹仟贰佰伍拾万元整）→ 回填《开标一览表》模板。**绝对禁止 LLM 自己算钱。**
- **非投标复用**：报销单（invoice-reimbursement / dhcc-reimbursement 同源）、报价单、预算表、发票核对、财务单据。
- **依据**：文章坑 1（浮点精度丢失）。
- **现状**：price_calc 全 float；无大写转换（正文只写"人民币（详见商务标）"）。
- **落地**：`money.py` 纯函数（Decimal 计算 + digit→大写）+ 单测（如 13356305.2 → 壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角）。最小的一步，先做。

### C7 答案保质期 + 置信度分级
- **规格**：知识条目带时间锚点（有效期/签订/落款）与置信档；检索结果附"过期/临期(≤90 天)/陈旧(≥3 年)"与置信度标注，过期事实不得直接进正文（只能提示复核）。
- **非投标复用**：客服 FAQ、售前问答、运维 runbook、一切知识库治理。
- **依据**：AutoRFP/Loopio"答案保质期机制+置信度分级打标"（文章点名最值得抄）；开源参考实现=LingYan freshness.py（「长期/永久」不告警、人工真值优先）+ roles.py（fact-verified/fact-candidate/writing-reference/unknown）。
- **现状**：无（OpenSoul 搜索已返回 confidence，补 freshness 即可）。
- **落地**：实现于 OpenSoul 检索层（与②一体两面），SoulMate 消费标注。

### C8 文本独特性指纹（去同构/防雷同）
- **规格**：n-gram/词频指纹 + 相似度检查（章节间/对素材库/对历史标书）+ 写作约束（**实体锚定重写**——每段以本次招标的具体业务对象展开；**句式结构轮换**——段落骨架交替）。
- **非投标复用**：内容查重、公众号原创检测、简历防撞、去 AI 腔、一切"复用素材的写作"。
- **依据**：电子评标"文本相似度+规律性异常检测"已上线；同 SaaS 输出的目录骨架词频指纹=自证串标（文章串标雷同预警）。
- **现状**：dedup.py TF-IDF 章节间查重雏形。
- **落地**：指纹检查器 + 写作约束并入 C1 的 prompt 协议。

### 四大坑 → 去向对照

| 坑 | 去向 |
|---|---|
| Decimal 报价/大写 | C6（①） |
| Pillow 压图 ≤300KB | C4（①） |
| LibreOffice TOC 页码+PDF 终稿 | C3（①） |
| 废标负样本库 | ②知识（见二层） |

---

## 二、OpenSoul 知识库层（②，领域知识全部入库）

**原则：禁做成插件内数据文件或硬编码规则**——compliance.py 的 70 条规则、技能资产的清单文档，全部迁 OpenSoul 知识库，走其检索/记忆体系（`/api/search` FTS5+confidence、`/api/knowledge`、知识图谱 entities/relations、tags、MCP `search_knowledge`）。

**知识条目形状**（统一封装，保质期/置信度字段直接支撑 C7）：

```yaml
id: bidding/rule/seal-checklist-112     # 域/类型/标识
type: rule | scoring-case | negative-sample | format-spec | template-text | formula
domain: bidding
title: 112项封标清单
content: <结构化正文/表格/正则>
metadata:
  source: <出处：技能资产/某招标文件/企业废标通报>
  valid_from: 2026-01-01                 # 时间锚点 → freshness 计算
  valid_until: null                      # null=长期（不告警）
  confidence: verified | candidate | reference
  scope: 通用 | 辽宁公共资源 | 国网ECP    # 适用范围
tags: [封标, 废标]
```

**入库清单**（本轮迁移 + 新建）：

| # | 知识 | 来源 | type | 状态 |
|---|---|---|---|---|
| 1 | 评分公式（最低价法/平均价法/线性插值） | skills/productivity/bidding/references | formula | 迁移 |
| 2 | 真实评分案例（few-shot 池） | real-world-scoring-examples.md + 9450 份招标文件学习库 | scoring-case | 迁移/扩充 |
| 3 | 112 项封标清单、评审 29 禁、负偏离规则 | 技能资产 21 份参考文档 | rule | 迁移 |
| 4 | compliance.py 70 条规则 + BidMaster 21 算子语义 | plugins/bidding + 对标 | rule | 迁移 |
| 5 | **废标负样本库**（3 年丢分项/废标通报→正则+审计规则） | 企业内部（文章坑 4） | negative-sample | **新建（缺）** |
| 6 | **暗标格式规则库**（小四宋体/行距固定 22 磅/无空行/无彩色像素；新点/广联达清标口径） | 走量 SaaS 可抄作业项 | format-spec | **新建（缺）** |
| 7 | 施工工艺套话库（国标工艺模板） | 9450 份招标文件提炼 | template-text | **新建（缺）** |

**SoulMate 写标书时如何取知识（检索接口）**：
1. 插件能力位 `knowledge.read`（plugin-system-design.md §能力清单）→ OpenSoul `/api/search?q=…&user_id=`（FTS5，返回 confidence）或 `/api/knowledge` 按 `domain=bidding&type=…` 过滤；Hermes 场景走 MCP `search_knowledge`。
2. 按流程节点取数：parse 前查评分表结构先验；write 时查 scoring-case（few-shot）+ template-text；报价前查 formula；review 时查 rule + negative-sample（Red-Team）。
3. 检索结果强制带 confidence + freshness 标注（C7），过期规则命中提示复核而非直接采用。
4. 结构化资产（C5 业绩/人员/证书）同样入 OpenSoul（entities/relations 图谱 + metadata.evidence_images），不建私有库。

---

## 三、投标流程层（③，插件纯 YAML，零业务代码）

`plugins/bidding/` 只留流程定义 + 工具注册清单（指向①能力与②知识检索）：

```yaml
name: bidding-flow            # plugins/bidding/flows/bid.yaml
steps:
  - id: parse                 # 拆标：锚点定位三关键位置（须知前附表/评标办法前附表/采购需求★▲）
    capability: doc.parse_structured          # 跨页评分表 HTML 保留 rowspan/colspan
    knowledge: [bidding/rule/*]
    sources: multi            # 主文件+补遗/答疑：角色化摄入，冲突标 conflicting 交人裁决
  - id: facts                 # 全局事实锁
    capability: write.fact_lock               # C1
    confirm: human
  - id: outline               # 目录按评分项组织，挂来源 ID（MAND/REQ/SCORE 式血缘）
    capability: write.outline
  - id: write                 # 逐节生成：留白纪律+证据挂接+去同构
    capability: write.long_doc                # C1/C2/C5/C8
    knowledge: [bidding/scoring-case/*, bidding/template-text/*]
  - id: review                # 合规审查：检查算子 + Red-Team 负样本
    capability: doc.audit
    knowledge: [bidding/rule/*, bidding/negative-sample/*]
  - id: compile               # 确定性编译+暗标清洗+TOC/PDF
    capability: doc.compile                   # C3/C4
    knowledge: [bidding/format-spec/*]
```

**现有 plugins/bidding（3519 行）处置表**：

| 现有 | 处置 |
|---|---|
| bid_engine.parse_tender（前附表/评分表/★▲参数/废标条款抽取） | 通用结构化抽取→①；招标字段语义/正则先验→②知识；编排→③YAML |
| build_outline / _section_paragraphs（9 章节模板） | 模板段落→②template-text；生成→C1；**删硬编码章节**（这就是"写死的投标系统"本体） |
| generate_bid 断点续跑（checkpoints JSON） | 思想保留→流程引擎通用断点（OpenBidKit/LingYan 同款），③引用 |
| _consistency_audit | 升级为 C1 事实锁审计 |
| _write_docx + exporter.py 双实现 | 合并为 C3 编译器（exporter OpenXML 路线为基） |
| compliance.py 70 条规则 | 规则内容→②rule；执行→①审计算子 |
| dedup.py | 升级为 C8 |
| price_calc | 升级为 C6（Decimal+大写） |
| parser/generator/outline.py（LLM 版，MiMo 硬编码） | 并入 C1 写作协议；模型路由归 SoulMate，删硬编码 |
| store.py knowledge 表 | 迁②OpenSoul；插件只留流程状态 |

---

## 四、差距清单（对照 plugins/bidding 现状，按性价比排序）

验证口径：47 测试绿（~/opensoul/.venv，2026-11-17 复跑）；真实文件端到端已跑通。
⚠️ 仓库内数据矛盾待修：README"70+ 规则" vs compliance.py docstring"300+ 条" vs **实际 70 条**（已数）。

| 序 | 项 | 现状 | 归层 | 性价比 |
|---|---|---|---|---|
| 1 | C6 Decimal 精算+大写 | ❌ 缺（float 裸算） | ① | **P0** 纯函数小改，消除废标级风险 |
| 2 | C1 全局事实锁 | ◐ 仅事后审计雏形 | ① | **P0** 中量，根治头号死穴 |
| 3 | C2 留白/待补协议 | ❌ 缺（LLM 可编数字） | ① | **P0** prompt+校验器起步 |
| 4 | 知识迁移（skills 规则+compliance 入 OpenSoul） | ❌ 缺 | ② | **P0** 纯数据搬迁，立即解锁流程层可配置 |
| 5 | C3 编译器统一+暗标清洗 | ◐ exporter 雏形 | ① | P1 合并双实现+scrub |
| 6 | C4 Mermaid/图管线+Pillow 压图 | ❌ 仅标记解析 | ① | P1 渲染+插入+压缩 |
| 7 | 多源摄入（补遗/澄清角色化+冲突登记） | ❌ 单文件解析 | ①能力+③流程 | P1 覆盖策略用 LingYan 保守式（冲突标+人裁决，勿做自动覆盖） |
| 8 | TOC 页码更新+PDF 终稿 | ❌ 仅 TOC 域 | ① | P1 soffice 脚本，工作量小 |
| 9 | C5 证据挂接（业绩库+选证贴图） | ❌ knowledge 表非结构化 | ①+② | P2 需②schema 先行 |
| 10 | C7 保质期/置信度 | ❌ 缺 | ②（①消费） | P2 检索层补 freshness |
| 11 | C8 独特指纹+实体锚定重写 | ◐ TF-IDF 章节查重 | ① | P2 防串标雷同 |
| 12 | C3 修订溯源（w:ins/w:del+自校验+批注） | ❌ 缺 | ① | P2 大件（LingYan 3645 行为参照，MIT） |

## 五、下一轮开发规格（第一批 P0，可直接开工）

1. **money.py**（①C6）：Decimal 报价计算 + 中文大写转换，纯函数 + 单测（13356305.2 → 壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角；0.7→柒角整等边界）。
2. **fact_lock**（①C1）：`locked_facts` schema + prompt 注入器 + audit 扩展（锁值缺失/多值/矛盾点名），接到 SoulMate 长文写作协议（不限投标）。
3. **知识迁移脚本**（②）：skills/productivity/bidding 的 21 份参考文档 + compliance.py 70 条 → 按上述条目形状封装入 OpenSoul `/api/knowledge`（domain=bidding，带 confidence/scope）；验证 `knowledge.read` 检索回读。
4. 顺手修数据矛盾：compliance.py docstring"300+"改"70"（或补足规则至宣称值，二选一）。

**红线**：此后不在 plugins/bidding 新增任何业务实现代码——新逻辑一律归①SoulMate 能力或②OpenSoul 知识，插件只加 YAML。
