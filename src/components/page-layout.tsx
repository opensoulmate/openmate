'use client';

import { createContext, useContext } from 'react';
import { useSidebar } from '@/components/ui/sidebar';
import { useAppStore } from '@/stores/app-store';
import { MainPanel, type MainPanelProps } from '@opensoulmate/openface';

// menu-audit P1：hub tab 里"整体搬入"旧页组件时，旧页自带的 PageLayout 会与 hub 外壳叠出
// 双标题（违 UI 铁律"布局组件只做壳"）。用 ShellContext 让嵌套的 PageLayout 只渲染内容、
// 外壳交给 hub，避免双标题；顶层 PageLayout 仍渲染 MainPanel 外壳。
const ShellContext = createContext(false);

/**
 * OpenMate's PageLayout wrapper (bridges to openface MainPanel).
 * Bridges sidebar state and right panel toggle from OpenMate's store
 * into @opensoulmate/openface's MainPanel.
 */
export function PageLayout(props: MainPanelProps) {
  const isNested = useContext(ShellContext);
  const { open: sidebarOpen, toggleSidebar } = useSidebar();
  const toggleRightPanel = useAppStore((s) => s.toggleRightPanel);

  if (isNested) {
    // 嵌在 hub tab 内：只渲染内容，不再套 MainPanel 外壳（避免双标题）
    return (
      <ShellContext.Provider value={true}>
        <div className="h-full min-h-0">{props.children}</div>
      </ShellContext.Provider>
    );
  }

  return (
    <ShellContext.Provider value={true}>
      <MainPanel
        {...props}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={toggleSidebar}
        onToggleRightPanel={toggleRightPanel}
      />
    </ShellContext.Provider>
  );
}

export type { MainPanelProps as PageLayoutProps };
