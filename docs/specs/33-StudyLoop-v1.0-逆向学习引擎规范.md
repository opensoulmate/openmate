# 33-StudyLoop-v1.0 逆向学习引擎规范（Study→Rebuild→Check 闭环）

> 状态：v1.0 草案（用户 2026-11-12 拍板"动手开干"）
> 语义来源：Craft Anything（Apache-2.0）的四步法与证据模型，**原生 Python 实现，归位 25 组件，不另立山头**
> 关联：31-Evolution-v1.0（evo 观察阶段扩展）、30-Sense-v1.0、open-soma-architecture.md v2.0

## 一、定位

让系统具备"**看懂任何软件/网站 → 证据化发现 → 规划搬回来 → 实现 → 比对验证**"的开放进化能力。
它是 evo 的第三种进化燃料（前两种：failure 日志、使用者习惯），把进化来源从封闭变开放。

## 二、组件归位（铁律：不新建组件）

| 循环步骤 | 归位组件 | 载体 |
|---|---|---|
| Study 研究目标 | **OpenSoma**（只读采集）+ **OpenSense**（多模态解析） | opensoul `src/soma/study/` + `/api/soma/study/*` |
| 规划/反思 | **OpenCortex** | evo `_plan`（吸收 study 发现） |
| Build 施工 | ACP builder（SoulMate/MiMo/外部 agent） | acp-proxy 既有通道 |
| Check 比对 | **OpenVision**（逐像素比对+差异清单） | study diff 工具 + Vision 组件 |
| 闭环编排 | **OpenWill** | evolution_pipeline 观察阶段 |
| 证据治幻觉 | 全链统一证据模型 | `EvidenceRecord` |

## 三、证据模型（全链统一，治"幻觉结论"）

每条结果必须是证据记录：

```json
{
  "id": "ev_<sha256>",
  "operation": "binary_triage | web_study | image_diff",
  "authority": "original-files | observed | inferred",
  "confidence": "observed | probable | speculative",
  "limitations": [{"code": "...", "message": "..."}],
  "params": {...}, "result": {...},
  "created_at": "ISO8601"
}
```

铁律：结论只能来自 `authority=original-files/observed` 的证据；`inferred` 仅作假设进 Cortex，不得直接进实施计划。

## 四、双模式

- **Clean-room**（默认）：只看访客可见（网页 HTML/截图、公开资源），法务安全
- **Deep**（显式开）：进代码/资源/二进制内部
- 模式必须出现在证据记录 `environment.clean_room`，越界调用拒绝

## 五、v1 工具箱（砍掉用不上的 70%，只做真有用的）

| 工具 | 能力 | Python 绑定 |
|---|---|---|
| `binary_triage` | PE/ELF/Mach-O 鉴定：格式/架构/入口/依赖库/打包器痕迹/资源清单 | pefile 或 LIEF（装一个即可）、python-magic |
| `strings_scan` | 可打印串+URL/路径/密钥形态启发（只标注不外传） | 自研（stdlib） |
| `web_study` | 拉页面：结构树（标题/导航/组件块）、技术指纹（框架/字体/配色）、三屏宽截图 | requests+bs4；chromium --headless --screenshot（375/768/1440） |
| `image_diff` | 两图逐像素比对：相似度%、差异热区、尺寸/留白差异 | PIL+numpy |
| `evidence_*` | 证据存储/检索/导出（SQLite `study_evidence`） | sqlite3 |

**明确不做（v2 再议）**：反汇编/反编译（capstone/Ghidra）、固件/游戏/pcap、3D 生成、WASM 插件。

## 六、API（opensoul `/api/soma/study/*`）

```
POST /api/soma/study/triage      {path}              → EvidenceRecord(binary_triage)
POST /api/soma/study/strings     {path, pattern?}    → EvidenceRecord(strings_scan)
POST /api/soma/study/web         {url, widths?}      → EvidenceRecord(web_study, 含截图路径)
POST /api/soma/study/diff        {image_a, image_b}  → EvidenceRecord(image_diff)
GET  /api/soma/study/evidence    ?op=&limit=         → 证据列表
GET  /api/soma/study/evidence/{id}                   → 单条
```

存储：`data/study/evidence.db`（SQLite）+ 截图落 `data/study/shots/`。
安全：`path` 只允许白名单根（~/openmate、~/opensoul、/tmp、用户显式传入的绝对路径）；禁遍历逃逸（resolve 后前缀校验）；strings 结果过密钥形态一律打码。

## 七、evo 钩子（31-Evolution 观察阶段扩展）

1. `acp-proxy/data/study_targets.json`：目标清单（url/path + 想学什么 + mode）
2. `evolution_pipeline.py` 观察阶段新增来源 `study`：跑目标→收证据→提纯"功能发现"（有证据支撑的差异/亮点）
3. 发现进 `_plan` prompt（与 feedback 并列），evo 规划"搬回来"的任务
4. **审核门不动**：evo 产出照旧进 Hermes 审核队列，study 发现不豁免
5. 频控：单轮 study ≤3 个目标，截图 ≤12 张，防资源爆炸

## 八、验收标准（我亲验，不收自报）

1. `binary_triage OpenMate.exe` → PE 鉴定证据（含依赖库清单）非空
2. `web_study https://climbing.top` → 结构树+三屏宽截图落盘
3. `image_diff` 同图=100%、异图<100% 且给出热区
4. 证据记录三要素齐全（authority/confidence/limitations）
5. evo 观察阶段能从 targets 清单产出至少 1 条"功能发现"进 plan
6. 测试脚本全绿（opensoul pytest + acp-proxy 验证脚本）

## 九、禁区

- 不 fork Craft Anything 代码（绑定成熟开源件，Apache-2.0 仅作方法论参考）
- 不新建组件命名（无"OpenStudy"）
- 密钥/凭据零外传，strings 命中即打码
- evo 产出必经 Hermes 审核，study 不开后门
