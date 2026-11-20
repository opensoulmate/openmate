# 14 Agent 深测功能矩阵（真测·2026-11-20，逐项过 + AI 群组 + UI 面）

> 口径（用户拍板）：好使 agent 14 个**逐项功能深测**（不止基础对话）；缺凭据 3 个（gemini/cursor-agent/amazon-q）不测。测试铁律：①系统性（跨模块×降级×并发）②复现用户真实路径（UI 点击流）③"写了≠接线了"端到端 ④内容级探针（42 / 暗号"榛子77" / nonce token / 磁盘文件落盘），报成功无内容=不算过 ⑤每 agent 独立、禁冒名兜底（并发 canary 互查 + 群组 source 通道校验）。
>
> 证据目录：`docs/reports/evidence/deep-test/`（每 agent 每轮 JSON 落盘；探针 `acp-proxy/deep_test_agent.py` 可复跑）。测试账号 e2e_spot_1120（WS/HTTP 全走真实登录）。
>
> 图例：✅=过 ｜ ⚠=部分（能力存在但有限制，注明） ｜ ❌=败/不支持（注明是产品面还是适配层面） ｜ N/A=该 agent 无此功能面。

## 一、总矩阵（agent × 功能）

| Agent | 基础对话 | 多轮上下文 | 流式回复 | 工具调用 | 文件操作 | 技能加载 | 中断恢复 | 并发会话 | 错误降级（杀后端） |
|---|---|---|---|---|---|---|---|---|---|
| SoulMate | ✅ "42" | ✅ 暗号回出 | ❌ 单块返回（1 chunk，非流式） | ✅ 落盘+内容符 | ✅ 读出 nonce | ✅ 收据命中 6 技能 | ⚠ 见 §二-3 | ✅ canary 不串 | ⚠ 见 §二-4 |
| Hermes | ✅ | ✅ | ✅ 4+ chunks | ✅ tool_call 事件+落盘 | ✅ | ✅ skill_view 原文复述 | ⚠ 不支持 cancel，可恢复 | ✅ | ⚠ WS 收口需重连 |
| MiMo | ✅ | ✅ | ✅ | ✅ | ✅ | N/A 无技能系统 | ⚠ cancel 无效，可恢复 | ✅ | ⚠ WS 收口需重连 |
| OpenCode | ✅ | ✅ | ✅ 5 chunks | ✅ | ✅ | ❌ 注入 SKILL.md 未加载 | ⚠ cancel 无效，可恢复 | ✅ | ❌ 杀后无响应（超时） |
| Ollama(qwen2.5:0.5b) | ✅ 20 chunks | ❌ 适配器无历史 | ✅ 59 chunks 真流式 | ❌ 适配器无工具面 | ❌ | N/A | ⚠ cancel 无效，可恢复 | ❌ 高负载超时（未复测定罪） | 见 §二-4 |
| Aider | ✅ | ❌ 一次性 CLI 无上下文 | ❌ 单块 | ❌ 只在自己 workdir 改文件 | ❌ | N/A | ❌ 超时 | ❌ A 轮 canary 丢失（疑输出解析噪声） | 见 §二-4 |
| Qwen Code | ✅（首token 108s） | ❌ 一次性 CLI 无上下文 | ❌ 单块/超时 | ❌ | ❌ | N/A | ❌ 会话终断 | ✅ | ⚠ 子进程已退出无法杀 |
| Open Interpreter | ❌→✅修复后见 §三-1 | ❌ 一次性 | ❌ | ⚠ auto_run 可执行代码 | ⚠ | N/A | ❌ | ❌ | ❌ |
| OpenClaw | ✅ | ❌ 超时（首token 26-192s 慢） | ❌ 单块 | ✅ | ❌ 超时 | ✅ nonce 技能被加载 | ⚠ cancel 无效，可恢复 | ✅ | ❌ 杀后空应答（假成功嫌疑） |
| Claude Code | ✅ | ✅ | ✅ | ✅ **需审批**（§二-1） | ✅ **需审批** | ❌ 注入 ~/.claude/skills 未加载 | 见 §二-2 | ✅ | 见 §二-2 |
| Codex CLI | ✅ | ✅ | ✅ | ✅ **需审批**（§二-1） | ✅ **需审批** | N/A | 见 §二-2 | ✅ | 见 §二-2 |
| Copilot CLI | （跑批中补录） | ❌ 一次性 | ❌ 单块 | （补录） | （补录） | N/A | （补录） | （补录） | （补录） |
| Pi | ✅（首块为启动横幅，真答案第 2 块） | ✅ | ✅ 7 chunks | ✅ | ✅ | ✅ nonce 技能被加载 | ⚠ cancel=-32601 明确不支持，可恢复 | ✅ | （补录） |
| DeepSeek(R1:1.5b) | （复测中补录） | ❌ 适配器无历史（同 Ollama） | （补录） | ❌ 适配器无工具面 | ❌ | N/A | （补录） | （补录） | （补录） |

