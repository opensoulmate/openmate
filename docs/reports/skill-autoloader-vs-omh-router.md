# 技能自动命中路由：我们 vs OMH 路由器 能力对比表

> P0 技能自动命中交付件 · 2026-11-19
> 对标对象：OMH（Oh My Hermes）技能路由器（155 技能按任务自动装进运行，"You never call them"）
> 机制引证：`omh-deepdive-vol1-engine-mechanics.md` §4（OMH 侧 file:line 全在此）
> 可抄清单来源：`omh-deepdive-vol2-adoption-map.md`（17 项清单中的技能路由相关项已全部灌入）
> 我们侧代码：`acp-proxy/agent/skill_matcher.py`（混合路由）、`skill_feedback.py`（命中记账）、
> `skill_semantic.py`（向量链）、`skill_load_verify.py`（装载验证）、`skill_loader.py`（接线收口）

## 0. 对比口径声明（先说清楚再比）

- OMH **未公开命中率数字**。其路由质量门 = `tests/test_routing_quality.py` 黄金集 100% 基线
  + `ROUTING_PRECISION_CASES` 负对照（普通问题不许被误路由）零误路由（vol1 §4.1）。
- 我们的基准（`skill_route_benchmark.py`）**同口径对齐**：黄金集 24 条换说法任务 100% 自动命中
  + 8 条负对照零误放行（实测达成，见 §2）；并额外给出 OMH 未公开的量化细分
  （top-1 / top-3 / MRR / 放行路径分布）。
- 结论"≥OMH 同类水平"= 同口径质量门达成 + 量化细分超出其公开能力面。

## 1. 能力对比表（能力 / 对手做法 / 我们做法 / 谁更强）

| 能力 | OMH 做法 | 我们做法 | 谁更强 |
|---|---|---|---|
| 触发打分 | 每技能 triggers/strong signals + 手写 situations 短语表 + 显式短语表（recommend.py:261-1130，6,613 行人工维护） | 触发词打分 15/10/5 三档 + CJK-aware 2字词门（`skill_manager.is_injectable_trigger` 单一真源）；触发词自动从任务原话学习（try_learn_skill + 账本情况语句） | **平**：机制同构；他们靠人工短语表更准，我们靠自动沉淀零维护 |
| BM25 词汇短名单 | lexical_shortlist.py:1-22，字段权重 name 3.0 / triggers 2.0 / situations 2.0 / description 1.0 / use_when 1.0 + 词干停用词 | 同款字段权重照抄（name 3.0 / triggers 2.0 / situations 2.0 / description 1.0 / tags 1.0），中文 2/3 字滑窗分词（`skill_matcher.bm25_score`） | **平**：权重表直接对齐（卷二"照抄"项） |
| "Ranking is not admission" | 入候选还要过锚词共享 + 技能前置条件（lexical_shortlist.py:21-23） | 同纪律三路证据放行：trigger 命中 / 情况语句语义 ≥T_sem / 锚词≥2+语义 ≥T_low（`skill_matcher.admit`） | **平**：纪律同款 |
| 语义向量层 | 无（纯词汇路由；换说法靠手写 situations 短语表兜） | embedding 供应商链（opensoul gland→ollama bge-m3→openai 兼容→hash）任务↔情况语句余弦，换说法直达（实测 sim 0.90-0.94） | **我们强**：换说法零摸索不依赖人工短语表 |
| 情况语句（situations） | 手写产品数据（trigger_packs/<lang>.json）+ 用户包；校验器拒未知技能 id | 自动沉淀：任务完成时原话入账本（`skill_feedback.record_hit`，去重/封顶20条/截80字） | **我们强**：零人工维护，越用越准；他们强在短语质量（人工打磨） |
| 命中反馈闭环 | 无（路由不记账；成功率在技能元数据 quality_tier 静态标注） | 三态记账（命中/有用/没用）+ Laplace 平滑增益进排序 + 任务→技能关联提升二次命中率（`skill_feedback`） | **我们强**：反馈直接进打分，二次命中率可量化提升 |
| 装载验证 | skill_load_protocol.py:1-24：nonce+digest 探针，LOAD_STATES 四态（跨宿主偏执，卷二§3判不抄） | 同名四态（all/partial/none/not_applicable）验证注入段真有技能体 + jsonl 收据（`skill_load_verify`，"写了≠接线了"验收钩子） | **平**：四态语义照抄；场景不同（他们验子代宿主，我们验自家注入面） |
| 预算裁剪留痕 | 召回包 over_budget 留痕（memory_recall_selector.py:786-811，卷二#17） | 注入预算截断自报 + 被挤掉技能 over_budget 清单进 `last_render_report`/收据（`skill_loader.render_local_section`） | **平**：理念平移（卷二#17 项） |
| 降级留痕 | 模型链 "No silent downgrade"（provider_failure_to_fallback 记录，卷二#16） | 向量供应商降级恒留痕（degraded_reason 进 stats/match_reason；实测 opensoul registry 抖动→ollama 兜底可见） | **平**：纪律同款 |
| 配置面 | 触发语言包 + 校验器 + sidecar 索引（skill_shortlist_sidecar.py）三处工件再生成 | 无配置面：账本即配置（自动沉淀）；env 可调阈值/供应商（SOULMATE_* 变量） | 他们强在可审计配置，我们强在零配置；**输一项** |
| 负对照质量门 | ROUTING_PRECISION_CASES：普通问题不许被误路由 | 基准负对照 8 条普通问题零误放行（pitfall 30 语义保留：纯描述匹配绝不放行） | **平**：同口径达成 |
| 兜底 | 兜底三件套 oh-my-hermes/plan/deep-interview（recommend.py:105-106） | 无命中=零注入（现状行为；LLM 照常干活，技能只是增益） | 他们强（有显式兜底流程）；**输一项** |
| 维护成本 | awareness.py 8,933 行 + recommend.py 6,613 行 + 短语表人工维护 | 4 个新模块 ≈ 1,100 行 + 账本自增长 | **我们强**：量级差一个数量级 |

