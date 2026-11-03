
import { useState, useRef, useCallback, useEffect, type FormEvent, type ReactNode, type ChangeEvent } from 'react';
import {
  X,
  Globe,
  FileText,
  Terminal as TerminalIcon,
  Info,
  Maximize2,
  Minimize2,
} from 'lucide-react';
import { SkirtTabs, type SkirtTab } from './skirt-tabs';
import { BrowserAIControl } from './file-viewer/renderers/browser-ai-control';
import { FileViewer } from './file-viewer';
import { cn } from '../lib/utils';
import { useIsMobile } from '../hooks/use-mobile';

// ── Types ────────────────────────────────────────────────────────

type WorkspaceTabType = 'new-tab' | 'web-browser' | 'file-preview' | 'terminal' | 'details';

interface WorkspaceTab {
  id: string;
  type: WorkspaceTabType;
  title: string;
  url?: string;
  filePath?: string;
  fileMimeType?: string;
  history: string[];
  historyIndex: number;
}

interface WorkspaceState {
  tabs: WorkspaceTab[];
  activeTabId: string;
}

// ── Helpers ────────────────────────────────────────────────────────

let tabCounter = 0;
function createTab(type: WorkspaceTabType, extra?: { url?: string; filePath?: string }): WorkspaceTab {
  tabCounter += 1;
  const titles: Record<WorkspaceTabType, string> = {
    'new-tab': 'New Tab',
    'web-browser': 'Web Browser',
    'file-preview': 'File Preview',
    terminal: 'Terminal',
    details: 'Details',
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

// ── New Tab View ───────────────────────────────────────────────────

function NewTabView({ onOpenBrowser, onOpenFile, onOpenTerminal, onOpenDetails, title }: {
  onOpenBrowser: (url: string) => void;
  onOpenFile: (file: { name: string; url: string; buffer?: ArrayBuffer }) => void;
  onOpenTerminal: () => void;
  onOpenDetails: () => void;
  title?: string;
}) {
  const [urlInput, setUrlInput] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleBrowse = (e: FormEvent) => {
    e.preventDefault();
    if (urlInput.trim()) onOpenBrowser(urlInput.trim());
  };

  const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      onOpenFile({ name: file.name, url: reader.result as string, buffer: undefined });
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 lg:gap-6 p-3 lg:p-6 text-center">
      <div className="text-lg lg:text-2xl font-bold text-foreground/80">{title || 'Workspace'}</div>
      <p className="text-xs lg:text-sm text-muted-foreground max-w-xs">
        Open a webpage, preview a file, or launch a terminal.
      </p>

      <div className="w-full max-w-sm space-y-4">
        <form onSubmit={handleBrowse} className="flex gap-2">
          <input
            placeholder="Enter URL to browse..."
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            className="flex-1 text-sm h-9 px-3 rounded-md border border-border bg-background outline-none focus:border-primary/50 transition-colors"
          />
          <button type="submit" disabled={!urlInput.trim()} className="px-3 h-9 rounded-md bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50 transition-opacity">
            <Globe className="w-4 h-4" />
          </button>
        </form>

        <div className="flex gap-2">
          <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileSelect} />
          <button onClick={() => fileInputRef.current?.click()} className="flex-1 h-9 rounded-md border border-border text-sm font-medium hover:bg-muted/50 transition-colors flex items-center justify-center gap-2">
            <FileText className="w-4 h-4" /> Open File
          </button>
        </div>

        <button onClick={onOpenTerminal} className="w-full h-9 rounded-md border border-border text-sm font-medium hover:bg-muted/50 transition-colors flex items-center justify-center gap-2">
          <TerminalIcon className="w-4 h-4" /> Open Terminal
        </button>

        <button onClick={onOpenDetails} className="w-full h-9 rounded-md border border-border text-sm font-medium hover:bg-muted/50 transition-colors flex items-center justify-center gap-2">
          <Info className="w-4 h-4" /> Details
        </button>
      </div>
    </div>
  );
}


// ── Terminal Placeholder ───────────────────────────────────────────

