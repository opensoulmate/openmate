import i18n from "./i18n";
import { getAgentRegistry, type RegisteredAgent, type MarketAgentType } from "./agent-registry";

// ⚠️ 2026-11-20 实测治理（去虚标）：本文件旧实现把"复制安装命令/模拟连接测试"
// 直接标 installed —— 是假成功。现在一律只标 available（=有待用户/系统真实验证），
// 真实可用口径以 docs/reports/agent-support-matrix.md（e2e 实测+证据）为准；
// 系统内真探测走 acp-proxy POST /agents/probe（ACP 握手）。禁止模拟通过。

export interface InstallProgress {
  stage: "copying" | "testing" | "saving" | "done" | "error";
  message: string;
}

export interface AgentDefinition {
  id: string;
  name: string;
  description: string;
  type: MarketAgentType;
  installCommand: string;
  needsApiKey: boolean;
  port?: number;
  checkCommand?: string;
  taskTypes?: string[];
}

// Known agents with their detection logic
const AGENT_DEFS: Record<string, AgentDefinition> = {
  "open-interpreter": {
    id: "open-interpreter",
    name: "Open Interpreter",
    description: i18n.t("agentTypes.computerUseDesc"),
    type: "local",
    installCommand: "pip install open-interpreter",
    needsApiKey: false,
    checkCommand: "which interpreter",
    taskTypes: ["code", "shell", "automation"],
  },
  ollama: {
    id: "ollama",
    name: "Ollama",
    description: i18n.t("agentTypes.ollamaDesc"),
    type: "local",
    installCommand: "curl -fsSL https://ollama.com/install.sh | sh",
    needsApiKey: false,
    checkCommand: "which ollama",
    port: 11434,
    taskTypes: ["chat", "code", "reasoning"],
  },
  aider: {
    id: "aider",
    name: "Aider",
    description: i18n.t("agentInstaller.aiderDesc"),
    type: "local",
    installCommand: "pip install aider-chat",
    needsApiKey: false,
    checkCommand: "which aider",
    taskTypes: ["code", "refactor"],
  },
  n8n: {
    id: "n8n",
    name: "n8n",
    description: i18n.t("agentTypes.n8nDesc"),
    type: "tool",
    installCommand: "docker run -it --rm -p 5678:5678 n8nio/n8n",
    needsApiKey: false,
    port: 5678,
    taskTypes: ["workflow", "automation"],
  },
  claude: {
    id: "claude",
    name: "Claude",
    description: "Anthropic AI assistant — excels at long text and reasoning.",
    type: "remote",
    installCommand: "",
    needsApiKey: true,
    taskTypes: ["chat", "code", "reasoning", "writing"],
  },
  gpt: {
    id: "gpt",
    name: "GPT",
    description: "OpenAI remote AI model supporting GPT-4o and earlier versions.",
    type: "remote",
    installCommand: "",
    needsApiKey: true,
    taskTypes: ["chat", "code", "reasoning"],
  },
  mimo: {
    id: "mimo",
    name: "MiMo",
    description: i18n.t("agentTypes.xiaomiDesc"),
    type: "remote",
    installCommand: "",
    needsApiKey: true,
    taskTypes: ["chat", "code", "reasoning"],
  },
  minio: {
    id: "minio",
    name: "MinIO",
    description: "High-performance object storage, S3-compatible, ideal for AI data lakes.",
    type: "tool",
    installCommand:
      "curl -fL -o minio https://github.com/minio/minio/releases/download/RELEASE.2025-09-07T16-13-09Z/minio.linux-amd64.RELEASE.2025-09-07T16-13-09Z && chmod +x minio && ./minio server /data --console-address :9001",
    needsApiKey: false,
    port: 9000,
    taskTypes: ["storage"],
  },
  nats: {
    id: "nats",
    name: "NATS",
    description: "Lightweight message queue built for microservices and Agent communication.",
    type: "tool",
    installCommand: "docker run -p 4222:4222 nats",
    needsApiKey: false,
    port: 4222,
    taskTypes: ["messaging"],
  },
};

