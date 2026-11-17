'use client';

import { useState, useRef, useCallback, useEffect, type FormEvent } from 'react';
import {
  Plus,
  X,
  Globe,
  FileText,
  Terminal as TerminalIcon,
  ChevronLeft,
  ChevronRight,
  RotateCw,
  PanelRightOpen,
  PanelRightClose,
  Info,
  ImageIcon,
  Paperclip,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SkirtTabs, type SkirtTab } from '@/components/skirt-tabs';
import { useAppStore, type WorkspaceTab, type WorkspaceTabType } from '@/stores/app-store';
import { useAIGroupsStore } from '@/stores/ai-groups-store';
import { usePathname } from 'next/navigation';
import { Input } from '@/components/ui/input';
import { useIsMobile } from '@/hooks/use-mobile';
import { cn } from '@/lib/utils';

// ── Types ──────────────────────────────────────────────────────────

type TabType = WorkspaceTabType;
type Tab = WorkspaceTab;

interface RightPanelProps {
  open: boolean;
  onToggle: () => void;
}

// ── Helpers ────────────────────────────────────────────────────────

let tabCounter = 0;
function createTab(type: TabType, extra?: { url?: string; filePath?: string }): Tab {
  tabCounter += 1;
  const titles: Record<TabType, string> = {
    'new-tab': '新标签页',
    'web-browser': '网页浏览器',
    'file-preview': '文件预览',
    terminal: 'Terminal',
    details: '详情',
  };
  return {
    id: `tab-${Date.now()}-${tabCounter}`,
    type,
    title: extra?.url ? new URL(ensureProtocol(extra.url)).hostname : titles[type],
    url: extra?.url,
    filePath: extra?.filePath,
    history: extra?.url ? [ensureProtocol(extra.url)] : [],
    historyIndex: extra?.url ? 0 : -1,
  };
}

function ensureProtocol(url: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  return `https://${url}`;
}

function isImageUrl(url: string): boolean {
  return /\.(png|jpe?g|gif|svg|webp|bmp|ico)(\?|$)/i.test(url);
}

function isPdfUrl(url: string): boolean {
  return /\.pdf(\?|$)/i.test(url);
}

// ── Sub-components ─────────────────────────────────────────────────

function NewTabView({ onOpenBrowser, onOpenFile, onOpenTerminal, onOpenDetails }: {
  onOpenBrowser: (url: string) => void;
  onOpenFile: (path: string) => void;
  onOpenTerminal: () => void;
  onOpenDetails: () => void;
}) {
  const [urlInput, setUrlInput] = useState('');
  const [fileInput, setFileInput] = useState('');

  const handleBrowse = (e: FormEvent) => {
    e.preventDefault();
    if (urlInput.trim()) onOpenBrowser(urlInput.trim());
  };

  const handleOpenFile = (e: FormEvent) => {
    e.preventDefault();
    if (fileInput.trim()) onOpenFile(fileInput.trim());
  };

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 lg:gap-6 p-3 lg:p-6 text-center">
      <div className="text-lg lg:text-2xl font-bold text-foreground/80">OpenMate 工作区</div>
      <p className="text-xs lg:text-sm text-muted-foreground max-w-xs">
        打开网页、预览文件或启动终端。
      </p>

      <div className="w-full max-w-sm space-y-4">
        {/* Browse URL */}
        <form onSubmit={handleBrowse} className="flex gap-2">
          <Input
            placeholder="输入网址浏览..."
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            className="flex-1 text-sm"
          />
          <Button type="submit" size="sm" disabled={!urlInput.trim()}>
            <Globe className="w-4 h-4 mr-1" />
            前往
          </Button>
        </form>

        {/* Open File */}
        <form onSubmit={handleOpenFile} className="flex gap-2">
          <Input
            placeholder="输入文件路径预览..."
            value={fileInput}
            onChange={(e) => setFileInput(e.target.value)}
            className="flex-1 text-sm"
          />
          <Button type="submit" size="sm" variant="outline" disabled={!fileInput.trim()}>
            <FileText className="w-4 h-4 mr-1" />
            打开
          </Button>
        </form>

        {/* Open Terminal */}
        <Button
          onClick={onOpenTerminal}
          variant="outline"
          className="w-full"
          size="sm"
        >
          <TerminalIcon className="w-4 h-4 mr-2" />
          打开终端
        </Button>

        {/* Session Details */}
        <Button
          onClick={onOpenDetails}
          variant="outline"
          className="w-full"
          size="sm"
        >
          <Info className="w-4 h-4 mr-2" />
          会话详情
        </Button>
      </div>
    </div>
  );
}