**净结论**：机制面（触发打分/BM25/放行纪律/装载验证/留痕）全面对齐 OMH；差异面里
语义向量层 + 反馈闭环 + 自动 situations 是我们独有的"近零摸索"机制（OMH 靠人工短语表）；
输两项（可审计配置面、显式兜底），均非本 P0 目标面。

## 2. 量化命中率（skill_route_benchmark.py 实测，2026-11-19）

黄金集 24 条（8 任务类 × 3 换说法）+ 负对照 8 条普通问题。语义供应商：opensoul
`/api/gland/embed`（registry 修复前 ollama bge-m3 兜底，degraded 留痕 1 次）。

| 系统 | 自动命中率 | top-1 | top-3 | MRR | 负对照误放行 |
|---|---|---|---|---|---|
| S0 线上现状（触发词 gate，改造前） | 8.3% | - | - | - | - |
| S1 混合排序（无反馈闭环） | 20.8% | - | 100.0% | - | - |
| S2 全链路（混合+证据放行+反馈闭环） | **100.0%** | **100.0%** | **100.0%** | **1.000** | **0/8** |

- S2 放行路径分布：semantic 18 / trigger 5 / anchor 1（换说法主要靠语义路——OMH 没有的能力）；
- S1 无反馈闭环时 top-3 已 100%（混合排序质量），但放行率仅 20.8%（无证据不放行）——
  **反馈闭环是"第二次零摸索"的机制本体**（对照组断言在 test_skill_autoload_e2e 锁定）；
- OMH 同口径质量门：黄金集 100% ✅ + 负对照零误路由 ✅ → **≥OMH 同类水平达成**。

## 3. 复跑方式

```bash
cd ~/openmate/acp-proxy
/usr/bin/python3 acceptance_skill_autoload.py   # 真实场景验收（12 项断言）
/usr/bin/python3 skill_route_benchmark.py      # 量化基准（本表 §2 数字复现）
/usr/bin/python3 -m pytest tests/test_skill_autoload_e2e.py tests/test_skill_matcher.py \
    tests/test_skill_feedback.py tests/test_skill_load_verify.py -q   # 回归
```

## 4. 已知边界（诚实标注）

1. **opensoul embedding registry 抖动**：`/api/gland/embed` 的 provider 注册表当前
   openai(404)/ollama(配了不存在的 nomic-embed-text) 会被自动禁用，接口间歇 502。
   我们的链自动兜底到 ollama `bge-m3`（同一台 ollama，opensoul services/embedding.py
   同协议）并留痕；registry 修复（把 ollama provider 的 embedding 模型改成 bge-m3）
   后自动回归首选——建议 opensoul 侧跟进，属外部系统配置，未擅自改。
2. **同名技能可见性修复**（skill_manager.list_skills）：auto-learned 技能名=工具链，
   同名极常见；原按 name 折叠会吞掉后续技能（第二次永远检索不到）。现按
   (name, description) 折叠，跨层同名仍标准优先（既有测试锁定语义不变）。
3. **话题近邻陷阱**：触发词路径继承自线上现状——"帮我写PDF阅读器心得"会命中 PDF
   解析技能（trigger:pdf）。OMH 用人工守卫重排处理此类；我们留待反馈闭环
   （没用记账压分）自然压制，基准负对照按 OMH 口径只测"普通问题"。
4. **try_learn_skill 触发词质量**：触发词取任务原话最大中文段（历史行为，未动）；
   二次命中已不依赖它（锚词/语义路覆盖），但触发词精化可再提 S0 基线。
