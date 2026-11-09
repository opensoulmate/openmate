import { useRef, useEffect, type ReactNode } from "react";
import { cn } from "../lib/utils";

/**
 * TopBar — abstract three-zone layout shell.
 *
 * ┌─────────┬──────────────────────────┬─────────┐
 * │  left   │  middle (scrollable)     │  right  │
 * └─────────┴──────────────────────────┴─────────┘
 *
 * Consumer injects whatever content they want via slots.
 * The shell only handles: layout, mouse-wheel horizontal scroll, sizing.
 */

export interface TopBarProps {
  left?: ReactNode;
  middle?: ReactNode;
  right?: ReactNode;
  className?: string;
}

export function TopBar({ left, middle, right, className }: TopBarProps) {
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
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  return (
    <div
      className={cn(
        // min-h-12（非 h-12）：safe-area-top 的让位会撑高行盒，不把内容挤出行底被下方盖住
        "flex min-h-12 shrink-0 items-center border-b border-border bg-background safe-area-top",
        className
      )}
    >
      {left && <div className="flex items-center shrink-0">{left}</div>}
      {middle && (
        <div
          ref={scrollRef}
          className="flex-1 flex items-center gap-1 overflow-x-auto"
          style={{ scrollbarWidth: "none" }}
        >
          {middle}
        </div>
      )}
      {right && <div className="flex items-center shrink-0">{right}</div>}
    </div>
  );
}