function WebBrowserView({ tab, onNavigate, onBack, onForward, onRefresh }: {
  tab: Tab;
  onNavigate: (url: string) => void;
  onBack: () => void;
  onForward: () => void;
  onRefresh: () => void;
}) {
  const [inputUrl, setInputUrl] = useState(tab.url || '');
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    setInputUrl(tab.url || '');
  }, [tab.url]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = inputUrl.trim();
    if (trimmed) onNavigate(ensureProtocol(trimmed));
  };

  const canBack = tab.historyIndex > 0;
  const canForward = tab.historyIndex < tab.history.length - 1;

  return (
    <div className="flex flex-col h-full min-w-0">
      {/* URL bar */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-border bg-muted/30 shrink-0">
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={onBack}
          disabled={!canBack}
          title="后退"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={onForward}
          disabled={!canForward}
          title="前进"
        >
          <ChevronRight className="w-3.5 h-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={onRefresh}
          title="刷新"
        >
          <RotateCw className="w-3.5 h-3.5" />
        </Button>
        <form onSubmit={handleSubmit} className="flex-1 flex">
          <Input
            value={inputUrl}
            onChange={(e) => setInputUrl(e.target.value)}
            placeholder="https://example.com"
            className="h-7 text-xs"
          />
        </form>
      </div>

      {/* iframe content */}
      {tab.url ? (
        <iframe
          ref={iframeRef}
          src={tab.url}
          className="flex-1 min-w-0 border-0 bg-white"
          style={{ width: '100%', height: '100%' }}
          title="网页浏览器"
        />
      ) : (
        <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
          Enter a URL above to browse
        </div>
      )}
    </div>
  );
}

function FilePreviewView({ filePath }: { filePath: string }) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!filePath) return;
    setLoading(true);
    setError(null);
    setContent(null);

    // If it's a URL, display based on type
    if (/^https?:\/\//i.test(filePath)) {
      setLoading(false);
      return;
    }

    // For local files, we'd need an API endpoint — show placeholder
    setLoading(false);
    setError('Local file preview requires a server endpoint. Use a URL instead.');
  }, [filePath]);

  // URL-based file preview
  if (/^https?:\/\//i.test(filePath)) {
    if (isImageUrl(filePath)) {
      return (
        <div className="flex items-center justify-center h-full p-4 bg-muted/20">
          <img
            src={filePath}
            alt="预览"
            className="max-w-full max-h-full object-contain rounded-md"
          />
        </div>
      );
    }

    if (isPdfUrl(filePath)) {
      return (
        <iframe
          src={filePath}
          className="w-full h-full border-0"
          title="PDF 预览"
        />
      );
    }

    // Generic URL — show in iframe
    return (
      <iframe
        src={filePath}
        className="w-full h-full border-0 bg-white"
        sandbox="allow-same-origin allow-scripts"
        title="文件预览"
      />
    );
  }

  // Local file
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 p-4 text-center">
        <FileText className="w-8 h-8 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <p className="text-xs text-muted-foreground/70 font-mono break-all">{filePath}</p>
      </div>
    );
  }

  if (content !== null) {
    return (
      <div className="h-full overflow-auto p-4">
        <pre className="text-xs font-mono whitespace-pre-wrap break-words bg-muted/30 rounded-md p-3">
          {content}
        </pre>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center h-full gap-2 text-muted-foreground">
      <FileText className="w-8 h-8" />
      <p className="text-sm">未加载文件</p>
      <p className="text-xs font-mono break-all">{filePath}</p>
    </div>
  );
}