function TerminalPlaceholder() {
  const [lines, setLines] = useState<string[]>(['Welcome to Terminal', 'Type "help" for available commands.', '']);
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [lines]);

  const handleCommand = (e: FormEvent) => {
    e.preventDefault();
    const cmd = input.trim();
    if (!cmd) return;
    const newLines = [...lines, `$ ${cmd}`];
    if (cmd === 'help') {
      newLines.push('Available commands:', '  help    - Show this help', '  clear   - Clear terminal', '  echo    - Echo text', '  date    - Show current date', '');
    } else if (cmd === 'clear') {
      setLines([]); setInput(''); return;
    } else if (cmd.startsWith('echo ')) {
      newLines.push(cmd.slice(5), '');
    } else if (cmd === 'date') {
      newLines.push(new Date().toLocaleString(), '');
    } else {
      newLines.push(`Command not found: ${cmd}`, '');
    }
    setLines(newLines); setInput('');
  };

  return (
    <div className="flex flex-col h-full bg-[#0a0a0f] text-green-400 font-mono text-xs">
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-0.5">
        {lines.map((line, i) => (
          <div key={i} className={line.startsWith('$ ') ? 'text-foreground' : ''}>{line || '\u00A0'}</div>
        ))}
      </div>
      <form onSubmit={handleCommand} className="flex items-center gap-2 px-3 py-2 border-t border-border/20">
        <span className="text-green-500 shrink-0">$</span>
        <input value={input} onChange={(e) => setInput(e.target.value)} className="flex-1 bg-transparent outline-none text-xs text-foreground placeholder:text-muted-foreground/50" placeholder="Type a command..." autoFocus />
      </form>
    </div>
  );
}

// ── WorkspacePanel Props ───────────────────────────────────────────

export interface WorkspacePanelProps {
  sessionId?: string;
  workspaceTitle?: string;
  pageWorkspace?: ReactNode;
  sessionDetails?: {
    agentIcon?: string;
    agentName?: string;
    agentDescription?: string;
    sessionName?: string;
    lastActive?: string;
    imageCount?: number;
    fileCount?: number;
  } | null;
  executeCommand?: (cmd: string, cwd?: string) => Promise<{ output: string; exit_code: number }>;
  onBrowserAIAction?: (context: any, instruction: string) => Promise<any[] | null>;
  onSelectionAIEdit?: (selectedText: string, instruction: string) => Promise<string | null>;
  className?: string;
  /** External file to preview — when this changes, automatically opens in a file-preview tab */
  pendingFilePreview?: { url: string; name: string; mimeType?: string } | null;
  /** 桌面壳桥注入：外链/新窗口 → 开 web-browser 新标签页 */
  pendingWebUrl?: string | null;
}

// ── WorkspacePanel ─────────────────────────────────────────────────

