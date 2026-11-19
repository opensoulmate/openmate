// Agent Auto-Detection - detects locally installed AI agents
// Inspired by AionUi's ACP protocol architecture
//
// ⚠️ 2026-11-20 实测治理：本文件为遗留检测目录（未接 UI）。"支持"以实测为准——
// 哪些 agent 端到端好使/缺凭据/不可装见 docs/reports/agent-support-matrix.md
// （证据：docs/reports/evidence/）。安装命令已按官方文档核实修正，禁止虚标。

export interface DetectedAgent {
  id: string;
  name: string;
  backend: string;
  description: string;
  icon: string;
  binary: string;
  available: boolean;
  version?: string;
  configPath?: string;
  installCommand?: string;
}

// Registry of known agents and their detection patterns
const AGENT_REGISTRY: Omit<DetectedAgent, 'available' | 'version'>[] = [
  { id: 'claude', name: 'Claude Code', backend: 'claude', description: 'Anthropic Claude Code CLI', icon: '🟣', binary: 'claude', installCommand: 'npm install -g @anthropic-ai/claude-code' },
  { id: 'codex', name: 'Codex CLI', backend: 'codex', description: 'OpenAI Codex CLI', icon: '🟢', binary: 'codex', installCommand: 'npm install -g @openai/codex' },
  { id: 'gemini', name: 'Gemini CLI', backend: 'gemini', description: 'Google Gemini CLI', icon: '🔵', binary: 'gemini', installCommand: 'npm install -g @google/gemini-cli' },
  { id: 'qwen', name: 'Qwen CLI', backend: 'qwen', description: 'Alibaba Qwen CLI', icon: '🟠', binary: 'qwen', installCommand: 'npm install -g @qwen-code/qwen-code@latest' },
  { id: 'opencode', name: 'OpenCode', backend: 'opencode', description: 'Open source coding agent', icon: '⚡', binary: 'opencode', installCommand: 'npm install -g opencode-ai' },
  { id: 'hermes', name: 'Hermes Agent', backend: 'hermes', description: 'Nous Research Hermes Agent', icon: '🏛️', binary: 'hermes', installCommand: 'pip install hermes-agent' },
  { id: 'mimo', name: 'MiMo Code', backend: 'mimo', description: 'Xiaomi MiMo Code CLI', icon: '📱', binary: 'mimo', installCommand: 'curl -fsSL https://mimo.xiaomi.com/install | bash' },
  { id: 'cursor', name: 'Cursor Agent', backend: 'cursor', description: 'Cursor AI coding agent', icon: '▶️', binary: 'agent', installCommand: 'curl https://cursor.com/install -fsSL | bash' },
  { id: 'copilot', name: 'GitHub Copilot', backend: 'copilot', description: 'GitHub Copilot CLI', icon: '🐙', binary: 'copilot', configPath: '~/.config/github-copilot/', installCommand: 'npm install -g @github/copilot' },
  { id: 'deepseek', name: 'DeepSeek', backend: 'deepseek', description: 'DeepSeek（无官方 CLI，作为模型 API 提供；需 DEEPSEEK_API_KEY）', icon: '🐋', binary: 'deepseek', installCommand: '（无官方 CLI：仅模型 API）' },
  { id: 'aider', name: 'Aider', backend: 'aider', description: 'AI pair programming in your terminal', icon: '🤝', binary: 'aider', installCommand: 'pip install aider-chat' },
  { id: 'continue', name: 'Continue', backend: 'continue', description: 'Continue dev - open source AI code assistant', icon: '🔄', binary: 'continue', installCommand: 'https://continue.dev' },
  { id: 'windsurf', name: 'Windsurf', backend: 'windsurf', description: 'Windsurf AI coding agent', icon: '🏄', binary: 'windsurf', installCommand: 'https://windsurf.ai' },
  { id: 'cline', name: 'Cline', backend: 'cline', description: 'Cline AI coding assistant', icon: '🔧', binary: 'cline', installCommand: 'VS Code extension' },
  { id: 'roo', name: 'Roo Code', backend: 'roo', description: 'Roo Code AI assistant', icon: '🦘', binary: 'roo', installCommand: 'VS Code extension' },
  { id: 'kilo', name: 'Kilo Code', backend: 'kilo', description: 'Kilo Code AI assistant', icon: '⚡', binary: 'kilo', installCommand: 'VS Code extension' },
  { id: 'kiro', name: 'Kiro', backend: 'kiro', description: 'AWS Kiro AI IDE', icon: '🎯', binary: 'kiro', installCommand: 'https://kiro.dev' },
  { id: 'amazon-q', name: 'Amazon Q', backend: 'amazon-q', description: 'Amazon Q Developer CLI', icon: '☁️', binary: 'q', installCommand: 'https://aws.amazon.com/q/developer/' },
  { id: 'tabby', name: 'Tabby', backend: 'tabby', description: 'Tabby AI coding assistant', icon: '📋', binary: 'tabby', installCommand: 'https://tabby.tabbyml.com' },
  { id: 'devin', name: 'Devin', backend: 'devin', description: 'Devin AI software engineer', icon: '🤖', binary: 'devin', installCommand: 'https://devin.ai' },
];

// Detect if a binary exists on the system
async function detectBinary(binary: string): Promise<{ exists: boolean; version?: string }> {
  try {
    const res = await fetch('/api/agent/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ binary }),
    });
    if (res.ok) {
      return await res.json();
    }
  } catch {}
  return { exists: false };
}

// Detect all agents
export async function detectAllAgents(): Promise<DetectedAgent[]> {
  const results: DetectedAgent[] = [];

  for (const agent of AGENT_REGISTRY) {
    const detection = await detectBinary(agent.binary);
    results.push({
      ...agent,
      available: detection.exists,
      version: detection.version,
    });
  }

  return results;
}

// Get agent by ID
export function getAgentById(id: string): Omit<DetectedAgent, 'available' | 'version'> | undefined {
  return AGENT_REGISTRY.find(a => a.id === id);
}

// Get all registered agent IDs
export function getAllAgentIds(): string[] {
  return AGENT_REGISTRY.map(a => a.id);
}
