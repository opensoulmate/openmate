"use client";
import { Plus, Trash2 } from "lucide-react";
import { SOULMATE_LOGO, SOULMATE_ICON } from "@/lib/agent-brand";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

import { TerminalPanel } from "@/components/terminal-panel";
import { BottomNav } from "@/components/bottom-nav";
import { TopBar } from "@/components/top-bar";
import { ShellTitleBar } from "@/components/shell-title-bar";
import { initTauriDesktopBridge } from "@/lib/tauri-desktop-bridge";
import { startBrowserTaskRunner } from "@/lib/browser-task-runner";
import { installBehaviorSensor } from "@/lib/behavior-sensor";
import { WorkspacePanel } from '@opensoulmate/openface';
import { AIGroupsSidebar } from "@/components/ai-groups-sidebar";
import { OrganNavSidebar } from "@/components/organ-nav-sidebar";
import { useAIGroupsStore } from "@/stores/ai-groups-store";

import { useVisibilityPoll } from "@/hooks/use-visibility-poll";
import { GlobalWebSocket } from "@/components/global-websocket";
import { EChartsThemeProvider } from "@/components/echarts-theme-provider";


import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useAppStore } from "@/stores/app-store";
import { useTranslation } from "react-i18next";
import { useState, useRef, useCallback, useEffect } from "react";
import { getUserId, getUserName, getApiBaseUrl, getToken } from "@/lib/api-client";
import { type ThemeId, persistTheme } from "@/lib/theme";
import {
  SidebarProvider,
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarFooter,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarRail,
  SidebarInset,
} from "@/components/ui/sidebar";
import { ConversationTree, type AgentInfo } from "@/components/conversation-tree";
import { LeftPanel } from "@/components/left-panel";
import { SwipeablePanels, getPanelIndex } from "@/components/swipeable-panels";
import { useIsMobile, useMediaQuery } from "@/hooks/use-mobile";

// ── Types ────────────────────────────────────────────────────────

interface Session {
  id: string;
  name?: string;
  title?: string;
  platform?: string;
  chat_id?: string;
  last_message?: string;
  unread?: number;
  workspace?: string;
  last_active?: string;
  updated_at?: string;
  created_at?: string;
  message_count?: number;
  source?: string;
  tags?: string[];
}
interface SourceGroup {
  source: string;
  label: string;
  icon: string;
  sessions: Session[];
  expanded: boolean;
}

// AgentInfo imported from conversation-tree.tsx

// Source metadata (i18n keys)
const SOURCE_META: Record<string, { labelKey: string; icon: string }> = {
  cli:      { labelKey: 'sessions.sourceCli',  icon: '⌨️' },
  weixin:   { labelKey: 'sessions.sourceWeixin', icon: '💬' },
  cron:     { labelKey: 'sessions.sourceCron', icon: '⏰' },
  acp:      { labelKey: 'sessions.sourceAcp', icon: '🔗' },
  tui:      { labelKey: 'sessions.sourceTui', icon: '🖥️' },
  tool:     { labelKey: 'sessions.sourceTool', icon: '🔧' },
  subagent: { labelKey: 'sessions.sourceSubagent', icon: '🤖' },
};

const PLATFORM_SOURCES = new Set(['cli', 'weixin', 'acp', 'tui']);
const SKIP_AGENT_IDS = new Set(['cron', 'unknown', 'tool', 'subagent']);

const AGENT_ICONS: Record<string, string> = {
  hermes: '🏛️', claude: '🟣', codex: '🟢', gemini: '🔵', mimo: '📱',
  opencode: '⚡', aider: '🤝', copilot: '🐙', cursor: '▶️', windsurf: '🏄',
  cline: '🔧', continue: '🔄', deepseek: '🐋', qwen: '🟠',
  'amazon-q': '☁️', openclaw: '🐾', 'pi-agent': 'π', ollama: '🦙',
};