export function WorkspacePanel({
  sessionId = '__default__',
  workspaceTitle = 'Workspace',
  pageWorkspace,
  sessionDetails,
  onBrowserAIAction,
  onSelectionAIEdit,
  className,
  pendingFilePreview,
  pendingWebUrl = null,
}: WorkspacePanelProps) {
  const isMobile = useIsMobile();
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [fsStyle, setFsStyle] = useState<React.CSSProperties>({});
  const rootRef = useRef<HTMLDivElement>(null);

  const toggleFullscreen = useCallback(() => {
    if (!isFullscreen && rootRef.current) {
      const rect = rootRef.current.getBoundingClientRect();
      const base = { position: 'fixed' as const, top: rect.top, height: rect.height, zIndex: 50 };
      // Step 1: snap to current position (no transition)
      setFsStyle({ ...base, left: rect.left, right: window.innerWidth - rect.right });
      // Step 2: next frame → slide to fullscreen
      requestAnimationFrame(() => {
        setFsStyle({ ...base, left: 0, right: 0, transition: 'left 300ms ease-in-out, right 300ms ease-in-out' });
      });
    } else {
      // Slide back to original position
      if (rootRef.current) {
        const rect = rootRef.current.getBoundingClientRect();
        setFsStyle(prev => ({ ...prev, left: rect.left, right: window.innerWidth - rect.right, transition: 'left 300ms ease-in-out, right 300ms ease-in-out' }));
        setTimeout(() => setFsStyle({}), 310);
      }
    }
    setIsFullscreen(!isFullscreen);
  }, [isFullscreen]);

  // Self-contained state — no external store dependency
  const [workspaceMap, setWorkspaceMap] = useState<Record<string, WorkspaceState>>({});

  // Initialize default tab for this session
  useEffect(() => {
    if (!workspaceMap[sessionId]) {
      const defaultTab = createTab('new-tab');
      setWorkspaceMap(prev => ({ ...prev, [sessionId]: { tabs: [defaultTab], activeTabId: defaultTab.id } }));
    }
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Handle external file preview requests
  useEffect(() => {
    if (!pendingFilePreview) return;
    setWorkspaceMap(prev => {
      const current = prev[sessionId] ?? { tabs: [createTab('new-tab')], activeTabId: '' };
      // Update the active tab if it's a new-tab, otherwise create a new one
      const activeTab = current.tabs.find(t => t.id === current.activeTabId);
      if (activeTab && activeTab.type === 'new-tab') {
        const updatedTabs = current.tabs.map(t =>
          t.id === activeTab.id
            ? { ...t, type: 'file-preview' as WorkspaceTabType, filePath: pendingFilePreview.url, title: pendingFilePreview.name, fileMimeType: pendingFilePreview.mimeType }
            : t
        );
        return { ...prev, [sessionId]: { ...current, tabs: updatedTabs } };
      } else {
        const newTab: WorkspaceTab = {
          id: `tab-${Date.now()}-ext`,
          type: 'file-preview',
          title: pendingFilePreview.name,
          filePath: pendingFilePreview.url,
          fileMimeType: pendingFilePreview.mimeType,
          history: [],
          historyIndex: -1,
        };
        return { ...prev, [sessionId]: { tabs: [...current.tabs, newTab], activeTabId: newTab.id } };
      }
    });
  }, [pendingFilePreview, sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 桌面壳桥注入：外链/新窗口 → workspace 新 web-browser 标签页
  useEffect(() => {
    if (!pendingWebUrl) return;
    addTab('web-browser', { url: pendingWebUrl });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingWebUrl, sessionId]);

  const wsState = workspaceMap[sessionId] ?? { tabs: [createTab('new-tab')], activeTabId: '' };
  const tabs = wsState.tabs;
  const activeTabId = wsState.activeTabId;

  // Helper to update workspace state immutably
  const updateWs = useCallback((fn: (prev: WorkspaceState) => Partial<WorkspaceState>) => {
    setWorkspaceMap(prev => {
      const current = prev[sessionId] ?? { tabs: [createTab('new-tab')], activeTabId: '' };
      return { ...prev, [sessionId]: { ...current, ...fn(current) } };
    });
  }, [sessionId]);

  // ── Tab management ──────────────────────────────────────────────

  const addTab = useCallback((type: WorkspaceTabType, extra?: { url?: string; filePath?: string }) => {
    const tab = createTab(type, extra);
    updateWs(prev => ({ tabs: [...prev.tabs, tab], activeTabId: tab.id }));
  }, [updateWs]);

  const closeTab = useCallback((tabId: string) => {
    updateWs(prev => {
      if (prev.tabs.length === 1) {
        const fresh = createTab('new-tab');
        return { tabs: [fresh], activeTabId: fresh.id };
      }
      const remaining = prev.tabs.filter(t => t.id !== tabId);
      const newActive = prev.activeTabId === tabId ? remaining[remaining.length - 1]?.id || '' : prev.activeTabId;
      return { tabs: remaining, activeTabId: newActive };
    });
  }, [updateWs]);

  const updateTab = useCallback((tabId: string, updates: Partial<WorkspaceTab>) => {
    updateWs(prev => ({
      tabs: prev.tabs.map(t => t.id === tabId ? { ...t, ...updates } : t),
    }));
  }, [updateWs]);

  const setActiveTabId = useCallback((id: string) => {
    updateWs(() => ({ activeTabId: id }));
  }, [updateWs]);





  // ── Active tab ──────────────────────────────────────────────────

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  // Wrap onSelectionAIEdit as onSendToAgent for FileViewer
  const handleSendToAgent = useCallback(async (
    content: string, instruction: string, _fileName: string,
  ): Promise<string> => {
    if (!onSelectionAIEdit) return content;
    const result = await onSelectionAIEdit(content, instruction);
    return result || content;
  }, [onSelectionAIEdit]);

  // Handle file save (write back via API)
  const handleFileSave = useCallback(async (
    newContent: string, _fileName: string,
  ) => {
    if (!activeTab?.filePath) return;
    try {
      await fetch('/api/file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: activeTab.filePath, content: newContent }),
      });
    } catch (e) {
      console.error('Save failed:', e);
    }
  }, [activeTab?.filePath]);

  // ── Render content ──────────────────────────────────────────────

  const renderContent = () => {
    if (!activeTab) return null;
    switch (activeTab.type) {
      case 'new-tab':
        return (
          <NewTabView
            title={workspaceTitle}
            onOpenBrowser={(url) => {
              updateTab(activeTab.id, {
                type: 'web-browser',
                url: ensureProtocol(url),
                title: (() => { try { return new URL(ensureProtocol(url)).hostname; } catch { return 'Web Browser'; } })(),
                history: [ensureProtocol(url)],
                historyIndex: 0,
              });
            }}
            onOpenFile={(file) => {
              updateTab(activeTab.id, {
                type: 'file-preview',
                filePath: file.url,
                title: file.name || 'File Preview',
              });
            }}
            onOpenTerminal={() => {
              updateTab(activeTab.id, { type: 'terminal', title: 'Terminal' });
            }}
            onOpenDetails={() => {
              updateTab(activeTab.id, { type: 'details', title: 'Details' });
            }}
          />
        );

      case 'web-browser':
        return (
          <BrowserAIControl
            initialUrl={activeTab.url}
            onAIAction={onBrowserAIAction || (async () => null)}
          />
        );

      case 'file-preview':
        return <FileViewer fileName={activeTab.title} fileUrl={activeTab.filePath} mimeType={activeTab.fileMimeType} onSave={handleFileSave} onSendToAgent={handleSendToAgent} />;

      case 'terminal':
        return <TerminalPlaceholder />;

      case 'details':
        return sessionDetails ? (
          <div className="flex-1 overflow-y-auto p-3 space-y-4">
            <div className="space-y-2">
              <div className="text-xs text-muted-foreground">Agent</div>
              <div className="flex items-center gap-2 p-2 rounded-lg bg-muted/50">
                {sessionDetails.agentIcon && <span className="text-lg shrink-0">{sessionDetails.agentIcon}</span>}
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium truncate">{sessionDetails.agentName}</div>
                  {sessionDetails.agentDescription && <div className="text-xs text-muted-foreground truncate">{sessionDetails.agentDescription}</div>}
                </div>
              </div>
            </div>
            {sessionDetails.sessionName && (
              <div className="space-y-2">
                <div className="text-xs text-muted-foreground">Session</div>
                <div className="text-sm font-medium">{sessionDetails.sessionName}</div>
                {sessionDetails.lastActive && <div className="text-xs text-muted-foreground">Last active: {sessionDetails.lastActive}</div>}
              </div>
            )}
            <div>
              <div className="text-xs text-muted-foreground mb-2">Statistics</div>
              <div className="grid grid-cols-2 gap-2">
                <div className="p-2 rounded bg-muted/50 text-center">
                  <div className="text-lg font-bold">{sessionDetails.imageCount ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Images</div>
                </div>
                <div className="p-2 rounded bg-muted/50 text-center">
                  <div className="text-lg font-bold">{sessionDetails.fileCount ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Files</div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full gap-3 p-6 text-center">
            <Info className="w-8 h-8 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">No active session</p>
          </div>
        );

      default:
        return null;
    }
  };

  // ── Tab icon ────────────────────────────────────────────────────

  const tabIcon = (type: WorkspaceTabType) => {
    const cls = 'w-3 h-3 shrink-0';
    switch (type) {
      case 'web-browser': return <Globe className={cls} />;
      case 'file-preview': return <FileText className={cls} />;
      case 'terminal': return <TerminalIcon className={cls} />;
      case 'details': return <Info className={cls} />;
      default: return <Globe className={cls} />;
    }
  };

  // ── Render ──────────────────────────────────────────────────────

  return (
    <div
      className={cn('flex flex-col h-full min-w-0 bg-background', className)}
      ref={rootRef}
      style={isFullscreen ? fsStyle : {}}
    >
      {/* Tab bar + fullscreen button */}
      <div className="flex items-center shrink-0">
        <SkirtTabs
          tabs={tabs.map((tab) => ({ id: tab.id, title: tab.title }))}
          activeTabId={activeTabId}
          onTabChange={setActiveTabId}
          onAddTab={() => addTab('new-tab')}
          minWidth={isMobile ? 100 : 140}
          maxWidth={isMobile ? 160 : 240}
          className="flex-1 min-w-0"
          extraRight={30}
          renderTabContent={(tab: SkirtTab, isActive: boolean) => (
            <>
              {tabIcon(tabs.find((t) => t.id === tab.id)?.type || 'new-tab')}
              <span className={`truncate flex-1 text-[13px] transition-colors ${isActive ? 'text-foreground' : 'text-muted-foreground'}`}>{tab.title}</span>
              <span role="button" onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }} className="shrink-0 flex items-center justify-center rounded-full opacity-0 group-hover:opacity-70 group-active:opacity-70 hover:!opacity-100 hover:bg-[rgba(255,255,255,0.1)] transition-all cursor-pointer touch-manipulation" style={{ width: 20, height: 20 }}>
                <X className="w-3 h-3 text-muted-foreground" />
              </span>
            </>
          )}
        />
        {(
          <button
            onClick={toggleFullscreen}
            className="shrink-0 p-1.5 rounded hover:bg-muted/30 text-muted-foreground transition-colors mr-1"
            title={isFullscreen ? '退出全屏' : '全屏'}
          >
            {isFullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-hidden min-h-0">
        {pageWorkspace || renderContent()}
      </div>
    </div>
  );
}