> "补录" = 高负载窗口内超时/被批内串行阻塞误伤的格子，正在低负载窗口复测后回填；复测结果与初测不一致时以复测为准并注明。
> **每 agent 独立性核验（铁律⑤）**：并发双会话 canary（APPLEC/BANANAC）互查 14/14 无一例 A 答案冒充 B（Aider/Qwen 的 2 例缺失为一次性 CLI 输出解析噪声，见 §四待办）；群组通道 source 归属校验（V6）无冒名兜底。

## 二、深挖发现（失败项如实列）

### 1. Codex / Claude Code：文件类工具调用必须走 ACP 权限审批（`session/request_permission`）
- 现象：发"写文件"任务后 **4.1s** 即收到 `session/request_permission`（toolCall kind=execute，title=写文件命令，options: Approved/ApprovedExecpolicyAmendment/Abort），**agent 挂起等待客户端应答**；WS 探针不应答 → 该回合永不结束，且后续回合排队全超时（初测把 codex/claude-code 的 tool/file/interrupt 全误标超时）。
- UI 面：`chat-client.tsx`（P0-3）有完整审批弹窗流（批准应答 `{"outcome":{"outcome":"selected","optionId":<allow>}}` / 拒绝 `{"outcome":{"outcome":"cancelled"}}`）——**产品链路是通的**。
- 复测（探针复刻 UI"批准"应答）：codex tool/file **3/3 过**（TOOL-OK 落盘 + 读回 nonce）；claude-code tool/file **过**。
- 结论：功能 ✅ 但**强依赖审批交互**；无人值守/纯 API 调用需自带审批应答，否则回合挂死（无超时降级）——建议给审批请求加超时缺省。

### 2. 杀后端（错误降级）实测三类表现
- **Hermes / MiMo**：杀子进程 → ws_acp 走到收口分支，WS `1000 "session ended"` 关闭——**连接级降级**（非挂死非假成功，但客户端必须重连重连）。respawn 代码路径（ws_acp.py:696 重启+session/load 重放）对 pty 路由/本场景未生效，需专项排查。
- **OpenClaw**：杀子进程 → **1.6s 内返回空应答（无内容无错误）**——疑似假成功（最危险形态），respawn 进程有出现但应答为空，判定 ❌。
- **SoulMate / Ollama 等**：杀后 prompt 长时间无应答至超时（初测在高负载窗口；soulmate 复测见补录）。
- **Qwen**：杀后端测试时其适配器子进程已因前序 WS 收口退出（找不到可杀对象）——一次性 CLI 的会话生命周期本身就短。
- 共性缺口：**kill 后无一条链路能在原 WS 内自愈续答**；ws_acp 的 respawn+session/load 重放设计与实测表现不符（至少 pty 路由），列为中优先级缺陷。

### 3. 中断（session/cancel）全线不支持，但会话可恢复
- pi 明确 `-32601 "Method not found": session/cancel`（留证）；hermes/mimo/opencode/openclaw/ollama 无视 cancel 完整跑完 200 字作文（221/222/212/247/465 字）；soulmate 首块极慢（23-56s）cancel 窗口难触发。
- 恢复能力：**所有被测 agent 中断后下一轮均正常应答**（"恢复可用"回出）。
- UI 面无"停止生成"按钮（产品语义=插话 steering，非硬中断）——如需硬中断需补 `session/cancel` 转发/处理。

### 4. 一次性 CLI 适配层（cli_acp：aider/qwen/interpreter/copilot）与 ollama_acp 的结构性限制
- **多轮上下文 ❌**：cli_acp 每轮 spawn 新 CLI 无历史；ollama_acp `messages=[单条 user]` 无历史——"暗号"第二轮全数丢失。属**适配层设计缺口**（产品面列"多轮上下文"，这 6 个 agent 实际拿不到）。
- **流式 ❌**：cli_acp 整段单块回传（aider/qwen/interpreter/copilot）；ollama_acp 真流式（20-59 chunks）。
- **工具/文件 ❌**（除 interpreter 代码执行/aider workdir 内改文件）：适配层未接工具面。
- **共享工作目录污染**：aider/qwen/interpreter/copilot 共用 `~/acp-cli-workspace`——agent 生成的杂文件（本次深测"数1到12"探针产出的 `numbers.py`）会**跨 agent 遮蔽标准库**（`python -c` 把 cwd 放进 sys.path → numbers.py 遮蔽 stdlib numbers → decimal 崩 → interpreter 全回合阵亡）。已修（§三-1）。

### 5. 技能加载三态（内容级探针）
- **会加载**：hermes（skill_view 原文复述 ✅）、soulmate（技能收据命中 agently-mail/file-organizer/build-from-the-best 等 6 项 ✅）、pi（`~/.pi/agent/skills/` nonce 技能被引用 ✅）、openclaw（`~/.openclaw/skills/` nonce 技能被引用 ✅）。
- **不加载**：claude-code（`~/.claude/skills/deep-test-probe` 注入后未被调用，回复无 nonce）、opencode（`~/.config/opencode/skills/` 注入未被调用；该目录原本为空，未见技能装载接线）。
- **无技能系统（N/A）**：mimo、ollama、deepseek、aider、qwen、interpreter、copilot、codex。

