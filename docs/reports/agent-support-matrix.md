# AI Agent 支持矩阵（真装真测·2026-11-20）

> 口径（用户拍板）：**"好使"=经系统（OpenMate ws_acp :8092 → agent）发真 prompt、收到真回复**，证据留档 `docs/reports/evidence/`；不验证就不说正常。缺凭据=如实列出向用户要；不可装=给原因。UI 声称与本矩阵同源（opensoul `/api/agents/detect` 输出 `support_tier` 字段；虚标已全清）。
>
> 测试 prompt：`请只回答数字：26+16等于多少？`（期望真回复含 `42`）。探针脚本：JWT→ws://127.0.0.1:8092/ws/acp→initialize→session/new→session/prompt→收集 agent_message_chunk。

## 一、端到端好使（✅ 9/9 通过真对话）

| Agent | 接入路径 | 后端模型 | 实测证据 | 本次修复了什么 |
|---|---|---|---|---|
| SoulMate | ws_acp `soulmate`（python -m agent.start） | 系统 LLM 路由 | `e2e-soulmate-20261120-010520.json` → "42" | 基线复验通过 |
| Hermes | ws_acp `hermes`（hermes acp, pty） | hermes 模型路由 | `e2e-hermes-20261120-010537.json` → "42" | 基线复验通过 |
| MiMo Code | ws_acp `mimo`（mimo acp） | xiaomi-token-plan-cn/mimo-v2.6-pro | `e2e-mimo-20261120-014223.json` → "42" | **修复 3 处**：①存的 API key 已过期（401），换用 ~/.hermes/.env 有效 XIAOMI_API_KEY；②项目级 `~/mimocode.json` 覆盖了全局配置且 model id（mimo-v2.5-pro）已下架；③ACP 模式默认模型错选 openai/gpt-5.6-terra-pro（上游 bug #865 变种）——补 `~/.config/mimocode/config.json` 指定 model/small_model |
| OpenCode | ws_acp `opencode`（opencode acp） | opencode 已配 provider | `e2e-opencode-20261120-015917.json` → "42" | **修复**：systemd 服务 PATH 缺 `~/.npm-global/bin` → execvp 找不到二进制（ws_acp.py 模块级 PATH 归一） |
| Ollama | ws_acp `ollama`（agent.ollama_acp 自研桥） | 本机 ollama qwen2.5:0.5b | `e2e-ollama-20261120-015412.json` → "42" | **重建**：路由引用的 `agent/ollama_acp.py` 源文件丢失（仅剩旧 pyc），子进程即起即死——按 ACP stdio 重写该适配器 |
| Aider | ws_acp `aider`（agent.cli_acp 新通用桥） | ollama_chat/qwen2.5:0.5b（本地，免凭据） | `e2e-aider-20261120-015514.json` → 含 "42" | **新装**（uv tool, python3.12）+ 新接入 |
| Qwen Code | ws_acp `qwen`（agent.cli_acp） | 本机 ollama qwen2.5:0.5b（OpenAI 兼容 :11434/v1） | `e2e-qwen-20261120-015618.json` → "26+16=42" | **新装**（npm @qwen-code/qwen-code）+ 接本地 ollama（`~/.qwen/settings.json` modelProviders） |
| Open Interpreter | ws_acp `interpreter`（agent.cli_acp） | ollama_chat/qwen2.5:0.5b | `e2e-interpreter-20261120-015802.json` → "26+16=42" | **新装**（uv tool, python3.12, setuptools<81 修复 pkg_resources）+ 新接入 |
| OpenClaw | ws_acp `openclaw`（openclaw acp → gateway :18789） | xiaomi-coding/mimo-v2.6-pro | `e2e-openclaw-20261120-021042.json` → "42" | **修复 3 处**：①gateway 服务 failed（agent DB schema v23 vs v38，停服后 `openclaw doctor --fix` 升级）；②凭据过期换有效 XIAOMI_API_KEY；③模型名 mimo-v2.5-pro 已下架→mimo-v2.6-pro，失效 fallback（freellmapi@:3001 已死）→ollama |

