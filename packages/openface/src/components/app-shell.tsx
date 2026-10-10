
import { useEffect, useRef, useState, type ReactNode, type ReactElement } from 'react';
import { TopBar } from './top-bar';
import { MainPanel, type MainPanelProps } from './main-panel';
import type { RightPanelProps } from './right-panel';
import { BottomBar } from './bottom-bar';
import { useAppStore } from '../store/app-store';
import { useIsMobile } from '../hooks/use-mobile';
import { Settings, LogOut, User } from 'lucide-react';
import { cn } from '../lib/utils';

// ── Types ────────────────────────────────────────────────────────

export interface NavItem {
  path: string;
  label: string;
  icon: ReactNode;
}

export interface AppShellProps {
  /** 应用名称，显示在TopBar左侧 */
  appName?: string;
  /** 应用Logo，显示在TopBar左侧名称前 */
  logo?: ReactNode;

  /** 导航项，用于BottomBar中间滚动导航 */
  nav?: NavItem[];
  /** 当前激活路径 */
  activePath?: string;
  /** 导航点击回调 */
  onNavigate?: (path: string) => void;

  /** TopBar左侧自定义内容（覆盖appName/logo） */
  topLeft?: ReactNode;
  /** TopBar中间自定义内容 */
  topMiddle?: ReactNode;
  /** TopBar右侧自定义内容（覆盖默认设置按钮） */
  topRight?: ReactNode;
  /** 点击设置按钮回调 */
  onSettings?: () => void;

  /** LeftPanel内容（不传则不显示sidebar） */
  leftPanel?: ReactElement | null;

  /** RightPanel内容 */
  rightPanel?: ReactElement<RightPanelProps> | null;

  /** BottomBar左侧自定义内容（覆盖默认用户头像） */
  bottomLeft?: ReactNode;
  /** BottomBar中间自定义内容（覆盖默认nav） */
  bottomMiddle?: ReactNode;
  /** BottomBar右侧自定义内容 */
  bottomRight?: ReactNode;

  /** 用户名（BottomBar头像显示首字母） */
  userName?: string;
  /** 退出登录回调 */
  onLogout?: () => void;
  /** 个人资料回调 */
  onProfile?: () => void;

  /** MainPanel额外props */
  mainPanelProps?: Omit<MainPanelProps, 'children' | 'sidebarOpen' | 'onToggleSidebar' | 'onToggleRightPanel'>;

  /** 主内容 */
  children: ReactNode;

  /** 左侧面板宽度 */
  leftPanelWidth?: number;
  /** 右侧面板宽度 */
  rightPanelWidth?: number;

  /** 额外className */
  className?: string;
}

// ── User Avatar Button ───────────────────────────────────────────

