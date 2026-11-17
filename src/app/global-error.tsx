"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="flex h-screen items-center justify-center bg-background text-foreground">
        <div className="w-full max-w-sm text-center p-8">
          <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-red-500/10">
            <AlertTriangle size={28} className="text-red-500" />
          </div>
          <h1 className="text-lg font-bold">应用错误</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            发生严重错误，请重新加载页面。
          </p>
          {error.digest && (
            <p className="mt-2 font-mono text-xs text-muted-foreground">
              ID: {error.digest}
            </p>
          )}
          <button
            onClick={reset}
            className="mt-6 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            <RefreshCw size={14} />
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
