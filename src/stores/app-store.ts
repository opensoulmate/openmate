type AgentInfo = { id: string; name: string; icon: string; description: string; available: boolean; category?: string; path?: string; version?: string; };
import { create } from "zustand";
import { type ThemeId, getStoredTheme, applyTheme } from "@/lib/theme";

export interface ToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  status: "running" | "success" | "error";
}

export interface FilePreview {
  path: string;
  language: string;
  content: string;
  lineCount: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  sources?: { title: string; url: string }[];
  toolCalls?: ToolCall[];
  filePreviews?: FilePreview[];
}

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
  pinned?: boolean;
  unreadCount?: number;
}

export interface KnowledgeItem {
  id: string;
  title: string;
  type: "document" | "note" | "link";
  tags: string[];
  updatedAt: string;
  excerpt: string;
  starred?: boolean;
  pinned?: boolean;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  version: string;
}

export interface LLMConfig {
  provider: string;
  url: string;
  apiKey: string;
  model: string;
}

export type AgentType = "soma" | "ai" | "mcp";

export interface AgentNode {
  id: string;
  name: string;
  type: AgentType;
  status: "online" | "offline" | "error";
  lastSeen: string;
  // Soma fields
  nodeId?: string;
  endpoint?: string;
  // AI fields
  provider?: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
  // MCP fields
  serverUrl?: string;
  tools?: string[];
}

export type Theme = ThemeId;

export type GroupDispatchMode = "auto" | "manual";

// ─── Team types ─────────────────────────────────────────────────────────────

export type TeamMemberRole = "leader" | "member" | "observer";
export type TeamMemberStatus = "online" | "offline" | "busy";
export type ActivityType = "task_created" | "task_completed" | "member_joined" | "member_left" | "code_committed";
export type TaskStatus = "todo" | "in_progress" | "done";
export type TaskPriority = "low" | "medium" | "high" | "urgent";

export interface TeamMember {
  id: string;
  agentId: string;
  name: string;
  type: AgentType;
  role: TeamMemberRole;
  status: TeamMemberStatus;
  capabilities: string[];
  joinedAt: number;
}

export interface TeamActivity {
  id: string;
  type: ActivityType;
  actorId: string;
  actorName: string;
  description: string;
  taskId?: string;
  timestamp: number;
}

export interface TeamTask {
  id: string;
  title: string;
  description?: string;
  status: TaskStatus;
  priority: TaskPriority;
  assigneeId?: string;
  assigneeName?: string;
  tags: string[];
  createdAt: number;
  updatedAt: number;
}

export interface Team {
  id: string;
  name: string;
  description: string;
  members: TeamMember[];
  activities: TeamActivity[];
  tasks: TeamTask[];
  createdAt: number;
  updatedAt: number;
}

export interface AgentGroup {
  id: string;
  name: string;
  description: string;
  masterAgentId: string;
  memberAgentIds: string[];
  dispatchMode: GroupDispatchMode;
  createdAt: number;
  updatedAt: number;
}

// ─── Workspace types ────────────────────────────────────────────────────────

export interface Workspace {
  id: string;
  name: string;
  path: string;
  lastModified: number;
  fileCount: number;
  size: number; // bytes
  description?: string;
}

export type WorkspaceTabType = 'new-tab' | 'web-browser' | 'file-preview' | 'terminal' | 'details';

export interface WorkspaceTab {
  id: string;
  type: WorkspaceTabType;
  title: string;
  url?: string;
  filePath?: string;
  fileBuffer?: ArrayBuffer;
  fileMimeType?: string;
  history: string[];
  historyIndex: number;
}

export interface WorkspaceState {
  tabs: WorkspaceTab[];
  activeTabId: string;
}

export interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
  size?: number;
  extension?: string;
}

export interface TerminalLine {
  id: string;
  type: "input" | "output" | "error";
  content: string;
  timestamp: number;
}

export interface GroupChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  agentId?: string;
  agentName?: string;
  agentType?: AgentType;
  timestamp: number;
}

export interface SessionSpending {
  sessionId: string;
  sessionName?: string;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCost: number;
  messageCount: number;
  lastUpdated: number;
}