function UserAvatarButton({ userName, onProfile, onLogout }: { userName?: string; onProfile?: () => void; onLogout?: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const initial = (userName ?? 'U')[0]?.toUpperCase() ?? 'U';

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div className="relative shrink-0 flex items-center justify-center w-12 h-full" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-[11px] font-semibold text-primary-foreground transition-transform hover:scale-105"
      >
        {initial}
      </button>
      {open && (
        <div className="absolute bottom-full left-0 mb-2 w-56 rounded-xl border border-border bg-card shadow-lg overflow-hidden z-50">
          <div className="p-3 border-b border-border">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-sm font-medium text-primary-foreground">{initial}</div>
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{userName || 'User'}</div>
              </div>
            </div>
          </div>
          <div className="py-1">
            {onProfile && (
              <button onClick={() => { onProfile(); setOpen(false); }}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-muted/50 transition-colors">
                <User size={14} className="text-muted-foreground" />个人资料
              </button>
            )}
          </div>
          {onLogout && (
            <div className="border-t border-border py-1">
              <button onClick={() => { onLogout(); setOpen(false); }}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-muted/50 transition-colors text-red-500">
                <LogOut size={14} />退出登录
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── BottomBar Nav (scrollable icons, like OpenMate) ─────────────

function BottomNavItems({ items, activePath, onNavigate }: { items: NavItem[]; activePath?: string; onNavigate?: (path: string) => void }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        el.scrollLeft += e.deltaY;
      }
    };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, []);

  return (
    <div ref={scrollRef} className="flex-1 flex h-full items-center gap-0.5 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
      {items.map((item) => {
        const active = item.path === '/' ? activePath === '/' : activePath?.startsWith(item.path);
        return (
          <button
            key={item.path}
            onClick={() => onNavigate?.(item.path)}
            className={cn(
              'relative flex shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg px-2.5 py-1.5 text-[9px] font-medium transition-colors min-w-[48px]',
              active ? 'text-primary bg-primary/10' : 'text-muted-foreground hover:text-foreground hover:bg-accent'
            )}
          >
            <span className="w-4 h-4">{item.icon}</span>
            <span className="truncate max-w-[42px] leading-tight">{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── AppShell ─────────────────────────────────────────────────────

/**
 * AppShell — openface标准布局容器
 *
 * 布局规则（不重复原则）：
 * - TopBar：左侧logo+名称，右侧设置按钮。不放导航。
 * - LeftPanel：消费者自定义内容（如详细搜索导航）。不放默认导航。
 * - BottomBar：左侧用户头像（弹出菜单），中间滚动导航图标，右侧自定义。
 * - nav只在BottomBar中间出现一次。
 */
export function AppShell({
  appName,
  logo,
  nav,
  activePath,
  onNavigate,
  topLeft,
  topMiddle,
  topRight,
  onSettings,
  leftPanel,
  rightPanel,
  bottomLeft,
  bottomMiddle,
  bottomRight,
  userName,
  onLogout,
  onProfile,
  mainPanelProps,
  children,
  leftPanelWidth = 256,
  rightPanelWidth = 280,
  className,
}: AppShellProps) {
  const rightPanelOpen = useAppStore((s) => s.rightPanelOpen);
  const toggleRightPanel = useAppStore((s) => s.toggleRightPanel);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const setPageWorkspace = useAppStore((s) => s.setPageWorkspace);
  const isMobile = useIsMobile();

  // RightPanel动态宽度: 桌面50vw, 移动端256px (跟OpenMate一致)
  const [dynamicRightWidth, setDynamicRightWidth] = useState(rightPanelWidth);
  useEffect(() => {
    const update = () => setDynamicRightWidth(isMobile ? 256 : Math.round(window.innerWidth / 2));
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, [isMobile]);

  useEffect(() => {
    if (rightPanel) {
      setPageWorkspace(rightPanel);
    }
    return () => setPageWorkspace(null);
  }, [rightPanel, setPageWorkspace]);

  // ── TopBar: 左侧logo+名称，右侧设置 ─────────────────────

  const resolvedTopLeft = topLeft || (
    <div className="flex items-center gap-2 px-3">
      {logo && <span className="w-5 h-5 shrink-0">{logo}</span>}
      {appName && <span className="text-sm font-bold">{appName}</span>}
    </div>
  );

  const resolvedTopMiddle = topMiddle || null;

  const resolvedTopRight = topRight || (
    onSettings ? (
      <button onClick={onSettings} className="relative w-8 h-8 flex items-center justify-center group" aria-label="设置">
        {/* 超椭圆 n=3 背景 */}
        <svg width="32" height="32" viewBox="-100 -100 200 200" className="absolute inset-0">
          <path d={(() => {
            const pts: string[] = [];
            for (let i = 0; i <= 72; i++) {
              const theta = (i / 72) * Math.PI * 2;
              const ct = Math.cos(theta);
              const st = Math.sin(theta);
              const x = 96 * Math.sign(ct) * Math.pow(Math.abs(ct), 2 / 3);
              const y = 96 * Math.sign(st) * Math.pow(Math.abs(st), 2 / 3);
              pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
            }
            return `M${pts.join('L')}Z`;
          })()}
          fill="currentColor"
          className="text-[var(--color-bg-panel)] group-hover:text-[var(--color-bg-elevated)] transition-colors"
        />
        </svg>
        <Settings className="relative w-3.5 h-3.5 text-muted-foreground group-hover:text-foreground transition-colors" />
      </button>
    ) : null
  );

  // ── BottomBar: 左侧用户头像，中间nav，右侧自定义 ────────

  const resolvedBottomLeft = bottomLeft || (
    <UserAvatarButton userName={userName} onProfile={onProfile} onLogout={onLogout} />
  );

  const resolvedBottomMiddle = bottomMiddle || (nav ? (
    <BottomNavItems items={nav} activePath={activePath} onNavigate={onNavigate} />
  ) : null);

  const resolvedBottomRight = bottomRight || null;

  return (
    <div className={'flex flex-col h-svh overflow-hidden' + (className ? ` ${className}` : '')}>
      {/* TopBar */}
      <TopBar left={resolvedTopLeft} middle={resolvedTopMiddle} right={resolvedTopRight} />

      {/* Middle: LeftPanel + MainPanel + RightPanel */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">
        {/* LeftPanel — 折叠/展开动画 */}
        {leftPanel && (
          <>
            {isMobile && !sidebarCollapsed && (
              <div className="fixed inset-0 z-20 bg-black/40 animate-in fade-in-0" onClick={toggleSidebar} aria-hidden="true" />
            )}
            <div
              className="shrink-0 overflow-hidden transition-[width] duration-200 ease-linear relative z-30"
              style={{ ...(isMobile ? { position: 'fixed', top: 0, bottom: 0, left: 0, zIndex: 30 } : {}), width: sidebarCollapsed ? 0 : leftPanelWidth }}
            >
              <div className="h-full bg-background" style={{ width: leftPanelWidth }}>
                {leftPanel}
              </div>
            </div>
          </>
        )}

        {/* MainPanel */}
        <MainPanel
          {...mainPanelProps}
          sidebarOpen={!sidebarCollapsed}
          onToggleSidebar={toggleSidebar}
          onToggleRightPanel={toggleRightPanel}
        >
          {children}
        </MainPanel>

        {/* RightPanel — 滑入滑出动画 */}
        {rightPanel && (
          <>
            <div className="shrink-0 transition-[width] duration-200 ease-linear max-lg:!w-0" style={{ width: rightPanelOpen ? dynamicRightWidth : 0 }} />
            {isMobile && rightPanelOpen && (
              <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setRightPanelOpen(false)} aria-hidden="true" />
            )}
            <div
              className="absolute inset-y-0 top-0 z-10 h-full min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden"
              style={{ width: dynamicRightWidth, right: rightPanelOpen ? 0 : -dynamicRightWidth }}
            >
              {rightPanel}
            </div>
          </>
        )}
      </div>

      {/* BottomBar */}
      <BottomBar left={resolvedBottomLeft} middle={resolvedBottomMiddle} right={resolvedBottomRight} />
    </div>
  );
}
