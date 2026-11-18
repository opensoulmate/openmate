'use client';

import { useSidebar } from '@/components/ui/sidebar';
import { useAppStore } from '@/stores/app-store';
import { MainPanel, type MainPanelProps } from '@opensoulmate/openface';

/**
 * OpenMate's PageLayout wrapper (bridges to openface MainPanel).
 * Bridges sidebar state and right panel toggle from OpenMate's store
 * into @opensoulmate/openface's MainPanel.
 */
export function PageLayout(props: MainPanelProps) {
  const { open: sidebarOpen, toggleSidebar } = useSidebar();
  const toggleRightPanel = useAppStore((s) => s.toggleRightPanel);

  return (
    <MainPanel
      {...props}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={toggleSidebar}
      onToggleRightPanel={toggleRightPanel}
    />
  );
}

export type { MainPanelProps as PageLayoutProps };
