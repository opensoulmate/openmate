
import { type ReactNode } from 'react';
import { MainHeader } from './main-header';
import { useAppStore } from '../store/app-store';
import { useIsMobile } from '../hooks/use-mobile';
import { cn } from '../lib/utils';

// ── Types ────────────────────────────────────────────────────────

export interface MainPanelProps {
  /** Page title displayed in banner */
  title?: string;
  /** Icon/emoji displayed before title */
  icon?: ReactNode;
  /** Badge/label displayed after title */
  badge?: string;
  /** In-page detail column content (optional). 渲染在页面自身右列（页内 detail），
   * 绝不进全局 workspace 面板——workspace 面板只属于工作区（文件/浏览器/终端/会话详情）。 */
  workspace?: ReactNode;
  /** Main content */
  children: ReactNode;
  /** Additional banner actions (buttons, etc.) */
  headerActions?: ReactNode;
  /** Custom banner content (replaces default MainHeader) */
  header?: ReactNode;
  /** Whether to show left panel toggle button (default: true) */
  showSidebarToggle?: boolean;
  /** Whether to show right panel toggle button (default: true) */
  showWorkspaceToggle?: boolean;
  /** Additional class names for the root container */
  className?: string;
  /** Left panel open state — consumer manages this */
  sidebarOpen?: boolean;
  /** Toggle left panel callback — consumer manages this */
  onToggleSidebar?: () => void;
  /** Toggle right panel callback — consumer manages this (falls back to useAppStore.toggleRightPanel) */
  onToggleRightPanel?: () => void;
}

// ── Main Component ───────────────────────────────────────────────

export function MainPanel({
  title,
  icon,
  badge,
  workspace,
  children,
  headerActions,
  header,
  showSidebarToggle = true,
  showWorkspaceToggle = true,
  className,
  sidebarOpen,
  onToggleSidebar,
  onToggleRightPanel,
}: MainPanelProps) {
  // 页内 detail 渲染（2026-11-19 菜单点击劫持全局根修）：detail 内容直接渲染在
  // 页面自身右列，不再经 app-store 的 pageWorkspace 通道塞进全局 workspace 面板。
  // （旧通道=「点击在 workspace 里打开」病灶；workspace 面板只属于工作区本身。）
  const storeToggleRight = useAppStore((s) => s.toggleRightPanel);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const isMobile = useIsMobile();

  const handleToggleLeft = () => {
    onToggleSidebar?.();
    if (isMobile) {
      setRightPanelOpen(false);
    }
  };

  const handleToggleRight = () => {
    // 优先用consumer传的callback，没有就用store
    if (onToggleRightPanel) {
      onToggleRightPanel();
    } else {
      storeToggleRight();
    }
    if (isMobile && sidebarOpen) {
      onToggleSidebar?.();
    }
  };

  return (
    <div className={cn('flex flex-1 min-h-0 relative', className)}>
      <div className="flex flex-1 flex-col min-w-0">
        {/* Banner */}
        {header || (
          <MainHeader
            title={title}
            icon={icon}
            badge={badge}
            actions={headerActions}
            showLeftToggle={showSidebarToggle}
            showRightToggle={showWorkspaceToggle}
            onToggleLeft={handleToggleLeft}
            onToggleRight={handleToggleRight}
          />
        )}

        {/* Content */}
        <div className="flex-1 flex flex-col overflow-hidden min-h-0">
          {children}
        </div>
      </div>

      {/* 页内 detail 右列（页面自己的三栏右格）——不是全局 workspace 面板 */}
      {workspace && (
        <aside className="relative z-20 flex shrink-0 flex-col overflow-hidden border-l border-border bg-background max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:w-full lg:w-[400px]">
          {workspace}
        </aside>
      )}
    </div>
  );
}
