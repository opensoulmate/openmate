import i18n from "./i18n";
import {
  Bot,
  MessageSquare,
  Globe,
  BrainCircuit,
  Server,
  Cpu,
  Workflow,
  Code2,
  Terminal,
  type LucideIcon,
} from "lucide-react";

// ─── Agent Type Enums (AionUi-inspired) ─────────────────────────────────────

export type AgentCategory = "local" | "remote" | "tool";

export type AgentSource = "internal" | "builtin" | "extension" | "custom";

export type AgentRuntimeStatus = "online" | "offline" | "missing" | "unchecked";

// ─── Agent Metadata ─────────────────────────────────────────────────────────

export interface DetectedAgent {
  id: string;
  name: string;
  binary?: string;
  category: AgentCategory;
  source: AgentSource;
  status: AgentRuntimeStatus;
  description: string;
  icon: LucideIcon;
  color: string;
  installCommand?: string;
  detectedAt?: number;
  taskTypes?: string[];
}

// ─── Predefined Agent Definitions ───────────────────────────────────────────

export const LOCAL_AGENT_DEFS: Omit<DetectedAgent, "status" | "detectedAt">[] = [
  {
    id: "hermes",
    name: "Hermes",
    binary: "hermes",
    category: "local",
    source: "builtin",
    description: i18n.t("agentTypes.hermesDesc"),
    icon: Workflow,
    color: "text-cyan-400",
    installCommand: "# Install Hermes",
    taskTypes: ["messaging", "automation"],
  },
  {
    id: "claude-cli",
    name: "Claude CLI",
    binary: "claude",
    category: "local",
    source: "builtin",
    description: "Anthropic Claude CLI client.",
    icon: MessageSquare,
    color: "text-orange-400",
    installCommand: "npm install -g @anthropic-ai/claude-code",
    taskTypes: ["chat", "code", "reasoning"],
  },
  {
    id: "codex",
    name: "Codex CLI",
    binary: "codex",
    category: "local",
    source: "builtin",
    description: "OpenAI Codex CLI coding assistant.",
    icon: Code2,
    color: "text-emerald-400",
    installCommand: "npm install -g @openai/codex",
    taskTypes: ["code", "reasoning"],
  },
  {
    id: "opencode",
    name: "OpenCode",
    binary: "opencode",
    category: "local",
    source: "builtin",
    description: i18n.t("agentTypes.openHandsDesc"),
    icon: Terminal,
    color: "text-blue-400",
    installCommand: "npm install -g opencode-ai",
    taskTypes: ["code", "chat"],
  },
  {
    id: "ollama",
    name: "Ollama",
    binary: "ollama",
    category: "local",
    source: "builtin",
    description: i18n.t("agentTypes.ollamaDesc"),
    icon: Cpu,
    color: "text-violet-400",
    installCommand: "curl -fsSL https://ollama.com/install.sh | sh",
    taskTypes: ["chat", "code", "reasoning"],
  },
  {
    id: "interpreter",
    name: "Open Interpreter",
    binary: "interpreter",
    category: "local",
    source: "builtin",
    description: i18n.t("agentTypes.computerUseDesc"),
    icon: Terminal,
    color: "text-amber-400",
    installCommand: "pip install open-interpreter",
    taskTypes: ["code", "shell", "automation"],
  },
  {
    id: "aider",
    name: "Aider",
    binary: "aider",
    category: "local",
    source: "builtin",
    description: i18n.t("agentInstaller.aiderDesc"),
    icon: Bot,
    color: "text-pink-400",
    installCommand: "pip install aider-chat",
    taskTypes: ["code", "refactor"],
  },
  {
    id: "n8n",
    name: "n8n",
    binary: "n8n",
    category: "local",
    source: "builtin",
    description: i18n.t("agentTypes.n8nDesc"),
    icon: Workflow,
    color: "text-rose-400",
    installCommand: "docker run -it --rm -p 5678:5678 n8nio/n8n",
    taskTypes: ["workflow", "automation"],
  },
];

export const REMOTE_AGENT_DEFS: Omit<DetectedAgent, "status" | "detectedAt">[] = [
  {
    id: "claude-api",
    name: "Claude API",
    category: "remote",
    source: "builtin",
    description: "Anthropic Claude API — excels at long text and reasoning.",
    icon: MessageSquare,
    color: "text-orange-400",
    taskTypes: ["chat", "code", "reasoning", "writing"],
  },
  {
    id: "openai-api",
    name: "OpenAI API",
    category: "remote",
    source: "builtin",
    description: "OpenAI GPT API supporting GPT-4o and earlier versions.",
    icon: Globe,
    color: "text-green-400",
    taskTypes: ["chat", "code", "reasoning"],
  },
  {
    id: "mimo-api",
    name: "MiMo API",
    category: "remote",
    source: "builtin",
    description: i18n.t("agentTypes.xiaomiDesc"),
    icon: BrainCircuit,
    color: "text-purple-400",
    taskTypes: ["chat", "code", "reasoning"],
  },
];

// ─── Icon Map for Registry Agents ───────────────────────────────────────────

export const AGENT_ICON_MAP: Record<string, LucideIcon> = {
  ollama: Cpu,
  claude: MessageSquare,
  gpt: Globe,
  mimo: BrainCircuit,
  "open-interpreter": Server,
  aider: Bot,
  n8n: Workflow,
  hermes: Workflow,
  codex: Code2,
  opencode: Terminal,
};
