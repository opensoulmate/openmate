"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw, Home, ChevronDown, ChevronUp } from "lucide-react";
import Link from "next/link";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    console.error("[AppError]", error);
  }, [error]);

  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-md rounded-xl border border-border bg-card p-8 text-center shadow-lg">
        {/* Icon */}
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-red-500/10">
          <AlertTriangle size={32} className="text-red-500" />
        </div>

        {/* Title */}
        <h2 className="text-xl font-bold text-foreground">出错了</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          发生未知错误。你可以重新加载页面或返回仪表板。
        </p>

        {/* Error digest */}
        {error.digest && (
          <p className="mt-2 font-mono text-xs text-muted-foreground">
            错误 ID： {error.digest}
          </p>
        )}

        {/* Toggle details */}
        <button
          onClick={() => setShowDetails(!showDetails)}
          className="mt-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors mx-auto"
        >
          {showDetails ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {showDetails ? "隐藏" : "显示"} 错误详情
        </button>

        {showDetails && (
          <div className="mt-3 rounded-lg border border-border bg-muted/50 p-3 text-left">
            <p className="font-mono text-xs text-red-400 break-all">
              {error.message || "Unknown error"}
            </p>
            {error.stack && (
              <pre className="mt-2 max-h-32 overflow-auto text-[10px] text-muted-foreground whitespace-pre-wrap">
                {error.stack.split("\n").slice(0, 5).join("\n")}
              </pre>
            )}
          </div>
        )}

        {/* Actions */}
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            onClick={reset}
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <RefreshCw size={14} />
            Try again
          </button>
          <Link
            href="/dashboard"
            className="flex items-center gap-2 rounded-lg border border-border px-4 py-2.5 text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <Home size={14} />
            Dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
