import { useRef, useEffect, type ReactNode } from "react";
import { cn } from "../lib/utils";

/**
 * BottomBar — abstract bottom navigation shell.
 *
 * ┌─────────┬──────────────────────────┬─────────┐
 * │  left   │  middle (scrollable)     │ center  │
 * └─────────┴──────────────────────────┴─────────┘
 *                              ↑ elevated center button (optional)
 *
 * Consumer injects content via slots. The shell handles:
 * - Scrollable middle zone with mouse-wheel horizontal scroll
 * - Auto-scroll to active item
 * - Elevated center button (voice, FAB, etc.)
 * - Safe-area insets, border, height
 */

export interface BottomBarProps {
  /** Left zone (user avatar, hamburger) */
  left?: ReactNode;
  /** Middle zone (scrollable nav items) */
  middle?: ReactNode;
  /** Elevated center button (voice, FAB) — positioned in the bump */
  centerButton?: ReactNode;
  /** Right zone (settings, fixed button) */
  right?: ReactNode;
  className?: string;
  /** Whether to show the wave bump for center button. Default: false */
  showBump?: boolean;
}

export function BottomBar({
  left,
  middle,
  centerButton,
  right,
  className,
  showBump = false,
}: BottomBarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Mouse wheel → horizontal scroll
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
    <nav
      className={cn(
        "relative z-20 shrink-0 min-h-12 bg-background border-t border-border safe-area-bottom",
        showBump && "nav-wave",
        className
      )}
    >
      {/* Wave bump for center button */}
      {showBump && centerButton && (
        <>
          <div className="nav-wave-bump" />
          <div className="nav-wave-border" />
          <div className="nav-wave-btn">{centerButton}</div>
        </>
      )}

      <div className="flex h-full items-center">
        {/* Left — fixed */}
        {left && (
          <div className="shrink-0 flex items-center justify-center h-full">
            {left}
          </div>
        )}

        {/* Middle — scrollable */}
        {middle && (
          <div
            ref={scrollRef}
            className="flex-1 flex h-full items-center gap-0.5 overflow-x-auto"
            style={{
              scrollbarWidth: "none",
              WebkitOverflowScrolling: "touch",
            }}
          >
            {middle}
          </div>
        )}

        {/* Right — fixed */}
        {right && (
          <div className="shrink-0 flex items-center justify-center h-full">
            {right}
          </div>
        )}
      </div>

      {/* Wave CSS — only injected when bump is shown */}
      {showBump && (
        <style
          dangerouslySetInnerHTML={{
            __html: `
              .nav-wave { overflow: visible; }
              .nav-wave-bump {
                position: absolute; left: 50%; transform: translateX(-50%);
                bottom: 100%; width: 80px; height: 40px;
                border-radius: 80px 80px 0 0;
                background: hsl(var(--background)); z-index: 5;
              }
              .nav-wave-border {
                position: absolute; left: 50%; transform: translateX(-50%);
                bottom: 100%; width: 80px; height: 40px;
                border-radius: 80px 80px 0 0;
                border: 1px solid hsl(var(--border)); border-bottom: none;
                z-index: 6; pointer-events: none;
              }
              .nav-wave-btn {
                position: absolute; left: 50%; transform: translateX(-50%);
                bottom: calc(100% + 4px); z-index: 10;
              }
            `,
          }}
        />
      )}
    </nav>
  );
}