export interface GlobalWSState {
  connected: boolean;
  unreadBySession: Record<string, number>;
  setConnected: (v: boolean) => void;
  incrementUnread: (sessionId: string) => void;
  clearUnread: (sessionId: string) => void;
  getTotalUnread: () => number;
}

export interface WorkspaceState {
  tabs: WorkspaceTab[];
  activeTabId: string;
}

interface AppState {
  // Onboarding
  hasCompletedOnboarding: boolean;
  completeOnboarding: () => void;

  sidebarCollapsed: boolean;
  toggleSidebar: () => void;

  // Right workspace panel (shared between top-bar and chat-client)
  rightPanelOpen: boolean;
  pendingFilePreview: { url: string; name: string; mimeType?: string } | null;
  toggleRightPanel: () => void;
  setRightPanelOpen: (open: boolean) => void;
  setPendingFilePreview: (file: { url: string; name: string; mimeType?: string } | null) => void;

  // Page-specific sidebar/workspace content (for PageLayout framework)
  pageSidebar: React.ReactNode | null;
  pageWorkspace: React.ReactNode | null;
  setPageSidebar: (content: React.ReactNode | null) => void;
  setPageWorkspace: (content: React.ReactNode | null) => void;

  // Mobile swipeable panels (synced with bottom nav)
  currentPanel: number;
  setCurrentPanel: (n: number) => void;

  // Active session (shared between sidebar and chat page)
  activeSessionId: string | null;
  activeAgentId: string | null;
  activeAgentIcon: string | null;
  activeAgentName: string | null;
  activeAgentDescription: string | null;
  activeSessionName: string | null;
  _isNewSessionFlow: boolean;
  setActiveSession: (sessionId: string | null, agentId: string | null, meta?: { agentIcon?: string; agentName?: string; agentDescription?: string; sessionName?: string; _isNewSessionFlow?: boolean }) => void;
  // 只设置 activeAgentId，不影响 activeSessionId（用于 toggleAgent 展开/折叠侧边栏）
  setActiveAgentId: (agentId: string | null) => void;

  // Session details (shared between chat and right panel workspace)
  sessionDetails: {
    agentIcon: string;
    agentName: string;
    agentDescription: string;
    sessionName: string;
    lastActive: string;
    imageCount: number;
    fileCount: number;
  } | null;
  setSessionDetails: (details: {
    agentIcon: string;
    agentName: string;
    agentDescription: string;
    sessionName: string;
    lastActive: string;
    imageCount: number;
    fileCount: number;
  } | null) => void;

  // Conversations
  conversations: Conversation[];
  activeConversationId: string | null;
  setActiveConversation: (id: string | null) => void;
  createConversation: () => string;
  deleteConversation: (id: string) => void;
  updateConversationTitle: (id: string, title: string) => void;
  togglePinConversation: (id: string) => void;
  addMessage: (msg: ChatMessage) => void;
  clearActiveConversation: () => void;

  // Legacy single-chat support
  messages: ChatMessage[];
  clearMessages: () => void;

  knowledgeItems: KnowledgeItem[];
  setKnowledgeItems: (items: KnowledgeItem[]) => void;
  toggleStar: (id: string) => void;
  togglePin: (id: string) => void;

  skills: Skill[];
  setSkills: (skills: Skill[]) => void;
  toggleSkill: (id: string) => void;

  searchQuery: string;
  setSearchQuery: (q: string) => void;

  // LLM Config
  llmConfig: LLMConfig;
  setLLMConfig: (config: Partial<LLMConfig>) => void;

  // Agent Nodes
  agentNodes: AgentNode[];
  setAgentNodes: (nodes: AgentNode[]) => void;

  // Agent Groups
  groups: AgentGroup[];
  setGroups: (groups: AgentGroup[]) => void;
  addGroup: (group: AgentGroup) => void;
  updateGroup: (id: string, updates: Partial<AgentGroup>) => void;
  deleteGroup: (id: string) => void;

