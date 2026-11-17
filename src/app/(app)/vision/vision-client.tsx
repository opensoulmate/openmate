"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  ImageIcon, RefreshCw, CheckCircle, XCircle,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';
import { VisionWorkspace } from './vision-workspace';

interface VisionStats {
  status: string;
  component: string;
  engine: string;
  backends: Record<string, boolean>;
  total_generated: number;
  errors: number;
  output_dir: string;
  saved_outputs: number;
}

function StatusBadge({ online }: { online: boolean }) {
  return online ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-500">
      <CheckCircle size={10} /> Online
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-500">
      <XCircle size={10} /> Offline
    </span>
  );
}

export function VisionClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [stats, setStats] = useState<VisionStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchStats = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${apiBase}/api/vision/stats`);
      if (res.ok) {
        setStats(await res.json());
      } else {
        setError(`HTTP ${res.status}`);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch stats");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    fetchStats();
    const interval = setInterval(fetchStats, 30000);
    return () => clearInterval(interval);
  }, [fetchStats]);

  return (
      <PageLayout title="视觉">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-3 lg:py-4 gap-2">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <ImageIcon size={18} className="text-indigo-500 shrink-0" />
          <h1 className="text-sm lg:text-lg font-semibold truncate">{t("vision.title") || "Vision Engine"}</h1>
          <span className="rounded-full bg-indigo-500/10 px-1.5 lg:px-2 py-0.5 text-[10px] lg:text-xs font-medium text-indigo-500 shrink-0">
            {t("vision.badge") || "Charts"}
          </span>
          {stats && <StatusBadge online={stats.status === "ok"} />}
        </div>
        <button
          onClick={() => { fetchStats(); }}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg border border-border px-2 lg:px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors disabled:opacity-50 shrink-0 touch-manipulation"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          {t("common.refresh") || "Refresh"}
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3 lg:p-6">
        {error && (
          <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 lg:p-4 text-xs lg:text-sm text-red-500">
            {error}
          </div>
        )}

        {/* 三区：生成 / 画布 / 历史（VisionWorkspace） */}
        <VisionWorkspace />

      </div>
    </div>
  
      </PageLayout>
    );
}
