# AI Agent 支持矩阵（真装真测·2026-11-20，二轮"mimo/ollama 当大脑"重接后）

> 口径（用户拍板）：**"好使"=经系统（OpenMate ws_acp :8092 → agent）发真 prompt、收到真回复**，证据留档 `docs/reports/evidence/`；不验证就不说正常。缺凭据=如实列出向用户要；不可装/接不了=给原因。UI 声称与本矩阵同源（opensoul `/api/agents/detect` 输出 `support_tier` 字段；虚标已全清）。
>
> 测试 prompt：`请只回答数字：26+16等于多少？`（期望真回复含 `42`）。探针脚本：JWT→ws://127.0.0.1:8092/ws/acp→initialize→session/new→session/prompt→收集 agent_message_chunk。
>
> **二轮修正（用户纠偏）**：上轮把 8 个 CLI 标"缺凭据"思路错了——这些 CLI 多数支持自定义 OpenAI/Anthropic 兼容端点，**mimo API（token-plan-cn.xiaomimimo.com）就是现成大脑**（XIAOMI_API_KEY 在 ~/.hermes/.env，gland 口径，无需各家原厂 key）。二轮按"逐个查官方文档→接 mimo/ollama→端到端真对话"重测：5 个转"好使"（claude-code/codex/copilot/pi/deepseek），3 个如实标注接不了的原因（gemini/cursor-agent/amazon-q）。

## 一、端到端好使（✅ 14/14 通过真对话）