  // Group Chat Messages (keyed by group id)
  groupMessages: Record<string, GroupChatMessage[]>;
  addGroupMessage: (groupId: string, msg: GroupChatMessage) => void;
  clearGroupMessages: (groupId: string) => void;

  // Teams
  teams: Team[];
  addTeam: (team: Team) => void;
  updateTeam: (id: string, updates: Partial<Team>) => void;
  deleteTeam: (id: string) => void;
  addTeamMember: (teamId: string, member: TeamMember) => void;
  removeTeamMember: (teamId: string, memberId: string) => void;
  updateTeamMember: (teamId: string, memberId: string, updates: Partial<TeamMember>) => void;
  addTeamActivity: (teamId: string, activity: TeamActivity) => void;
  addTeamTask: (teamId: string, task: TeamTask) => void;
  updateTeamTask: (teamId: string, taskId: string, updates: Partial<TeamTask>) => void;
  moveTeamTask: (teamId: string, taskId: string, status: TaskStatus) => void;
  deleteTeamTask: (teamId: string, taskId: string) => void;

  // Theme
  theme: ThemeId;
  setTheme: (theme: ThemeId) => void;

  // Workspaces
  workspaces: Workspace[];
  addWorkspace: (ws: Workspace) => void;
  removeWorkspace: (id: string) => void;
  updateWorkspace: (id: string, updates: Partial<Workspace>) => void;

  // Per-session workspace tabs (right panel state)
  workspaceTabsBySession: Record<string, WorkspaceState>;
  getWorkspaceTabs: (sessionId: string) => WorkspaceState;
  setWorkspaceTabs: (sessionId: string, state: WorkspaceState) => void;
  updateWorkspaceTab: (sessionId: string, tabId: string, updates: Partial<WorkspaceTab>) => void;
  addWorkspaceTab: (sessionId: string, tab: WorkspaceTab, makeActive?: boolean) => void;
  removeWorkspaceTab: (sessionId: string, tabId: string) => void;
  setActiveWorkspaceTab: (sessionId: string, tabId: string) => void;
  navigateWorkspaceTab: (sessionId: string, tabId: string, url: string) => void;
  goBackWorkspaceTab: (sessionId: string, tabId: string) => void;
  goForwardWorkspaceTab: (sessionId: string, tabId: string) => void;
  setWorkspaceTabFilePath: (sessionId: string, tabId: string, filePath: string) => void;

  // Session Spending Stats
  sessionSpending: Record<string, SessionSpending>;
  addSessionSpending: (sessionId: string, tokens: { input: number; output: number; cost: number }) => void;
  getSessionSpending: (sessionId: string) => SessionSpending;
  getAllSpending: () => SessionSpending[];

  // Global WebSocket + Unread
  globalWsConnected: boolean;
  unreadBySession: Record<string, number>;
  setGlobalWsConnected: (v: boolean) => void;
  incrementUnread: (sessionId: string) => void;
  clearUnread: (sessionId: string) => void;
  syncUnreadFromServer: (serverUnread: Record<string, number>) => void;
  getTotalUnread: () => number;

  // Cached agents (avoid re-fetching /api/agents/detect on every mount)
  cachedAgents: AgentInfo[] | null;
  setCachedAgents: (agents: AgentInfo[]) => void;

  // ─── Sidebar Agent List (single source of truth) ────────────────
  // Built by app-shell's fetchSessions(), read by chat-client and conversation-tree.
  // DO NOT maintain separate agent lists in components — use this store.
  // SoulMate is always first (empty group), hermes agent has cli/weixin/acp/tui sessions.
  sidebarAgents: any[];
  setSidebarAgents: (agents: any[] | ((prev: any[]) => any[])) => void;
  sidebarRefreshKey: number;  // Increment to trigger app-shell re-fetch
  refreshSidebar: () => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  // Onboarding
  hasCompletedOnboarding:
    typeof window !== "undefined"
      ? localStorage.getItem("openmate-onboarding-completed") === "true"
      : false,
  completeOnboarding: () => {
    set({ hasCompletedOnboarding: true });
    if (typeof window !== "undefined") {
      localStorage.setItem("openmate-onboarding-completed", "true");
    }
  },

