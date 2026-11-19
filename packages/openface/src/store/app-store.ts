import { create } from "zustand";

// ─── UI Layout State (subset of full app store) ─────────────────────────────

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

interface AppLayoutState {
  // Sidebar
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;

  // Right workspace panel
  rightPanelOpen: boolean;
  toggleRightPanel: () => void;
  setRightPanelOpen: (open: boolean) => void;

  // Page-specific sidebar content
  // （pageWorkspace→workspace 面板通道已整体拆除：页面 detail 一律页内渲染，
  //   workspace 面板只属于工作区——结构性杜绝「点击开进 workspace」）
  pageSidebar: React.ReactNode | null;
  setPageSidebar: (content: React.ReactNode | null) => void;

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
}

export const useAppStore = create<AppLayoutState>((set, get) => ({
  sidebarCollapsed: false,
  toggleSidebar: () =>
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  rightPanelOpen: false,
  toggleRightPanel: () =>
    set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  setRightPanelOpen: (open: boolean) =>
    set({ rightPanelOpen: open }),

  pageSidebar: null,
  setPageSidebar: (content) => set({ pageSidebar: content }),

  // ── Per-session workspace tabs ──────────────────────────────────

  workspaceTabsBySession: {},

  getWorkspaceTabs: (sessionId) => {
    const state = get();
    const existing = state.workspaceTabsBySession[sessionId];
    if (existing) return existing;
    const defaultTab: WorkspaceTab = { id: `tab-${Date.now()}-0`, type: 'new-tab', title: '新标签页', history: [], historyIndex: -1 };
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
      const filtered = existing.tabs.filter((t) => t.id !== tabId);
      const newActive = existing.activeTabId === tabId
        ? (filtered[filtered.length - 1]?.id || '')
        : existing.activeTabId;
      return {
        workspaceTabsBySession: {
          ...s.workspaceTabsBySession,
          [sessionId]: { tabs: filtered, activeTabId: newActive },
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
              return { ...t, url, history: newHistory, historyIndex: newHistory.length - 1 };
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
            tabs: existing.tabs.map((t) =>
              t.id === tabId ? { ...t, filePath, title: filePath.split('/').pop() || t.title } : t
            ),
          },
        },
      };
    }),
}));
