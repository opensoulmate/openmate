'use client';

import * as React from 'react';

export interface BoardPanelProps {
  /** 是否展开（0↔width 平移动画，leftpanel 同款手感） */
  open: boolean;
  /** 移动端全宽视图（对话列隐藏时） */
  fullWidth?: boolean;
  /** 桌面端面板宽度，默认 340（任务看板同款） */
  width?: number;
  /** 标题行图标（如 <Bookmark className="w-3.5 h-3.5 text-primary" />） */
  icon?: React.ReactNode;
  title?: React.ReactNode;
  /** 标题行计数（如 "4 个任务"） */
  count?: React.ReactNode;
  /** 标题行右侧扩展位 */
  extra?: React.ReactNode;
  children?: React.ReactNode;
}

/**
 * 看板式侧栏面板（检查点/任务看板/群组管理统一壳）：
 * - 宽度 0↔width 平移展开（transition-[width] duration-200 ease-linear，leftpanel 同款）
 * - 标题行 h-12：与分类栏下边线同一水平线（48px 节奏）
 * - border-t 上边线带 -mt-px 1px 校准：与 topbar 下边线同一像素行
 */
export function BoardPanel({ open, fullWidth, width = 340, icon, title, count, extra, children }: BoardPanelProps) {
  return (
    <aside
      className="relative flex flex-col min-h-0 overflow-hidden shrink-0 -mt-px transition-[width] duration-200 ease-linear"
      style={{ width: open ? (fullWidth ? '100%' : width) : 0 }}
    >
      <div
        className="h-full flex flex-col bg-card border-t border-l border-border"
        style={{ width: fullWidth ? '100%' : width }}
      >
        <div className="flex items-center gap-1.5 px-3 h-12 border-b border-border shrink-0">
          {icon}
          <span className="text-xs font-medium">{title}</span>
          {count != null && <span className="text-[10px] text-muted-foreground">{count}</span>}
          <div className="flex-1" />
          {extra}
        </div>
        {children}
      </div>
    </aside>
  );
}