// ── Main component ────────────────────────────────────────────────

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeSessionId = searchParams.get('session');
  const activeSessionIdFromStore = useAppStore((s) => s.activeSessionId);
  const setActiveSession = useAppStore((s) => s.setActiveSession);
  const collapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggle = useAppStore((s) => s.toggleSidebar);
  const storeTheme = useAppStore((s) => s.theme);
  const setStoreTheme = useAppStore((s) => s.setTheme);
  const [menuOpen, setMenuOpen] = useState(false);
  const rightPanelOpen = useAppStore((s) => s.rightPanelOpen);
  const pendingFilePreview = useAppStore((s) => s.pendingFilePreview);
  const toggleRightPanel = useAppStore((s) => s.toggleRightPanel);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  // 桌面壳桥：外链/新窗口 → workspace 新 web-browser 标签页 + 展开右栏
  const [pendingWebUrl, setPendingWebUrl] = useState<string | null>(null);
  // 桌面壳：更新就绪 → 主题化弹窗（替代原生对话框，与 app 风格一致）
  const [updateInfo, setUpdateInfo] = useState<{ version: string } | null>(null);
  useEffect(() => {
    // Tauri 壳补同款桥（Electron 走自己的 preload 桥，此处只在 Tauri 生效）
    initTauriDesktopBridge();
    const bridge = (window as any).openmateDesktop;
    if (!bridge?.onUpdateReady) return;
    const off = bridge.onUpdateReady((data: { version: string }) => setUpdateInfo(data));
    return typeof off === 'function' ? off : undefined;
  }, []);
  // 内置浏览器自动化：SoulMate 的 browser 工具经 acp-proxy 通道驱动本窗口的 webview
  useEffect(() => {
    const g = window as any;
    g.__omOpenWebTab = (url: string) => {
      setPendingWebUrl(url);
      setRightPanelOpen(true);
    };
    installBehaviorSensor();
    startBrowserTaskRunner();
    // 毛玻璃：桌面壳挂 om-glass（CSS 端磨砂生效；网页版不挂=实底）
    if ((window as any).openmateDesktop || (window as any).__TAURI_INTERNALS__) {
      document.documentElement.classList.add('om-glass');
    }
    return () => {
      delete g.__omOpenWebTab;
    };
  }, [setRightPanelOpen]);

  useEffect(() => {
    const bridge = (window as any).openmateDesktop;
    if (bridge?.onOpenWebTab) {
      const off = bridge.onOpenWebTab((url: string) => {
        setPendingWebUrl(url);
        setRightPanelOpen(true);
      });
      return typeof off === 'function' ? off : undefined;
    }
    // Tauri 壳：Rust 侧 on_new_window 拦截 target=_blank → open-web-tab 事件
    //（Electron 拦 new-window 同款语义：新窗请求落成 workspace 新 web-browser 标签）
    if ((window as any).__TAURI_INTERNALS__) {
      let unlisten: (() => void) | undefined;
      let cancelled = false;
      import('@tauri-apps/api/event').then(({ listen }) => {
        listen<string>('open-web-tab', (e) => {
          console.error('[newwin] 事件到达前端:', e.payload);
          setPendingWebUrl(e.payload);
          setRightPanelOpen(true);
        }).then((f) => {
          if (cancelled) f();
          else unlisten = f;
        });
      });
      return () => {
        cancelled = true;
        unlisten?.();
      };
    }
  }, [setRightPanelOpen]);
  const pageSidebar = useAppStore((s) => s.pageSidebar);
  const pageWorkspace = useAppStore((s) => s.pageWorkspace);
  const isMobile = useIsMobile();
  // Auto-collapse sidebar on mid-sized screens (lg but not xl)
  // User can override by clicking the toggle button
  const isMidScreen = useMediaQuery("(min-width: 1024px) and (max-width: 1279px)");
  const [midScreenExpanded, setMidScreenExpanded] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const effectiveCollapsed = isMobile ? !mobileSidebarOpen : (collapsed || (isMidScreen && !midScreenExpanded));
  // ── 全屏↔左栏互斥（openface 工作台全屏经 CustomEvent 协调）──
  // 进全屏：左栏自动折叠（记住原状态）；退出全屏：恢复；全屏中手动展开左栏=退出全屏
  const [fsActive, setFsActive] = useState(false);
  const fsWasCollapsed = useState<{ v: boolean | null }>({ v: null })[0];
  useEffect(() => {
    const onFs = (e: Event) => {
      const on = (e as CustomEvent).detail?.on;
      if (on) {
        fsWasCollapsed.v = effectiveCollapsed;
        setFsActive(true);
        if (isMobile) { if (mobileSidebarOpen) setMobileSidebarOpen(false); }
        else if (isMidScreen) { if (midScreenExpanded) setMidScreenExpanded(false); }
        else if (!collapsed) toggle();
      } else {
        const was = fsWasCollapsed.v;
        fsWasCollapsed.v = null;
        setFsActive(false);
        if (was === false) {
          if (isMobile) { if (!mobileSidebarOpen) setMobileSidebarOpen(true); }
          else if (isMidScreen) { if (!midScreenExpanded) setMidScreenExpanded(true); }
          else if (collapsed) toggle();
        }
      }
    };
    window.addEventListener('om-fullscreen-state', onFs);
    return () => window.removeEventListener('om-fullscreen-state', onFs);
  }, [effectiveCollapsed, isMobile, isMidScreen, mobileSidebarOpen, midScreenExpanded, collapsed, toggle, fsWasCollapsed]);
  const currentPanel = useAppStore((s) => s.currentPanel);
  const setCurrentPanel = useAppStore((s) => s.setCurrentPanel);
  const { t } = useTranslation();
  const [eventCount, setEventCount] = useState(0);
  const [rightPanelWidth, setRightPanelWidth] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);

  // ── Conversation list state ──────────────────────────────────────
  const agents = useAppStore((s) => s.sidebarAgents) as AgentInfo[];
  const setSidebarAgents = useAppStore((s) => s.setSidebarAgents);
  const [clearedUnreads, setClearedUnreads] = useState<Set<string>>(new Set());
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null); // 删除确认弹框目标

  // Clear unread for a session (called on click)
  const clearSessionUnread = useCallback((sessionId: string) => {
    setClearedUnreads((prev) => {
      const next = new Set(prev);
      next.add(sessionId);
      return next;
    });
    // Also clear store unread
    useAppStore.getState().clearUnread(sessionId);
    // Also call API to clear server-side
    const apiBase = getApiBaseUrl();
    const token = getToken();
    if (apiBase && token) {
      fetch(`${apiBase}/api/sessions/${sessionId}/read`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {});
    }
  }, []);

  // Get effective unread count — combine API unread + WS real-time unread
  const unreadBySession = useAppStore((s) => s.unreadBySession);
  const getUnread = useCallback((session: Session): number => {
    if (clearedUnreads.has(session.id)) return 0;
    // WS real-time unread takes priority (more accurate)
    const wsUnread = unreadBySession[session.id] || 0;
    const apiUnread = session.unread ?? 0;
    return Math.max(wsUnread, apiUnread);
  }, [clearedUnreads, unreadBySession]);

  // Total unread across all sessions
  const totalUnread = agents.reduce((sum, a) =>
    sum + a.sessions.reduce((s, session) => s + getUnread(session), 0), 0
  );

  // Fetch sessions + build agent tree (like chat page)
  const fetchSessions = useCallback(async () => {
    const apiBase = getApiBaseUrl();
    const token = getToken();
    if (!apiBase) return;
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    // 1. Detect agents
    let detectedAgents: Array<{ id: string; name: string; icon: string; description: string; available: boolean; logo?: string }> = [];
    // Check store cache first
    const cachedAgents = useAppStore.getState().cachedAgents;
    if (cachedAgents && cachedAgents.length > 0) {
      detectedAgents = cachedAgents as any;
    } else {
      try {
        const r = await fetch(`${apiBase}/api/agents/detect`, { headers });
        if (r.ok) { const d = await r.json(); detectedAgents = d.agents || []; } else { console.error("agents/detect failed:", r.status); }
        useAppStore.getState().setCachedAgents(detectedAgents as any);
      } catch (e) { console.error("agents/detect error:", e); }
    }

    // 1b. Registry（探测即注册=已安装+连接成功+必能开会话）——列表的"谁出现"只认它，
    // detect 目录只提供图标/描述元数据。未注册不进列表（agent 铁律）。
    let registeredIds = new Set<string>();
    try {
      const r = await fetch(`${apiBase}/api/agents/registry`, { headers });
      if (r.ok) { const d = await r.json(); (d.agents || []).forEach((a: any) => { if (a.agent_id && a.registered) registeredIds.add(a.agent_id); }); }
      else { console.error("agents/registry failed:", r.status); }
    } catch (e) { console.error("agents/registry error:", e); }

    // 2. Fetch sessions
    let sessions: Session[] = [];
    try {
      const r = await fetch(`${apiBase}/api/sessions?limit=500`, { headers });
      if (r.ok) { const d = await r.json(); sessions = d.sessions || []; } else { console.error("sessions failed:", r.status); }
    } catch (e) { console.error("sessions error:", e); }

    // 2b. 未读以服务端为真源（账号分区 session_reads，2026-11-17）：本地未读缓存
    // 覆盖归正——任一端读过，同一账号所有端未读归零，不再各设备各一份。
    try {
      const serverUnread: Record<string, number> = {};
      for (const s of sessions) {
        if (typeof s.unread === 'number') serverUnread[s.id] = s.unread;
      }
      useAppStore.getState().syncUnreadFromServer(serverUnread);
    } catch { /* 缓存归正失败不影响列表 */ }

    // 3. Group sessions by agent (check session tags first, then fallback to platform)
    const agentSessionMap: Record<string, Session[]> = {};
    for (const s of sessions) {
      if (!s.platform && s.source) s.platform = s.source;
      const src = s.platform || s.source || '';
      if (src === 'cron') continue; // filter cron sessions
      const HERMES_SOURCES = new Set(['cli', 'weixin', 'acp', 'tui']);
      // Check server-side tags first (e.g. "agent:soulmate"), then platform detection
      const agentTag = (s.tags || []).find((t: string) => t.startsWith('agent:'));
      const agentKey = agentTag ? agentTag.replace('agent:', '') : (HERMES_SOURCES.has(src) ? 'hermes' : (s.platform || src || 'unknown'));
      if (!agentSessionMap[agentKey]) agentSessionMap[agentKey] = [];
      agentSessionMap[agentKey].push(s);
    }

    console.log('[app-shell-debug] agentSessionMap keys:', Object.keys(agentSessionMap), 'cron sessions filtered:', sessions.filter(s => (s.platform || s.source) === 'cron').length);

    // 4. Build source groups
    const buildSourceGroups = (agentSessions: Session[], agentId: string): SourceGroup[] | undefined => {
      const sourceMap: Record<string, Session[]> = {};
      for (const s of agentSessions) {
        const src = (s.platform || s.source || agentId);
        if (!sourceMap[src]) sourceMap[src] = [];
        sourceMap[src].push(s);
      }
      const sources = Object.keys(sourceMap);
      if (sources.length <= 1) return undefined;
      return sources.map(src => {
        const meta = SOURCE_META[src] || { labelKey: src, icon: '💬' };
        return {
          source: src, label: t(meta.labelKey), icon: meta.icon,
          sessions: sourceMap[src], expanded: false,
        };
      });
    };

    // 5. Build agent list
    const agentMap = new Map<string, AgentInfo>();

    // SoulMate (OpenMate platform) ALWAYS shows first
    const soulmateSessions = agentSessionMap['soulmate'] || [];
    agentMap.set('soulmate', {
      id: 'soulmate', name: 'SoulMate', icon: SOULMATE_ICON, logo: SOULMATE_LOGO, description: 'OpenMate Platform',
      installed: true, available: true, sessions: soulmateSessions,
      expanded: false, sourceGroups: buildSourceGroups(soulmateSessions, 'soulmate'),
    });

    // Only add REGISTERED (installed+connected) agents — 未注册不进列表
    for (const [key, sessions] of Object.entries(agentSessionMap)) {
      if (SKIP_AGENT_IDS.has(key) || agentMap.has(key)) continue;
      if (!registeredIds.has(key)) continue;
      const detected = detectedAgents.find(a => a.id === key);
      agentMap.set(key, {
        id: key, name: detected?.name || key,
        icon: detected?.icon || AGENT_ICONS[key] || '🤖',
        logo: detected?.logo, description: detected?.description || key,
        installed: detected?.available || false, available: detected?.available || false,
        sessions, expanded: false, sourceGroups: buildSourceGroups(sessions, key),
      });
    }

    // Add registered agents (connected, can open sessions) that have no sessions yet
    for (const detected of detectedAgents) {
      if (SKIP_AGENT_IDS.has(detected.id) || agentMap.has(detected.id)) continue;
      if (!registeredIds.has(detected.id)) continue; // 未注册不进列表
      agentMap.set(detected.id, {
        id: detected.id, name: detected.name || detected.id,
        icon: detected.icon || AGENT_ICONS[detected.id] || '🤖',
        logo: detected.logo, description: detected.description || detected.id,
        installed: detected.available || false, available: detected.available || false,
        sessions: [], expanded: false, sourceGroups: undefined,
      });
    }

    // 兜底：已注册但不在检测目录/无会话的 agent 也必须出现（连接成功即可发起会话）
    for (const rid of registeredIds) {
      if (SKIP_AGENT_IDS.has(rid) || agentMap.has(rid)) continue;
      const sessions = agentSessionMap[rid] || [];
      agentMap.set(rid, {
        id: rid, name: rid, icon: AGENT_ICONS[rid] || '🤖', description: rid,
        installed: true, available: true,
        sessions, expanded: false, sourceGroups: buildSourceGroups(sessions, rid),
      });
    }

    const agentList = Array.from(agentMap.values());
    agentList.sort((a, b) => {
      if (a.id === 'soulmate') return -1;
      if (b.id === 'soulmate') return 1;
      return b.sessions.length - a.sessions.length;
    });

    // 7. Update state preserving expanded
    setSidebarAgents((prev: AgentInfo[]) => {
      const expandedIds = new Set(prev.filter(a => a.expanded).map(a => a.id));
      const expandedSrcs = new Map<string, Set<string>>();
      prev.forEach(a => a.sourceGroups?.forEach(g => {
        if (g.expanded) {
          if (!expandedSrcs.has(a.id)) expandedSrcs.set(a.id, new Set());
          expandedSrcs.get(a.id)!.add(g.source);
        }
      }));
      return agentList.map(a => ({
        ...a,
        expanded: expandedIds.has(a.id),
        sourceGroups: a.sourceGroups?.map(g => ({
          ...g,
          expanded: expandedSrcs.get(a.id)?.has(g.source) ?? false,
        })),
      }));
    });
  }, [t]);

  // ── 旧 chunk 自动换血（2026-10-25）：部署会换 chunk 哈希，用户开着的旧会话引用失效 chunk
  // 后事件链静默失效（"按钮点了没反应"）。发现加载失败→自动换新一次（sessionStorage 防循环）。
  useEffect(() => {
    const handler = (e: ErrorEvent) => {
      const msg = String(e.message || '');
      const isChunk = /ChunkLoadError|Loading chunk .* failed|Failed to fetch dynamically imported module|error loading dynamically imported module/i.test(msg);
      if (!isChunk) return;
      if (sessionStorage.getItem('openmate-chunk-reloaded') === '1') return;
      sessionStorage.setItem('openmate-chunk-reloaded', '1');
      window.location.reload();
    };
    window.addEventListener('error', handler);
    window.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => handler({ message: String(e.reason) } as ErrorEvent));
    return () => window.removeEventListener('error', handler);
  }, []);

  // Only fetch sessions on chat page — other pages don't need the session list
  const isChatRoute = pathname.startsWith('/chat');
  const sidebarRefreshKey = useAppStore((s) => s.sidebarRefreshKey);
  useEffect(() => {
    if (!isChatRoute) return;
    // 250ms 收敛：一次操作的多个刷新信号（注册/标题更新/迁移）合并为一次拉取。
    // 依赖去掉 fetchSessions/activeSessionId：切会话不再整表重拉（"老是自动刷新"的主因），
    // fetchSessions 引用波动也不再触发风暴。
    const timer = setTimeout(() => { fetchSessions(); }, 250);
    return () => clearTimeout(timer);
  }, [isChatRoute, sidebarRefreshKey]);

  // Toggle agent expand
  const toggleAgent = useCallback((agentId: string) => {
    setSidebarAgents((prev: AgentInfo[]) => prev.map(a =>
      a.id === agentId ? { ...a, expanded: !a.expanded } : a
    ));
    // 点击 agent 标题时，同时设置 activeAgentId（让 selectedAgentRef 更新）
    useAppStore.getState().setActiveSession(null, agentId === 'soulmate' ? null : agentId, {});
  }, []);

  // Toggle source group expand
  const toggleSourceGroup = useCallback((agentId: string, source: string) => {
    setSidebarAgents((prev: AgentInfo[]) => prev.map(a =>
      a.id === agentId ? {
        ...a,
        sourceGroups: a.sourceGroups?.map(g =>
          g.source === source ? { ...g, expanded: !g.expanded } : g
        ),
      } : a
    ));
  }, []);

  // Fetch event count
  useVisibilityPoll(() => {
    const apiBase = getApiBaseUrl();
    if (!apiBase) return;
    fetch(`${apiBase}/api/events/summary`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.total_events && data.total_events > 0) setEventCount(Math.min(data.total_events, 99));
      })
      .catch(() => {});
  }, 30000, []);

  const userId = getUserName() || getUserId() || "User";

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  // Reset mid-screen override when screen size changes
  useEffect(() => {
    setMidScreenExpanded(false);
  }, [isMidScreen]);

  // Close mobile sidebar when switching to desktop (prevents stale state)
  useEffect(() => {
    if (!isMobile) setMobileSidebarOpen(false);
  }, [isMobile]);

  // Track right panel width — desktop: 50vw, mobile: 75vw (capped at 384px)
  useEffect(() => {
    const update = () => setRightPanelWidth(isMobile ? 256 : Math.round(window.innerWidth / 2));
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, [isMobile]);

  useEffect(() => { setMenuOpen(false); setRightPanelOpen(false); setMobileSidebarOpen(false); }, [pathname]);

  // Sync swipeable panel index with current route
  useEffect(() => {
    const idx = getPanelIndex(pathname);
    if (idx >= 0 && idx !== currentPanel) {
      setCurrentPanel(idx);
    }
  }, [pathname, currentPanel, setCurrentPanel]);

  function toggleTheme() {
    const next: ThemeId = storeTheme === "dark" ? "light" : storeTheme === "light" ? "purple" : "dark";
    persistTheme(next);
    setStoreTheme(next);
  }

  function handleLogout() {
    localStorage.removeItem("openmate-token");
    localStorage.removeItem("openmate-api-url");
    window.location.href = "/login";
  }

  // Detect AI Groups route for conditional sidebar/workspace rendering
  const isAIGroupsRoute = pathname.startsWith('/ai-groups');
  const fetchAIGroups = useAIGroupsStore((s) => s.fetchGroups);

  // Fetch AI groups when on the ai-groups route
  useEffect(() => {
    if (isAIGroupsRoute) {
      fetchAIGroups();
    }
  }, [isAIGroupsRoute, fetchAIGroups]);

  return (
    <div className="flex flex-col h-svh overflow-hidden">
      <GlobalWebSocket />
      <EChartsThemeProvider>
      {/* Top utility bar — full screen width */}
      <ShellTitleBar />
      {/* shrink-0：整行永不被 flex 压矮，防止图标溢出后被下方内容区背景盖住 */}
      <div className="om-chrome flex shrink-0 items-center">
        <div className="flex-1 min-w-0">
          <TopBar
            eventCount={eventCount}
          />
        </div>
      </div>

      {/* Middle: sidebar + content + right panel */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">
        <SidebarProvider open={!effectiveCollapsed} onOpenChange={(open) => {
          // 互斥：全屏中手动展开左侧 = 退出全屏
          if (open && fsActive) window.dispatchEvent(new CustomEvent('om-fullscreen-exit'));
          if (open === effectiveCollapsed) {
            if (isMobile) {
              setMobileSidebarOpen(prev => !prev);
              if (!mobileSidebarOpen) setRightPanelOpen(false); // mutual exclusion: close right panel when sidebar opens
            } else if (isMidScreen) {
              setMidScreenExpanded(prev => !prev);
            } else {
              toggle();
            }
          }
        }} className="flex-1 min-h-0 overflow-hidden h-full">
          {/* Sidebar — unified mobile/desktop */}
          <Sidebar collapsible="offcanvas">

              <SidebarContent>
                {isAIGroupsRoute ? (
                  <AIGroupsSidebar />
                ) : pageSidebar ? (
                  pageSidebar
                ) : isChatRoute ? (
                  <>
                    <LeftPanel
                      placeholder={t("sidebar.searchPlaceholder", "搜索会话...")}

                      renderContent={(query) => (
                        <ConversationTree
                          agents={agents}
                          activeSessionId={activeSessionIdFromStore}
                          getUnread={getUnread}
                          onToggleAgent={toggleAgent}
                          onToggleSourceGroup={toggleSourceGroup}
                          onSelectSession={(session, agent) => {
                            clearSessionUnread(session.id);
                            setActiveSession(session.id, agent.id, {
                              agentIcon: agent.icon,
                              agentName: agent.name,
                              agentDescription: agent.description || '',
                              sessionName: session.name || session.title || '',
                            });
                            router.push('/chat');
                          }}
                          onNewSession={(agentId) => {
                            const agent = agents.find((a: AgentInfo) => a.id === agentId);
                            const tempSessionId = `temp-${Date.now()}`;
                            useAppStore.getState().setActiveSession(tempSessionId, agentId === 'soulmate' ? null : agentId, { agentName: agent?.name || agentId, _isNewSessionFlow: true });
                            // 不再用 ?new=时间戳强制重挂载：重挂载的清理逻辑会杀掉正在握手的新连接（"连接已断开"根因）。
                            // 会话切换由 _isNewSessionFlow 状态机制完成；仅跨页面时才导航。
                            if (window.location.pathname !== '/chat') {
                              router.push('/chat');
                            }
                          }}
                          onDeleteSession={async (sessionId) => {
                            setDeleteTargetId(sessionId);
                          }}
                          search={query}
                          className="group-data-[collapsible=icon]:hidden"
                        />
                      )}
                    />
                  </>
                ) : (
                  <OrganNavSidebar />
                )}
              </SidebarContent>
              <SidebarFooter />
              <SidebarRail />
            </Sidebar>

      {/* Main content area */}
      <SidebarInset className="min-h-0 overflow-hidden" onClick={() => {
        if (isMobile) {
          if (mobileSidebarOpen) setMobileSidebarOpen(false);
          if (rightPanelOpen) setRightPanelOpen(false);
        }
      }}>
        <div className="flex flex-1 flex-col overflow-hidden min-h-0">
          <SwipeablePanels isHomePage={getPanelIndex(pathname) >= 0}>
            {children}
          </SwipeablePanels>
        </div>
      </SidebarInset>

      {/* Terminal Panel */}
      <TerminalPanel apiBase="" token={typeof window !== 'undefined' ? localStorage.getItem('openmate-token') || '' : ''} />
        </SidebarProvider>

      {/* Right Panel — sidebar-style sliding, unified mobile/desktop */}
      {/* Gap: reserves space on desktop, zero on mobile (overlay mode) */}
      <div
        className="shrink-0 transition-[width] duration-200 ease-linear max-lg:!w-0"
        style={{ width: rightPanelOpen ? rightPanelWidth : 0 }}
      />
      {/* Mobile backdrop — tap to close right panel */}
      {isMobile && rightPanelOpen && (
        <div
          className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0"
          onClick={() => setRightPanelOpen(false)}
          aria-hidden="true"
        />
      )}
      {/* 桌面壳：更新就绪提示（主题化） */}
      {updateInfo && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="mx-4 w-full max-w-sm rounded-2xl border border-border bg-card p-5 shadow-2xl">
            <h3 className="text-base font-semibold text-foreground">新版本 v{updateInfo.version} 已就绪</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
              已在后台下载完成，<span className="text-foreground">不打断正在跑的任务</span>。
              {(window as any).__TAURI_INTERNALS__
                ? '选"稍后"则下次启动时再提醒。'
                : '下次退出应用时自动安装，下次打开即新版本。'}
              要现在更新点"立即更新"（会重启应用）。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => (window as any).openmateDesktop?.updateAction?.('install')}
                className="rounded-lg px-3.5 py-1.5 text-sm text-muted-foreground hover:bg-muted/60 transition-colors"
              >
                立即更新
              </button>
              <button
                onClick={() => setUpdateInfo(null)}
                className="rounded-lg bg-primary px-3.5 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity"
              >
                稍后（推荐）
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Container: absolute positioned, slides with right property */}
      <div
        className="absolute inset-y-0 top-0 z-10 h-full min-w-0 border-l border-border transition-[opacity,transform] duration-200 ease-linear flex flex-col overflow-hidden"
        style={{
          width: rightPanelWidth,
          right: 0,
          // 收起=透明停靠（绝不滑出视口）：滑出/裁剪会让 <webview> 丢 VizSurface，
          // capturePage 挂死或抛 UnknownVizError（五态实测：opacity:0 是唯一保表面的隐藏态）
          opacity: rightPanelOpen ? 1 : 0,
          // 开=必须 transform:none（transform≠none 会让面板内 position:fixed 全屏动画失效）
          transform: rightPanelOpen ? 'none' : 'translateX(24px)',
          pointerEvents: rightPanelOpen ? 'auto' : 'none',
        }}
      >
        <WorkspacePanel sessionId={activeSessionIdFromStore || '__default__'} className="h-full" pendingFilePreview={pendingFilePreview} pendingWebUrl={pendingWebUrl} />
      </div>
      </div>

      {/* Bottom navigation bar — full screen width */}
      <BottomNav totalUnread={totalUnread} onOpenConversations={() => { if (isMobile) { setMobileSidebarOpen(true); } else { toggle(); } setRightPanelOpen(false); }} />
      </EChartsThemeProvider>

      {/* 删除会话确认弹框 */}
      <AlertDialog open={!!deleteTargetId} onOpenChange={(open) => { if (!open) setDeleteTargetId(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('chat.deleteSessionConfirm', '确定删除此会话？')}</AlertDialogTitle>
            <AlertDialogDescription>删除后不可恢复</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={async () => {
              if (!deleteTargetId) return;
              try {
                const r = await fetch(`${getApiBaseUrl()}/api/sessions/${deleteTargetId}`, { method: 'DELETE', headers: { Authorization: `Bearer ${getToken()}` } });
                if (r.ok) {
                  setSidebarAgents((prev: AgentInfo[]) => prev.map(a => ({
                    ...a,
                    sessions: a.sessions.filter(s => s.id !== deleteTargetId),
                    sourceGroups: a.sourceGroups?.map(g => ({ ...g, sessions: g.sessions.filter(s => s.id !== deleteTargetId) })).filter(g => g.sessions.length > 0),
                  })));
                }
              } catch (e) { console.error('Delete session failed:', e); }
              setDeleteTargetId(null);
            }}>删除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