| Agent | 接入路径 | 后端模型 | 实测证据 | 本次修复了什么 |
|---|---|---|---|---|
| SoulMate | ws_acp `soulmate`（python -m agent.start） | 系统 LLM 路由 | `e2e-soulmate-20261120-010520.json` → "42" | 基线复验通过 |
| Hermes | ws_acp `hermes`（hermes acp, pty） | hermes 模型路由 | `e2e-hermes-20261120-010537.json` → "42" | 基线复验通过 |
| MiMo Code | ws_acp `mimo`（mimo acp） | xiaomi-token-plan-cn/mimo-v2.6-pro | `e2e-mimo-20261120-014223.json` → "42" | （一轮修复 3 处：key/模型/ACP 默认模型） |
| OpenCode | ws_acp `opencode`（opencode acp） | opencode 已配 provider | `e2e-opencode-20261120-015917.json` → "42" | （一轮修复 PATH 归一） |
| Ollama | ws_acp `ollama`（agent.ollama_acp 自研桥） | 本机 ollama qwen2.5:0.5b | `e2e-ollama-20261120-015412.json` → "42" | （一轮重建适配器） |
| Aider | ws_acp `aider`（agent.cli_acp 新通用桥） | ollama_chat/qwen2.5:0.5b（本地，免凭据） | `e2e-aider-20261120-015514.json` → 含 "42" | 基线复验通过 |
| Qwen Code | ws_acp `qwen`（agent.cli_acp） | 本机 ollama qwen2.5:0.5b（OpenAI 兼容 :11434/v1） | `e2e-qwen-20261120-015618.json` → "26+16=42" | 基线复验通过 |
| Open Interpreter | ws_acp `interpreter`（agent.cli_acp） | ollama_chat/qwen2.5:0.5b | `e2e-interpreter-20261120-015802.json` → "26+16=42" | 基线复验通过 |
| OpenClaw | ws_acp `openclaw`（openclaw acp → gateway :18789） | xiaomi-coding/mimo-v2.6-pro | `e2e-openclaw-20261120-021042.json` → "42" | （一轮修复 3 处） |
| **Claude Code** | ws_acp `claude-code`（claude-agent-acp 桥） | **mimo-v2.6-pro（mimo Anthropic 兼容端点）** | `e2e-claude-code-20261120-083637.json` → "42" | **二轮转好使**：小米官方就有 Anthropic 兼容协议 `token-plan-cn.xiaomimimo.com/anthropic`（官方 Claude Code 配置指南，实测 `/v1/messages` 真回 "42"）——`~/.claude/settings.json` env 指 ANTHROPIC_BASE_URL+ANTHROPIC_AUTH_TOKEN+ANTHROPIC_MODEL=mimo-v2.6-pro。**无需协议翻译代理**；一轮"Anthropic 协议接不了"结论撤销 |
| **Codex CLI** | ws_acp `codex`（@zed-industries/codex-acp 桥） | **mimo-v2.6-pro（mimo OpenAI 兼容端点）** | `e2e-codex-20261120-084734.json` → "42" | **二轮转好使**：`~/.codex/config.toml` 自定义 `[model_providers.mimo]`（env_key=XIAOMI_API_KEY，wire_api=responses）。**踩坑 4 处**：①TOML 顶层键写在 [tui] 之后被并进小节静默失效（model_provider 没生效、codex 还在打 OpenAI）；②codex 0.170 弃用 wire_api="chat"，只剩 "responses"；③mimo 网关不支持 Responses WebSocket 传输 → `supports_websockets=false` 直走 HTTPS（否则 5 次重连空转）；④mimo 网关拒收 web_search 工具（responses_feature_not_supported）→ 顶层 `web_search="disabled"`，另 `model_catalog_json` 指向 v2.6 元数据文件消除 fallback 警告 |
| **Copilot CLI** | ws_acp `copilot`（agent.cli_acp） | **mimo-v2.6-pro（官方 BYOK）** | `e2e-copilot-20261120-083653.json` → "42" | **二轮转好使**：GitHub 官方 BYOK（docs.github.com "use-byok-models"，2026-04 起）——cli_acp copilot profile 注入 COPILOT_PROVIDER_BASE_URL/API_KEY/COPILOT_MODEL（凭据取 XIAOMI_API_KEY），**GitHub 认证可选**。一轮"要 Fine-Grained PAT"清单作废 |
| **Pi** | ws_acp `pi`（pi-acp 桥） | **mimo-v2.6-pro（pi 内置 xiaomi-token-plan-cn provider）** | `e2e-pi-20261120-084719.json` → "42" | **二轮转好使**：pi 官方内置小米 Token Plan(China) provider（凭据变量 XIAOMI_TOKEN_PLAN_CN_API_KEY），`pi update --models` 刷出 v2.6 模型目录 + `~/.pi/agent/settings.json` defaultModel。**踩坑 3 处**：①auth.json 存过期 tp- key 抢先于 env（401）→ 换 gland 现值；②`~/node_modules/.bin/pi` 是 2026-04 遗留旧 @mariozechner 分支（依赖 cli-highlight 断裂、启动即 TypeError），pi-acp 的 npm 式 PATH 前插优先捡到它 → 路由钉死 `PI_ACP_PI_COMMAND` 绝对路径 + 重命名坏符号链接；③pi-acp 启动信息里 `spawnSync("pi")` 只认 PATH（不认 PI_ACP_PI_COMMAND）→ 同上修复后干净 |
| **DeepSeek** | ws_acp `deepseek`（agent.ollama_acp + route env OLLAMA_MODEL） | **deepseek-r1:1.5b（R1 蒸馏版）经本地 ollama** | `e2e-deepseek-20261120-084028.json` → "42"（R1 风格推理 + boxed 答案） | **二轮转好使（条目如实归位）**：DeepSeek **无官方 CLI**（一轮证伪 pip deepseek-cli；本轮查实官方只出模型 API + 桌面端 **DeepSeek Harness**（macOS/Windows GUI，非 headless CLI，自带 xiaomi 供应商配置））；"deepseek-r1 经 ollama 已可用"实测**此前并未拉取**（ollama 仅有 bge-m3/qwen2.5:0.5b）——本轮实拉 deepseek-r1:1.5b（1.1GB）+ 新增 ws_acp deepseek 路由，端到端真对话通过。注意是 **R1 蒸馏版**非 671B 满血，更大变体按需 `ollama pull` |