## 二、缺凭据（❌ 端到端不通——已装/可装，缺 key 或需交互登录；向用户要的清单）

| Agent | 已装? | 卡点（实测证据） | 需要用户提供 |
|---|---|---|---|
| Claude Code | ✅ 已装（2.1.327） | `e2e-claude-code-20261120-015952.json`："Authentication required"（~/.claude 无凭据） | `claude /login` 交互登录 或 ANTHROPIC_API_KEY |
| Codex CLI | ✅ 已装（0.170.0） | `e2e-codex-20261120-015931.json`："Authentication required"（~/.codex/auth.json 不存在） | ChatGPT 账号 `codex login` 或 OPENAI_API_KEY |
| Gemini CLI | ✅ 已装（0.69.0） | `e2e-gemini-20261120-015811.json`："Please set an Auth method…GEMINI_API_KEY" | GEMINI_API_KEY 或 Google 账号交互登录 |
| Copilot CLI | ✅ 已装（@github/copilot 1.0.114） | `e2e-copilot-20261120-020145.json`："No authentication information found"；classic token 被拒 | **Fine-Grained PAT（勾选 "Copilot Requests" 权限）** 设到 COPILOT_GITHUB_TOKEN/GH_TOKEN，或 `copilot` 内 `/login` OAuth（旧 gh-copilot 扩展已废弃空壳，勿用） |
| Pi | ✅ 已装（@earendil-works/pi-coding-agent 1.5.2） | `e2e-pi-20261120-015956.json` + 直测 "401: Invalid API Key" | pi 支持的任一云厂商 API key（`pi auth login`） |
| Cursor Agent | ⛔ 可装未装（官方安装脚本执行需人工确认，已如实停手） | 直测缺 CURSOR_API_KEY | 装：`curl https://cursor.com/install -fsSL \| bash`（二进制是 `agent` 不是 `cursor`）+ CURSOR_API_KEY |
| Amazon Q | ⛔ 可装未装（同上） | 官方 zip 安装器+登录均为交互式 | 装：官方 zip+`./install.sh`；登录：AWS Builder ID（交互） |
| DeepSeek | — 无官方 CLI | DeepSeek 只提供模型 API（社区第三方 CLI 非官方） | DEEPSEEK_API_KEY（即可接入系统模型路由 deepseek-chat/r1） |
| Claude API / GPT API（技能预置里的 remote 三件） | — 远端模型 | 无 ANTHROPIC_API_KEY / OPENAI_API_KEY | 对应 API key |

> 凭据安全：key 只进本机配置文件（chmod 600），不进对话、不进 git。本次修复用到的 XIAOMI_API_KEY 来自用户本机 ~/.hermes/.env（文件对文件拷贝，未外显）。

## 三、不可装 / 目录项（61 项目录里 50 项——非可对话 CLI，已从"支持"话术降级为"检测目录"）

- **IDE 扩展/桌面 IDE**（不可装为 headless CLI）：cline、roo、kilo、continue、windsurf、kiro、supermaven、codeium、codium
- **云端 SaaS**（无本地 CLI）：devin
- **GUI/平台类**（有 Docker/服务形态但不是对话 Agent，本次未装）：lmstudio、jan、chatbox、lobe-chat、dify、flowise、perplexity、phind、tabby（补全服务器，镜像>2G 未拉）
- **框架/库**（import 而非可对话 CLI）：gptme、mentat、sweep、autogpt、crewai、autogen、langchain、llamaindex、semantic-kernel、haystack、openai-agents、smolagents、pydantic-ai、letta、browser-use、mastra、composio、agentstack、phidata
- **运维工具（混进 AI 目录即虚标，已标注 catalog-only）**：k9s、lazydocker、terraform、ansible

## 四、工具预置（技能市场 9 项）全量结果

