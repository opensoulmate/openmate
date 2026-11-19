"use client";

import { useRef, useState, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";

// ── Panel route order — matches bottom nav order exactly ──────────

const PANEL_ROUTES = [
  "/chat",
  "/dashboard",
  "/notifications",
  "/ai-groups",
  "/knowledge",
  "/learn",
  "/graph",
  "/graph-builder",
  "/search",
  "/knowledge-requests",

  "/cron",
  "/workflow",
  "/workflow",
  "/will",
  "/skills",
  "/mcp",
  "/workspace",
  "/capture",
  "/download",
  "/tags",
  "/body-map",
  "/soma",
  "/discovery",
  "/cortex",
  "/vein",
  "/gene",
  "/vital",
  "/gland",
  "/hippo",
  "/reflex",
  "/heredity",
  "/pulse",
  "/nerve",
  "/sense",
  "/immune",
  "/marrow",
  "/echo",
  "/mirror",
  "/link",
  "/nest",
  "/limb",
  "/voice",
  "/vision",
  "/mind",
  "/system",
  "/soul",
  "/soma-admin",
  "/admin",
  "/permission",
  "/enterprise",
  "/sessions",
  "/diagnostics",
  "/metrics",
  "/benchmark",
  "/intelligence",
  "/ai-engine",
  "/healer",
  "/topology",
  "/registry",
  "/trajectory",
  "/timeline",
  "/changelog",
  "/plugins",
  "/marketplace",
];

const SWIPE_THRESHOLD = 50;

// ── SwipeablePanels ───────────────────────────────────────────────

interface SwipeablePanelsProps {
  children: React.ReactNode;
  isHomePage: boolean;
}

export function SwipeablePanels({ children, isHomePage }: SwipeablePanelsProps) {
  const isMobile = useIsMobile();
  const router = useRouter();
  const pathname = usePathname();

  const touchStartRef = useRef<{ x: number; y: number; time: number } | null>(null);
  const lockedAxisRef = useRef<"horizontal" | "vertical" | null>(null);
  const [swipeDir, setSwipeDir] = useState<"left" | "right" | null>(null);

  // Get current panel index
  const currentIndex = PANEL_ROUTES.findIndex((r) => pathname.startsWith(r));

  const handleSwipe = useCallback(
    (direction: "left" | "right") => {
      if (currentIndex < 0) return;
      const nextIndex = direction === "left" ? currentIndex + 1 : currentIndex - 1;
      if (nextIndex < 0 || nextIndex >= PANEL_ROUTES.length) return;
      setSwipeDir(direction);
      router.push(PANEL_ROUTES[nextIndex]);
      setTimeout(() => setSwipeDir(null), 300);
    },
    [currentIndex, router],
  );

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (!isHomePage) return;
      const touch = e.touches[0];
      touchStartRef.current = { x: touch.clientX, y: touch.clientY, time: Date.now() };
      lockedAxisRef.current = null;
    },
    [isHomePage],
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!touchStartRef.current || !isHomePage) return;
      const touch = e.touches[0];
      const dx = touch.clientX - touchStartRef.current.x;
      const dy = touch.clientY - touchStartRef.current.y;

      if (lockedAxisRef.current === null) {
        if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
          lockedAxisRef.current = Math.abs(dx) >= Math.abs(dy) ? "horizontal" : "vertical";
        }
        return;
      }

      // Horizontal swipe — prevent vertical scroll
      if (lockedAxisRef.current === "horizontal") {
        e.preventDefault();
      }
    },
    [isHomePage],
  );

  const handleTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      if (!touchStartRef.current || !isHomePage) return;
      const touch = e.changedTouches[0];
      const dx = touch.clientX - touchStartRef.current.x;
      const elapsed = Date.now() - touchStartRef.current.time;
      const velocity = Math.abs(dx) / elapsed;
      const isQuickSwipe = velocity > 0.3 && Math.abs(dx) > 20;

      if (lockedAxisRef.current === "horizontal" || isQuickSwipe) {
        if (dx < -SWIPE_THRESHOLD || (isQuickSwipe && dx < 0)) {
          handleSwipe("left");
        } else if (dx > SWIPE_THRESHOLD || (isQuickSwipe && dx > 0)) {
          handleSwipe("right");
        }
      }

      touchStartRef.current = null;
      lockedAxisRef.current = null;
    },
    [isHomePage, handleSwipe],
  );

  // Desktop or not a swipeable page — render children directly
  if (!isMobile || !isHomePage || currentIndex < 0) {
    return <>{children}</>;
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
      {/* Content area with swipe gesture */}
      <div
        className={cn(
          "flex-1 min-h-0 overflow-hidden flex flex-col",
          swipeDir === "left" && "animate-swipe-left",
          swipeDir === "right" && "animate-swipe-right",
        )}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
      >
        {children}
      </div>

      {/* Page indicator removed — bottom nav already shows current position */}

      <style jsx global>{`
        @keyframes swipe-left {
          from { opacity: 1; transform: translateX(0); }
          to { opacity: 0; transform: translateX(-30px); }
        }
        @keyframes swipe-right {
          from { opacity: 1; transform: translateX(0); }
          to { opacity: 0; transform: translateX(30px); }
        }
        .animate-swipe-left { animation: swipe-left 0.2s ease-out; }
        .animate-swipe-right { animation: swipe-right 0.2s ease-out; }
      `}</style>
    </div>
  );
}

// ── Exports ───────────────────────────────────────────────────────

export function getPanelIndex(pathname: string): number {
  return PANEL_ROUTES.findIndex((r) => pathname.startsWith(r));
}