## 二、确实接不了 / 仍缺凭据（❌ 如实标注原因）

| Agent | 卡点（实测/官方文档证据） | 结论 |
|---|---|---|
| Gemini CLI | `e2e-gemini-20261120-084745.json`："Please set an Auth method…"。查实：gemini-cli 0.70 **只讲 Gemini 协议**（bundle 0 处 OpenAI 兼容内容生成器；GOOGLE_GEMINI_BASE_URL 只换 Gemini 协议地址，不能接 OpenAI 兼容端点）；mimo 三个域名 `/v1beta*` Gemini 端点实测均 404 | **协议不通，接不了 mimo**（除非自建 Gemini 协议翻译层，本轮未做）；要好使给 GEMINI_API_KEY 或 Google 交互登录 |
| Cursor Agent | 官方文档（cursor.com/docs/cli）：cursor-agent **只连 Cursor 自家后端**，CURSOR_API_KEY 是 Cursor 账号 key，无自定义模型端点（第三方 BYOK 只有社区 TLS MITM 桥方案，不采用） | **官方架构接不了 mimo**；要好使需 CURSOR_API_KEY（Cursor 订阅/账号） |
| Amazon Q | 官方 zip 安装器 + AWS Builder IdC 交互登录；模型由 AWS 后端托管，无 BYOK/自定义端点 | **官方架构接不了 mimo**；要好使需 AWS Builder ID 交互登录 |
| Claude API / GPT API（技能预置 remote 三件） | 远端模型 API，非 CLI | 仍缺 ANTHROPIC_API_KEY / OPENAI_API_KEY（注：要"Claude 体验+mimo 大脑"直接用上面 Claude Code 条目即可） |

> 凭据安全：key 只进本机配置文件（chmod 600）与子进程 env（ws_acp 从 ~/.hermes/.env 白名单注入），不进对话、不进 git。本轮所有 mimo 接入共用 gland 现成 XIAOMI_API_KEY，**零新凭据**。

## 三、不可装 / 目录项（61 项目录里 50 项——非可对话 CLI，已从"支持"话术降级为"检测目录"）

- **IDE 扩展/桌面 IDE**（不可装为 headless CLI）：cline、roo、kilo、continue、windsurf、kiro、supermaven、codeium、codium
- **云端 SaaS**（无本地 CLI）：devin
- **GUI/平台类**（有 Docker/服务形态但不是对话 Agent，未装）：lmstudio、jan、chatbox、lobe-chat、dify、flowise、perplexity、phind、tabby（补全服务器，镜像>2G 未拉）
- **框架/库**（import 而非可对话 CLI）：gptme、mentat、sweep、autogpt、crewai、autogen、langchain、llamaindex、semantic-kernel、haystack、openai-agents、smolagents、pydantic-ai、letta、browser-use、mastra、composio、agentstack、phidata
- **运维工具（混进 AI 目录即虚标，已标注 catalog-only）**：k9s、lazydocker、terraform、ansible

## 四、工具预置（技能市场 9 项）全量结果

| 预置 | 状态 | 证据 |
|---|---|---|
| Ollama | ✅ 好使 | :11434 在跑（bge-m3、qwen2.5:0.5b、**deepseek-r1:1.5b**）；经 ollama 路由端到端 "42" |
| Open Interpreter | ✅ 好使 | e2e-interpreter（ollama 后端） |
| Aider | ✅ 好使 | e2e-aider（ollama 后端） |
| n8n | ✅ 好使 | docker `om-n8n` :5678 `/healthz` 200 |
| MinIO | ✅ 好使 | systemd `minio.service` :9000 `/minio/health/live` 200（社区二进制 RELEASE.2025-09-07；官方 dl.min.io 已 410、Docker Hub 镜像已停发） |
| NATS | ✅ 好使 | docker `om-nats` :4222 协议级验证 INFO→PING→PONG |
| MiMo（remote） | ✅ 好使 | e2e-mimo（XIAOMI_API_KEY 有效） |
| Claude（remote） | ❌ 缺凭据 | 远端 Anthropic API，要 ANTHROPIC_API_KEY（mimo 已能当 Claude Code 大脑，见 §一） |
| GPT（remote） | ❌ 缺凭据 | 远端 OpenAI API，要 OPENAI_API_KEY |

