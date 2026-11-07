'use client';

import * as React from 'react';
import { ChevronDown } from 'lucide-react';

export interface TopbarLineProps {
  /** 右侧面板是否展开：展开时下边线缩短到对话区宽，折叠钮随线平移居中 */
  panelOpen: boolean;
  /** 移动端（面板全宽时线不缩短） */
  fullWidth?: boolean;
  /** 面板宽度，默认 340 */
  width?: number;
  /** 折叠内容是否展开（控制 chevron 旋转） */
  expanded?: boolean;
  onToggleExpanded?: () => void;
  toggleTitle?: string;
}

/**
 * topbar 下边线（缩短行）：
 * - 面板展开时线缩短到对话区宽（占位与面板同宽同动画），分类折叠钮随线平移到居中
 * - 线本身 -mt-px：与 BoardPanel 的 border-t 同一像素行（1px 校准）
 */
export function TopbarLine({ panelOpen, fullWidth, width = 340, expanded, onToggleExpanded, toggleTitle }: TopbarLineProps) {
  return (
    <div className="flex shrink-0 -mt-px h-px">
      <div className="flex-1 relative bg-border">
        <button
          onClick={onToggleExpanded}
          className="absolute left-1/2 -translate-x-1/2 -top-[9px] z-10 p-0.5 rounded-full bg-background border border-border hover:bg-muted/50 active:bg-muted transition-colors text-muted-foreground touch-manipulation"
          title={toggleTitle}
        >
          <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 ${expanded ? 'rotate-180' : 'rotate-0'}`} />
        </button>
      </div>
      <div
        className="overflow-hidden shrink-0 transition-[width] duration-200 ease-linear"
        style={{ width: panelOpen && !fullWidth ? width : 0 }}
      />
    </div>
  );
}