export class AgentInstaller {
  private registry = getAgentRegistry();

  getDefinition(agentId: string): AgentDefinition | undefined {
    return AGENT_DEFS[agentId];
  }

  async install(
    agentId: string,
    onProgress: (p: InstallProgress) => void,
  ): Promise<boolean> {
    const def = AGENT_DEFS[agentId];
    if (!def) {
      onProgress({ stage: "error", message: i18n.t("agentInstaller.unknownAgent") });
      return false;
    }

    if (def.type === "remote") {
      return this.installRemote(def, onProgress);
    }

    if (def.type === "local") {
      return this.installLocal(def, onProgress);
    }

    // tool
    return this.installTool(def, onProgress);
  }

  async installRemote(
    def: AgentDefinition,
    onProgress: (p: InstallProgress) => void,
  ): Promise<boolean> {
    // 真实性约束（2026-11-20）：未经真实连接测试不得标 installed。
    onProgress({ stage: "testing", message: i18n.t("agentInstaller.testingConnection", { name: def.name }) });
    await delay(300);

    onProgress({ stage: "saving", message: i18n.t("agentInstaller.savingConfig") });

    this.registry.register({
      id: def.id,
      name: def.name,
      type: def.type,
      status: "available", // 未验证——不冒充 installed
      config: {},
      installed_at: Date.now(),
      description: def.description,
      taskTypes: def.taskTypes,
    });

    onProgress({ stage: "done", message: `${def.name}: 配置已保存但连接未验证——需 API key 真实测试后才算可用` });
    return true;
  }

  async installRemoteWithKey(
    agentId: string,
    apiKey: string,
    onProgress: (p: InstallProgress) => void,
  ): Promise<boolean> {
    const def = AGENT_DEFS[agentId];
    if (!def || def.type !== "remote") {
      onProgress({ stage: "error", message: i18n.t("agentInstaller.invalidAgent") });
      return false;
    }

    onProgress({ stage: "saving", message: i18n.t("agentInstaller.savingApiKey") });
    await delay(300);

    // Save API key to localStorage
    try {
      const keys = JSON.parse(localStorage.getItem("openmate-api-keys") ?? "{}");
      keys[def.id] = apiKey;
      localStorage.setItem("openmate-api-keys", JSON.stringify(keys));
    } catch {
      // ignore
    }

    onProgress({ stage: "testing", message: i18n.t("agentInstaller.testingConnection", { name: def.name }) });

    // 真实连接测试（2026-11-20 去虚标）：真发一次鉴权请求，2xx 才算通；
    // 浏览器 CORS/网络不通=如实"未验证"，绝不模拟成功。
    let connected = false;
    try {
      const probeUrl: Record<string, string> = {
        claude: "https://api.anthropic.com/v1/models",
        gpt: "https://api.openai.com/v1/models",
        mimo: "https://token-plan-cn.xiaomimimo.com/v1/models",
      };
      const url = probeUrl[def.id];
      if (url) {
        const headers: Record<string, string> = def.id === "claude"
          ? { "x-api-key": apiKey, "anthropic-version": "2023-06-01" }
          : { "Authorization": `Bearer ${apiKey}` };
        const res = await fetch(url, { headers });
        connected = res.ok;
      }
    } catch {
      connected = false;
    }

    if (!connected) {
      onProgress({ stage: "error", message: `${def.name}: 真实连接测试未通过（或浏览器无法直连验证）——不标记为可用` });
      // 仍保存 key 便于后续验证，但状态不冒充
      this.registry.register({
        id: def.id,
        name: def.name,
        type: def.type,
        status: "available",
        config: { apiKey },
        installed_at: Date.now(),
        description: def.description,
        taskTypes: def.taskTypes,
      });
      return false;
    }

    this.registry.register({
      id: def.id,
      name: def.name,
      type: def.type,
      status: "installed",
      config: { apiKey },
      installed_at: Date.now(),
      description: def.description,
      taskTypes: def.taskTypes,
    });

    onProgress({ stage: "done", message: i18n.t("agentInstaller.connected", { name: def.name }) });
    return true;
  }