function TerminalPlaceholder() {
  const [lines, setLines] = useState<string[]>([
    'Welcome to OpenMate Terminal',
    'Type "help" for available commands.',
    '',
  ]);
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  const handleCommand = (e: FormEvent) => {
    e.preventDefault();
    const cmd = input.trim();
    if (!cmd) return;

    const newLines = [...lines, `$ ${cmd}`];

    if (cmd === 'help') {
      newLines.push(
        '可用命令：',
        '  help    - Show this help',
        '  clear   - Clear terminal',
        '  echo    - Echo text',
        '  date    - Show current date',
        ''
      );
    } else if (cmd === 'clear') {
      setLines([]);
      setInput('');
      return;
    } else if (cmd.startsWith('echo ')) {
      newLines.push(cmd.slice(5), '');
    } else if (cmd === 'date') {
      newLines.push(new Date().toLocaleString(), '');
    } else {
      newLines.push(`Command not found: ${cmd}`, '');
    }

    setLines(newLines);
    setInput('');
  };

  return (
    <div className="flex flex-col h-full bg-[#0a0a0f] text-green-400 font-mono text-xs">
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-0.5">
        {lines.map((line, i) => (
          <div key={i} className={line.startsWith('$ ') ? 'text-foreground' : ''}>
            {line || '\u00A0'}
          </div>
        ))}
      </div>
      <form onSubmit={handleCommand} className="flex items-center gap-2 px-3 py-2 border-t border-border/20">
        <span className="text-green-500 shrink-0">$</span>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="flex-1 bg-transparent outline-none text-xs text-foreground placeholder:text-muted-foreground/50"
          placeholder="输入命令..."
          autoFocus
        />
      </form>
    </div>
  );
}

// ── Session Details View ─────────────────────────────────────────

