"use client";
import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import {
  Camera, RefreshCw, Trash2, ExternalLink, Search,
  FileText, Type, CheckCircle, Loader2, ArrowUpCircle,
  Globe, Clock, Hash,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface Capture {
  id: number;
  capture_type: string;
  title: string;
  url: string;
  description: string;
  keywords: string[];
  content: string;
  content_hash: string;
  status: string;
  created_at: number;
  user_id: string;
}

interface CaptureStats {
  total_captures: number;
  page_captures: number;
  selection_captures: number;
}

function TypeBadge({ type, t }: { type: string; t: (k: string) => string }) {
  return type === "page" ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-500">
      <Globe size={10} /> {t("capture.page")}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-purple-500/10 px-2 py-0.5 text-[10px] font-medium text-purple-500">
      <Type size={10} /> {t("capture.selection")}
    </span>
  );
}

function StatusBadge({ status, t }: { status: string; t: (k: string) => string }) {
  const colors: Record<string, string> = {
    captured: "bg-emerald-500/10 text-emerald-500",
    duplicate: "bg-yellow-500/10 text-yellow-500",
    promoted: "bg-blue-500/10 text-blue-500",
  };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium", colors[status] || "bg-muted-foreground/10 text-muted-foreground")}>
      {t(`capture.${status}`) || status}
    </span>
  );
}

function formatTime(ts: number, t: (k: string, o?: Record<string, unknown>) => string): string {
  const diff = Date.now() - ts * 1000;
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return t("notifications.justNow") || "Just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("capture.t13587", { floordiff60: minutes });
  const hours = Math.floor(minutes / 60);
  return t("capture.t58929", { floordiff3600: hours });
}

export function CaptureClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [stats, setStats] = useState<CaptureStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<"all" | "page" | "selection">("all");
  const [search, setSearch] = useState("");
  const [promoting, setPromoting] = useState<number | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const typeParam = filter !== "all" ? `&capture_type=${filter}` : "";
      const [listRes, healthRes] = await Promise.all([
        fetch(`${apiBase}/api/capture/list?limit=100${typeParam}`),
        fetch(`${apiBase}/api/capture/health`),
      ]);
      const listData = await listRes.json();
      const healthData = await healthRes.json();
      setCaptures(listData.captures || []);
      setStats(healthData);
    } catch {}
    setLoading(false);
  }, [apiBase, filter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleDelete = async (id: number) => {
    try {
      await fetch(`${apiBase}/api/capture/${id}`, { method: "DELETE" });
      setCaptures(prev => prev.filter(c => c.id !== id));
    } catch {}
  };

  const handlePromote = async (id: number) => {
    setPromoting(id);
    try {
      const res = await fetch(`${apiBase}/api/capture/${id}/promote?user_id=default`, { method: "POST" });
      if (res.ok) {
        setCaptures(prev => prev.map(c => c.id === id ? { ...c, status: "promoted" } : c));
      }
    } catch {}
    setPromoting(null);
  };

  const filteredCaptures = captures.filter(c => {
    if (search) {
      const q = search.toLowerCase();
      return c.title.toLowerCase().includes(q) || c.url.toLowerCase().includes(q) || c.content.toLowerCase().includes(q);
    }
    return true;
  });

  return (
      <PageLayout title="采集">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-orange-500/10">
            <Camera size={18} className="text-orange-500" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t("capture.title")}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.capture')}</p>
          </div>
        </div>
        <button onClick={fetchData} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-accent">
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> {t("capture.refresh")}
        </button>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 border-b border-border px-3 lg:px-6 py-3">
          <div className="rounded-lg bg-muted/50 px-3 py-2">
            <div className="text-[10px] text-muted-foreground">{t("capture.total")}</div>
            <div className="text-lg font-bold">{stats.total_captures}</div>
          </div>
          <div className="rounded-lg bg-blue-500/5 px-3 py-2">
            <div className="text-[10px] text-muted-foreground">{t("capture.pageCapture")}</div>
            <div className="text-lg font-bold text-blue-500">{stats.page_captures}</div>
          </div>
          <div className="rounded-lg bg-purple-500/5 px-3 py-2">
            <div className="text-[10px] text-muted-foreground">{t("capture.textCapture")}</div>
            <div className="text-lg font-bold text-purple-500">{stats.selection_captures}</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2 border-b border-border px-3 lg:px-6 py-2.5 overflow-x-auto">
        <div className="flex rounded-lg border border-border p-0.5">
          {(["all", "page", "selection"] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={cn("rounded-md px-2.5 py-1 text-xs transition-colors", filter === f ? "bg-accent font-medium" : "text-muted-foreground hover:text-foreground")}>
              {t(`capture.${f}`)}
            </button>
          ))}
        </div>
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input value={search} onChange={e => setSearch(e.target.value)}
            placeholder={t("capture.searchPlaceholder")} className="w-full rounded-lg border border-border bg-background py-1.5 pl-7 pr-3 text-xs" />
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6">
        {loading && captures.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 size={16} className="animate-spin mr-2" /> {t("capture.loading")}
          </div>
        ) : filteredCaptures.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Camera size={32} className="mb-2 opacity-30" />
            <p className="text-sm">{t("capture.noContent")}</p>
            <p className="text-xs mt-1">{t("capture.noContentHint")}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredCaptures.map(capture => (
              <div key={capture.id} className="group rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 transition-colors hover:border-orange-500/30">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <TypeBadge type={capture.capture_type} t={t} />
                      <StatusBadge status={capture.status} t={t} />
                      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                        <Clock size={10} /> {formatTime(capture.created_at, t)}
                      </span>
                    </div>
                    <h3 className="text-sm font-medium truncate">{capture.title || t("capture.untitled")}</h3>
                    <a href={capture.url} target="_blank" rel="noopener noreferrer"
                      className="flex items-center gap-1 text-xs text-muted-foreground hover:text-blue-500 truncate mt-0.5">
                      <ExternalLink size={10} /> {capture.url}
                    </a>
                    {capture.content && (
                      <p className="mt-2 text-xs text-muted-foreground line-clamp-2">{capture.content}</p>
                    )}
                    {capture.description && capture.description !== capture.content && (
                      <p className="mt-1 text-[10px] text-muted-foreground/70 italic">{capture.description}</p>
                    )}
                    {capture.keywords && capture.keywords.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {capture.keywords.map((kw, i) => (
                          <span key={i} className="inline-flex items-center gap-0.5 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            <Hash size={8} /> {kw}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                    {capture.status !== "promoted" && (
                      <button onClick={() => handlePromote(capture.id)}
                        disabled={promoting === capture.id}
                        className="flex items-center gap-1 rounded-lg bg-blue-500/10 px-2 py-1.5 text-[10px] font-medium text-blue-500 hover:bg-blue-500/20 disabled:opacity-50"
                        title={t("capture.promoteToKB")}>
                        {promoting === capture.id ? <Loader2 size={10} className="animate-spin" /> : <ArrowUpCircle size={10} />}
                        {t("capture.promote")}
                      </button>
                    )}
                    <button onClick={() => handleDelete(capture.id)}
                      className="flex items-center gap-1 rounded-lg bg-red-500/10 px-2 py-1.5 text-[10px] font-medium text-red-500 hover:bg-red-500/20"
                      title={t("capture.delete")}>
                      <Trash2 size={10} />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
