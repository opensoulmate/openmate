"use client";
import { useState, useEffect, useCallback } from "react";
import { getApiBaseUrl } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import {
  Server, RefreshCw, Loader2, CheckCircle2, XCircle, AlertCircle,
  Search, ChevronDown, ChevronRight, Zap, Link2, Layers, Filter,
  Grid3X3, List, ArrowUpRight, Package, Shield, Cpu, Brain,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { PageLayout } from '@/components/page-layout';

interface ComponentInfo {
  id: string;
  name: string;
  emoji: string;
  category: string;
  layer: string;
  description: string;
  api_prefix: string;
  health_endpoint: string;
  capabilities: string[];
  dependencies: string[];
  version: string;
  phase: string;
}

interface RegistryStats {
  total_components: number;
  by_category: Record<string, number>;
  by_phase: Record<string, number>;
}

interface HealthStatus {
  status: string;
  healthy: number;
  total: number;
  organs: Record<string, string>;
}

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  core: { bg: "bg-violet-500/10", text: "text-violet-400", border: "border-violet-500/20" },
  platform: { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/20" },
  advanced: { bg: "bg-amber-500/10", text: "text-amber-400", border: "border-amber-500/20" },
  system: { bg: "bg-emerald-500/10", text: "text-emerald-400", border: "border-emerald-500/20" },
};

const PHASE_COLORS: Record<string, string> = {
  "Phase 1": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  "Phase 2": "bg-blue-500/10 text-blue-400 border-blue-500/20",
  "Phase 3": "bg-amber-500/10 text-amber-400 border-amber-500/20",
  "Phase 4": "bg-purple-500/10 text-purple-400 border-purple-500/20",
};

export function RegistryClient() {
  const { t } = useTranslation();
  const [components, setComponents] = useState<ComponentInfo[]>([]);
  const [stats, setStats] = useState<RegistryStats | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");
  const [phaseFilter, setPhaseFilter] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchAll = useCallback(async () => {
    const apiBase = getApiBaseUrl();
    if (!apiBase) return;
    try {
      const [compRes, statsRes, healthRes] = await Promise.all([
        fetch(`${apiBase}/api/registry/components`).then(r => r.json()).catch(() => ({ components: [] })),
        fetch(`${apiBase}/api/registry/stats`).then(r => r.json()).catch(() => null),
        fetch(`${apiBase}/api/health/all`).then(r => r.json()).catch(() => null),
      ]);
      setComponents(compRes.components || []);
      setStats(statsRes);
      setHealth(healthRes);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = setInterval(fetchAll, 30000);
    return () => clearInterval(timer);
  }, [autoRefresh, fetchAll]);

  const filtered = components.filter((c) => {
    if (categoryFilter && c.category !== categoryFilter) return false;
    if (phaseFilter && c.phase !== phaseFilter) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return (
        c.name.toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q) ||
        c.capabilities.some((cap) => cap.toLowerCase().includes(q))
      );
    }
    return true;
  });

  function getHealthStatus(id: string): string {
    if (!health?.organs) return "unknown";
    // Try exact match first, then partial
    if (health.organs[id]) return health.organs[id];
    // Some organs have compound keys
    for (const [key, val] of Object.entries(health.organs)) {
      if (key.startsWith(id)) return val;
    }
    return "unknown";
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const categories = Object.keys(stats?.by_category || {});
  const phases = Object.keys(stats?.by_phase || {});

  return (
      <PageLayout title="注册表">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg lg:text-xl font-semibold text-foreground flex items-center gap-2">
              <Package className="h-5 w-5 text-primary" />
              {t("registry.title", "Component Registry")}
            </h1>
            <p className="mt-1 text-xs lg:text-sm text-muted-foreground">
              {t("registry.subtitle", "System organ registry — {{total}} components across {{phases}} phases", {
                total: stats?.total_components || components.length,
                phases: phases.length,
              })}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setViewMode(viewMode === "grid" ? "list" : "grid")}
              className="rounded-lg border border-border p-2 hover:bg-accent transition-colors"
              title={viewMode === "grid" ? "列表视图" : "网格视图"}
            >
              {viewMode === "grid" ? <List className="h-4 w-4" /> : <Grid3X3 className="h-4 w-4" />}
            </button>
            <button
              onClick={() => fetchAll()}
              className="rounded-lg border border-border p-2 hover:bg-accent transition-colors"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Stats Summary */}
        <div className="mt-4 flex flex-wrap gap-3">
          <div className="rounded-lg border border-border bg-card px-3 py-2">
            <div className="text-xs text-muted-foreground">{t("registry.totalComponents", "Components")}</div>
            <div className="text-lg font-semibold">{stats?.total_components || components.length}</div>
          </div>
          {health && (
            <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
              <div className="text-xs text-emerald-400">{t("registry.healthy", "Healthy")}</div>
              <div className="text-lg font-semibold text-emerald-400">{health.healthy}/{health.total}</div>
            </div>
          )}
          {categories.map((cat) => (
            <div
              key={cat}
              className={cn("rounded-lg border px-3 py-2", CATEGORY_COLORS[cat]?.border, CATEGORY_COLORS[cat]?.bg)}
            >
              <div className={cn("text-xs", CATEGORY_COLORS[cat]?.text)}>{cat}</div>
              <div className={cn("text-lg font-semibold", CATEGORY_COLORS[cat]?.text)}>
                {stats?.by_category?.[cat] || 0}
              </div>
            </div>
          ))}
          {phases.map((phase) => (
            <div
              key={phase}
              className={cn("rounded-lg border px-3 py-2", PHASE_COLORS[phase] || "border-border")}
            >
              <div className="text-xs text-muted-foreground">{phase}</div>
              <div className="text-lg font-semibold">{stats?.by_phase?.[phase] || 0}</div>
            </div>
          ))}
        </div>

        {/* Filters */}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t("registry.searchPlaceholder", "Search components...")}
              className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-xs lg:text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm"
          >
            <option value="">{t("registry.allCategories", "All Categories")}</option>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <select
            value={phaseFilter}
            onChange={(e) => setPhaseFilter(e.target.value)}
            className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm"
          >
            <option value="">{t("registry.allPhases", "All Phases")}</option>
            {phases.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <span className="text-xs lg:text-sm text-muted-foreground">
            {filtered.length} {t("registry.results", "results")}
          </span>
        </div>
      </div>

      {/* Component List */}
      <div className="flex-1 overflow-y-auto p-3 lg:p-6">
        {viewMode === "grid" ? (
          <div className="grid gap-2 lg:gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filtered.map((comp) => {
              const hs = getHealthStatus(comp.id);
              const isExpanded = expandedId === comp.id;
              return (
                <div
                  key={comp.id}
                  className={cn(
                    "group rounded-xl border border-border bg-card p-3 lg:p-4 transition-all hover:border-primary/30 hover:shadow-md cursor-pointer",
                    isExpanded && "ring-1 ring-primary/30"
                  )}
                  onClick={() => setExpandedId(isExpanded ? null : comp.id)}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-2xl">{comp.emoji}</span>
                      <div>
                        <div className="font-medium text-xs lg:text-sm">{comp.name}</div>
                        <div className="text-[11px] text-muted-foreground">{comp.layer}</div>
                      </div>
                    </div>
                    <div className={cn(
                      "flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
                      hs === "ok" ? "bg-emerald-500/10 text-emerald-400" :
                      hs === "unknown" ? "bg-muted text-muted-foreground" :
                      "bg-red-500/10 text-red-400"
                    )}>
                      {hs === "ok" ? <CheckCircle2 className="h-3 w-3" /> :
                       hs === "unknown" ? <AlertCircle className="h-3 w-3" /> :
                       <XCircle className="h-3 w-3" />}
                      {hs === "ok" ? "UP" : hs === "unknown" ? "N/A" : hs.toUpperCase()}
                    </div>
                  </div>

                  <p className="mt-2 text-xs text-muted-foreground line-clamp-2">{comp.description}</p>

                  <div className="mt-3 flex flex-wrap gap-1">
                    <span className={cn("rounded-full border px-2 py-0.5 text-[10px]", CATEGORY_COLORS[comp.category]?.border, CATEGORY_COLORS[comp.category]?.text)}>
                      {comp.category}
                    </span>
                    <span className={cn("rounded-full border px-2 py-0.5 text-[10px]", PHASE_COLORS[comp.phase] || "border-border text-muted-foreground")}>
                      {comp.phase}
                    </span>
                    <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted-foreground">
                      v{comp.version}
                    </span>
                  </div>

                  {isExpanded && (
                    <div className="mt-3 border-t border-border pt-3 space-y-2">
                      <div>
                        <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1">
                          {t("registry.capabilities", "Capabilities")}
                        </div>
                        <div className="flex flex-wrap gap-1">
                          {comp.capabilities.map((cap) => (
                            <span key={cap} className="rounded bg-accent px-1.5 py-0.5 text-[10px] text-foreground">
                              {cap}
                            </span>
                          ))}
                        </div>
                      </div>
                      {comp.dependencies.length > 0 && (
                        <div>
                          <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1">
                            {t("registry.dependencies", "Dependencies")}
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {comp.dependencies.map((dep) => (
                              <span key={dep} className="flex items-center gap-1 rounded bg-accent px-1.5 py-0.5 text-[10px] text-foreground">
                                <Link2 className="h-2.5 w-2.5" />
                                {dep}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      <div className="text-[10px] text-muted-foreground">
                        API: <code className="rounded bg-muted px-1">{comp.api_prefix}</code>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((comp) => {
              const hs = getHealthStatus(comp.id);
              const isExpanded = expandedId === comp.id;
              return (
                <div
                  key={comp.id}
                  className={cn(
                    "rounded-lg border border-border bg-card transition-all hover:border-primary/30 cursor-pointer",
                    isExpanded && "ring-1 ring-primary/30"
                  )}
                  onClick={() => setExpandedId(isExpanded ? null : comp.id)}
                >
                  <div className="flex items-center gap-2 lg:gap-4 px-4 py-3">
                    <span className="text-xl">{comp.emoji}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-xs lg:text-sm">{comp.name}</span>
                        <span className={cn(
                          "flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
                          hs === "ok" ? "bg-emerald-500/10 text-emerald-400" :
                          hs === "unknown" ? "bg-muted text-muted-foreground" :
                          "bg-red-500/10 text-red-400"
                        )}>
                          {hs === "ok" ? "UP" : hs === "unknown" ? "N/A" : hs.toUpperCase()}
                        </span>
                        <span className={cn("rounded-full border px-2 py-0.5 text-[10px]", CATEGORY_COLORS[comp.category]?.border, CATEGORY_COLORS[comp.category]?.text)}>
                          {comp.category}
                        </span>
                        <span className={cn("rounded-full border px-2 py-0.5 text-[10px]", PHASE_COLORS[comp.phase] || "border-border text-muted-foreground")}>
                          {comp.phase}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5 truncate">{comp.description}</p>
                    </div>
                    <div className="text-xs text-muted-foreground shrink-0">{comp.layer}</div>
                    {isExpanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                  </div>

                  {isExpanded && (
                    <div className="border-t border-border px-4 py-3 space-y-2">
                      <div className="grid grid-cols-2 gap-2 lg:gap-4">
                        <div>
                          <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1">能力</div>
                          <div className="flex flex-wrap gap-1">
                            {comp.capabilities.map((cap) => (
                              <span key={cap} className="rounded bg-accent px-1.5 py-0.5 text-[10px]">{cap}</span>
                            ))}
                          </div>
                        </div>
                        <div>
                          <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider mb-1">依赖</div>
                          <div className="flex flex-wrap gap-1">
                            {comp.dependencies.length > 0 ? comp.dependencies.map((dep) => (
                              <span key={dep} className="flex items-center gap-1 rounded bg-accent px-1.5 py-0.5 text-[10px]">
                                <Link2 className="h-2.5 w-2.5" />{dep}
                              </span>
                            )) : <span className="text-xs text-muted-foreground">None</span>}
                          </div>
                        </div>
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        API: <code className="rounded bg-muted px-1">{comp.api_prefix}</code>
                        {" · "}Health: <code className="rounded bg-muted px-1">{comp.health_endpoint}</code>
                        {" · "}Version: {comp.version}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