function SessionDetailsView() {
  const details = useAppStore((s) => s.sessionDetails);

  if (!details) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 p-6 text-center">
        <Info className="w-8 h-8 text-muted-foreground/30" />
        <p className="text-sm text-muted-foreground">无活动会话</p>
        <p className="text-xs text-muted-foreground/60">选择会话查看详情</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-4">
      {/* Agent Info */}
      <div className="space-y-2">
        <div className="text-xs text-muted-foreground">Agent</div>
        <div className="flex items-center gap-2 p-2 rounded-lg bg-muted/50">
          <span className="text-lg shrink-0">{details.agentIcon}</span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium truncate">{details.agentName}</div>
            {details.agentDescription && (
              <div className="text-xs text-muted-foreground truncate" title={details.agentDescription}>
                {details.agentDescription}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Session Info */}
      {details.sessionName && (
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">会话</div>
          <div className="text-sm font-medium">{details.sessionName}</div>
          {details.lastActive && (
            <div className="text-xs text-muted-foreground">最近活跃：{details.lastActive}</div>
          )}
        </div>
      )}

      {/* Statistics */}
      <div>
        <div className="text-xs text-muted-foreground mb-2">统计</div>
        <div className="grid grid-cols-2 gap-2">
          <div className="p-2 rounded bg-muted/50 text-center">
            <div className="text-lg font-bold">{details.imageCount}</div>
            <div className="text-[10px] text-muted-foreground">图片</div>
          </div>
          <div className="p-2 rounded bg-muted/50 text-center">
            <div className="text-lg font-bold">{details.fileCount}</div>
            <div className="text-[10px] text-muted-foreground">文件</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Tab icon helper ────────────────────────────────────────────────

function TabIcon({ type }: { type: TabType }) {
  const cls = 'w-3 h-3 shrink-0';
  switch (type) {
    case 'web-browser': return <Globe className={cls} />;
    case 'file-preview': return <FileText className={cls} />;
    case 'terminal': return <TerminalIcon className={cls} />;
    case 'details': return <Info className={cls} />;
    default: return <Globe className={cls} />;
  }
}

// ── Main component ─────────────────────────────────────────────────

export function RightPanel({ open, onToggle }: RightPanelProps) {
  const pathname = usePathname();
  const activeSessionId = useAppStore((s) => s.activeSessionId);
  const selectedGroup = useAIGroupsStore((s) => s.selectedGroup);
  // Workspace key: group ID for ai-groups route, session ID otherwise
  const sessionId = pathname.startsWith('/ai-groups')
    ? (selectedGroup?.id || '__no_group__')
    : (activeSessionId || '__default__');

  const wsBySession = useAppStore((s) => s.workspaceTabsBySession);
  const storeSetWorkspaceTabs = useAppStore((s) => s.setWorkspaceTabs);

  // Persist default workspace for new sessionId
  useEffect(() => {
    if (!wsBySession[sessionId]) {
      const defaultTab = { id: `tab-${sessionId}-0`, type: 'new-tab' as const, title: '新标签页', history: [] as string[], historyIndex: -1 };
      storeSetWorkspaceTabs(sessionId, { tabs: [defaultTab], activeTabId: defaultTab.id });
    }
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const wsState = wsBySession[sessionId] ?? { tabs: [{ id: `tab-${sessionId}-0`, type: 'new-tab' as const, title: '新标签页', history: [], historyIndex: -1 }], activeTabId: `tab-${sessionId}-0` };
  const storeAddWorkspaceTab = useAppStore((s) => s.addWorkspaceTab);
  const storeRemoveWorkspaceTab = useAppStore((s) => s.removeWorkspaceTab);
  const storeUpdateWorkspaceTab = useAppStore((s) => s.updateWorkspaceTab);
  const storeSetActiveTab = useAppStore((s) => s.setActiveWorkspaceTab);
  const storeNavigateTab = useAppStore((s) => s.navigateWorkspaceTab);
  const storeGoBack = useAppStore((s) => s.goBackWorkspaceTab);
  const storeGoForward = useAppStore((s) => s.goForwardWorkspaceTab);

  const tabs = wsState.tabs;
  const activeTabId = wsState.activeTabId;

  const [panelWidth, setPanelWidth] = useState(typeof window !== 'undefined' ? Math.min(384, Math.round(window.innerWidth / 2)) : 384);
  const resizeRef = useRef<HTMLDivElement>(null);
  const isResizingRef = useRef(false);
  const isMobile = useIsMobile();

  // ── Tab management (delegates to store) ───────────────────────────

  const addTab = useCallback((type: TabType, extra?: { url?: string; filePath?: string }) => {
    const tab = createTab(type, extra);
    storeAddWorkspaceTab(sessionId, tab, true);
  }, [sessionId, storeAddWorkspaceTab]);


  const closeTab = useCallback((tabId: string) => {
    if (tabs.length === 1) {
      const fresh = createTab('new-tab');
      storeSetWorkspaceTabs(sessionId, { tabs: [fresh], activeTabId: fresh.id });
      return;
    }
    storeRemoveWorkspaceTab(sessionId, tabId);
  }, [sessionId, tabs.length, storeRemoveWorkspaceTab, storeSetWorkspaceTabs]);

  const updateTab = useCallback((tabId: string, updates: Partial<Tab>) => {
    storeUpdateWorkspaceTab(sessionId, tabId, updates);
  }, [sessionId, storeUpdateWorkspaceTab]);

  // ── Browser navigation (delegates to store) ──────────────────────

  const navigateTab = useCallback((tabId: string, url: string) => {
    storeNavigateTab(sessionId, tabId, url);
  }, [sessionId, storeNavigateTab]);

  const goBack = useCallback((tabId: string) => {
    storeGoBack(sessionId, tabId);
  }, [sessionId, storeGoBack]);

  const goForward = useCallback((tabId: string) => {
    storeGoForward(sessionId, tabId);
  }, [sessionId, storeGoForward]);

  const refreshTab = useCallback((tabId: string) => {
    storeUpdateWorkspaceTab(sessionId, tabId, { url: '' });
    setTimeout(() => {
      const ws = useAppStore.getState().getWorkspaceTabs(sessionId);
      const tab = ws.tabs.find((t) => t.id === tabId);
      if (tab) storeUpdateWorkspaceTab(sessionId, tabId, { url: tab.history[tab.historyIndex] || '' });
    }, 50);
  }, [sessionId, storeUpdateWorkspaceTab]);

  // ── Resize handling ──────────────────────────────────────────────

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizingRef.current = true;
    const startX = e.clientX;
    const startWidth = panelWidth;

    const onMouseMove = (ev: MouseEvent) => {
      if (!isResizingRef.current) return;
      const delta = startX - ev.clientX;
      const newWidth = Math.min(Math.max(startWidth + delta, 280), 500);
      setPanelWidth(newWidth);
    };

    const onMouseUp = () => {
      isResizingRef.current = false;
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, [panelWidth]);

  // ── Active tab ───────────────────────────────────────────────────

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  // ── Render content ───────────────────────────────────────────────

  const renderContent = () => {
    switch (activeTab.type) {
      case 'new-tab':
        return (
          <NewTabView
            onOpenBrowser={(url) => {
              // Replace current tab with browser tab
              updateTab(activeTab.id, {
                type: 'web-browser',
                url: ensureProtocol(url),
                title: (() => { try { return new URL(ensureProtocol(url)).hostname; } catch { return '网页浏览器'; } })(),
                history: [ensureProtocol(url)],
                historyIndex: 0,
              });
            }}
            onOpenFile={(path) => {
              updateTab(activeTab.id, {
                type: 'file-preview',
                filePath: path,
                title: path.split('/').pop() || '文件预览',
              });
            }}
            onOpenTerminal={() => {
              updateTab(activeTab.id, {
                type: 'terminal',
                title: 'Terminal',
              });
            }}
            onOpenDetails={() => {
              updateTab(activeTab.id, {
                type: 'details',
                title: '详情',
              });
            }}
          />
        );

      case 'web-browser':
        return (
          <WebBrowserView
            tab={activeTab}
            onNavigate={(url) => navigateTab(activeTab.id, url)}
            onBack={() => goBack(activeTab.id)}
            onForward={() => goForward(activeTab.id)}
            onRefresh={() => refreshTab(activeTab.id)}
          />
        );

      case 'file-preview':
        return <FilePreviewView filePath={activeTab.filePath || ''} />;

      case 'terminal':
        return <TerminalPlaceholder />;

      case 'details':
        return <SessionDetailsView />;

      default:
        return null;
    }
  };

  // ── Toggle button (rendered outside panel) ───────────────────────

  if (!open) {
    return null;
  }

  return (
    <div
      key={sessionId}
      className="flex flex-col h-full min-w-0 bg-background relative"
      style={{ width: '100%' }}
    >
      {/* Resize handle — desktop only */}
      {!isMobile && (
        <div
          ref={resizeRef}
          onMouseDown={handleResizeStart}
          className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-primary/30 transition-colors z-10"
        />
      )}

      {/* Tab bar — doubao-style SVG skirt tabs */}
      <SkirtTabs
        tabs={tabs.map((tab) => ({ id: tab.id, title: tab.title }))}
        activeTabId={activeTabId}
        onTabChange={(id) => storeSetActiveTab(sessionId, id)}
        onAddTab={() => addTab('new-tab')}
        minWidth={isMobile ? 100 : 140}
        maxWidth={isMobile ? 160 : 240}
        renderTabContent={(tab: SkirtTab, isActive: boolean) => (
          <>
            <Globe className="w-4 h-4 shrink-0 text-muted-foreground" />
            <span className={`truncate flex-1 text-[13px] transition-colors ${isActive ? 'text-foreground' : 'text-muted-foreground'}`}>{tab.title}</span>
            <span role="button" onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }} className="shrink-0 flex items-center justify-center rounded-full opacity-0 group-hover:opacity-70 group-active:opacity-70 hover:!opacity-100 hover:bg-[rgba(255,255,255,0.1)] transition-all cursor-pointer touch-manipulation" style={{ width: 20, height: 20 }}>
              <X className="w-3 h-3 text-muted-foreground" />
            </span>
          </>
        )}
      />

      {/* Content area */}
      <div className="flex-1 overflow-hidden min-h-0">
        {renderContent()}
      </div>
    </div>
  );
}