  sidebarCollapsed: false,
  toggleSidebar: () =>
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  rightPanelOpen: false,
  pendingFilePreview: null,
  toggleRightPanel: () =>
    set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  setRightPanelOpen: (open: boolean) =>
    set({ rightPanelOpen: open }),
  setPendingFilePreview: (file) =>
    set({ pendingFilePreview: file }),

  // Page-specific sidebar/workspace content
  pageSidebar: null,
  pageWorkspace: null,
  setPageSidebar: (content) => set({ pageSidebar: content }),
  setPageWorkspace: (content) => set({ pageWorkspace: content }),

  currentPanel: 0,
  setCurrentPanel: (n) => set({ currentPanel: n }),

  // Active session
  activeSessionId: null,
  activeAgentId: null,
  activeAgentIcon: null,
  activeAgentName: null,
  activeAgentDescription: null,
  activeSessionName: null,
  _isNewSessionFlow: false,
  setActiveSession: (sessionId, agentId, meta) =>
    set({
      activeSessionId: sessionId,
      activeAgentId: agentId,
      activeAgentIcon: meta?.agentIcon ?? null,
      activeAgentName: meta?.agentName ?? null,
      activeAgentDescription: meta?.agentDescription ?? null,
      activeSessionName: meta?.sessionName ?? null,
      ...(meta?._isNewSessionFlow !== undefined && { _isNewSessionFlow: meta._isNewSessionFlow }),
    }),
  // 只设置 activeAgentId，不触碰 activeSessionId（toggleAgent 专用）
  setActiveAgentId: (agentId) =>
    set({ activeAgentId: agentId }),

  // Session details
  sessionDetails: null,
  setSessionDetails: (details) => set({ sessionDetails: details }),

  // LLM Config
  llmConfig: {
    provider: "openai",
    url: "https://api.openai.com/v1",
    apiKey: "",
    model: "gpt-4o",
  },
  setLLMConfig: (config) =>
    set((s) => ({ llmConfig: { ...s.llmConfig, ...config } })),

  // Agent Nodes
  agentNodes: [
    { id: "soma-1", name: "Collector-Beijing", type: "soma", status: "online", lastSeen: "Just now", nodeId: "soma-bj-01", endpoint: "ws://10.0.1.12:8900" },
    { id: "soma-2", name: "Collector-Shanghai", type: "soma", status: "offline", lastSeen: "1 hour ago", nodeId: "soma-sh-01", endpoint: "ws://10.0.2.12:8900" },
    { id: "ai-1", name: "GPT-4o", type: "ai", status: "online", lastSeen: "Just now", provider: "OpenAI", model: "gpt-4o", baseUrl: "https://api.openai.com/v1" },
    { id: "ai-2", name: "Claude Sonnet", type: "ai", status: "online", lastSeen: "3 min ago", provider: "Claude", model: "claude-sonnet-4-20250514", baseUrl: "https://api.anthropic.com/v1" },
    { id: "mcp-1", name: "File System MCP", type: "mcp", status: "online", lastSeen: "5 min ago", serverUrl: "http://localhost:3001", tools: ["read_file", "write_file", "list_dir"] },
    { id: "mcp-2", name: "Database MCP", type: "mcp", status: "error", lastSeen: "30 min ago", serverUrl: "http://localhost:3002", tools: ["query", "execute", "schema"] },
  ],
  setAgentNodes: (nodes) => set({ agentNodes: nodes }),

  // Agent Groups
  groups: [],
  setGroups: (groups) => set({ groups }),
  addGroup: (group) => set((s) => ({ groups: [group, ...s.groups] })),
  updateGroup: (id, updates) =>
    set((s) => ({
      groups: s.groups.map((g) =>
        g.id === id ? { ...g, ...updates, updatedAt: Date.now() } : g,
      ),
    })),
  deleteGroup: (id) =>
    set((s) => ({
      groups: s.groups.filter((g) => g.id !== id),
    })),