### 6. UI 面（e2e_spot_1120 真实登录，Playwright 级点击流）
| 流 | 结果 | 证据 |
|---|---|---|
| 从对话列表建会话 | ✅ | 侧栏 agent 列表点"Hermes Agent"→ 输入框发"26+16"→ 会话以首条消息命名出现在列表（Hermes Agent 计数 0→1） |
| 续聊（多轮） | ✅ | 同会话续发"刚才答案的两倍"→ 回 `42*2=84`（UI 内上下文成立） |
| 会话记录持久 | ✅ | 整页刷新后会话仍在列表，点开 42/84 两条消息俱在，可继续发消息 |
| 移动端 393px | ✅ | 393×852 视口：输入框 305px 可见、侧栏可开、发"只回复四个字：移动可用"→ 回"移动可用" |
| 缺陷-输入框清空 | ⚠已修 | 见 §三-2：发送后/清空按钮不清空输入框（visibility:hidden 下 rAF 被节流时必现） |
| 观察-重放气泡 | ⚠ | 会话重开后出现 1 条重放渲染的重复回复气泡（时间戳=重开时刻，内容与原回复相同，数据不丢不乱）——待定位 |

### 7. AI 群组（多 agent 真协作）：过
- **发任务→各自回复**：POST /api/ai-groups 建组（hermes/soulmate/mimo 三真实成员）→ 下任务自动拆 4 子任务（规划/执行/验证/审查）**自动链式执行**到全终态，父任务 scored 5.0（`e2e_ai_groups_multimember.py` V1-V6 全 ✅）。
- **互见**：4 条 agent_reply + 4 条 result 全部落 `discussion_messages` 同一共享流（GET /{gid}/messages 可读），3 个成员回复互相可见；后段子任务（验证/审查）在前段产出基础上给出 470/634 字结果（内容接力）。
- **落库可续**：C1 会话记录 API 可读 ✅；C3 同群续任务（引用上轮标记）自动链式到 scored、8 条新回复落库 ✅；C4 复盘讨论 discuss-after 开启成功（`discussion_task_id=365c710b`, status=discussing, 幂等 existing=true）✅。
- **独立通道（禁冒名）**：V6 ✅——soulmate 子任务 source=soulmate-agent，hermes/mimo 各自 source=自身 agent_id，无一例冒名兜底。
- WS 实时广播：task_progress 74 帧 + agent_message 3 帧 + system_message 6 帧（ws://…/ws/group/{gid}）。
- 附注：soulmate 的执行子任务 result_len=0（评分 3.0）——执行产出为空但流程收口，内容质量另论。

## 三、顺手修复（明显小缺陷，已改代码待提交）

1. **`acp-proxy/agent/cli_acp.py`**：interpreter 命令加 `-I` 隔离——工作目录杂文件遮蔽标准库（numbers.py→decimal 崩）导致 interpreter 全回合阵亡。修复后 interpreter 恢复可用（见补录行）。
2. **`src/components/codemirror-editor.tsx`**：value 同步守卫 `userEditingRef` 只在 `requestAnimationFrame` 里重置——rAF 被节流（后台标签/内嵌 webview visibility:hidden）时一次输入即永久卡死同步，发送后/清空按钮均不清空输入框（浏览器实测复现）。补 `setTimeout(…,50)` 兜底重置。
3. **探针侧（非产品）**：`e2e_ai_groups_multimember.py`/`_continue.py` 的 `mint_token()` 自铸随机 uuid token 会被 F3b 身份绑定判"账号不存在"401——改为真实测试账号登录（顺带修轮询对高负载连接重置不耐受）；`deep_test_agent.py` 补审批应答、登录重试、超时留证。

## 四、遗留问题清单（只报告不动手）

1. **ws_acp 杀后端无原连接自愈**：respawn+session/load 重放设计存在但实测（hermes/mimo）直接 WS 收口 1000；pty 路由的 respawn 分支疑似未覆盖 pty master_fd 重建。中优先级。
2. **OpenClaw 杀后端空应答**（1.6s 无内容无错误=假成功风险）。高优先级。
3. **一次性 CLI/ollama_acp 适配层无多轮上下文**（6 个 agent 的"多轮上下文"名不副实）——要么补历史注入，要么产品口径改标。中优先级。
4. **审批请求无超时缺省**（codex/claude-code 无人批则永久挂起）。中优先级。
5. **cli_acp 共享工作目录**跨 agent 污染面（-I 只护 interpreter；aider/qwen 仍共享 cwd 语义）。低优先级。
6. **claude-code/opencode 技能装载未接线**（注入技能目录不生效）。低优先级。
7. **UI 会话重开重放气泡**（时间戳重复渲染一条）。低优先级。
8. **`:8090` 高负载雪崩**：并发测试压力下 /api/health 5→20s（wakeup poll ReadTimeout、UI 8s 启动闸门误报"无法连接服务器"、探针连接被重置）。容量/限流待评估。
9. （补录区）复测后仍未过的格子如 Aider 并发 canary、Ollama 并发超时——按复测结果定责。