| 预置 | 状态 | 证据 |
|---|---|---|
| Ollama | ✅ 好使 | :11434 在跑（bge-m3、qwen2.5:0.5b）；经 ollama 路由端到端 "42" |
| Open Interpreter | ✅ 好使 | e2e-interpreter（ollama 后端） |
| Aider | ✅ 好使 | e2e-aider（ollama 后端） |
| n8n | ✅ 好使 | docker `om-n8n` :5678 `/healthz` 200 |
| MinIO | ✅ 好使 | systemd `minio.service` :9000 `/minio/health/live` 200（社区二进制 RELEASE.2025-09-07；官方 dl.min.io 已 410、Docker Hub 镜像已停发，装法见 §五） |
| NATS | ✅ 好使 | docker `om-nats` :4222 协议级验证 INFO→PING→PONG |
| MiMo（remote） | ✅ 好使 | e2e-mimo（XIAOMI_API_KEY 有效） |
| Claude（remote） | ❌ 缺凭据 | 见 §二 |
| GPT（remote） | ❌ 缺凭据 | 见 §二 |

## 五、本次为"真支持"动过的地方（全部实测后才写）

**新装**：aider（uv tool py3.12）、open-interpreter（uv tool py3.12 + setuptools<81）、@qwen-code/qwen-code、@google/gemini-cli、@github/copilot、MinIO 二进制（GitHub release，110MB）、n8n/minio 镜像与容器、gh-copilot（后发现已废弃，换 @github/copilot）。

**代码接入（openmate/acp-proxy）**：
- `agent/ollama_acp.py`：重建丢失的 Ollama ACP stdio 桥
- `agent/cli_acp.py`：新增通用一次性 CLI→ACP 适配器（profiles: aider/qwen/gemini/interpreter/copilot；失败如实回 JSON-RPC error，禁假成功）
- `ws_acp.py`：AGENT_ROUTES 注册 aider/qwen/gemini/interpreter/copilot 5 条新路由 + PATH 归一（修 opencode 等 spawn 失败）

**本机配置修复**：mimo 三处（key/模型/ACP 默认模型）、qwen 指本地 ollama、openclaw 三处（gateway 升级/模型 key/模型名+fallback）。

**去虚标（claims 修正）**：opensoul `agents.py`（mimo/qwen/deepseek/amazon-q/copilot/cursor-agent/openclaw/pi-agent/opencode 9 处假安装命令改正 + cursor 二进制名 + `support_tier` 字段）、openmate `agents-manager.tsx`（实测状态徽章）、`agent-detector.ts`/`agent-installer.ts`/`agent-types.ts`（假"安装即成功/模拟连接测试必过"全删，未验证只标 available）。

## 六、磁盘（根分区 /dev/sdb3，492G）

- 前（12:11 基线）：可用 **42G**（92% 用量）
- 后（清理安装缓存后）：可用 **40G**（92% 用量）——净消耗约 2G（npm 全局包、uv tool 两套、MinIO 110MB、docker 镜像 n8n/nats）
- 已回收可再生缓存：uv cache 2.0G、pip cache 367M、npm cache、/tmp 安装残留；全程未触碰用户数据，可用量未低于 15G 警戒线

## 七、给用户的凭据清单（补齐哪项→哪项立刻转"好使"）

1. **Fine-Grained PAT（Copilot Requests 权限）** → Copilot CLI 好使
2. **claude /login（交互）** 或 ANTHROPIC_API_KEY → Claude Code 好使
3. **codex login（交互）** 或 OPENAI_API_KEY → Codex 好使
4. **GEMINI_API_KEY** 或 Google 登录（交互）→ Gemini 好使
5. **pi auth login（交互）** → Pi 好使
6. **CURSOR_API_KEY**（+装 cursor-agent）→ Cursor Agent 好使
7. **AWS Builder ID 登录**（+装 Amazon Q）→ Amazon Q 好使
8. **DEEPSEEK_API_KEY** → DeepSeek 进模型路由（无官方 CLI）
9. **ANTHROPIC_API_KEY / OPENAI_API_KEY** → 技能预置 Claude/GPT remote 转好使