  // Group Chat Messages
  groupMessages: {},
  addGroupMessage: (groupId, msg) =>
    set((s) => ({
      groupMessages: {
        ...s.groupMessages,
        [groupId]: [...(s.groupMessages[groupId] ?? []), msg],
      },
    })),
  clearGroupMessages: (groupId) =>
    set((s) => ({
      groupMessages: { ...s.groupMessages, [groupId]: [] },
    })),

  // Teams
  teams: [],
  addTeam: (team) => set((s) => ({ teams: [team, ...s.teams] })),
  updateTeam: (id, updates) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === id ? { ...t, ...updates, updatedAt: Date.now() } : t,
      ),
    })),
  deleteTeam: (id) =>
    set((s) => ({
      teams: s.teams.filter((t) => t.id !== id),
    })),
  addTeamMember: (teamId, member) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? { ...t, members: [...t.members, member], updatedAt: Date.now() }
          : t,
      ),
    })),
  removeTeamMember: (teamId, memberId) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? { ...t, members: t.members.filter((m) => m.id !== memberId), updatedAt: Date.now() }
          : t,
      ),
    })),
  updateTeamMember: (teamId, memberId, updates) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? {
              ...t,
              members: t.members.map((m) =>
                m.id === memberId ? { ...m, ...updates } : m,
              ),
              updatedAt: Date.now(),
            }
          : t,
      ),
    })),
  addTeamActivity: (teamId, activity) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? { ...t, activities: [activity, ...t.activities], updatedAt: Date.now() }
          : t,
      ),
    })),
  addTeamTask: (teamId, task) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? { ...t, tasks: [...t.tasks, task], updatedAt: Date.now() }
          : t,
      ),
    })),
  updateTeamTask: (teamId, taskId, updates) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? {
              ...t,
              tasks: t.tasks.map((tk) =>
                tk.id === taskId ? { ...tk, ...updates, updatedAt: Date.now() } : tk,
              ),
              updatedAt: Date.now(),
            }
          : t,
      ),
    })),
  moveTeamTask: (teamId, taskId, status) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? {
              ...t,
              tasks: t.tasks.map((tk) =>
                tk.id === taskId ? { ...tk, status, updatedAt: Date.now() } : tk,
              ),
              updatedAt: Date.now(),
            }
          : t,
      ),
    })),
  deleteTeamTask: (teamId, taskId) =>
    set((s) => ({
      teams: s.teams.map((t) =>
        t.id === teamId
          ? {
              ...t,
              tasks: t.tasks.filter((tk) => tk.id !== taskId),
              updatedAt: Date.now(),
            }
          : t,
      ),
    })),

  // Theme
  theme: getStoredTheme(),
  setTheme: (theme) => {
    set({ theme });
    applyTheme(theme);
    if (typeof window !== "undefined") {
      localStorage.setItem("openmate-theme", theme);
      // Apply custom colors when switching to custom theme
      if (theme === "custom") {
        try {
          const cc = JSON.parse(localStorage.getItem("openmate-custom-colors") || "{}");
          const defs: [string, string, string][] = [["bg","--custom-bg","#1e1e2e"],["fg","--custom-fg","#cdd6f4"],["card","--custom-card","#26273a"],["accent","--custom-accent","#89b4fa"],["secondary","--custom-secondary","#313244"],["border","--custom-border","#313244"],["sidebar","--custom-sidebar","#181825"],["danger","--custom-danger","#f38ba8"],["success","--custom-success","#a6e3a1"]];
          for (const [key, cssVar, fallback] of defs) {
            document.documentElement.style.setProperty(cssVar, cc[key] || fallback);
          }
        } catch {}
      }
    }
  },

  // Conversations
  conversations: [],
  activeConversationId: null,
  setActiveConversation: (id) => set({ activeConversationId: id }),
  createConversation: () => {
    const id = Math.random().toString(36).slice(2, 10);
    const conv: Conversation = {
      id,
      title: "New conversation",
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    set((s) => ({
      conversations: [conv, ...s.conversations],
      activeConversationId: id,
    }));
    return id;
  },
  deleteConversation: (id) =>
    set((s) => {
      const convs = s.conversations.filter((c) => c.id !== id);
      return {
        conversations: convs,
        activeConversationId:
          s.activeConversationId === id
            ? convs[0]?.id ?? null
            : s.activeConversationId,
      };
    }),
  updateConversationTitle: (id, title) =>
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title } : c,
      ),
    })),
  togglePinConversation: (id) =>
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, pinned: !c.pinned } : c,
      ),
    })),
  addMessage: (msg) =>
    set((s) => {
      let convId = s.activeConversationId;
      if (!convId) {
        convId = Math.random().toString(36).slice(2, 10);
        const conv: Conversation = {
          id: convId,
          title: msg.content.slice(0, 40),
          messages: [msg],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        return {
          conversations: [conv, ...s.conversations],
          activeConversationId: convId,
          messages: [...s.messages, msg],
        };
      }
      const convs = s.conversations.map((c) =>
        c.id === convId
          ? {
              ...c,
              messages: [...c.messages, msg],
              updatedAt: Date.now(),
              title:
                c.messages.length === 0 && msg.role === "user"
                  ? msg.content.slice(0, 40)
                  : c.title,
            }
          : c,
      );
      return { conversations: convs, messages: [...s.messages, msg] };
    }),
  clearActiveConversation: () =>
    set((s) => {
      if (!s.activeConversationId) return s;
      return {
        conversations: s.conversations.filter(
          (c) => c.id !== s.activeConversationId,
        ),
        activeConversationId: null,
        messages: [],
      };
    }),

  messages: [],
  clearMessages: () => set({ messages: [], activeConversationId: null }),

  knowledgeItems: [],
  setKnowledgeItems: (items) => set({ knowledgeItems: items }),
  toggleStar: (id) =>
    set((s) => ({
      knowledgeItems: s.knowledgeItems.map((i) =>
        i.id === id ? { ...i, starred: !i.starred } : i,
      ),
    })),
  togglePin: (id) =>
    set((s) => ({
      knowledgeItems: s.knowledgeItems.map((i) =>
        i.id === id ? { ...i, pinned: !i.pinned } : i,
      ),
    })),

  skills: [],
  setSkills: (skills) => set({ skills }),
  toggleSkill: (id) =>
    set((s) => ({
      skills: s.skills.map((sk) =>
        sk.id === id ? { ...sk, enabled: !sk.enabled } : sk,
      ),
    })),

  searchQuery: "",
  setSearchQuery: (q) => set({ searchQuery: q }),

  // Workspaces
  workspaces: [
    {
      id: "ws-1",
      name: "OpenMate Frontend",
      path: "~/projects/openmate",
      lastModified: Date.now() - 3600000,
      fileCount: 156,
      size: 2457600,
      description: "OpenMate main frontend codebase",
    },
    {
      id: "ws-2",
      name: "AI Agent Backend",
      path: "~/projects/agent-backend",
      lastModified: Date.now() - 86400000,
      fileCount: 89,
      size: 1536000,
      description: "Agent backend service",
    },
    {
      id: "ws-3",
      name: "Data Processing Pipeline",
      path: "~/projects/data-pipeline",
      lastModified: Date.now() - 172800000,
      fileCount: 42,
      size: 819200,
    },
  ],
  addWorkspace: (ws) => set((s) => ({ workspaces: [ws, ...s.workspaces] })),
  removeWorkspace: (id) =>
    set((s) => ({ workspaces: s.workspaces.filter((w) => w.id !== id) })),
  updateWorkspace: (id, updates) =>
    set((s) => ({
      workspaces: s.workspaces.map((w) =>
        w.id === id ? { ...w, ...updates, lastModified: Date.now() } : w,
      ),
    })),

  // Per-session workspace tabs (right panel state)
  workspaceTabsBySession: {},
  getWorkspaceTabs: (sessionId) => {
    const state = get();
    const existing = state.workspaceTabsBySession[sessionId];
    if (existing && existing.tabs.length > 0) return existing;
    // Default: one empty new-tab
    const defaultTab: WorkspaceTab = { id: `tab-${Date.now()}-0`, type: 'new-tab', title: 'New Tab', history: [], historyIndex: -1 };
    return { tabs: [defaultTab], activeTabId: defaultTab.id };
  },
  setWorkspaceTabs: (sessionId, wsState) =>
    set((s) => ({ workspaceTabsBySession: { ...s.workspaceTabsBySession, [sessionId]: wsState } })),
  addWorkspaceTab: (sessionId, tab, makeActive) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId] || { tabs: [], activeTabId: '' };
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: {
            tabs: [...existing.tabs, tab],
            activeTabId: makeActive ? tab.id : existing.activeTabId,
          },
        },
      };
    }),
  removeWorkspaceTab: (sessionId, tabId) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId];
      if (!existing) return s;
      const nextTabs = existing.tabs.filter((t) => t.id !== tabId);
      const nextActive = existing.activeTabId === tabId
        ? (nextTabs[Math.max(0, existing.tabs.findIndex((t) => t.id === tabId) - 1)]?.id || '')
        : existing.activeTabId;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: { tabs: nextTabs, activeTabId: nextActive },
        },
      };
    }),
  updateWorkspaceTab: (sessionId, tabId, updates) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId];
      if (!existing) return s;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: {
            ...existing,
            tabs: existing.tabs.map((t) => (t.id === tabId ? { ...t, ...updates } : t)),
          },
        },
      };
    }),
  setActiveWorkspaceTab: (sessionId, tabId) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId];
      if (!existing) return s;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: { ...existing, activeTabId: tabId },
        },
      };
    }),
  navigateWorkspaceTab: (sessionId, tabId, url) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId];
      if (!existing) return s;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: {
            ...existing,
            tabs: existing.tabs.map((t) => {
              if (t.id !== tabId) return t;
              const newHistory = [...t.history.slice(0, t.historyIndex + 1), url];
              return { ...t, url, history: newHistory, historyIndex: newHistory.length - 1, title: new URL(url).hostname };
            }),
          },
        },
      };
    }),
  goBackWorkspaceTab: (sessionId, tabId) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId];
      if (!existing) return s;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: {
            ...existing,
            tabs: existing.tabs.map((t) => {
              if (t.id !== tabId || t.historyIndex <= 0) return t;
              const newIndex = t.historyIndex - 1;
              return { ...t, url: t.history[newIndex], historyIndex: newIndex };
            }),
          },
        },
      };
    }),
  goForwardWorkspaceTab: (sessionId, tabId) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId];
      if (!existing) return s;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: {
            ...existing,
            tabs: existing.tabs.map((t) => {
              if (t.id !== tabId || t.historyIndex >= t.history.length - 1) return t;
              const newIndex = t.historyIndex + 1;
              return { ...t, url: t.history[newIndex], historyIndex: newIndex };
            }),
          },
        },
      };
    }),
  setWorkspaceTabFilePath: (sessionId, tabId, filePath) =>
    set((s) => {
      const existing = s.workspaceTabsBySession[sessionId];
      if (!existing) return s;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: {
            ...existing,
            tabs: existing.tabs.map((t) => (t.id === tabId ? { ...t, filePath } : t)),
          },
        },
      };
    }),

  // Session Spending Stats
  sessionSpending: typeof window !== "undefined"
    ? (() => { try { return JSON.parse(localStorage.getItem("openmate-session-spending") || "{}"); } catch { return {}; } })()
    : {},
  addSessionSpending: (sessionId, tokens) =>
    set((s) => {
      const existing = s.sessionSpending[sessionId] || {
        sessionId,
        totalInputTokens: 0,
        totalOutputTokens: 0,
        totalCost: 0,
        messageCount: 0,
        lastUpdated: 0,
      };
      const updated: SessionSpending = {
        ...existing,
        sessionId,
        totalInputTokens: existing.totalInputTokens + tokens.input,
        totalOutputTokens: existing.totalOutputTokens + tokens.output,
        totalCost: existing.totalCost + tokens.cost,
        messageCount: existing.messageCount + 1,
        lastUpdated: Date.now(),
      };
      const next = { ...s.sessionSpending, [sessionId]: updated };
      try { localStorage.setItem("openmate-session-spending", JSON.stringify(next)); } catch {}
      return { sessionSpending: next };
    }),
  getSessionSpending: (sessionId) => {
    return get().sessionSpending[sessionId] || {
      sessionId, totalInputTokens: 0, totalOutputTokens: 0, totalCost: 0, messageCount: 0, lastUpdated: 0,
    };
  },
  getAllSpending: () => {
    return Object.values(get().sessionSpending).sort((a, b) => b.lastUpdated - a.lastUpdated);
  },

  // Global WebSocket + Unread state
  // 未读以服务端为真源（session_reads 按账号分区，2026-11-17）：本地缓存只是
  // 实时尾巴的加速缓存，且按账号分桶——设备不再作为数据归属（同一账号换设备
  // 看到同一份未读；切账号不串桶）。
  globalWsConnected: false,
  unreadBySession: typeof window !== "undefined"
    ? (() => {
        try {
          const uid = localStorage.getItem("openmate-user-id") || "";
          return JSON.parse(localStorage.getItem(`openmate-unread:${uid}`) || "{}");
        } catch { return {}; }
      })()
    : {},
  setGlobalWsConnected: (v) => set({ globalWsConnected: v }),
  incrementUnread: (sessionId) =>
    set((s) => {
      const next = { ...s.unreadBySession, [sessionId]: (s.unreadBySession[sessionId] || 0) + 1 };
      try {
        const uid = typeof window !== "undefined" ? (localStorage.getItem("openmate-user-id") || "") : "";
        localStorage.setItem(`openmate-unread:${uid}`, JSON.stringify(next));
      } catch {}
      return { unreadBySession: next };
    }),
  clearUnread: (sessionId) =>
    set((s) => {
      if (!s.unreadBySession[sessionId]) return s;
      const next = { ...s.unreadBySession };
      delete next[sessionId];
      try {
        const uid = typeof window !== "undefined" ? (localStorage.getItem("openmate-user-id") || "") : "";
        localStorage.setItem(`openmate-unread:${uid}`, JSON.stringify(next));
      } catch {}
      return { unreadBySession: next };
    }),
  syncUnreadFromServer: (serverUnread) =>
    // 服务端为准（加速缓存降级）：拉列表时用账号分区的服务端未读覆盖本地缓存，
    // 本地只剩"本端实时流内尚未刷新"的尾巴（incrementUnread 累加的部分被覆盖归正）。
    set((s) => {
      const next: Record<string, number> = {};
      for (const [sid, n] of Object.entries(serverUnread || {})) {
        if (n > 0) next[sid] = n;
      }
      try {
        const uid = typeof window !== "undefined" ? (localStorage.getItem("openmate-user-id") || "") : "";
        localStorage.setItem(`openmate-unread:${uid}`, JSON.stringify(next));
      } catch {}
      return { unreadBySession: next };
    }),
  getTotalUnread: () => {
    return Object.values(get().unreadBySession).reduce((sum, n) => sum + n, 0);
  },

  // Cached agents
  cachedAgents: null,
  setCachedAgents: (agents) => set({ cachedAgents: agents }),

  // Sidebar agent list
  sidebarAgents: [],
  setSidebarAgents: (agentsOrFn) =>
    set((s) => ({
      sidebarAgents: typeof agentsOrFn === 'function' ? agentsOrFn(s.sidebarAgents) : agentsOrFn,
    })),
  sidebarRefreshKey: 0,
  refreshSidebar: () => set((s) => ({ sidebarRefreshKey: s.sidebarRefreshKey + 1 })),
}));

// ─── localStorage persistence for conversations ────────────────────────────
// 已降级（2026-11-17 跨端单一真源）：conversations 不再持久化/回灌 localStorage。
// 修前每台设备各存一份 openmate-conversations 并在启动时当记录回灌 store——
// 设备本地存储被当成数据真源，桌面/移动各见各的（违反"账号区分，设备只是入口"）。
// 消息/会话真源=服务端 opensoul.db（GET /api/sessions/{id}/messages），本地零副本。