## 五、二轮为"真支持"动过的地方（全部实测后才写）

**本机配置（key 均从 gland 口径 ~/.hermes/.env 读取，chmod 600，不进 git）**：
- `~/.claude/settings.json`（新建）+ `~/.claude.json`（hasCompletedOnboarding）：按小米官方 Claude Code 指南接 Anthropic 兼容端点
- `~/.codex/config.toml`：mimo 自定义 provider（顶层键顺序、supports_websockets=false、web_search="disabled"、model_catalog_json）+ `~/.codex/model-catalogs/model-catalogs.json`（v2.6 元数据，字段口径同小米官方 Codex 指南）
- `~/.pi/agent/settings.json`（defaultModel=xiaomi-token-plan-cn/mimo-v2.6-pro）+ `~/.pi/agent/auth.json`（过期 key 换 gland 现值）+ `pi update --models`（v2.6 模型目录）
- ollama：`ollama pull deepseek-r1:1.5b`（一轮 matrix 称"已跑 deepseek-r1"实测为误，本轮实拉取补上）
- 清理：`~/node_modules/.bin/pi` 坏符号链接（旧 @mariozechner 分支遗留）重命名为 `pi.stale-mariozechner-fork.broken`（可逆）

**代码接入（openmate/acp-proxy）**：
- `ws_acp.py`：`_inject_cred_env()`——子进程 env 白名单注入 XIAOMI_API_KEY/XIAOMI_BASE_URL 及别名（pi 口径 XIAOMI_TOKEN_PLAN_CN_API_KEY、文档口径 MIMO_API_KEY）；route 级 `env` 支持（初始 spawn + 崩溃 respawn 两分支）；新增 `deepseek` 路由（ollama_acp + OLLAMA_MODEL=deepseek-r1:1.5b）；pi 路由钉死 `PI_ACP_PI_COMMAND` 绝对路径
- `agent/cli_acp.py`：copilot profile `_profile_env()` 注入 COPILOT_PROVIDER_*（官方 BYOK 口径）
- 零新增依赖、零硬编码 key（全部运行时从 ~/.hermes/.env 读取）

**口径同步**：opensoul `src/api/agents.py`（SUPPORT_TIER 5 处转 e2e-ok、pi-agent 描述修正"Inflection AI"误标、deepseek 安装说明补 DeepSeek Harness 事实）、本矩阵重写。

## 六、磁盘（根分区 /dev/sdb3，492G）

- 二轮前：可用 **39G**（92% 用量）
- 二轮后：可用 **38G**（92% 用量）——净消耗约 1.1GB（deepseek-r1:1.5b 模型）；未触碰用户数据，未低于 15G 警戒线

## 七、剩余凭据清单（补齐哪项→哪项立刻转"好使"）

1. **GEMINI_API_KEY** 或 Google 交互登录 → Gemini CLI 好使（走 Google 后端；接 mimo 需另建 Gemini 协议翻译层）
2. **CURSOR_API_KEY**（+装 cursor-agent）→ Cursor Agent 好使（Cursor 后端，非 mimo）
3. **AWS Builder ID 登录**（+装 Amazon Q）→ Amazon Q 好使（AWS 后端，非 mimo）
4. **ANTHROPIC_API_KEY / OPENAI_API_KEY** → 技能预置 Claude/GPT remote 转好使

> 二轮后"缺凭据"从 8 项收敛到 4 项，其中 3 项是"官方架构就接不了 mimo"（Gemini 协议 / Cursor 后端 / AWS 后端），纯缺 key 的只剩 remote 三件共 1 项。
