"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Network, RefreshCw, CheckCircle, XCircle, Loader2,
  Layers, GitBranch, Activity, Clock, Zap,
  Box, Server, Cpu, Globe,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface TopologyStats {
  status: string;
  component: string;
  total_nodes: number;
  total_edges: number;
  by_category: Record<string, number>;
}

interface TopologyNode {
  id: string;
  name: string;
  category: string;
  layer: string;
  emoji: string;
  health: string;
  response_time_ms: number;
}

interface TopologyEdge {
  source: string;
  target: string;
  type: string;
}

interface TopologyGraph {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  core: { bg: "bg-violet-500/10", text: "text-violet-500", border: "border-violet-500/30" },
  service: { bg: "bg-amber-500/10", text: "text-amber-500", border: "border-amber-500/30" },
  organ: { bg: "bg-cyan-500/10", text: "text-cyan-500", border: "border-cyan-500/30" },
  system: { bg: "bg-emerald-500/10", text: "text-emerald-500", border: "border-emerald-500/30" },
  platform: { bg: "bg-blue-500/10", text: "text-blue-500", border: "border-blue-500/30" },
  advanced: { bg: "bg-rose-500/10", text: "text-rose-500", border: "border-rose-500/30" },
};

function HealthBadge({ health }: { health: string }) {
  if (health === "ok" || health === "healthy") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-500">
        <CheckCircle size={10} /> Healthy
      </span>
    );
  }
  if (health === "error" || health === "unhealthy") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-500">
        <XCircle size={10} /> Error
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-500">
      <Clock size={10} /> {health || "未知"}
    </span>
  );
}

function ResponseTimeBadge({ ms }: { ms: number }) {
  const color = ms < 50 ? "text-emerald-500" : ms < 200 ? "text-amber-500" : ms < 1000 ? "text-orange-500" : "text-red-500";
  return (
    <span className={cn("text-[10px] font-mono", color)}>
      {ms.toFixed(1)}ms
    </span>
  );
}

