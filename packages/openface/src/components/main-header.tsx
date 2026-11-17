'use client';

import { type ReactNode } from 'react';
import { PanelLeft } from 'lucide-react';
import { cn } from '../lib/utils';

/**
 * MainHeader — horizontal bar at the top of the middle content area.
 *
 * Contains toggle buttons for left panel and right panel, plus a title area.
 *
 * ┌──────────────────────────────────────────────┐
 * │ [◁] icon  title  badge          actions  [▷] │
 * └──────────────────────────────────────────────┘
 *
 * Pure layout shell — consumer controls what the toggle buttons do.
 */

export interface MainHeaderProps {
  /** Title text */
  title?: string;
  /** Icon/emoji before title */
  icon?: ReactNode;
  /** Badge/label after title */
  badge?: string;
  /** Extra actions on the right side (before right toggle) */
  actions?: ReactNode;
  /** Whether to show left panel toggle (default: true) */
  showLeftToggle?: boolean;
  /** Whether to show right panel toggle (default: true) */
  showRightToggle?: boolean;
  /** Callback when left toggle is clicked */
  onToggleLeft?: () => void;
  /** Callback when right toggle is clicked */
  onToggleRight?: () => void;
  className?: string;
}

export function MainHeader({
  title,
  icon,
  badge,
  actions,
  showLeftToggle = true,
  showRightToggle = true,
  onToggleLeft,
  onToggleRight,
  className,
}: MainHeaderProps) {
  return (
    <div
      className={cn(
        'h-12 border-b border-border flex items-center px-3 lg:px-4 justify-between shrink-0',
        className,
      )}
    >
      {/* Left: toggle + title */}
      <div className="flex items-center gap-1.5 lg:gap-2 min-w-0 flex-1">
        {showLeftToggle && (
          <button
            onClick={onToggleLeft}
            className="shrink-0 p-2 rounded-md hover:bg-zinc-800 active:bg-zinc-700 transition-colors text-zinc-400 hover:text-zinc-200 touch-manipulation cursor-pointer"
            aria-label="切换左面板"
          >
            <PanelLeft className="w-4 h-4" />
          </button>
        )}
        {icon && <span className="text-sm shrink-0">{icon}</span>}
        {title && (
          <span className="font-medium text-sm truncate">{title}</span>
        )}
        {badge && (
          <span className="text-[10px] lg:text-xs text-muted-foreground px-1 lg:px-1.5 py-0.5 rounded bg-muted shrink-0 truncate max-w-[80px] lg:max-w-none">
            {badge}
          </span>
        )}
      </div>

      {/* Right: actions + toggle */}
      <div className="flex items-center gap-1 shrink-0">
        {actions}
        {showRightToggle && (
          <button
            onClick={onToggleRight}
            className="shrink-0 p-2 rounded-md hover:bg-zinc-800 active:bg-zinc-700 transition-colors text-zinc-400 hover:text-zinc-200 touch-manipulation cursor-pointer"
            aria-label="切换右面板"
          >
            <PanelLeft className="w-4 h-4 scale-x-[-1]" />
          </button>
        )}
      </div>
    </div>
  );
}