  async installLocal(
    def: AgentDefinition,
    onProgress: (p: InstallProgress) => void,
  ): Promise<boolean> {
    onProgress({ stage: "copying", message: i18n.t("agentInstaller.copyingInstallCmd") });

    try {
      await navigator.clipboard.writeText(def.installCommand);
    } catch {
      // clipboard may fail in some environments
    }

    onProgress({
      stage: "copying",
      message: i18n.t("agentInstaller.copied", { cmd: def.installCommand }),
    });

    // Wait a bit then check if available
    await delay(1500);
    onProgress({ stage: "testing", message: i18n.t("agentInstaller.detecting", { name: def.name }) });

    // We can't actually run shell commands from the browser,
    // 所以不假标 installed——只标 available，等真实检测确认（2026-11-20 去虚标）
    await delay(500);

    this.registry.register({
      id: def.id,
      name: def.name,
      type: def.type,
      status: "available", // 复制命令≠已安装，待真实验证
      config: {},
      installed_at: Date.now(),
      description: def.description,
      installCommand: def.installCommand,
      taskTypes: def.taskTypes,
    });

    onProgress({
      stage: "done",
      message: `${def.name}: 安装命令已复制——未验证安装；请执行后用真实检测（/api/agents/probe）确认`,
    });
    return true;
  }

  async installTool(
    def: AgentDefinition,
    onProgress: (p: InstallProgress) => void,
  ): Promise<boolean> {
    onProgress({ stage: "copying", message: i18n.t("agentInstaller.copyingDockerCmd") });

    try {
      await navigator.clipboard.writeText(def.installCommand);
    } catch {
      // clipboard may fail
    }

    onProgress({
      stage: "copying",
      message: i18n.t("agentInstaller.copied", { cmd: def.installCommand }),
    });

    await delay(1500);
    onProgress({ stage: "testing", message: i18n.t("agentInstaller.detectingPort", { port: def.port }) });

    // Check port availability (best effort from browser)
    if (def.port) {
      const portOk = await this.checkPort(def.port);
      if (portOk) {
        this.registry.register({
          id: def.id,
          name: def.name,
          type: def.type,
          status: "installed",
          config: {},
          installed_at: Date.now(),
          description: def.description,
          installCommand: def.installCommand,
          port: def.port,
          taskTypes: def.taskTypes,
        });
        onProgress({ stage: "done", message: i18n.t("agentInstaller.readyOnPort", { name: def.name, port: def.port }) });
        return true;
      }
    }

    // Port not reachable — 如实标未验证，绝不假标 installed（2026-11-20 去虚标）
    this.registry.register({
      id: def.id,
      name: def.name,
      type: def.type,
      status: "available", // 端口不通=未验证
      config: {},
      installed_at: Date.now(),
      description: def.description,
      installCommand: def.installCommand,
      port: def.port,
      taskTypes: def.taskTypes,
    });

    onProgress({
      stage: "done",
      message: `${def.name}: 端口 ${def.port ?? "?"} 未验证到服务——标记为待验证，不冒充已安装`,
    });
    return true;
  }

  private async checkPort(port: number): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2000);
      await fetch(`http://localhost:${port}`, {
        mode: "no-cors",
        signal: controller.signal,
      });
      clearTimeout(timeout);
      return true;
    } catch {
      return false;
    }
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Singleton
let installerInstance: AgentInstaller | null = null;

export function getAgentInstaller(): AgentInstaller {
  if (!installerInstance) {
    installerInstance = new AgentInstaller();
  }
  return installerInstance;
}