export function TopologyClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [stats, setStats] = useState<TopologyStats | null>(null);
  const [graph, setGraph] = useState<TopologyGraph | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<string>("all");
  const [sortBy, setSortBy] = useState<"name" | "response" | "category">("category");

  const fetchStats = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [statsRes, graphRes] = await Promise.all([
        fetch(`${apiBase}/api/topology/stats`),
        fetch(`${apiBase}/api/topology/graph`),
      ]);
      if (statsRes.ok) setStats(await statsRes.json());
      else setError(`Stats: HTTP ${statsRes.status}`);
      if (graphRes.ok) setGraph(await graphRes.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch topology");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    fetchStats();
    const interval = setInterval(fetchStats, 30000);
    return () => clearInterval(interval);
  }, [fetchStats]);

  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const categories = stats?.by_category || {};

  const filteredNodes = filter === "all"
    ? nodes
    : nodes.filter((n) => n.category === filter);

  const sortedNodes = [...filteredNodes].sort((a, b) => {
    if (sortBy === "response") return a.response_time_ms - b.response_time_ms;
    if (sortBy === "name") return a.name.localeCompare(b.name);
    return a.category.localeCompare(b.category);
  });

  const categoryIcons: Record<string, React.ReactNode> = {
    core: <Cpu size={12} />,
    service: <Server size={12} />,
    organ: <Activity size={12} />,
    system: <Box size={12} />,
    platform: <Globe size={12} />,
    advanced: <Zap size={12} />,
  };

  return (
      <PageLayout title="系统拓扑">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-3">
          <Network size={20} className="text-violet-500" />
          <h1 className="text-lg font-semibold">{t("topology.title") || "System Topology"}</h1>
          <span className="rounded-full bg-violet-500/10 px-2 py-0.5 text-xs font-medium text-violet-500">
            {t("topology.badge") || "Graph"}
          </span>
        </div>
        <button
          onClick={fetchStats}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          {t("common.refresh") || "Refresh"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 lg:p-6 space-y-3 lg:space-y-6">
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 lg:p-4 text-xs lg:text-sm text-red-500">
            {error}
          </div>
        )}

        {stats && (
          <>
            {/* Summary Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("topology.totalNodes") || "Nodes"}</span>
                  <div className="rounded-lg p-1.5 bg-violet-500/10"><Box size={14} className="text-violet-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{stats.total_nodes}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("topology.totalEdges") || "Edges"}</span>
                  <div className="rounded-lg p-1.5 bg-blue-500/10"><GitBranch size={14} className="text-blue-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{stats.total_edges}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("topology.categories") || "Categories"}</span>
                  <div className="rounded-lg p-1.5 bg-emerald-500/10"><Layers size={14} className="text-emerald-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{Object.keys(categories).length}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("topology.status") || "Status"}</span>
                  <div className="rounded-lg p-1.5 bg-amber-500/10"><Activity size={14} className="text-amber-500" /></div>
                </div>
                <p className="text-lg font-bold capitalize">{stats.status}</p>
              </div>
            </div>

            {/* Category Breakdown */}
            <div className="rounded-xl border border-border bg-card p-5">
              <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2 mb-4">
                <Layers size={14} className="text-violet-500" />
                {t("topology.categoryBreakdown") || "Category Breakdown"}
              </h3>
              <div className="flex flex-wrap gap-3">
                {Object.entries(categories).map(([cat, count]) => {
                  const colors = CATEGORY_COLORS[cat] || CATEGORY_COLORS.system;
                  return (
                    <button
                      key={cat}
                      onClick={() => setFilter(filter === cat ? "all" : cat)}
                      className={cn(
                        "inline-flex items-center gap-2 rounded-lg border px-4 py-2.5 text-xs lg:text-sm transition-all",
                        filter === cat
                          ? `${colors.bg} ${colors.text} ${colors.border} ring-1 ring-current`
                          : "border-border bg-card hover:bg-muted"
                      )}
                    >
                      {categoryIcons[cat] || <Box size={12} />}
                      <span className="capitalize">{cat}</span>
                      <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium", colors.bg, colors.text)}>
                        {count}
                      </span>
                    </button>
                  );
                })}
                {filter !== "all" && (
                  <button
                    onClick={() => setFilter("all")}
                    className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground hover:bg-muted"
                  >
                    {t("topology.clearFilter") || "Clear filter"}
                  </button>
                )}
              </div>
            </div>
          </>
        )}

        {/* Node List */}
        {nodes.length > 0 && (
          <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="px-5 py-3 border-b border-border bg-muted/30">
              <div className="flex items-center justify-between">
                <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                  <Network size={14} className="text-violet-500" />
                  {t("topology.nodeList") || "Nodes"}
                  <span className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-500">
                    {filteredNodes.length}
                  </span>
                </h3>
                <div className="flex gap-1">
                  {(["category", "name", "response"] as const).map((s) => (
                    <button
                      key={s}
                      onClick={() => setSortBy(s)}
                      className={cn(
                        "rounded-md px-2 py-1 text-[10px] transition-colors",
                        sortBy === s ? "bg-violet-500/10 text-violet-500" : "text-muted-foreground hover:bg-muted"
                      )}
                    >
                      {s === "response" ? (t("topology.sortByResponse") || "Response") : s === "name" ? (t("topology.sortByName") || "Name") : (t("topology.sortByCategory") || "Category")}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="divide-y divide-border max-h-[600px] overflow-y-auto">
              {sortedNodes.map((node) => {
                const colors = CATEGORY_COLORS[node.category] || CATEGORY_COLORS.system;
                return (
                  <div key={node.id} className="flex items-center gap-2 lg:gap-4 px-5 py-3 hover:bg-muted/30 transition-colors">
                    <span className="text-lg">{node.emoji || "⚙️"}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs lg:text-sm font-medium">{node.name}</span>
                        <span className={cn("rounded-full px-1.5 py-0.5 text-[9px] capitalize", colors.bg, colors.text)}>
                          {node.category}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground truncate">{node.layer}</p>
                    </div>
                    <HealthBadge health={node.health} />
                    <ResponseTimeBadge ms={node.response_time_ms} />
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Loading state */}
        {!stats && loading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 size={24} className="animate-spin text-violet-500" />
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
